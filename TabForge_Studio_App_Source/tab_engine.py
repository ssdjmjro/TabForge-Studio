#!/usr/bin/env python3
import copy
import os
import tempfile
import threading
import wave
from collections import namedtuple
from fractions import Fraction

import numpy as np
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

SAMPLE_RATE = 44100
EPSILON = 1e-6  # tolerance when comparing lengths in quarter notes
NOTE_NAMES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "16th", 32: "32nd"}
ASCII_RHYTHM = {1: "w", 2: "h", 4: "q", 8: "e", 16: "s", 32: "t"}

TUNINGS = {
    "Standard E":     ([329.63, 246.94, 196.00, 146.83, 110.00, 82.41],  ['e', 'B', 'G', 'D', 'A', 'E']),
    "Drop D":         ([329.63, 246.94, 196.00, 146.83, 110.00, 73.42],  ['e', 'B', 'G', 'D', 'A', 'D']),
    "Half-Step Down": ([311.13, 233.08, 185.00, 138.59, 103.83, 77.78],  ['Eb', 'Bb', 'Gb', 'Db', 'Ab', 'Eb']),
    "Full-Step Down": ([293.66, 220.00, 174.61, 130.81, 98.00,  73.42],  ['D', 'A', 'F', 'C', 'G', 'D']),
    "Drop C":         ([293.66, 220.00, 174.61, 130.81, 98.00,  65.41],  ['d', 'A', 'F', 'C', 'G', 'C']),
    "Open G":         ([293.66, 246.94, 196.00, 146.83, 98.00,  73.42],  ['D', 'B', 'G', 'D', 'G', 'D']),
    "Open D":         ([293.66, 220.00, 185.00, 146.83, 110.00, 73.42],  ['D', 'A', 'F#', 'D', 'A', 'D']),
    "DADGAD":         ([293.66, 220.00, 196.00, 146.83, 110.00, 73.42],  ['D', 'A', 'G', 'D', 'A', 'D']),
}

BUILTIN_TUNINGS = frozenset(TUNINGS)

TimelineBeat = namedtuple('TimelineBeat', 'time duration measure beat')
Render = namedtuple('Render', 'wav_path lead_in end_time timeline loop')


def note_value(quarters):
    """Names a length: (denominator, dotted, triplet), or None if it isn't a standard value."""
    for den in (1, 2, 4, 8, 16, 32):
        for factor, dotted, triplet in ((1.0, False, False), (1.5, True, False), (2.0 / 3.0, False, True)):
            if abs(4.0 / den * factor - quarters) < 1e-4:
                return den, dotted, triplet
    return None


def note_label(quarters):
    value = note_value(quarters)
    if value is None:
        return f"{quarters:.3g} beats"
    den, dotted, triplet = value
    return ("dotted " if dotted else "") + NOTE_NAMES[den] + (" triplet" if triplet else "")


def flag_count(quarters):
    """Flags on the stem of a note of this length."""
    value = note_value(quarters)
    den = value[0] if value else 4.0 / quarters
    return 3 if den >= 32 else 2 if den >= 16 else 1 if den >= 8 else 0


def length_text(quarters):
    """A length as a fraction of a whole note: "1/8", "1/8." dotted, "1/8t" triplet, "5/16"."""
    value = note_value(quarters)
    if value is None:
        fraction = Fraction(quarters / 4.0).limit_denominator(256)
        return f"{fraction.numerator}/{fraction.denominator}"
    den, dotted, triplet = value
    return f"1/{den}" + ("." if dotted else "") + ("t" if triplet else "")


def rhythm_letter(quarters):
    """Short rhythm mark for text tabs, e.g. "q." for a dotted quarter."""
    value = note_value(quarters)
    if value is None:
        return "?"
    den, dotted, triplet = value
    return ASCII_RHYTHM[den] + ("." if dotted else "") + ("3" if triplet else "")


def tech_tokens(technique):
    """Techniques stack as space-separated codes, e.g. "PM /"."""
    return set((technique or "").split())


