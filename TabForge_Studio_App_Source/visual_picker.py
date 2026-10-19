#!/usr/bin/env python3
import math
import cairo
import os
import tempfile
import wave
import array
import random
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, Gst, GObject

Gst.init(None)

OPEN_STRING_FREQS = [329.63, 246.94, 196.00, 146.83, 110.00, 82.41]
STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']

# High-End Studio Neon String Palette
STRING_COLORS = [
    (1.0,  0.32, 0.32), # High E - Neon Coral
    (1.0,  0.70, 0.20), # B - Gold Orange
    (1.0,  0.90, 0.20), # G - Cyber Yellow
    (0.0,  0.90, 0.46), # D - Emerald Green
    (0.0,  0.90, 1.00), # A - Electric Cyan
    (0.70, 0.50, 1.00), # Low E - Neon Violet
]

from tab_engine import synthesize_fret_sound, TUNINGS

_PLUCK_PLAYER = None

def get_pluck_player():
    global _PLUCK_PLAYER
    if _PLUCK_PLAYER is None:
        _PLUCK_PLAYER = Gst.ElementFactory.make('playbin', 'tabforge_pluck_player')
    return _PLUCK_PLAYER

def play_pluck_sound(string_idx, fret, mode="", tuning_name="Standard E", capo=0):
    freqs, _ = TUNINGS.get(tuning_name, TUNINGS["Standard E"])
    open_freq = freqs[string_idx]
    if capo > 0:
        open_freq *= (2.0 ** (capo / 12.0))
    sample_rate = 44100
    duration = 0.5
    output = synthesize_fret_sound(open_freq, fret, duration, mode, sample_rate)

    temp_wav = os.path.join(tempfile.gettempdir(), f"tabforge_pluck_{string_idx}_{fret}.wav")
    try:
        mono_ints = [max(-32768, min(32767, int(v * 18000.0))) for v in output]
        out_samples = array.array('h', mono_ints)
        with wave.open(temp_wav, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(out_samples.tobytes())
    except Exception as e:
        print("Pluck Audio Error:", e)
        return

    player = get_pluck_player()
    if player:
        player.set_state(Gst.State.NULL)
        player.set_property('uri', f"file://{temp_wav}")
        player.set_state(Gst.State.PLAYING)

class VisualGuitarPicker(Gtk.Picture):
    """
    High-End Studio 22-Fret Guitar Neck visual picker with neon strings,
    mother-of-pearl inlays, metallic fret wires, and glowing note badges.
    """
    __gsignals__ = {
        'fret-clicked': (GObject.SignalFlags.RUN_FIRST, None, (int, int, str)),
    }

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_size_request(-1, 115)
        self.set_content_fit(Gtk.ContentFit.FILL)

        self.last_clicked_string = -1
        self.last_clicked_fret = -1
        self.harmonic_mode = ""
        self.tuning_name = "Standard E"
        self.capo = 0
        self.string_names = ['e', 'B', 'G', 'D', 'A', 'E']

        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("pressed", self._on_clicked)
        self.add_controller(click_gesture)

        self.connect("notify::width", lambda *a: self.queue_draw())
        self.connect("notify::height", lambda *a: self.queue_draw())

    def set_harmonic_mode(self, mode_str):
        self.harmonic_mode = mode_str
        self.queue_draw()

    def set_tuning(self, tuning_name):
        self.tuning_name = tuning_name
        _, self.string_names = TUNINGS.get(tuning_name, TUNINGS["Standard E"])
        self.queue_draw()

    def set_capo(self, capo):
        self.capo = capo
        self.queue_draw()

    def _on_clicked(self, gesture, n_press, x, y):
        width = max(400, self.get_width())
        height = max(90, self.get_height())

        margin_x = 38
        margin_y = 12
        fb_w = width - margin_x * 2
        fb_h = height - margin_y * 2

        rel_x = x - margin_x
        rel_y = y - margin_y

        if 0 <= rel_y <= fb_h and fb_w > 0:
            string_idx = int(round((rel_y / float(fb_h)) * 5))
            string_idx = max(0, min(5, string_idx))

            if rel_x < 0:
                fret = 0
            else:
                fret_w = fb_w / 22.0
                fret = int(rel_x // fret_w) + 1
                fret = max(1, min(22, fret))

            self.last_clicked_string = string_idx
            self.last_clicked_fret = fret

            self.emit('fret-clicked', string_idx, fret, self.harmonic_mode)
            self.queue_draw()

    def queue_draw(self):
        w = max(400, self.get_width())
        h = max(95, self.get_height())
        self.render_picker(w, h)

    def render_picker(self, width, height):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        # Background - Deep Studio Dark
        cr.set_source_rgb(0.08, 0.09, 0.11)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        margin_x = 38
        margin_y = 12
        fb_w = width - margin_x * 2
        fb_h = height - margin_y * 2

        # Mahogany Ebony Wood Neck Background
        grad = cairo.LinearGradient(margin_x, margin_y, margin_x, margin_y + fb_h)
        grad.add_color_stop_rgb(0.0, 0.14, 0.15, 0.20)
        grad.add_color_stop_rgb(0.5, 0.20, 0.22, 0.29)
        grad.add_color_stop_rgb(1.0, 0.14, 0.15, 0.20)

        cr.set_source(grad)
        cr.rectangle(margin_x, margin_y, fb_w, fb_h)
        cr.fill()

        # Bone Nut
        cr.set_source_rgb(0.95, 0.93, 0.86)
        cr.rectangle(margin_x - 9, margin_y, 9, fb_h)
        cr.fill()

        # Frets 1 to 22
        num_frets = 22
        fret_w = fb_w / float(num_frets)
        inlay_dots = [3, 5, 7, 9, 12, 15, 17, 19, 21]
        harmonic_nodes = [12, 7, 5, 4]

        for f in range(1, num_frets + 1):
            fx = margin_x + f * fret_w

            # Natural Harmonic Node Gold Glow Lines
            if self.harmonic_mode == "NH" and f in harmonic_nodes:
                cr.set_source_rgba(1.0, 0.85, 0.2, 0.45)
                cr.set_line_width(5.0)
                cr.move_to(fx, margin_y); cr.line_to(fx, margin_y + fb_h); cr.stroke()

            # Mother of pearl Inlay dots
            if f in inlay_dots:
                dot_x = margin_x + (f - 0.5) * fret_w
                cr.set_source_rgba(0.9, 0.95, 1.0, 0.45)
                if f == 12:
                    cr.arc(dot_x, margin_y + fb_h * 0.28, 3.5, 0, 2 * math.pi); cr.fill()
                    cr.arc(dot_x, margin_y + fb_h * 0.72, 3.5, 0, 2 * math.pi); cr.fill()
                else:
                    cr.arc(dot_x, margin_y + fb_h / 2.0, 4, 0, 2 * math.pi); cr.fill()

            # Chrome Fret Wire Lines
            cr.set_source_rgb(0.75, 0.78, 0.85)
            cr.set_line_width(1.8)
            cr.move_to(fx, margin_y); cr.line_to(fx, margin_y + fb_h); cr.stroke()

            # Fret number text
            cr.set_source_rgb(0.65, 0.7, 0.8)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(8)
            text_str = str(f)
            text_ext = cr.text_extents(text_str)
            cr.move_to(fx - fret_w / 2.0 - text_ext.width / 2.0, margin_y + fb_h + 12)
            cr.show_text(text_str)

        # Draw Capo Clamp on Neck
        if self.capo > 0 and 1 <= self.capo <= num_frets:
            capo_x = margin_x + (self.capo - 0.5) * fret_w
            cr.save()
            cr.set_source_rgba(1.0, 0.65, 0.15, 0.45)
            cr.rectangle(capo_x - 6, margin_y - 3, 12, fb_h + 6)
            cr.fill()

            grad_c = cairo.LinearGradient(capo_x - 4, margin_y, capo_x + 4, margin_y)
            grad_c.add_color_stop_rgb(0.0, 0.95, 0.70, 0.20)
            grad_c.add_color_stop_rgb(0.5, 1.00, 0.88, 0.45)
            grad_c.add_color_stop_rgb(1.0, 0.85, 0.55, 0.10)
            cr.set_source(grad_c)
            cr.rectangle(capo_x - 4, margin_y - 5, 8, fb_h + 10)
            cr.fill()

            cr.set_source_rgb(0.15, 0.15, 0.15)
            cr.rectangle(capo_x - 2, margin_y, 4, fb_h)
            cr.fill()

            cr.set_source_rgb(1.0, 0.95, 0.8)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(8)
            c_str = f"CAPO {self.capo}"
            c_ext = cr.text_extents(c_str)
            cr.move_to(capo_x - c_ext.width / 2.0, margin_y - 6)
            cr.show_text(c_str)
            cr.restore()

        # 6 Strings with Vibrant Neon Glow
        string_spacing = fb_h / 5.0
        for s in range(6):
            sy = margin_y + s * string_spacing
            sr, sg, sb = STRING_COLORS[s]

            # Soft Ambient Neon Glow Behind String
            cr.set_source_rgba(sr, sg, sb, 0.25)
            cr.set_line_width(6.0 - s * 0.4)
            cr.move_to(margin_x - 9, sy)
            cr.line_to(margin_x + fb_w, sy)
            cr.stroke()

            # Crisp Core String Wire Line
            cr.set_source_rgb(sr, sg, sb)
            cr.set_line_width(2.4 - s * 0.3)
            cr.move_to(margin_x - 9, sy)
            cr.line_to(margin_x + fb_w, sy)
            cr.stroke()

            # String Name Label
            cr.set_source_rgb(sr, sg, sb)
            cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(10)
            cr.move_to(margin_x - 30, sy + 3.5)
            cr.show_text(self.string_names[s])

        # Highlight last clicked note badge with Neon Drop Glow
        if self.last_clicked_string >= 0:
            s_idx = self.last_clicked_string
            fret = self.last_clicked_fret
            sy = margin_y + s_idx * string_spacing
            
            if fret == 0:
                cx = margin_x - 4
            else:
                cx = margin_x + (fret - 0.5) * fret_w

            if self.harmonic_mode == "NH":
                r_c, g_c, b_c = 1.0, 0.85, 0.0 # Gold
                t_str = f"<{fret}>"
            elif self.harmonic_mode == "AH":
                r_c, g_c, b_c = 1.0, 0.4, 0.1 # Orange
                t_str = f"[{fret}]"
            else:
                r_c, g_c, b_c = 0.0, 0.9, 1.0 # Cyan
                t_str = str(fret)

            # Outer Glow Ring
            cr.arc(cx, sy, 12.5, 0, 2 * math.pi)
            cr.set_source_rgba(r_c, g_c, b_c, 0.35)
            cr.fill()

            # Solid Badge Circle
            cr.set_source_rgb(r_c, g_c, b_c)
            cr.arc(cx, sy, 9.0, 0, 2 * math.pi)
            cr.fill()

            # Inner Text
            cr.set_source_rgb(0.05, 0.05, 0.08)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(8.5)
            ext = cr.text_extents(t_str)
            cr.move_to(cx - ext.width / 2.0, sy + ext.height / 2.0)
            cr.show_text(t_str)

        data = surface.get_data()
        pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            bytes(data), GdkPixbuf.Colorspace.RGB, True, 8, width, height, surface.get_stride()
        )
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.set_paintable(texture)

