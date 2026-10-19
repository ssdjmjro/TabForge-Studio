#!/usr/bin/env python3
import json
import wave
import array
import math
import os
import tempfile
import random
import time
import hashlib
import threading
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

TUNINGS = {
    "Standard E":     ([329.63, 246.94, 196.00, 146.83, 110.00, 82.41],  ['e', 'B', 'G', 'D', 'A', 'E']),
    "Drop D":         ([329.63, 246.94, 196.00, 146.83, 110.00, 73.42],  ['e', 'B', 'G', 'D', 'A', 'D']),
    "Half-Step Down": ([311.13, 233.08, 185.00, 138.59, 103.83, 77.78],  ['Eb', 'Bb', 'Gb', 'Db', 'Ab', 'Eb']),
    "Full-Step Down": ([293.66, 220.00, 174.61, 130.81, 98.00,  65.41],  ['D', 'A', 'F', 'C', 'G', 'D']),
    "Drop C":         ([293.66, 220.00, 174.61, 130.81, 98.00,  65.41],  ['d', 'A', 'F', 'C', 'G', 'C']),
    "Open G":         ([293.66, 246.94, 196.00, 146.83, 98.00,  73.42],  ['D', 'B', 'G', 'D', 'G', 'D']),
    "Open D":         ([293.66, 220.00, 185.00, 146.83, 110.00, 73.42],  ['D', 'F#', 'A', 'D', 'A', 'D']),
    "DADGAD":         ([293.66, 246.94, 196.00, 146.83, 110.00, 73.42],  ['D', 'A', 'G', 'D', 'A', 'D']),
}