class Note:
    def __init__(self, string_idx, fret, technique="", grace_fret=None, bend_val=None, bend_type=None):
        self.string_idx = string_idx  # 0 = high e, 5 = low E
        self.fret = fret
        self.technique = technique    # space-separated codes: NH AH x PM h p / \ b ~ t
        self.grace_fret = grace_fret
        self.bend_val = bend_val      # in whole steps: 0.5, 1.0, 1.5 or 2.0
        self.bend_type = bend_type    # "bend", "bend_release", "prebend" or "prebend_release"

    def to_dict(self):
        d = {"string_idx": self.string_idx, "fret": self.fret, "technique": self.technique}
        if self.grace_fret is not None:
            d["grace_fret"] = self.grace_fret
        if self.bend_val is not None:
            d["bend_val"] = self.bend_val
        if self.bend_type is not None:
            d["bend_type"] = self.bend_type
        return d

    @staticmethod
    def from_dict(d):
        return Note(
            d["string_idx"],
            d["fret"],
            d.get("technique", ""),
            d.get("grace_fret", None),
            d.get("bend_val", None),
            d.get("bend_type", None)
        )

class Beat:
    def __init__(self, duration=0.5):
        self.duration = duration
        self.notes = {}

    def set_note(self, string_idx, fret, technique="", bend_val=None, bend_type=None):
        """Writes a note, keeping what the old note had; a negative fret erases it."""
        old = self.notes.get(string_idx)
        if fret < 0:
            self.notes.pop(string_idx, None)
            return
        if technique == "x":
            fret = 0
        grace_fret = None
        if old:
            if not technique and old.technique != "x":  # typing a fret over a dead note replaces it
                technique = old.technique
            grace_fret = old.grace_fret
            bend_val = old.bend_val if bend_val is None else bend_val
            bend_type = old.bend_type if bend_type is None else bend_type
        self.notes[string_idx] = Note(string_idx, fret, technique, grace_fret, bend_val, bend_type)

    def to_dict(self):
        return {
            "duration": self.duration,
            "notes": {str(k): v.to_dict() for k, v in self.notes.items()}
        }

    @staticmethod
    def from_dict(d):
        b = Beat(d.get("duration", 0.5))
        for k, v in d.get("notes", {}).items():
            b.notes[int(k)] = Note.from_dict(v)
        return b

class Measure:
    def __init__(self, slots=8, beat_duration=0.5, time_sig=(4, 4)):
        self.beats = [Beat(beat_duration) for _ in range(slots)]
        self.time_sig = tuple(time_sig)

    def quarters(self):
        """Bar length in quarter notes, fixed by the time signature."""
        return self.time_sig[0] * 4.0 / self.time_sig[1]

    def beat_quarters(self):
        """Length of each slot in quarter notes; slots always fill the bar exactly."""
        total = sum(b.duration for b in self.beats)
        scale = self.quarters() / total if total > 0 else 0.0
        return [b.duration * scale for b in self.beats]

    def click_grid(self):
        """(clicks per bar, quarters per click). Compound metres click dotted quarters."""
        num, den = self.time_sig
        unit = 4.0 / den
        if den == 8 and num % 3 == 0:
            return num // 3, unit * 3
        return num, unit

    def normalize(self):
        """Stores slot lengths as true quarter-note values."""
        for beat, quarters in zip(self.beats, self.beat_quarters()):
            beat.duration = quarters

    def slot_edges(self):
        """Slot boundaries across the bar, 0.0 to 1.0, in proportion to time, so a note's
        width on screen is its length and beats line up from bar to bar."""
        edges = [0.0]
        for q in self.beat_quarters():
            edges.append(edges[-1] + q / self.quarters())
        return edges

    def grid(self):
        """The length shared by every slot, or None for a bar of mixed lengths."""
        lengths = self.beat_quarters()
        return lengths[0] if max(lengths) - min(lengths) < EPSILON else None

    def _slots_for(self, total, quarters):
        """Slot lengths that fill `total`: as many as fit, then whatever is left over."""
        count = int((total + EPSILON) / quarters)
        lengths = [quarters] * count
        if total - count * quarters > EPSILON:
            lengths.append(total - count * quarters)
        return lengths

    def _refill_gap(self, idx, quarters):
        """Recuts the empty slots from idx up to the next note into this length."""
        end = idx
        while end < len(self.beats) and not self.beats[end].notes:
            end += 1
        gap = sum(b.duration for b in self.beats[idx:end])
        if gap > EPSILON:
            self.beats[idx:end] = [Beat(q) for q in self._slots_for(gap, quarters)]

    def set_slot_duration(self, idx, quarters, fill=False):
        """Gives one slot a new length, keeping every later note at the same time.
        A shorter slot leaves new empty slots of the same value; a longer one absorbs
        the slots it now covers. With fill, the empty slots up to the next note take
        the new length too, ready to type into. Returns the notes absorbed, or None if
        it won't fit."""
        self.normalize()
        beat = self.beats[idx]
        start = sum(b.duration for b in self.beats[:idx])
        if start + quarters > self.quarters() + EPSILON:
            return None

        old = beat.duration
        beat.duration = quarters
        dropped = 0
        if quarters < old - EPSILON:
            remainder = old - quarters
            self.beats[idx + 1:idx + 1] = [Beat(q) for q in self._slots_for(remainder, quarters)]
        else:
            end = start + quarters
            now = start + old
            while idx + 1 < len(self.beats) and now < end - EPSILON:
                absorbed = self.beats.pop(idx + 1)
                dropped += len(absorbed.notes)
                now += absorbed.duration
                if now > end + EPSILON:
                    self.beats.insert(idx + 1, Beat(now - end))
        if fill:
            self._refill_gap(idx + 1, quarters)
        return dropped

    def split_slot(self, idx, parts):
        """Divides a slot into equal parts, e.g. a quarter into three triplet eighths."""
        self.normalize()
        beat = self.beats[idx]
        beat.duration /= parts
        self.beats[idx + 1:idx + 1] = [Beat(beat.duration) for _ in range(parts - 1)]

    def merge_triplet(self, idx):
        """Undoes a triplet: its three slots become one. Returns (first slot, notes
        absorbed), or None if the slot isn't part of a complete group of three."""
        self.normalize()
        length = self.beats[idx].duration
        run_start = idx
        while run_start > 0 and abs(self.beats[run_start - 1].duration - length) < EPSILON:
            run_start -= 1
        first = idx - (idx - run_start) % 3
        group = self.beats[first:first + 3]
        if len(group) < 3 or any(abs(b.duration - length) > EPSILON for b in group):
            return None
        return first, self.set_slot_duration(first, length * 3)

    def set_grid(self, quarters):
        """Recuts the bar into slots of one length, moving each note to the nearest slot.
        Returns how many notes had to merge away."""
        new_beats = [Beat(q) for q in self._slots_for(self.quarters(), quarters)]
        new_starts = [0.0]
        for b in new_beats[:-1]:
            new_starts.append(new_starts[-1] + b.duration)
        dropped = 0
        start = 0.0
        for beat, length in zip(self.beats, self.beat_quarters()):
            nearest = min(range(len(new_starts)), key=lambda i: abs(new_starts[i] - start))
            target = new_beats[nearest]
            for s, note in beat.notes.items():
                if s in target.notes:
                    dropped += 1
                else:
                    target.notes[s] = note
            start += length
        self.beats = new_beats
        return dropped

    def set_time_signature(self, time_sig):
        """Changes the bar length, keeping slot lengths. A shorter bar cuts slots off the
        end (returns the notes lost); a longer one gains empty slots."""
        self.normalize()
        self.time_sig = tuple(time_sig)
        bar = self.quarters()
        kept, now, dropped = [], 0.0, 0
        for beat in self.beats:
            if now < bar - EPSILON:
                beat.duration = min(beat.duration, bar - now)
                kept.append(beat)
                now += beat.duration
            else:
                dropped += len(beat.notes)
        slot = kept[-1].duration
        while now < bar - EPSILON:
            kept.append(Beat(min(slot, bar - now)))
            now += kept[-1].duration
        self.beats = kept
        return dropped

    def to_dict(self):
        d = {
            "beats_per_measure": len(self.beats),
            "beats": [b.to_dict() for b in self.beats]
        }
        if self.time_sig != (4, 4):
            d["time_sig"] = list(self.time_sig)
        return d

    @staticmethod
    def from_dict(d):
        m = Measure(0, time_sig=d.get("time_sig", (4, 4)))
        m.beats = [Beat.from_dict(b) for b in d.get("beats", [])] or [Beat(m.quarters())]
        return m