class Note:
    def __init__(self, string_idx, fret, technique="", grace_fret=None, bend_val=None, bend_type=None):
        self.string_idx = string_idx  # 0 = High E, 5 = Low E
        self.fret = fret              # 0 to 22, or -1 for none
        self.technique = technique    # "", "NH", "AH", "x", "PM", "h", "p", "/", "\", "b", "~", "t"
        self.grace_fret = grace_fret  # Decorative grace note fret (0-22 or None)
        self.bend_val = bend_val      # Bend magnitude in steps (0.5=1/2, 1.0=Full, 1.5=1 1/2, 2.0=2)
        self.bend_type = bend_type    # "bend", "bend_release", "prebend", "prebend_release"

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

    def set_note(self, string_idx, fret, technique="", grace_fret=None, bend_val=None, bend_type=None):
        if fret is None or fret < 0:
            if technique == "x":
                self.notes[string_idx] = Note(string_idx, 0, "x", grace_fret, bend_val, bend_type)
            elif string_idx in self.notes:
                del self.notes[string_idx]
        else:
            if technique == "x":
                fret = 0
            current_tech = technique
            current_grace = grace_fret
            current_bend_val = bend_val
            current_bend_type = bend_type
            if string_idx in self.notes:
                if not technique:
                    current_tech = self.notes[string_idx].technique
                if grace_fret is None:
                    current_grace = self.notes[string_idx].grace_fret
                if bend_val is None:
                    current_bend_val = self.notes[string_idx].bend_val
                if bend_type is None:
                    current_bend_type = self.notes[string_idx].bend_type
            self.notes[string_idx] = Note(string_idx, fret, current_tech, current_grace, current_bend_val, current_bend_type)

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
    def __init__(self, beats_per_measure=8, beat_duration=0.5):
        self.beats_per_measure = beats_per_measure
        self.beats = [Beat(beat_duration) for _ in range(beats_per_measure)]

    def set_beats_count(self, num_beats, duration_per_beat=None):
        self.beats_per_measure = max(1, min(32, num_beats))
        if duration_per_beat is None:
            duration_per_beat = 4.0 / float(self.beats_per_measure)
        new_beats = []
        for i in range(self.beats_per_measure):
            if i < len(self.beats):
                b = self.beats[i]
                b.duration = duration_per_beat
                new_beats.append(b)
            else:
                new_beats.append(Beat(duration_per_beat))
        self.beats = new_beats

    def to_dict(self):
        return {
            "beats_per_measure": self.beats_per_measure,
            "beats": [b.to_dict() for b in self.beats]
        }

    @staticmethod
    def from_dict(d):
        num_beats = d.get("beats_per_measure", 8)
        m = Measure(num_beats)
        m.beats = [Beat.from_dict(b) for b in d.get("beats", [])]
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

    def set_beats_per_bar_all(self, num_beats):
        for m in self.measures:
            m.set_beats_count(num_beats)

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

    def add_measure(self):
        num_b = self.measures[-1].beats_per_measure if self.measures else 8
        dur_b = self.measures[-1].beats[0].duration if self.measures and self.measures[-1].beats else 0.5
        self.measures.append(Measure(num_b, dur_b))

    def remove_measure(self, idx):
        if len(self.measures) > 1 and 0 <= idx < len(self.measures):
            self.measures.pop(idx)

    def to_dict(self):
        d = {
            "title": self.title,
            "artist": self.artist,
            "bpm": self.bpm,
            "speed_multiplier": getattr(self, "speed_multiplier", 1.0),
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
        t.custom_open_freqs = d.get("custom_open_freqs", None)
        t.custom_string_names = d.get("custom_string_names", None)
        if t.custom_open_freqs and t.custom_string_names:
            TUNINGS[t.tuning_name] = (t.custom_open_freqs, t.custom_string_names)
        t.measures = [Measure.from_dict(m) for m in d.get("measures", [])]
        return t

    def export_ascii(self):
        lines = [f"Title: {self.title}", f"Tuning: {self.tuning_name} (Capo: {self.capo})", f"BPM: {self.bpm}\n"]
        string_names = self.get_string_names()
        
        for m_idx, measure in enumerate(self.measures):
            lines.append(f"Measure {m_idx + 1} ({measure.beats_per_measure} beats):")
            str_lines = {s: f"{string_names[s]:>2}|" for s in range(6)}
            
            for beat in measure.beats:
                for s in range(6):
                    if s in beat.notes:
                        n = beat.notes[s]
                        if n.technique == "x":
                            f_str = "x"
                        elif n.technique == "NH":
                            f_str = f"<{n.fret}>"
                        elif n.technique == "AH":
                            f_str = f"[{n.fret}]"
                        else:
                            f_str = str(n.fret)
                            if n.technique:
                                f_str += n.technique
                        str_lines[s] += f"-{f_str.center(4, '-')}-|"
                    else:
                        str_lines[s] += "----|"
                        
            for s in range(6):
                lines.append(str_lines[s])
            lines.append("")
            
        return "\n".join(lines)


def synthesize_fret_sound(open_freq, fret, duration_sec, technique="", sample_rate=44100, bend_val=None, bend_type=None, prev_fret=None):
    if fret < 0 or open_freq <= 0:
        return [0.0] * int(sample_rate * duration_sec)

    n_samples = int(sample_rate * duration_sec)

    if technique == "x":
        output = [0.0] * n_samples
        for i in range(n_samples):
            t = i / float(sample_rate)
            envelope = math.exp(-45.0 * t)
            noise = (random.random() * 2.0 - 1.0)
            thud = math.sin(2 * math.pi * 120.0 * t) * 0.5
            output[i] = (noise * 0.7 + thud) * envelope * 0.55
        return output

    elif technique == "NH":
        if fret in (12, 24): harm_freq = open_freq * 2.0
        elif fret in (7, 19): harm_freq = open_freq * 3.0
        elif fret in (5, 17): harm_freq = open_freq * 4.0
        elif fret in (4, 9, 16): harm_freq = open_freq * 5.0
        else: harm_freq = open_freq * 2.0
        output = [0.0] * n_samples
        decay_rate = 3.2
        for i in range(n_samples):
            t = i / float(sample_rate)
            envelope = math.exp(-decay_rate * t)
            output[i] = (0.85 * math.sin(2 * math.pi * harm_freq * t) + 0.15 * math.sin(2 * math.pi * harm_freq * 2 * t)) * envelope * 0.73
        return output

    elif technique == "AH":
        fretted_freq = open_freq * (2.0 ** (fret / 12.0))
        harm_freq = fretted_freq * 2.0
        output = [0.0] * n_samples
        decay_rate = 4.0
        for i in range(n_samples):
            t = i / float(sample_rate)
            envelope = math.exp(-decay_rate * t)
            vibrato = 1.0 + 0.005 * math.sin(2 * math.pi * 6.0 * t)
            output[i] = (0.7 * math.sin(2 * math.pi * harm_freq * vibrato * t) + 0.3 * math.sin(2 * math.pi * harm_freq * 3 * t)) * envelope * 0.79
        return output

    elif "b" in technique or technique in ("~", "/", "\\", "s", "S") or "/" in technique or "\\" in technique or "s" in technique or "S" in technique:
        # Lowest string (open_freq <= 85.0 Hz, e.g. Low E string) slide notes should be an octave higher
        if open_freq <= 85.0 and ("/" in technique or "\\" in technique or "s" in technique or "S" in technique):
            open_freq *= 2.0

        start_fret = fret
        end_fret = fret
        if "/" in technique or "s" in technique:
            if prev_fret is not None and prev_fret < fret:
                start_fret = prev_fret
            else:
                start_fret = max(0, fret - 2)
        elif "\\" in technique or "S" in technique:
            if prev_fret is not None and prev_fret > fret:
                start_fret = prev_fret
            else:
                start_fret = fret + 2

        output = [0.0] * n_samples
        decay_rate = 3.2
        amp = 0.55 if technique == "PM" else 0.67

        b_val = bend_val if bend_val is not None else 1.0
        b_type = bend_type if bend_type is not None else "bend"
        bend_semitones = b_val * 2.0

        phase = 0.0
        for i in range(n_samples):
            t = i / float(sample_rate)
            envelope = math.exp(-decay_rate * t)
            progress = t / float(duration_sec) if duration_sec > 0 else 1.0

            if technique == "~":
                vib = 0.015 * math.sin(2 * math.pi * 6.0 * t)
                cur_freq = open_freq * (2.0 ** ((fret + vib) / 12.0))
            elif "b" in technique:
                if b_type == "bend_release":
                    pitch_shift = bend_semitones * (progress * 2.0) if progress < 0.5 else bend_semitones * (2.0 - progress * 2.0)
                elif b_type == "prebend":
                    pitch_shift = bend_semitones
                elif b_type == "prebend_release":
                    pitch_shift = bend_semitones if progress < 0.3 else bend_semitones * (1.0 - (progress - 0.3) / 0.7)
                else:
                    pitch_shift = bend_semitones * min(1.0, progress * 1.8)
                cur_freq = open_freq * (2.0 ** ((fret + pitch_shift) / 12.0))
            else:
                slide_progress = min(1.0, progress * 3.5)
                cur_fret = start_fret + (end_fret - start_fret) * slide_progress
                cur_freq = open_freq * (2.0 ** (cur_fret / 12.0))

            phase += 2.0 * math.pi * cur_freq / sample_rate

            pick_attack = math.exp(-90.0 * t) * (random.random() * 2.0 - 1.0) * 0.12
            guitar_harmonics = (
                0.50 * math.sin(phase) +
                0.26 * math.sin(phase * 2.0) +
                0.14 * math.sin(phase * 3.0) +
                0.07 * math.sin(phase * 4.0) +
                0.03 * math.sin(phase * 5.0)
            )

            output[i] = (guitar_harmonics + pick_attack) * envelope * amp

        return output

    fundamental_freq = open_freq * (2.0 ** (fret / 12.0))
    buffer_size = max(1, int(sample_rate / fundamental_freq))
    ring_buffer = [(random.random() * 2.0 - 1.0) for _ in range(buffer_size)]

    output = [0.0] * n_samples
    decay_factor = 0.996
    if technique == "PM":
        decay_factor = 0.920
    elif technique in ("h", "p", "t"):
        decay_factor = 0.988

    amp = 0.55 if technique == "PM" else 0.67

    idx = 0
    for i in range(n_samples):
        val = ring_buffer[idx]
        next_idx = (idx + 1) % buffer_size
        ring_buffer[idx] = (ring_buffer[idx] + ring_buffer[next_idx]) * 0.5 * decay_factor
        idx = next_idx
        output[i] = val * amp

    return output


class TabAudioPlayer:
    def __init__(self):
        self.player = Gst.ElementFactory.make('playbin', 'tab_audio_player')
        self.temp_wav = None
        self.cached_wav = None
        self.cached_hash = None
        self.is_playing = False
        self.is_compiling = False
        self.compile_id = 0
        self.on_start_cb = None
        self.on_stop_cb = None
        self._precompile_timer = None
        self._lock = threading.Lock()

        if self.player:
            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect('message::eos', self._on_eos)
            bus.connect('message::error', self._on_error)

    def _on_eos(self, bus, message):
        if self.is_playing:
            GLib.idle_add(self._notify_stop)

    def _on_error(self, bus, message):
        if self.is_playing:
            GLib.idle_add(self._notify_stop)

    def _notify_stop(self):
        if not self.is_playing:
            return False
        self.stop()
        if callable(self.on_stop_cb):
            self.on_stop_cb()
        return False

    def get_track_hash(self, tab_track):
        try:
            d = {
                'bpm': tab_track.bpm,
                'speed_multiplier': getattr(tab_track, 'speed_multiplier', 1.0),
                'tuning': tab_track.tuning_name,
                'capo': tab_track.capo,
                'freqs': tab_track.custom_open_freqs,
                'measures': [m.to_dict() for m in tab_track.measures]
            }
            return hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()
        except Exception:
            return None

    def invalidate_cache(self):
        self.cached_hash = None
        if self.cached_wav and os.path.exists(self.cached_wav):
            try:
                os.remove(self.cached_wav)
            except Exception:
                pass
            self.cached_wav = None

    def request_precompile(self, tab_track):
        with self._lock:
            if self._precompile_timer is not None:
                try:
                    self._precompile_timer.cancel()
                except Exception:
                    pass

            def _do_precompile():
                if self.is_playing or self.is_compiling:
                    return
                track_hash = self.get_track_hash(tab_track)
                if not track_hash or (self.cached_wav and os.path.exists(self.cached_wav) and self.cached_hash == track_hash):
                    return
                try:
                    wav_path = self._compile_track_to_wav(
                        tab_track,
                        check_cancelled=lambda: self.is_playing or self.is_compiling
                    )
                    if wav_path and not (self.is_playing or self.is_compiling):
                        self.cached_wav = wav_path
                        self.cached_hash = self.get_track_hash(tab_track)
                except Exception:
                    pass

            self._precompile_timer = threading.Timer(0.4, _do_precompile)
            self._precompile_timer.daemon = True
            self._precompile_timer.start()

    def _compile_track_to_wav(self, tab_track, check_cancelled=None):
        if check_cancelled is None:
            check_cancelled = lambda: False

        if check_cancelled():
            return None

        temp_dir = tempfile.gettempdir()
        wav_path = os.path.join(temp_dir, f"tab_playback_{int(time.time() * 1000)}_{random.randint(1000,9999)}.wav")

        sample_rate = 44100
        quarter_note_sec = (60.0 / float(max(1, tab_track.bpm)))
        open_freqs = tab_track.get_open_freqs()

        total_samples_float = 0.0
        for measure in tab_track.measures:
            m_sum_dur = sum(b.duration for b in measure.beats)
            m_scale = 4.0 / float(max(0.001, m_sum_dur)) if m_sum_dur > 0 else 1.0
            for beat in measure.beats:
                total_samples_float += (beat.duration * m_scale) * quarter_note_sec * sample_rate

        total_samples = int(round(total_samples_float))

        if total_samples <= 0 or check_cancelled():
            return None

        mix_buffer = [0.0] * (total_samples + int(sample_rate * 2.0))
        string_timeline = {s: [] for s in range(6)}

        curr_sample_exact = 0.0
        for measure in tab_track.measures:
            m_sum_dur = sum(b.duration for b in measure.beats)
            m_scale = 4.0 / float(max(0.001, m_sum_dur)) if m_sum_dur > 0 else 1.0
            for beat in measure.beats:
                beat_duration_sec = (beat.duration * m_scale) * quarter_note_sec
                beat_samples_exact = beat_duration_sec * sample_rate
                curr_sample = int(round(curr_sample_exact))

                for string_idx, note in beat.notes.items():
                    string_timeline[string_idx].append((curr_sample, beat_duration_sec, note))

                curr_sample_exact += beat_samples_exact

        for string_idx, events in string_timeline.items():
            if check_cancelled():
                return None
            open_freq = open_freqs[string_idx]
            prev_fret = None
            for idx, (start_sample, beat_dur_sec, note) in enumerate(events):
                if check_cancelled():
                    return None
                if idx + 1 < len(events):
                    next_start_sample = events[idx + 1][0]
                    sustain_samples = next_start_sample - start_sample
                else:
                    max_trail_sec = max(beat_dur_sec * 2.0, 1.2)
                    sustain_samples = min(int(max_trail_sec * sample_rate), len(mix_buffer) - start_sample)

                sustain_sec = max(beat_dur_sec, sustain_samples / float(sample_rate))

                if getattr(note, "grace_fret", None) is not None:
                    grace_sec = min(0.06, beat_dur_sec * 0.3)
                    g_audio = synthesize_fret_sound(open_freq, note.grace_fret, grace_sec, "h", sample_rate)
                    g_len = min(int(grace_sec * sample_rate), len(g_audio))
                    for i in range(g_len):
                        t_idx = start_sample + i
                        if t_idx < len(mix_buffer):
                            mix_buffer[t_idx] += g_audio[i] * 0.33

                b_val = getattr(note, "bend_val", None)
                b_type = getattr(note, "bend_type", None)
                audio = synthesize_fret_sound(open_freq, note.fret, sustain_sec, note.technique, sample_rate, bend_val=b_val, bend_type=b_type, prev_fret=prev_fret)
                prev_fret = note.fret
                audio_len = len(audio)
                limit = min(sustain_samples, audio_len)
                fade_len = min(200, limit)
                has_next_event = (idx + 1 < len(events))
                normal_len = (limit - fade_len) if (has_next_event and limit >= fade_len) else limit

                end_normal = start_sample + normal_len
                if end_normal > len(mix_buffer):
                    normal_len = max(0, len(mix_buffer) - start_sample)
                    limit = min(limit, len(mix_buffer) - start_sample)

                for i in range(normal_len):
                    mix_buffer[start_sample + i] += audio[i]

                if has_next_event and normal_len < limit:
                    for i in range(normal_len, limit):
                        fade_factor = (sustain_samples - i) / float(fade_len)
                        mix_buffer[start_sample + i] += audio[i] * max(0.0, min(1.0, fade_factor))

        if check_cancelled():
            return None

        final_sample_count = min(len(mix_buffer), total_samples + int(sample_rate * 1.2))
        mono_ints = [max(-32768, min(32767, int(v * 18000.0))) for v in mix_buffer[:final_sample_count]]
        out_samples = array.array('h', [0]) * (len(mono_ints) * 2)
        out_samples[0::2] = array.array('h', mono_ints)
        out_samples[1::2] = out_samples[0::2]

        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(out_samples.tobytes())

        return wav_path

    def compile_and_play(self, tab_track):
        with self._lock:
            self.compile_id += 1
            self.is_compiling = True
            current_id = self.compile_id

        track_hash = self.get_track_hash(tab_track)

        # 1. Instant Cache Hit (0.1ms latency!)
        if track_hash and self.cached_wav and os.path.exists(self.cached_wav) and self.cached_hash == track_hash:
            self.temp_wav = self.cached_wav
            GLib.idle_add(self._start_gst_playback_main, current_id)
            return

        # 2. Compile full track cleanly in single step
        full_wav = self._compile_track_to_wav(
            tab_track,
            check_cancelled=lambda: self.compile_id != current_id or not self.is_compiling
        )

        if full_wav and self.compile_id == current_id and self.is_compiling:
            self.temp_wav = full_wav
            self.cached_wav = full_wav
            self.cached_hash = track_hash
            GLib.idle_add(self._start_gst_playback_main, current_id)
        else:
            self.is_compiling = False
            GLib.idle_add(self._notify_stop)

    def _start_gst_playback_main(self, current_id):
        if self.compile_id != current_id or not self.is_compiling:
            return False
        self.is_compiling = False
        if self.temp_wav and os.path.exists(self.temp_wav):
            if self.player:
                try:
                    self.player.set_state(Gst.State.NULL)
                except Exception:
                    pass
                self.player = None
            self.player = Gst.ElementFactory.make('playbin', 'tab_audio_player')
            if self.player:
                bus = self.player.get_bus()
                bus.add_signal_watch()
                bus.connect('message::eos', self._on_eos)
                bus.connect('message::error', self._on_error)
                self.player.set_property('uri', f"file://{self.temp_wav}")
                self.player.set_state(Gst.State.PLAYING)
                self.is_playing = True
                if callable(self.on_start_cb):
                    self.on_start_cb()
        return False

    def stop(self):
        self.compile_id += 1
        self.is_compiling = False
        self.is_playing = False
        if self.player:
            try:
                self.player.set_state(Gst.State.NULL)
            except Exception:
                pass
            self.player = None
        if self.temp_wav and self.temp_wav != self.cached_wav and os.path.exists(self.temp_wav):
            try:
                os.remove(self.temp_wav)
            except Exception:
                pass
        return False