class TabTrack:
    def __init__(self, title="My Guitar Track", bpm=120, speed_multiplier=1.0):
        self.title = title
        self.artist = "Guitarist"
        self.bpm = bpm
        self.speed_multiplier = speed_multiplier
        self.tuning_name = "Standard E"
        self.custom_open_freqs = None
        self.custom_string_names = None
        self.capo = 0
        self.measures = [Measure(8, 0.5), Measure(8, 0.5), Measure(8, 0.5)]

    def seconds_per_quarter(self):
        return 60.0 / (max(1, self.bpm) * max(0.01, self.speed_multiplier))

    def has_notes(self):
        return any(b.notes for m in self.measures for b in m.beats)

    def get_open_freqs(self):
        if self.custom_open_freqs and len(self.custom_open_freqs) == 6:
            freqs = list(self.custom_open_freqs)
        else:
            freqs, _ = TUNINGS.get(self.tuning_name, TUNINGS["Standard E"])
        if self.capo > 0:
            freqs = [f * (2.0 ** (self.capo / 12.0)) for f in freqs]
        return freqs

    def get_string_names(self):
        if self.custom_string_names and len(self.custom_string_names) == 6:
            return self.custom_string_names
        _, names = TUNINGS.get(self.tuning_name, TUNINGS["Standard E"])
        return names

    def add_measure(self, after=None):
        """Inserts an empty bar after index `after` (default: at the end), matching its neighbour."""
        idx = len(self.measures) - 1 if after is None else after
        ref = self.measures[idx]
        count = len(ref.beats)
        self.measures.insert(idx + 1, Measure(count, ref.quarters() / count, ref.time_sig))

    def to_dict(self):
        d = {
            "title": self.title,
            "artist": self.artist,
            "bpm": self.bpm,
            "speed_multiplier": self.speed_multiplier,
            "tuning_name": self.tuning_name,
            "capo": self.capo,
            "measures": [m.to_dict() for m in self.measures]
        }
        if self.custom_open_freqs:
            d["custom_open_freqs"] = self.custom_open_freqs
        if self.custom_string_names:
            d["custom_string_names"] = self.custom_string_names
        return d

    @staticmethod
    def from_dict(d):
        t = TabTrack(d.get("title", "My Guitar Track"), d.get("bpm", 120), d.get("speed_multiplier", 1.0))
        t.artist = d.get("artist", "Guitarist")
        t.tuning_name = d.get("tuning_name", "Standard E")
        t.capo = d.get("capo", 0)
        if t.tuning_name not in BUILTIN_TUNINGS:
            t.custom_open_freqs = d.get("custom_open_freqs", None)
            t.custom_string_names = d.get("custom_string_names", None)
            if t.custom_open_freqs and t.custom_string_names:
                TUNINGS[t.tuning_name] = (t.custom_open_freqs, t.custom_string_names)
        t.measures = [Measure.from_dict(m) for m in d.get("measures", [])] or [Measure(8, 0.5)]
        return t

    def export_ascii(self, bars_per_line=4):
        header = [f"Title: {self.title}", f"Tuning: {self.tuning_name} (Capo: {self.capo})", f"BPM: {self.bpm}",
                  "Rhythm: w whole, h half, q quarter, e eighth, s 16th, t 32nd, . dotted, 3 triplet", ""]
        names = self.get_string_names()

        def cell(note):
            tokens = tech_tokens(note.technique)
            if "x" in tokens:
                return "x"
            if "NH" in tokens:
                return f"<{note.fret}>"
            if "AH" in tokens:
                return f"[{note.fret}]"
            return str(note.fret) + "".join(c for c in ("h", "p", "/", "\\", "b", "~") if c in tokens)

        lines = list(header)
        for first in range(0, len(self.measures), bars_per_line):
            bars = self.measures[first:first + bars_per_line]
            rows = [f"{names[s]:>2}|" for s in range(6)]
            rhythm = "   "
            for bar in bars:
                for beat, quarters in zip(bar.beats, bar.beat_quarters()):
                    cells = [cell(beat.notes[s]) if s in beat.notes else "" for s in range(6)]
                    width = max(len(c) for c in cells) + 1
                    for s in range(6):
                        rows[s] += cells[s].ljust(width, "-")
                    rhythm += rhythm_letter(quarters).ljust(width) if beat.notes else " " * width
                rows = [r + "|" for r in rows]
                rhythm += " "
            lines.append(f"Bars {first + 1}-{first + len(bars)}")
            lines.extend(rows)
            lines.append(rhythm.rstrip())
            lines.append("")
        return "\n".join(lines)


def _karplus_strong(freq, n_samples, decay, sample_rate):
    """Plucked string, tuned exactly: the integer delay line gives a period of
    N + 0.5 samples, so the result is resampled onto the true pitch period."""
    period = sample_rate / freq
    n = max(2, int(period - 0.5))
    ratio = (n + 0.5) / period
    length = int(n_samples * ratio) + 3
    y = np.zeros(max(length, n + 2))
    y[:n + 1] = np.random.uniform(-1.0, 1.0, n + 1)
    k = n + 1
    while k < len(y):
        e = min(k + n, len(y))
        y[k:e] = 0.5 * decay * (y[k - n:e - n] + y[k - n - 1:e - n - 1])
        k = e
    return np.interp(np.arange(n_samples) * ratio, np.arange(len(y)), y)


def synthesize_fret_sound(open_freq, fret, duration_sec, technique="", sample_rate=SAMPLE_RATE,
                          bend_val=None, bend_type=None, prev_fret=None):
    n_samples = int(sample_rate * duration_sec)
    if fret < 0 or open_freq <= 0 or n_samples <= 0:
        return np.zeros(max(0, n_samples))

    tokens = tech_tokens(technique)
    t = np.arange(n_samples) / float(sample_rate)
    is_slide_up = bool(tokens & {"/", "s"})
    is_slide_down = bool(tokens & {"\\", "S"})

    if "x" in tokens:
        noise = np.random.uniform(-1.0, 1.0, n_samples)
        thud = np.sin(2 * np.pi * 120.0 * t) * 0.5
        return (noise * 0.7 + thud) * np.exp(-45.0 * t) * 0.55

    if "NH" in tokens:
        multiple = {12: 2.0, 24: 2.0, 7: 3.0, 19: 3.0, 5: 4.0, 17: 4.0, 4: 5.0, 9: 5.0, 16: 5.0}.get(fret, 2.0)
        harm_freq = open_freq * multiple
        wave_ = 0.85 * np.sin(2 * np.pi * harm_freq * t) + 0.15 * np.sin(2 * np.pi * harm_freq * 2 * t)
        return wave_ * np.exp(-3.2 * t) * 0.73

    if "AH" in tokens:
        harm_freq = open_freq * (2.0 ** (fret / 12.0)) * 2.0
        vibrato = 1.0 + 0.005 * np.sin(2 * np.pi * 6.0 * t)
        wave_ = 0.7 * np.sin(2 * np.pi * harm_freq * vibrato * t) + 0.3 * np.sin(2 * np.pi * harm_freq * 3 * t)
        return wave_ * np.exp(-4.0 * t) * 0.79

    if "b" in tokens or "~" in tokens or is_slide_up or is_slide_down:
        progress = t / float(duration_sec)
        if "b" in tokens:
            semis = (bend_val if bend_val is not None else 1.0) * 2.0
            b_type = bend_type or "bend"
            if b_type == "bend_release":
                shift = semis * (1.0 - np.abs(1.0 - progress * 2.0))
            elif b_type == "prebend":
                shift = np.full(n_samples, semis)
            elif b_type == "prebend_release":
                shift = semis * np.where(progress < 0.3, 1.0, 1.0 - (progress - 0.3) / 0.7)
            else:
                shift = semis * np.minimum(1.0, progress * 1.8)
            frets = fret + shift
        elif "~" in tokens and not (is_slide_up or is_slide_down):
            frets = fret + 0.015 * np.sin(2 * np.pi * 6.0 * t)
        else:
            if is_slide_up:
                start_fret = prev_fret if prev_fret is not None and prev_fret < fret else max(0, fret - 2)
            else:
                start_fret = prev_fret if prev_fret is not None and prev_fret > fret else fret + 2
            frets = start_fret + (fret - start_fret) * np.minimum(1.0, progress * 3.5)

        phase = np.cumsum(2.0 * np.pi * open_freq * (2.0 ** (frets / 12.0)) / sample_rate)
        pick = np.exp(-90.0 * t) * np.random.uniform(-1.0, 1.0, n_samples) * 0.12
        harmonics = sum(a * np.sin(phase * h) for h, a in
                        ((1.0, 0.50), (2.0, 0.26), (3.0, 0.14), (4.0, 0.07), (5.0, 0.03)))
        amp = 0.55 if "PM" in tokens else 0.67
        return (harmonics + pick) * np.exp(-3.2 * t) * amp

    decay = 0.996
    if "PM" in tokens:
        decay = 0.920
    elif tokens & {"h", "p", "t"}:
        decay = 0.988
    amp = 0.55 if "PM" in tokens else 0.67
    return _karplus_strong(open_freq * (2.0 ** (fret / 12.0)), n_samples, decay, sample_rate) * amp


def _click_sound(accent):
    t = np.arange(int(0.03 * SAMPLE_RATE)) / float(SAMPLE_RATE)
    freq = 1760.0 if accent else 1175.0
    return np.sin(2 * np.pi * freq * t) * np.exp(-180.0 * t) * (0.75 if accent else 0.55)


def build_timeline(track, start_measure=0, end_measure=None):
    """The single source of timing: beat onsets in seconds from the start of
    `start_measure`. Audio and the playhead both read this, so they cannot disagree."""
    end_measure = len(track.measures) - 1 if end_measure is None else end_measure
    sec_per_quarter = track.seconds_per_quarter()
    timeline, now = [], 0.0
    for m_idx in range(start_measure, end_measure + 1):
        measure = track.measures[m_idx]
        for b_idx, quarters in enumerate(measure.beat_quarters()):
            dur = quarters * sec_per_quarter
            timeline.append(TimelineBeat(now, dur, m_idx, b_idx))
            now += dur
    return timeline, now


def render_track(track, wav_path, start_measure=0, end_measure=None, loop=False, metronome=False, count_in=False):
    """Renders bars start_measure..end_measure to a WAV file and returns its Render."""
    sr = SAMPLE_RATE
    end_measure = len(track.measures) - 1 if end_measure is None else end_measure
    timeline, body_sec = build_timeline(track, start_measure, end_measure)
    if body_sec <= 0:
        return None

    sec_per_quarter = track.seconds_per_quarter()
    clicks = []  # (time, accent)
    lead_in = 0.0
    if count_in:
        n, unit = track.measures[start_measure].click_grid()
        unit_sec = unit * sec_per_quarter
        lead_in = n * unit_sec
        clicks += [(i * unit_sec, i == 0) for i in range(n)]
    if metronome:
        bar_start = lead_in
        for m_idx in range(start_measure, end_measure + 1):
            measure = track.measures[m_idx]
            n, unit = measure.click_grid()
            clicks += [(bar_start + i * unit * sec_per_quarter, i == 0) for i in range(n)]
            bar_start += measure.quarters() * sec_per_quarter

    tail_sec = 0.0 if loop else 1.2
    mix = np.zeros(int(round((lead_in + body_sec + tail_sec) * sr)) + 1)
    open_freqs = track.get_open_freqs()

    def add(audio, start, gain=1.0):
        start = max(0, start)
        stop = min(len(mix), start + len(audio))
        if stop > start:
            mix[start:stop] += audio[:stop - start] * gain

    string_events = {s: [] for s in range(6)}
    for tb in timeline:
        for s, note in track.measures[tb.measure].beats[tb.beat].notes.items():
            string_events[s].append((int(round((lead_in + tb.time) * sr)), tb.duration, note))

    for s, events in string_events.items():
        prev_fret = None
        for i, (start, dur, note) in enumerate(events):
            has_next = i + 1 < len(events)
            if has_next:
                length = events[i + 1][0] - start
            else:
                length = min(int(max(dur * 2.0, 1.2) * sr), len(mix) - start)
            if length <= 0:
                continue

            if note.grace_fret is not None:
                # A grace note leads into the beat so the main note stays on time.
                grace_sec = min(0.06, dur * 0.3)
                g_audio = synthesize_fret_sound(open_freqs[s], note.grace_fret, grace_sec, "h")
                add(g_audio, start - len(g_audio), 0.33)

            audio = synthesize_fret_sound(open_freqs[s], note.fret, length / float(sr), note.technique,
                                          bend_val=note.bend_val, bend_type=note.bend_type, prev_fret=prev_fret)
            prev_fret = note.fret
            fade = min(200, len(audio))
            if has_next and fade:
                audio = audio.copy()
                audio[-fade:] *= np.linspace(1.0, 0.0, fade)
            add(audio, start)

    for when, accent in clicks:
        add(_click_sound(accent), int(round(when * sr)), 0.8)

    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    gain = 18000.0 if peak * 18000.0 <= 32000.0 else 32000.0 / peak  # never clip big chords
    mono = (mix * gain).astype('<i2')
    with wave.open(wav_path, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.repeat(mono, 2).tobytes())

    return Render(wav_path, lead_in, lead_in + body_sec, timeline, loop)


class TabAudioPlayer:
    """Renders off the main thread, then plays through one reusable playbin.
    The pipeline position is the only clock the UI follows."""

    def __init__(self):
        self.player = Gst.ElementFactory.make('playbin', 'tab_audio_player')
        self.render = None
        self.is_playing = False
        self.is_compiling = False
        self.on_start_cb = None
        self.on_stop_cb = None
        self.on_error_cb = None
        self._generation = 0
        self._seek_stage = None

        if self.player:
            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect('message::eos', self._on_eos)
            bus.connect('message::error', self._on_error)
            bus.connect('message::async-done', self._on_async_done)
            bus.connect('message::segment-done', self._on_segment_done)

    def play(self, tab_track, **options):
        """options: start_measure, end_measure, loop, metronome, count_in"""
        self.stop()
        self.is_compiling = True
        generation = self._generation
        snapshot = copy.deepcopy(tab_track)  # the user may keep editing during the render

        def worker():
            fd, wav_path = tempfile.mkstemp(prefix="tabforge_", suffix=".wav")
            os.close(fd)
            try:
                render = render_track(snapshot, wav_path, **options)
            except Exception as e:
                print("Render error:", e)
                render = None
            GLib.idle_add(self._start, generation, render, wav_path)

        threading.Thread(target=worker, daemon=True).start()

    def _start(self, generation, render, wav_path):
        if generation != self._generation or render is None or not self.player:
            _remove_quietly(wav_path)
            if generation == self._generation:
                self.is_compiling = False
                self._fail("Could not render this tab")
            return False

        self.is_compiling = False
        self.render = render
        self.player.set_property('uri', GLib.filename_to_uri(render.wav_path, None))
        self._seek_stage = 'preroll' if render.loop else None
        target = Gst.State.PAUSED if render.loop else Gst.State.PLAYING
        if self.player.set_state(target) == Gst.StateChangeReturn.FAILURE:
            self._fail("Audio output is unavailable")
            return False
        self.is_playing = True
        if callable(self.on_start_cb):
            self.on_start_cb()
        return False

    def _seek(self, flags, start_sec):
        self.player.seek(1.0, Gst.Format.TIME, flags,
                         Gst.SeekType.SET, int(start_sec * Gst.SECOND),
                         Gst.SeekType.SET, int(self.render.end_time * Gst.SECOND))

    def _on_async_done(self, bus, message):
        if self._seek_stage == 'preroll':
            self._seek_stage = 'seeking'
            self._seek(Gst.SeekFlags.FLUSH | Gst.SeekFlags.SEGMENT | Gst.SeekFlags.ACCURATE, 0.0)
        elif self._seek_stage == 'seeking':
            self._seek_stage = None
            self.player.set_state(Gst.State.PLAYING)

    def _on_segment_done(self, bus, message):
        if self.render:
            self._seek(Gst.SeekFlags.SEGMENT | Gst.SeekFlags.ACCURATE, self.render.lead_in)

    def _on_eos(self, bus, message):
        if self.is_playing:
            self._finish()

    def _on_error(self, bus, message):
        if self.is_playing:
            err, _ = message.parse_error()
            self._fail(f"Playback error: {err.message}")

    def _fail(self, text):
        self._finish()
        if callable(self.on_error_cb):
            self.on_error_cb(text)

    def _finish(self):
        self.stop()
        if callable(self.on_stop_cb):
            self.on_stop_cb()

    def position(self):
        """Seconds into the current render, as actually reported by the audio pipeline."""
        if not self.player or not self.is_playing:
            return None
        ok, nanos = self.player.query_position(Gst.Format.TIME)
        return nanos / Gst.SECOND if ok and nanos >= 0 else None

    def stop(self):
        self._generation += 1
        self.is_compiling = False
        self.is_playing = False
        self._seek_stage = None
        if self.player:
            self.player.set_state(Gst.State.NULL)
        if self.render:
            _remove_quietly(self.render.wav_path)
            self.render = None


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass
