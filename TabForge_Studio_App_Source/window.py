#!/usr/bin/env python3
import bisect
import json
import math
import cairo
import os
import time
import threading
import zipfile
from datetime import datetime
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib, Gio

from tab_engine import (TabTrack, TabAudioPlayer, Measure, TUNINGS, BUILTIN_TUNINGS, EPSILON, NOTE_NAMES,
                        note_value, note_label, length_text, tech_tokens)
from audio_recorder import AudioInterfaceRecorder
from tab_canvas import TabCanvasWidget, DEFAULT_DISPLAY
from visual_picker import VisualGuitarPicker, play_pluck_sound

TAB_LIBRARY_DIR = os.path.expanduser("~/My_Guitar_Tabs")
PDF_EXPORT_DIR = os.path.expanduser("~/My_Guitar_PDFs")
APP_ICON_PATH = os.path.join(os.path.dirname(__file__), 'stratocaster.svg')
SETTINGS_PATH = os.path.expanduser("~/.config/tabforge/settings.json")
AUTO_SAVE_SECONDS = 120
ZOOM_STEP = 0.15
TIME_SIGNATURES = [(4, 4), (3, 4), (2, 4), (5, 4), (6, 8), (7, 8), (9, 8), (12, 8)]
SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]
NOTE_DENOMINATORS = (1, 2, 4, 8, 16, 32)
SHORTEST_NOTE = 0.125
GRIDS = [("Whole", 4.0), ("Half", 2.0), ("Quarter", 1.0), ("Eighth", 0.5), ("Eighth triplet", 1.0 / 3.0),
         ("16th", 0.25), ("16th triplet", 1.0 / 6.0), ("32nd", 0.125)]
MIXED_GRID = len(GRIDS)  # dropdown row shown when a bar mixes lengths
CUSTOM_TUNING_LABEL = "Custom Tuning…"

# (code stored on the note, button label, tooltip)
TECHNIQUES = [
    ("PM", "PM", "Palm mute (M)"),
    ("h", "h", "Hammer-on (H)"),
    ("p", "p", "Pull-off (P)"),
    ("t", "T", "Tap (T)"),
    ("/", "/", "Slide up (S or /)"),
    ("\\", "\\", "Slide down (\\)"),
    ("~", "~", "Vibrato (~)"),
]

# (label, bend in steps, bend type)
BEND_OPTIONS = [
    ("1/2 Bend (1 semitone)", 0.5, "bend"),
    ("Full Bend (2 semitones)", 1.0, "bend"),
    ("1 ½ Bend (3 semitones)", 1.5, "bend"),
    ("2 Step Bend (4 semitones)", 2.0, "bend"),
    ("Bend & Release", 1.0, "bend_release"),
    ("Pre-Bend", 1.0, "prebend"),
    ("Pre-Bend & Release", 1.0, "prebend_release"),
]

SHORTCUTS = [
    ("Playback", [
        ("Space", "Play or stop from the cursor's bar"),
        ("L", "Loop selected bars"),
        ("K", "Metronome"),
    ]),
    ("Writing", [
        ("0-9", "Type a fret"),
        ("Del", "Erase note"),
        ("X", "Dead note"),
        ("H  P  M  T", "Hammer, pull, palm mute, tap"),
        ("S or /", "Slide up"),
        ("\\", "Slide down"),
        ("~", "Vibrato"),
        ("B", "Bend"),
        ("G", "Grace note"),
    ]),
    ("Note Length", [
        ("Alt+1 to 6", "Whole down to 32nd"),
        ("Drag", "Pull a note's end to resize it"),
        ("Right-click", "Length menu for a slot"),
        ("[  ]", "Halve or double"),
        (".", "Dotted"),
        (";", "Triplet, or undo it"),
    ]),
    ("Bars", [
        ("Shift+Left/Right", "Select bars"),
        ("Shift+Click", "Select bars"),
        ("Ctrl+C  X  V", "Copy, cut, paste bars"),
        ("Ctrl+Del", "Remove bar"),
        ("Esc", "Clear selection"),
        ("Home  End", "First or last bar"),
    ]),
    ("Song", [
        ("Ctrl+Z", "Undo"),
        ("Ctrl+Shift+Z  Ctrl+Y", "Redo"),
        ("Ctrl+S", "Save"),
        ("Ctrl+O", "Open"),
        ("Ctrl+T", "New song tab"),
        ("Ctrl+W", "Close song tab"),
        ("Ctrl +  -  0", "Zoom"),
    ]),
]


def load_settings():
    """Saved preferences over the defaults. A missing or damaged file just gives defaults."""
    settings = dict(DEFAULT_DISPLAY, labels="both", paper=False, custom_lengths=[])
    try:
        with open(SETTINGS_PATH, "r") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return settings
    if isinstance(saved, dict):
        for key, default in list(settings.items()):
            if type(saved.get(key)) is type(default):
                settings[key] = saved[key]
    if settings["labels"] not in ("both", "numbers", "names"):
        settings["labels"] = "both"
    if settings["rhythm"] not in ("notation", "numbers", "off"):
        settings["rhythm"] = "notation"
    settings["custom_lengths"] = [pair for pair in settings["custom_lengths"]
                                  if isinstance(pair, list) and len(pair) == 2
                                  and all(type(x) is int and x > 0 for x in pair)]
    return settings


def save_settings(settings):
    temp_path = SETTINGS_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(temp_path, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(temp_path, SETTINGS_PATH)
    except OSError as e:
        print("Settings error:", e)


def safe_file_name(title, fallback):
    name = "".join(c for c in (title or "") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    return name or fallback


def tool_group(*widgets):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.set_valign(Gtk.Align.CENTER)
    for w in widgets:
        box.append(w)
    return box


def linked(*widgets):
    box = tool_group(*widgets)
    box.set_spacing(0)
    box.add_css_class("linked")
    return box


def wrap_bar(*groups):
    """A toolbar that wraps onto another line instead of hiding controls off-screen."""
    if hasattr(Adw, 'WrapBox'):  # libadwaita 1.7+
        bar = Adw.WrapBox()
        bar.set_child_spacing(14)
        bar.set_line_spacing(4)
    else:
        bar = Gtk.FlowBox()
        bar.set_selection_mode(Gtk.SelectionMode.NONE)
        bar.set_max_children_per_line(24)
        bar.set_column_spacing(14)
        bar.set_row_spacing(4)
        bar.set_focusable(False)
    bar.add_css_class("tool-row")
    for g in groups:
        bar.append(g)
        if isinstance(g.get_parent(), Gtk.FlowBoxChild):
            g.get_parent().set_focusable(False)
    return bar


def caption(text):
    lbl = Gtk.Label(label=text)
    lbl.add_css_class("tool-caption")
    return lbl


def icon_button(icon_name, tooltip, callback):
    btn = Gtk.Button.new_from_icon_name(icon_name)
    btn.set_tooltip_text(tooltip)
    btn.connect("clicked", callback)
    return btn


def text_button(label, tooltip, callback):
    btn = Gtk.Button(label=label)
    btn.set_tooltip_text(tooltip)
    btn.connect("clicked", callback)
    return btn


def toggle_button(label, tooltip):
    btn = Gtk.ToggleButton(label=label)
    btn.set_tooltip_text(tooltip)
    return btn


def content_button(icon_name, label):
    """A button whose icon and label change together. Returns (button, content)."""
    content = Adw.ButtonContent(icon_name=icon_name, label=label)
    btn = Gtk.Button()
    btn.set_child(content)
    return btn, content


def _ellipse(cr, x, y, rx, ry, angle):
    cr.save()
    cr.translate(x, y)
    cr.rotate(angle)
    cr.scale(rx, ry)
    cr.new_sub_path()
    cr.arc(0, 0, 1.0, 0, 2 * math.pi)
    cr.restore()


def draw_note(cr, den):
    """Engraved-style note on a 20 x 26 grid: slanted head, straight stem, tapered flags."""
    if den == 1:
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        _ellipse(cr, 10.0, 18.0, 6.2, 3.9, 0.0)
        _ellipse(cr, 10.0, 18.0, 3.4, 2.1, 1.0)
        cr.fill()
        return

    head_x, head_y = 7.0, 21.0
    if den == 2:
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        _ellipse(cr, head_x, head_y, 4.9, 3.4, -0.4)
        _ellipse(cr, head_x, head_y, 4.1, 1.6, -0.55)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
    else:
        _ellipse(cr, head_x, head_y, 4.9, 3.4, -0.4)
        cr.fill()

    stem_x = head_x + 3.9
    cr.rectangle(stem_x, 2.0, 1.25, head_y - 3.0)
    cr.fill()

    for f in range({8: 1, 16: 2, 32: 3}.get(den, 0)):
        top = 2.0 + f * 3.7
        x = stem_x + 1.2
        cr.move_to(x, top)
        cr.curve_to(x + 0.5, top + 3.6, x + 6.2, top + 5.0, x + 4.4, top + 10.5)
        cr.curve_to(x + 4.7, top + 7.0, x + 1.8, top + 5.8, x, top + 4.5)
        cr.close_path()
        cr.fill()


def note_icon(den):
    def draw(area, cr, width, height):
        color = area.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.scale(width / 20.0, height / 26.0)
        draw_note(cr, den)

    area = Gtk.DrawingArea()
    area.set_content_width(20)
    area.set_content_height(26)
    area.set_valign(Gtk.Align.CENTER)
    area.set_draw_func(draw)
    return area


def mark_current(widget, is_current):
    """Highlights a plain button that reflects the slot or note under the cursor."""
    if is_current:
        widget.add_css_class("current")
    else:
        widget.remove_css_class("current")


def keep_keyboard_on_tab(widget):
    """Clicking toolbar buttons must not steal the keys used for writing tabs."""
    if isinstance(widget, (Gtk.Entry, Gtk.SpinButton)):
        return
    widget.set_focus_on_click(False)
    child = widget.get_first_child()
    while child:
        keep_keyboard_on_tab(child)
        child = child.get_next_sibling()


def disable_spin_scroll(spin_widget):
    ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
    ctrl.connect("scroll", lambda c, dx, dy: True)
    spin_widget.add_controller(ctrl)


def number_spin(low, high, value, tooltip=None):
    spin = Gtk.SpinButton.new_with_range(low, high, 1)
    spin.set_value(value)
    spin.set_width_chars(2)
    spin.set_tooltip_text(tooltip)
    disable_spin_scroll(spin)
    return spin


def modal_window(parent, title, content, width, height):
    dialog = Adw.Window(title=title, transient_for=parent, modal=True, default_width=width, default_height=height)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Adw.HeaderBar())
    box.append(content)
    dialog.set_content(box)

    keys = Gtk.EventControllerKey.new()
    keys.connect("key-pressed", lambda c, keyval, code, state: keyval == Gdk.KEY_Escape and (dialog.close() or True))
    dialog.add_controller(keys)
    return dialog


class TabWriterWindow(Adw.ApplicationWindow):
    """Main window: song tabs, playback controls, editing tools and the guitar neck."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("TabForge Studio")
        self.set_icon_name("io.github.tabforge.studio")

        def_w, def_h = 1120, 760
        display = Gdk.Display.get_default()
        if display:
            monitors = display.get_monitors()
            if monitors.get_n_items() > 0:
                geom = monitors.get_item(0).get_geometry()
                def_w = min(1240, max(750, int(geom.width * 0.85)))
                def_h = min(820, max(620, int(geom.height * 0.8)))
        self.set_default_size(def_w, def_h)

        os.makedirs(TAB_LIBRARY_DIR, exist_ok=True)
        os.makedirs(PDF_EXPORT_DIR, exist_ok=True)

        self.audio_player = TabAudioPlayer()
        self.audio_player.on_start_cb = self._on_audio_started
        self.audio_player.on_stop_cb = self._stop_playback
        self.audio_player.on_error_cb = self.show_toast
        self.interface_recorder = AudioInterfaceRecorder()
        self.interface_recorder.connect('level-changed', self._on_vu_level_changed)

        self.typed_fret_buffer = ""
        self.commit_timer_id = 0

        self.playing_canvas = None
        self.tick_id = 0
        self.beat_times = []
        self.restart_timer_id = 0
        self.tap_times = []
        self.bar_clipboard = []
        self.active_technique = ""
        self.active_bend_val = None
        self.active_bend_type = None
        self.settings = load_settings()
        self._syncing = False  # True while widgets are being updated from a song

        self._build_actions()
        self._build_ui()
        self._setup_shortcuts()
        keep_keyboard_on_tab(self.get_content())
        self.connect("close-request", self._on_close_request)
        self._get_active_canvas().grab_focus()

        GLib.timeout_add_seconds(AUTO_SAVE_SECONDS, self._periodic_auto_save)

    def _build_actions(self):
        actions = {
            "export-pdf": self._on_export_pdf_clicked,
            "export-ascii": self._on_export_ascii_clicked,
            "open-folder": self._on_open_specialized_folder_clicked,
            "backup": self._on_backup_clicked,
            "shortcuts": self._show_shortcuts,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda a, p, cb=callback: cb(None))
            self.add_action(action)

        for name, key in (("paper-view", "paper"), ("show-ribbons", "ribbons"), ("show-beats", "beats")):
            toggle = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(self.settings[key]))
            toggle.connect("activate", self._on_setting_toggled, key)
            self.add_action(toggle)

        for name, key in (("length-labels", "labels"), ("rhythm-style", "rhythm")):
            choice = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"),
                                                   GLib.Variant.new_string(self.settings[key]))
            choice.connect("activate", self._on_setting_chosen, key)
            self.add_action(choice)

    def _build_ui(self):
        self.toast_overlay = Adw.ToastOverlay()
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        tab_bar = Adw.TabBar()
        tab_bar.set_view(self.tab_view)
        self.tab_view.connect('notify::selected-page', self._on_selected_page_changed)
        self.tab_view.connect('close-page', self._on_close_page)

        neck_panel = self._build_neck()  # first: the technique buttons talk to the picker
        status_bar = self._build_status_bar()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(self._build_header())
        main_box.append(tab_bar)
        main_box.append(self._build_transport_bar())
        song_area = Gtk.Overlay()
        song_area.set_child(self.tab_view)
        song_area.add_overlay(self._build_page_turner())
        main_box.append(song_area)
        main_box.append(self._build_edit_bar())
        main_box.append(neck_panel)
        main_box.append(status_bar)

        self.toast_overlay.set_child(main_box)
        self.set_content(self.toast_overlay)
        self.add_new_song_tab("Song 1")

    def _build_header(self):
        header = Adw.HeaderBar()

        if os.path.exists(APP_ICON_PATH):
            app_icon = Gtk.Image.new_from_file(APP_ICON_PATH)
            app_icon.set_pixel_size(22)
            app_icon.set_margin_start(4)
            app_icon.set_margin_end(4)
            header.pack_start(app_icon)

        header.pack_start(icon_button("tab-new-symbolic", "New song tab (Ctrl+T)", lambda b: self.add_new_song_tab()))
        header.pack_start(icon_button("document-open-symbolic", "Open a saved tab (Ctrl+O)", self._on_open_clicked))
        header.pack_start(icon_button("document-save-symbolic", "Save (Ctrl+S)", self._on_save_clicked))
        header.pack_start(linked(
            icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", lambda b: self._undo(False)),
            icon_button("edit-redo-symbolic", "Redo (Ctrl+Shift+Z)", lambda b: self._undo(True)),
        ))

        self.entry_song_title = Gtk.Entry()
        self.entry_song_title.set_text("Song 1")
        self.entry_song_title.set_placeholder_text("Song title")
        self.entry_song_title.set_alignment(0.5)
        self.entry_song_title.set_width_chars(26)
        self.entry_song_title.add_css_class("title-entry")
        self.entry_song_title.connect("changed", self._on_song_title_changed)
        self.entry_song_title.connect("activate", lambda e: self._get_active_canvas().grab_focus())
        header.set_title_widget(self.entry_song_title)

        export = Gio.Menu()
        export.append("Export Text Tab", "win.export-ascii")
        files = Gio.Menu()
        files.append("Open Tabs Folder", "win.open-folder")
        files.append("Back Up Everything", "win.backup")
        view = Gio.Menu()
        view.append("Paper View", "win.paper-view")
        view.append("Keyboard Shortcuts", "win.shortcuts")
        menu = Gio.Menu()
        menu.append_section(None, export)
        menu.append_section(None, files)
        menu.append_section(None, view)
        menu.append_submenu("Length Display", self._build_length_menu())

        btn_menu = Gtk.MenuButton(icon_name="open-menu-symbolic")
        btn_menu.set_tooltip_text("Menu")
        btn_menu.set_menu_model(menu)
        header.pack_end(btn_menu)

        btn_pdf = Gtk.Button(label="Export PDF")
        btn_pdf.add_css_class("suggested-action")
        btn_pdf.set_tooltip_text("Save this song as a printable PDF in ~/My_Guitar_PDFs/")
        btn_pdf.set_action_name("win.export-pdf")
        header.pack_end(btn_pdf)

        btn_neck = toggle_button("Neck", "Show or hide the guitar neck")
        btn_neck.set_active(True)
        btn_neck.connect("toggled", lambda b: self.neck_panel.set_visible(b.get_active()))
        self.btn_neck_write = toggle_button("Write", "Off: neck clicks only preview the note. "
                                                     "On: they also write it at the cursor")
        header.pack_end(linked(btn_neck, self.btn_neck_write))
        return header

    def _build_length_menu(self):
        labels = Gio.Menu()
        labels.append("Symbol and Number", "win.length-labels::both")
        labels.append("Number Only", "win.length-labels::numbers")
        labels.append("Name", "win.length-labels::names")
        marks = Gio.Menu()
        marks.append("Rhythm Notation", "win.rhythm-style::notation")
        marks.append("Numbers", "win.rhythm-style::numbers")
        marks.append("Nothing", "win.rhythm-style::off")
        extras = Gio.Menu()
        extras.append("Length Ribbons", "win.show-ribbons")
        extras.append("Beat Numbers", "win.show-beats")
        menu = Gio.Menu()
        menu.append_section("Length Buttons", labels)
        menu.append_section("Under the Staff", marks)
        menu.append_section("On the Staff", extras)
        return menu

    def _build_transport_bar(self):
        self.btn_play, self.play_content = content_button("media-playback-start-symbolic", "Play")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.add_css_class("transport-btn")
        self.btn_play.set_tooltip_text("Play from the cursor's bar (Space)")
        self.btn_play.connect("clicked", self._on_play_clicked)

        self.btn_loop = toggle_button("Loop", "Loop the selected bars, or the cursor's bar (L)")
        self.btn_loop.connect("toggled", lambda b: self._restart_playback_soon())
        self.btn_metronome = toggle_button("Click", "Metronome (K)")
        self.btn_metronome.connect("toggled", lambda b: self._restart_playback_soon())
        self.btn_count_in = toggle_button("Count-In", "Click one bar before playback starts")

        self.spin_bpm = number_spin(30, 400, 120, "Tempo in beats per minute")
        self.spin_bpm.connect("value-changed", self._on_bpm_changed)
        btn_tap = text_button("Tap", "Tap repeatedly to set the tempo", self._on_tap_tempo)

        self.combo_speed = Gtk.DropDown.new_from_strings([f"{s:g}x" for s in SPEEDS])
        self.combo_speed.set_selected(SPEEDS.index(1.0))
        self.combo_speed.set_tooltip_text("Practice speed: multiplies the tempo during playback")
        self.combo_speed.connect("notify::selected", self._on_speed_changed)

        self.combo_tuning = Gtk.DropDown.new_from_strings(self._tuning_labels())
        self.combo_tuning.set_tooltip_text("Guitar tuning")
        self.combo_tuning.connect("notify::selected", self._on_tuning_changed)

        self.spin_capo = number_spin(0, 12, 0, "Capo fret (0 = no capo)")
        self.spin_capo.connect("value-changed", self._on_capo_changed)

        self.btn_input = toggle_button("Input", "Show the input level meter (uses the microphone)")
        self.btn_input.connect("toggled", self._on_input_meter_toggled)
        self.progress_vu = Gtk.ProgressBar()
        self.progress_vu.add_css_class("level-meter")
        self.progress_vu.set_valign(Gtk.Align.CENTER)

        self.btn_record, self.record_content = content_button("media-record-symbolic", "Record")
        self.btn_record.add_css_class("record-btn")
        self.btn_record.set_tooltip_text("Record a take to ~/guitar_recordings/")
        self.btn_record.connect("clicked", self._on_record_clicked)

        return wrap_bar(
            tool_group(self.btn_play, linked(self.btn_loop, self.btn_metronome, self.btn_count_in)),
            tool_group(caption("Tempo"), self.spin_bpm, btn_tap, self.combo_speed),
            tool_group(caption("Tuning"), self.combo_tuning, caption("Capo"), self.spin_capo),
            tool_group(self.btn_input, self.progress_vu, self.btn_record),
        )

    def _build_edit_bar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._build_rhythm_row())
        box.append(self._build_note_row())
        return box

    def _build_rhythm_row(self):
        self.combo_time_sig = Gtk.DropDown.new_from_strings([f"{n}/{d}" for n, d in TIME_SIGNATURES])
        self.combo_time_sig.set_tooltip_text("Time signature of the selected bars (or the cursor's bar)")
        self.combo_time_sig.connect("notify::selected", self._on_time_sig_changed)

        self.combo_grid = Gtk.DropDown.new_from_strings([label for label, _ in GRIDS] + ["Mixed"])
        self.combo_grid.set_tooltip_text("Give the selected bars one note length throughout, "
                                         "moving notes to the nearest slot")
        self.combo_grid.connect("notify::selected", self._on_grid_changed)

        self.btn_all_bars = toggle_button("All Bars", "Apply Time and Grid to the whole song")

        self.value_buttons = {}
        self.length_parts = {}
        for den in NOTE_DENOMINATORS:
            key = NOTE_DENOMINATORS.index(den) + 1
            btn = Gtk.Button()
            btn.set_tooltip_text(f"{NOTE_NAMES[den].capitalize()} note (Alt+{key}). "
                                 "The empty space ahead follows this length")
            btn.connect("clicked", lambda b, q=4.0 / den: self._set_note_length(q))
            icon, label = note_icon(den), Gtk.Label()
            content = Gtk.Box(spacing=3)
            content.append(icon)
            content.append(label)
            btn.set_child(content)
            self.value_buttons[den] = btn
            self.length_parts[den] = (icon, label)
        self.btn_dot = text_button("Dotted", "Dotted note: 1.5x as long (.)", lambda b: self._toggle_dotted())
        self.btn_triplet = text_button("Triplet", "Split this slot into three equal notes, or undo that (;)",
                                       lambda b: self._split_triplet())

        self.custom_box = linked()
        self.custom_buttons = {}
        self._apply_length_labels()
        self._rebuild_custom_buttons()

        btn_custom = Gtk.MenuButton(label="Custom")
        btn_custom.set_tooltip_text("Use any length, such as 5/16, and keep it as a button")
        btn_custom.set_popover(self._build_custom_popover())

        return wrap_bar(
            tool_group(caption("Bar"), linked(
                icon_button("list-add-symbolic", "Add a bar after this one", self._on_add_measure),
                icon_button("list-remove-symbolic", "Remove the selected bars (Ctrl+Delete)", self._on_remove_measure),
            )),
            tool_group(caption("Time"), self.combo_time_sig, caption("Grid"), self.combo_grid, self.btn_all_bars),
            tool_group(caption("Length"), linked(*self.value_buttons.values()),
                       linked(self.btn_dot, self.btn_triplet), self.custom_box, btn_custom),
        )

    def _build_custom_popover(self):
        popover = Gtk.Popover()
        spin_top = number_spin(1, 64, 3, "How many")
        combo_bottom = Gtk.DropDown.new_from_strings([str(d) for d in NOTE_DENOMINATORS])
        combo_bottom.set_selected(NOTE_DENOMINATORS.index(16))

        def chosen():
            return int(spin_top.get_value()), NOTE_DENOMINATORS[combo_bottom.get_selected()]

        def apply(b):
            popover.popdown()
            top, bottom = chosen()
            self._set_note_length(4.0 * top / bottom)

        def keep(b):
            popover.popdown()
            if list(chosen()) not in self.settings["custom_lengths"]:
                self.settings["custom_lengths"].append(list(chosen()))
                save_settings(self.settings)
                self._rebuild_custom_buttons()

        popover.set_child(tool_group(spin_top, caption("/"), combo_bottom,
                                     text_button("Apply", "Use this length here", apply),
                                     text_button("Keep", "Add a button for this length", keep)))
        return popover

    def _rebuild_custom_buttons(self):
        while self.custom_box.get_first_child():
            self.custom_box.remove(self.custom_box.get_first_child())
        self.custom_buttons = {}
        for top, bottom in self.settings["custom_lengths"]:
            quarters = 4.0 * top / bottom
            btn = text_button(f"{top}/{bottom}", "Custom length. Right-click to remove",
                              lambda b, q=quarters: self._set_note_length(q))
            btn.set_focus_on_click(False)
            remove = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            remove.connect("pressed", lambda g, n, x, y, pair=[top, bottom]: self._remove_custom_length(pair))
            btn.add_controller(remove)
            self.custom_box.append(btn)
            self.custom_buttons[quarters] = btn
        self.custom_box.set_visible(bool(self.settings["custom_lengths"]))

    def _remove_custom_length(self, pair):
        self.settings["custom_lengths"].remove(pair)
        save_settings(self.settings)
        self._rebuild_custom_buttons()

    def _apply_length_labels(self):
        style = self.settings["labels"]
        for den, (icon, label) in self.length_parts.items():
            icon.set_visible(style == "both")
            label.set_label(NOTE_NAMES[den].capitalize() if style == "names" else f"1/{den}")

    def _on_setting_toggled(self, action, param, key):
        self.settings[key] = not self.settings[key]
        action.set_state(GLib.Variant.new_boolean(self.settings[key]))
        self._after_setting_change()

    def _on_setting_chosen(self, action, param, key):
        self.settings[key] = param.get_string()
        action.set_state(param)
        self._after_setting_change()

    def _after_setting_change(self):
        save_settings(self.settings)
        self._apply_length_labels()
        for canvas in self._all_canvases():
            canvas.dark_mode = not self.settings["paper"]
            canvas.queue_draw()

    def _build_note_row(self):
        self.spin_direct_fret = number_spin(0, 22, 0, "Fret for the note under the cursor")
        self.spin_direct_fret.connect("value-changed", self._on_direct_fret_changed)
        btn_apply_fret = text_button("Set", "Write this fret and move on", lambda b: self._commit_direct_fret_entry())

        btn_normal = toggle_button("Normal", "New notes are played normally")
        btn_normal.set_active(True)
        btn_nh = toggle_button("NH", "New notes are natural harmonics")
        btn_ah = toggle_button("AH", "New notes are pinch harmonics")
        btn_nh.set_group(btn_normal)
        btn_ah.set_group(btn_normal)
        for btn, mode in ((btn_normal, ""), (btn_nh, "NH"), (btn_ah, "AH")):
            btn.connect("toggled", lambda b, m=mode: b.get_active() and self.visual_picker.set_harmonic_mode(m))

        self.tech_buttons = {}
        for code, label, tooltip in TECHNIQUES:
            self.tech_buttons[code] = text_button(label, tooltip, lambda b, t=code: self._apply_technique(t))

        self.btn_bend = Gtk.MenuButton(label="Bend")
        self.btn_bend.set_tooltip_text("Bend (B for a full bend)")
        self.btn_bend.set_popover(self._build_bend_popover())

        self.spin_grace_fret = number_spin(0, 22, 5, "Fret of the grace note")
        self.btn_grace = Gtk.MenuButton(label="Grace")
        self.btn_grace.set_tooltip_text("Grace note (G)")
        self.btn_grace.set_popover(self._build_grace_popover())

        self.btn_dead = text_button("Dead", "Dead note (X)", lambda b: self._insert_dead_note())
        btn_clear = text_button("Clear", "Remove every note in this slot", lambda b: self._clear_current_beat())

        return wrap_bar(
            tool_group(caption("Fret"), self.spin_direct_fret, btn_apply_fret, btn_clear),
            tool_group(caption("Sound"), linked(btn_normal, btn_nh, btn_ah)),
            tool_group(caption("Technique"), linked(*self.tech_buttons.values()),
                       linked(self.btn_bend, self.btn_grace, self.btn_dead)),
        )

    def _build_bend_popover(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, amount, kind in BEND_OPTIONS:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda b, v=amount, t=kind: (popover.popdown(), self._apply_bend(v, t)))
            box.append(btn)
        popover.set_child(box)
        return popover

    def _build_grace_popover(self):
        popover = Gtk.Popover()
        btn_add = text_button("Add or Remove", "Add this grace note to the note under the cursor, or remove it",
                              lambda b: (popover.popdown(), self._apply_grace_note()))
        popover.set_child(tool_group(caption("Fret"), self.spin_grace_fret, btn_add))
        return popover

    def _build_neck(self):
        self.visual_picker = VisualGuitarPicker()
        self.visual_picker.connect("fret-clicked", self._on_visual_fret_clicked)
        self.neck_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.neck_panel.add_css_class("neck-panel")
        self.neck_panel.append(self.visual_picker)
        return self.neck_panel

    def _build_page_turner(self):
        """Page controls floating over the bottom of the song, where the eye already is."""
        self.btn_prev_page, prev_content = content_button("go-previous-symbolic", "Previous")
        self.btn_next_page, next_content = content_button("go-next-symbolic", "Next")
        self.btn_prev_page.set_tooltip_text("Previous page (PageUp)")
        self.btn_next_page.set_tooltip_text("Next page (PageDown)")
        self.btn_prev_page.connect("clicked", lambda b: self._get_active_canvas().prev_page())
        self.btn_next_page.connect("clicked", lambda b: self._get_active_canvas().next_page())

        self.lbl_page_num = Gtk.Label(label="Page 1 / 1")
        self.lbl_page_num.add_css_class("page-number")

        self.page_turner = tool_group(self.btn_prev_page, self.lbl_page_num, self.btn_next_page)
        self.page_turner.add_css_class("page-turner")
        self.page_turner.set_halign(Gtk.Align.CENTER)
        self.page_turner.set_valign(Gtk.Align.END)
        self.page_turner.set_margin_bottom(6)
        return self.page_turner

    def _build_status_bar(self):
        self.lbl_position = Gtk.Label(label="Bar 1")
        self.lbl_position.add_css_class("status-text")
        self.lbl_autosave = Gtk.Label(label=f"Auto-saves every {AUTO_SAVE_SECONDS // 60} min")
        self.lbl_autosave.add_css_class("status-dim")
        self.lbl_autosave.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self.lbl_autosave.set_max_width_chars(40)
        self.lbl_zoom = Gtk.Label(label="100%")
        self.lbl_zoom.add_css_class("status-text")
        self.lbl_zoom.set_width_chars(5)

        def flat(button):
            button.add_css_class("flat")
            return button

        zoom = tool_group(
            flat(icon_button("zoom-out-symbolic", "Zoom out (Ctrl -)", lambda b: self._zoom_by(-ZOOM_STEP))),
            self.lbl_zoom,
            flat(icon_button("zoom-in-symbolic", "Zoom in (Ctrl +)", lambda b: self._zoom_by(ZOOM_STEP))),
        )
        zoom.set_spacing(0)

        status = Gtk.CenterBox()
        status.add_css_class("status-bar")
        status.set_start_widget(self.lbl_position)
        status.set_end_widget(tool_group(self.lbl_autosave, zoom))
        return status

    def add_new_song_tab(self, title=None):
        if not title:
            title = f"Song {self.tab_view.get_n_pages() + 1}"

        canvas = TabCanvasWidget()
        canvas.dark_mode = not self.settings["paper"]
        canvas.display = self.settings
        canvas.tab_track.title = title
        canvas.mark_saved()
        canvas.connect('cursor-changed', self._on_cursor_changed)
        canvas.connect('page-changed', self._on_page_changed)
        canvas.connect('zoom-changed', self._on_zoom_changed)
        canvas.connect('edited', self._on_song_edited)
        canvas.connect('length-dragged', self._on_length_dragged)
        canvas.connect('length-menu', self._on_length_menu)

        page = self.tab_view.append(canvas)
        page.set_title(title)
        self.tab_view.set_selected_page(page)
        canvas.grab_focus()

    def _get_active_canvas(self):
        page = self.tab_view.get_selected_page()
        return page.get_child() if page else None

    def _all_canvases(self):
        return [self.tab_view.get_nth_page(i).get_child() for i in range(self.tab_view.get_n_pages())]

    def _refresh_tab_title(self, canvas):
        page = self.tab_view.get_page(canvas)
        page.set_title(f"{'● ' if canvas.is_dirty() else ''}{canvas.tab_track.title}")

    def _tuning_labels(self):
        return list(TUNINGS.keys()) + [CUSTOM_TUNING_LABEL]

    def _sync_ui_from_canvas(self):
        """Makes every control show the active song's real values."""
        canvas = self._get_active_canvas()
        if not canvas:
            return
        track = canvas.tab_track
        self._syncing = True
        try:
            self.entry_song_title.set_text(track.title)
            self.spin_bpm.set_value(track.bpm)
            if track.speed_multiplier in SPEEDS:
                self.combo_speed.set_selected(SPEEDS.index(track.speed_multiplier))
            self.spin_capo.set_value(track.capo)
            self.combo_tuning.set_model(Gtk.StringList.new(self._tuning_labels()))
            if track.tuning_name in TUNINGS:
                self.combo_tuning.set_selected(list(TUNINGS.keys()).index(track.tuning_name))
            self.visual_picker.set_tuning(track.tuning_name)
            self.visual_picker.set_capo(track.capo)
        finally:
            self._syncing = False
        self._on_cursor_changed(None, canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string)
        self._on_page_changed(None, canvas.current_page + 1, canvas.get_total_pages())
        self._on_zoom_changed(None, canvas.zoom_level)

    def _sync_bar_controls(self, canvas):
        """Time and Grid always show the cursor's bar."""
        measure = canvas.tab_track.measures[canvas.cursor_measure]
        self._syncing = True
        if measure.time_sig in TIME_SIGNATURES:
            self.combo_time_sig.set_selected(TIME_SIGNATURES.index(measure.time_sig))
        grid = measure.grid()
        match = [i for i, (_, q) in enumerate(GRIDS) if grid is not None and abs(q - grid) < EPSILON]
        self.combo_grid.set_selected(match[0] if match else MIXED_GRID)
        self._syncing = False
        self._sync_edit_buttons(canvas)

    def _sync_edit_buttons(self, canvas):
        """Highlights the length and techniques of the slot and note under the cursor,
        plus any technique armed for the next note."""
        measure = canvas.tab_track.measures[canvas.cursor_measure]
        den, dotted, triplet = note_value(measure.beat_quarters()[canvas.cursor_beat]) or (None, False, False)
        for value, btn in self.value_buttons.items():
            mark_current(btn, value == den)
        mark_current(self.btn_dot, dotted)
        mark_current(self.btn_triplet, triplet)
        slot_length = measure.beat_quarters()[canvas.cursor_beat]
        for quarters, btn in self.custom_buttons.items():
            mark_current(btn, abs(quarters - slot_length) < EPSILON)

        note = measure.beats[canvas.cursor_beat].notes.get(canvas.cursor_string)
        tokens = tech_tokens(note.technique) if note else set()
        tokens.add(self.active_technique)
        for code, btn in self.tech_buttons.items():
            mark_current(btn, code in tokens)
        mark_current(self.btn_bend, "b" in tokens)
        mark_current(self.btn_dead, "x" in tokens)
        mark_current(self.btn_grace, note is not None and note.grace_fret is not None)

    def _on_song_edited(self, canvas):
        self._refresh_tab_title(canvas)
        if canvas is self._get_active_canvas():
            self._sync_edit_buttons(canvas)

    def _on_song_title_changed(self, entry):
        new_title = entry.get_text().strip()
        canvas = self._get_active_canvas()
        if self._syncing or not new_title or not canvas:
            return
        canvas.tab_track.title = new_title
        self._refresh_tab_title(canvas)

    def _on_tuning_changed(self, dropdown, param):
        if self._syncing:
            return
        selected_idx = dropdown.get_selected()
        tuning_keys = list(TUNINGS.keys())
        if selected_idx == len(tuning_keys):
            self._open_custom_tuning_dialog()
            return

        canvas = self._get_active_canvas()
        if canvas and 0 <= selected_idx < len(tuning_keys):
            tuning_name = tuning_keys[selected_idx]
            freqs, names = TUNINGS[tuning_name]
            is_custom = tuning_name not in BUILTIN_TUNINGS
            canvas.tab_track.tuning_name = tuning_name
            canvas.tab_track.custom_open_freqs = freqs if is_custom else None
            canvas.tab_track.custom_string_names = names if is_custom else None
            canvas.queue_draw()
            self.visual_picker.set_tuning(tuning_name)
            self._refresh_tab_title(canvas)

    def _open_custom_tuning_dialog(self):
        notes = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        default_strings = [("High e", 4, 4), ("B", 3, 11), ("G", 3, 7), ("D", 3, 2), ("A", 2, 9), ("Low E", 2, 4)]

        grid = Gtk.Grid(row_spacing=8, column_spacing=10)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        grid.set_margin_top(14)
        grid.set_margin_bottom(18)

        entry_name = Gtk.Entry(hexpand=True)
        entry_name.set_text("Custom Tuning 1")
        grid.attach(caption("Name"), 0, 0, 1, 1)
        grid.attach(entry_name, 1, 0, 2, 1)

        string_combos = []
        octave_spins = []
        for s, (name, octave, note_idx) in enumerate(default_strings):
            label = caption(f"String {s + 1} ({name})")
            label.set_xalign(0)
            combo_note = Gtk.DropDown.new_from_strings(notes)
            combo_note.set_selected(note_idx)
            spin_oct = number_spin(0, 6, octave, "Octave")
            grid.attach(label, 0, s + 1, 1, 1)
            grid.attach(combo_note, 1, s + 1, 1, 1)
            grid.attach(spin_oct, 2, s + 1, 1, 1)
            string_combos.append(combo_note)
            octave_spins.append(spin_oct)

        dialog = modal_window(self, "Custom Tuning", grid, 420, -1)
        dialog.connect("close-request", lambda d: self._sync_ui_from_canvas())  # puts the dropdown back on Cancel

        def _on_apply(b):
            t_name = entry_name.get_text().strip() or "Custom Tuning"
            calc_freqs = []
            calc_names = []
            for s in range(6):
                n_idx = string_combos[s].get_selected()
                midi = 12 * (int(octave_spins[s].get_value()) + 1) + n_idx
                calc_freqs.append(round(440.0 * (2.0 ** ((midi - 69) / 12.0)), 2))
                calc_names.append(notes[n_idx])

            TUNINGS[t_name] = (calc_freqs, calc_names)

            canvas = self._get_active_canvas()
            if canvas:
                canvas.tab_track.tuning_name = t_name
                canvas.tab_track.custom_open_freqs = calc_freqs
                canvas.tab_track.custom_string_names = calc_names
                canvas.queue_draw()
                self._refresh_tab_title(canvas)

            dialog.close()

        btn_apply = text_button("Apply", "Use this tuning for the current song", _on_apply)
        btn_apply.add_css_class("suggested-action")
        btn_apply.set_margin_top(6)
        btn_apply.set_halign(Gtk.Align.END)
        grid.attach(btn_apply, 1, 8, 2, 1)
        dialog.present()

    def _show_shortcuts(self, btn):
        grid = Gtk.Grid(row_spacing=5, column_spacing=14)
        grid.set_margin_start(20)
        grid.set_margin_end(20)
        grid.set_margin_top(12)
        grid.set_margin_bottom(20)

        split = 2  # sections in the left-hand column
        for column, sections in ((0, SHORTCUTS[:split + 1]), (2, SHORTCUTS[split + 1:])):
            row = 0
            for title, entries in sections:
                heading = Gtk.Label(label=title, xalign=0)
                heading.add_css_class("shortcut-title")
                heading.set_margin_top(10 if row else 0)
                grid.attach(heading, column, row, 2, 1)
                row += 1
                for keys, text in entries:
                    key_label = Gtk.Label(label=keys, xalign=0)
                    key_label.add_css_class("shortcut-key")
                    text_label = Gtk.Label(label=text, xalign=0)
                    text_label.add_css_class("shortcut-text")
                    grid.attach(key_label, column, row, 1, 1)
                    grid.attach(text_label, column + 1, row, 1, 1)
                    row += 1

        modal_window(self, "Keyboard Shortcuts", grid, 680, -1).present()

    def _on_selected_page_changed(self, tab_view, param_spec):
        if self.playing_canvas or self.audio_player.is_compiling:
            self._stop_playback()
        self._sync_ui_from_canvas()

    def _on_close_page(self, tab_view, page):
        canvas = page.get_child()
        if canvas is self.playing_canvas:
            self._stop_playback()
        self._save_canvas(canvas)
        if tab_view.get_n_pages() == 1:
            self.add_new_song_tab("Song 1")
        return False

    def _on_close_request(self, window):
        self._stop_playback()
        if self.interface_recorder.is_recording:
            self._on_record_clicked(None)
        for canvas in self._all_canvases():
            self._save_canvas(canvas)
        return False

    def show_toast(self, text):
        toast = Adw.Toast.new(text)
        toast.set_use_markup(False)  # song titles may contain & or <
        toast.set_timeout(2)
        self.toast_overlay.add_toast(toast)

    # --- Backup and saving ---

    def _on_backup_clicked(self, btn):
        """Zips the app source, the tab library and the exported PDFs into the home folder."""
        self._save_all()
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_filename = f"TabForge_Studio_App_Backup_{date_str}.zip"
        backup_path = os.path.expanduser(f"~/{backup_filename}")

        app_dir = os.path.dirname(os.path.abspath(__file__))
        repo_dir = os.path.dirname(app_dir)

        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root_file in ["install.sh", "README.md", ".gitignore"]:
                    rf_path = os.path.join(repo_dir, root_file)
                    if os.path.exists(rf_path):
                        zipf.write(rf_path, arcname=os.path.join("TabForge_Studio_App", root_file))

                if os.path.exists(app_dir):
                    for root, dirs, files in os.walk(app_dir):
                        if "__pycache__" in root or ".git" in root:
                            continue
                        for file in files:
                            if file.endswith(".pyc"):
                                continue
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, app_dir)
                            zipf.write(full_path, arcname=os.path.join("TabForge_Studio_App", "TabForge_Studio_App_Source", rel_path))

                if os.path.exists(TAB_LIBRARY_DIR):
                    for root, dirs, files in os.walk(TAB_LIBRARY_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, TAB_LIBRARY_DIR)
                            zipf.write(full_path, arcname=os.path.join("TabForge_Studio_App", "My_Guitar_Tabs", rel_path))

                if os.path.exists(PDF_EXPORT_DIR):
                    for root, dirs, files in os.walk(PDF_EXPORT_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, PDF_EXPORT_DIR)
                            zipf.write(full_path, arcname=os.path.join("TabForge_Studio_App", "My_Guitar_PDFs", rel_path))

            self.show_toast(f"Backup saved to ~/{backup_filename}")
            Gio.AppInfo.launch_default_for_uri(f"file://{os.path.expanduser('~')}", None)
        except Exception as e:
            print("Backup Error:", e)
            self.show_toast("Could not create the backup archive")

    def _new_song_path(self, track):
        """A library path no other song uses, so a new song never overwrites an old one."""
        base = safe_file_name(track.title, "Untitled_Riff")
        taken = {c.file_path for c in self._all_canvases()}
        path = os.path.join(TAB_LIBRARY_DIR, f"{base}.json")
        n = 2
        while path in taken or os.path.exists(path):
            path = os.path.join(TAB_LIBRARY_DIR, f"{base}_{n}.json")
            n += 1
        return path

    def _save_canvas(self, canvas):
        """Saves a song if it has changed. Untouched blank tabs are never written.
        Returns the file name when something was written."""
        if not canvas.is_dirty() or not (canvas.tab_track.has_notes() or canvas.file_path):
            return None
        path = canvas.file_path or self._new_song_path(canvas.tab_track)
        temp_path = path + ".tmp"
        try:
            with open(temp_path, "w") as f:
                json.dump(canvas.tab_track.to_dict(), f, indent=2)
            os.replace(temp_path, path)  # a crash mid-save can't damage the old file
        except OSError as e:
            print("Save error:", e)
            self.show_toast(f"Could not save '{canvas.tab_track.title}': {e.strerror}")
            return None
        canvas.file_path = path
        canvas.mark_saved()
        self._refresh_tab_title(canvas)
        return os.path.basename(path)

    def _save_all(self):
        saved = [name for name in map(self._save_canvas, self._all_canvases()) if name]
        if saved:
            self.lbl_autosave.set_label(f"Saved {datetime.now().strftime('%H:%M')}: {', '.join(saved)}")
        return saved

    def _periodic_auto_save(self):
        self._save_all()
        return True

    def _on_export_pdf_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return

        safe_name = safe_file_name(canvas.tab_track.title, "Guitar_Tab")
        pdf_path = os.path.join(PDF_EXPORT_DIR, f"{safe_name}.pdf")

        try:
            canvas.export_pdf(pdf_path)
            self.show_toast(f"Exported PDF to ~/My_Guitar_PDFs/{safe_name}.pdf")
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(pdf_path, None), None)
        except Exception as e:
            print("PDF Export Error:", e)
            self.show_toast("Could not export the PDF")

    def _on_open_specialized_folder_clicked(self, btn):
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(TAB_LIBRARY_DIR, None), None)

    def _on_open_clicked(self, btn):
        chooser = Gtk.FileChooserNative.new("Open Guitar Tab", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel")
        chooser.set_current_folder(Gio.File.new_for_path(TAB_LIBRARY_DIR))
        filter_json = Gtk.FileFilter()
        filter_json.set_name("Guitar Tab Files (*.json)")
        filter_json.add_pattern("*.json")
        chooser.add_filter(filter_json)

        def _on_response(dialog, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                self._open_song_file(dialog.get_file().get_path())
            self._file_chooser = None

        chooser.connect("response", _on_response)
        self._file_chooser = chooser  # native dialogs need a live reference
        chooser.show()

    def _open_song_file(self, file_path):
        for canvas in self._all_canvases():
            if canvas.file_path == file_path:
                self.tab_view.set_selected_page(self.tab_view.get_page(canvas))
                return

        try:
            with open(file_path, "r") as f:
                track = TabTrack.from_dict(json.load(f))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            print("Open error:", e)
            self.show_toast(f"Could not open {os.path.basename(file_path)}")
            return

        track.title = track.title or os.path.splitext(os.path.basename(file_path))[0]
        canvas = self._get_active_canvas()
        if canvas is None or canvas.file_path or canvas.tab_track.has_notes():
            self.add_new_song_tab(track.title)  # otherwise reuse the blank tab
            canvas = self._get_active_canvas()
        canvas.set_tab_track(track)
        canvas.file_path = file_path
        self._refresh_tab_title(canvas)
        self._sync_ui_from_canvas()

    def _on_page_changed(self, widget, curr_page, total_pages):
        self.lbl_page_num.set_label(f"Page {curr_page} / {total_pages}")
        self.btn_prev_page.set_sensitive(curr_page > 1)
        self.btn_next_page.set_sensitive(curr_page < total_pages)
        self.page_turner.set_visible(total_pages > 1)

    def _on_zoom_changed(self, widget, zoom):
        self.lbl_zoom.set_label(f"{round(zoom * 100)}%")

    def _zoom_by(self, delta):
        canvas = self._get_active_canvas()
        if canvas:
            canvas.set_zoom(canvas.zoom_level + delta)

    # --- Playback ---

    def _on_play_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        if self.playing_canvas or self.audio_player.is_compiling:
            self._stop_playback()
        else:
            self._start_playback(canvas)

    def _start_playback(self, canvas, from_measure=None):
        if not canvas.tab_track.has_notes():
            self.show_toast("Nothing to play yet: add some notes first")
            return

        first, last = canvas.selected_bars()
        loop = self.btn_loop.get_active()
        if loop or canvas.selection_anchor is not None:
            end_measure = last
        else:
            first = canvas.cursor_measure if from_measure is None else from_measure
            end_measure = None

        self.play_content.set_label("Loading")
        self.play_content.set_icon_name("media-playback-stop-symbolic")
        self.btn_play.remove_css_class("suggested-action")
        self.btn_play.add_css_class("destructive-action")
        self.playing_canvas = canvas
        self.audio_player.play(canvas.tab_track, start_measure=first, end_measure=end_measure, loop=loop,
                               metronome=self.btn_metronome.get_active(),
                               count_in=self.btn_count_in.get_active() and from_measure is None)

    def _on_audio_started(self):
        canvas = self.playing_canvas
        if not canvas:
            return
        self.beat_times = [tb.time for tb in self.audio_player.render.timeline]
        first = self.audio_player.render.timeline[0]
        canvas.is_playing = True
        canvas.set_playhead_position(first.measure, 0.0)
        canvas.follow_measure(first.measure)

        self.play_content.set_label("Stop")
        self.lbl_position.add_css_class("playback-badge")
        self.tick_id = canvas.add_tick_callback(self._on_playback_tick)

    def _on_playback_tick(self, canvas, frame_clock):
        """Runs once per display frame and follows the audio pipeline's own position."""
        render = self.audio_player.render
        position = self.audio_player.position()
        if render is None:
            self.tick_id = 0
            return GLib.SOURCE_REMOVE
        if position is None:
            return GLib.SOURCE_CONTINUE

        elapsed = position - render.lead_in
        if elapsed < 0:
            self._set_position_text("Count-in…")
            return GLib.SOURCE_CONTINUE

        tb = render.timeline[max(0, bisect.bisect_right(self.beat_times, elapsed) - 1)]
        fraction = min(1.0, (elapsed - tb.time) / tb.duration) if tb.duration > 0 else 0.0
        canvas.set_playhead_position(tb.measure, tb.beat + fraction)

        track = canvas.tab_track
        tempo = track.bpm * track.speed_multiplier
        self._set_position_text(f"Bar {tb.measure + 1} of {len(track.measures)}  ·  Slot {tb.beat + 1}"
                                f"  ·  {int(position // 60):02d}:{int(position % 60):02d}  ·  ♩ = {tempo:g}")
        return GLib.SOURCE_CONTINUE

    def _set_position_text(self, text):
        if self.lbl_position.get_label() != text:
            self.lbl_position.set_label(text)

    def _stop_playback(self):
        if self.restart_timer_id:
            GLib.source_remove(self.restart_timer_id)
            self.restart_timer_id = 0
        self.audio_player.stop()

        canvas = self.playing_canvas
        self.playing_canvas = None
        if canvas:
            if self.tick_id:
                canvas.remove_tick_callback(self.tick_id)
                self.tick_id = 0
            canvas.is_playing = False
            canvas.queue_draw()
            canvas.follow_measure(canvas.cursor_measure)

        self.play_content.set_label("Play")
        self.play_content.set_icon_name("media-playback-start-symbolic")
        self.btn_play.remove_css_class("destructive-action")
        self.btn_play.add_css_class("suggested-action")
        self.lbl_position.remove_css_class("playback-badge")

        active = self._get_active_canvas()
        if active:
            self._on_cursor_changed(None, active.cursor_measure, active.cursor_beat, active.cursor_string)

    def _restart_playback_soon(self):
        """Applies tempo-like changes to running playback, waiting for spin clicks to settle."""
        if not self.playing_canvas:
            return
        if self.restart_timer_id:
            GLib.source_remove(self.restart_timer_id)
        self.restart_timer_id = GLib.timeout_add(300, self._restart_playback)

    def _restart_playback(self):
        self.restart_timer_id = 0
        canvas = self.playing_canvas
        if canvas:
            bar = canvas.playhead_measure
            self._stop_playback()
            self._start_playback(canvas, from_measure=bar)
        return False

    def _on_tap_tempo(self, btn):
        now = time.monotonic()
        if self.tap_times and now - self.tap_times[-1] > 2.0:
            self.tap_times = []
        self.tap_times = self.tap_times[-7:] + [now]
        if len(self.tap_times) >= 2:
            avg = (self.tap_times[-1] - self.tap_times[0]) / (len(self.tap_times) - 1)
            self.spin_bpm.set_value(round(60.0 / avg))

    def _on_cursor_changed(self, widget, measure_idx, beat_idx, string_idx):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        self._sync_bar_controls(canvas)
        if self.playing_canvas:
            return
        measure = canvas.tab_track.measures[measure_idx]
        slot_length = measure.beat_quarters()[beat_idx]
        length = f"{note_label(slot_length)} ({length_text(slot_length)})"
        text = f"Bar {measure_idx + 1}  ·  Slot {beat_idx + 1} of {len(measure.beats)}  ·  {length}"
        first, last = canvas.selected_bars()
        if last > first:
            text += f"  ·  Bars {first + 1}-{last + 1} selected"
        self.lbl_position.set_label(text)

    def _on_direct_fret_changed(self, spin):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        fret_val = int(spin.get_value())
        tech = self.visual_picker.harmonic_mode
        canvas.set_fret_at_cursor(fret_val, tech)

    def _on_capo_changed(self, spin):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        val = int(spin.get_value())
        canvas.tab_track.capo = val
        self.visual_picker.set_capo(val)
        canvas.queue_draw()
        self._refresh_tab_title(canvas)

    def _apply_bend(self, bend_val=1.0, bend_type="bend"):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        m_idx = canvas.cursor_measure
        b_idx = canvas.cursor_beat
        s_idx = canvas.cursor_string

        m = canvas.tab_track.measures[m_idx]
        b = m.beats[b_idx]
        if s_idx in b.notes:
            note = b.notes[s_idx]
            if "b" in note.technique and note.bend_val == bend_val and note.bend_type == bend_type:
                note.technique = note.technique.replace("b", "").replace("  ", " ").strip()
                note.bend_val = None
                note.bend_type = None
            else:
                if "b" not in note.technique:
                    note.technique = (note.technique + " b").strip()
                note.bend_val = bend_val
                note.bend_type = bend_type
            canvas.commit_edit()
            canvas.queue_draw()
        elif (self.active_technique, self.active_bend_val, self.active_bend_type) == ("b", bend_val, bend_type):
            self.active_technique = ""
            self._sync_edit_buttons(canvas)
        else:
            self.active_technique = "b"
            self.active_bend_val = bend_val
            self.active_bend_type = bend_type
            self._sync_edit_buttons(canvas)

    def _commit_direct_fret_entry(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        fret_val = int(self.spin_direct_fret.get_value())
        tech, b_val, b_type = self._armed_technique()
        canvas.set_fret_at_cursor(fret_val, tech, bend_val=b_val, bend_type=b_type)

        s_idx = canvas.cursor_string
        play_pluck_sound(s_idx, fret_val, tech, canvas.tab_track)

        self._advance_after_write(canvas)

    def _armed_technique(self):
        """(technique, bend amount, bend type) for the next note written."""
        tech = self.active_technique or self.visual_picker.harmonic_mode
        if tech == "b":
            return tech, self.active_bend_val, self.active_bend_type
        return tech, None, None

    def _after_structure_change(self, canvas, dropped=0):
        canvas.cursor_measure = min(canvas.cursor_measure, len(canvas.tab_track.measures) - 1)
        canvas.cursor_beat = min(canvas.cursor_beat, len(canvas.tab_track.measures[canvas.cursor_measure].beats) - 1)
        canvas.commit_edit()
        canvas.follow_measure(canvas.cursor_measure)
        self._on_cursor_changed(None, canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string)
        if dropped:
            self.show_toast(f"{dropped} note(s) didn't fit and were removed (Ctrl+Z to undo)")
        self._restart_playback_soon()

    def _target_bars(self, canvas):
        if self.btn_all_bars.get_active():
            return canvas.tab_track.measures
        first, last = canvas.selected_bars()
        return canvas.tab_track.measures[first:last + 1]

    def _on_grid_changed(self, combo, param):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        if combo.get_selected() == MIXED_GRID:  # a description of the bar, not a choice
            self._sync_bar_controls(canvas)
            return
        length = GRIDS[combo.get_selected()][1]
        self._after_structure_change(canvas, sum(m.set_grid(length) for m in self._target_bars(canvas)))

    def _on_time_sig_changed(self, combo, param):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        sig = TIME_SIGNATURES[combo.get_selected()]
        self._after_structure_change(canvas, sum(m.set_time_signature(sig) for m in self._target_bars(canvas)))

    def _cursor_slot(self, canvas):
        self._commit_typed_fret_immediately()
        measure = canvas.tab_track.measures[canvas.cursor_measure]
        return measure, measure.beat_quarters()[canvas.cursor_beat]

    def _set_note_length(self, quarters, fill=True):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        measure, _ = self._cursor_slot(canvas)
        first, last = canvas.selected_bars()
        if last > first:
            self._after_structure_change(canvas, sum(m.set_grid(quarters) for m in self._target_bars(canvas)))
            return
        dropped = measure.set_slot_duration(canvas.cursor_beat, quarters, fill)
        if dropped is None:
            self.show_toast("That note is longer than the rest of this bar")
        else:
            self._after_structure_change(canvas, dropped)

    def _on_length_dragged(self, canvas, measure_idx, beat_idx, string_idx, quarters):
        canvas.selection_anchor = None
        canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string = measure_idx, beat_idx, string_idx
        self._set_note_length(quarters)

    def _on_length_menu(self, canvas, x, y):
        """A length picker beside the pointer, for working without leaving the score."""
        popover = Gtk.Popover()
        popover.set_parent(canvas)
        spot = Gdk.Rectangle()
        spot.x, spot.y, spot.width, spot.height = int(x), int(y), 1, 1
        popover.set_pointing_to(spot)

        def entry(child, tooltip, action):
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.set_focus_on_click(False)
            btn.set_child(child)
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", lambda b: (popover.popdown(), action()))
            return btn

        row = tool_group()
        row.set_spacing(2)
        for den in NOTE_DENOMINATORS:
            row.append(entry(note_icon(den), f"{NOTE_NAMES[den].capitalize()} note",
                             lambda q=4.0 / den: self._set_note_length(q)))
        row.append(entry(Gtk.Label(label="Dotted"), "Dotted note", self._toggle_dotted))
        row.append(entry(Gtk.Label(label="Triplet"), "Triplet", self._split_triplet))
        popover.set_child(row)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _scale_note_length(self, factor):
        canvas = self._get_active_canvas()
        if canvas:
            quarters = self._cursor_slot(canvas)[1] * factor
            if quarters < SHORTEST_NOTE - EPSILON:
                self.show_toast("32nd notes are the shortest length")
            else:
                self._set_note_length(quarters)

    def _toggle_dotted(self):
        canvas = self._get_active_canvas()
        if canvas:
            quarters = self._cursor_slot(canvas)[1]
            value = note_value(quarters)
            dotted = bool(value and value[1])
            # Adding a dot leaves the off-beat free; removing one tidies the gap it leaves.
            self._set_note_length(quarters * (1.0 / 1.5 if dotted else 1.5), fill=dotted)

    def _split_triplet(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        measure, quarters = self._cursor_slot(canvas)
        value = note_value(quarters)
        if value and value[2]:
            merged = measure.merge_triplet(canvas.cursor_beat)
            if merged:
                canvas.cursor_beat = merged[0]
                self._after_structure_change(canvas, merged[1])
                return
        measure.split_slot(canvas.cursor_beat, 3)
        self._after_structure_change(canvas)

    def _advance_after_write(self, canvas):
        """Moves on after a note. Writing past the last bar adds one, and an empty next
        bar takes this bar's length so a run of notes keeps going without resetting."""
        measure = canvas.tab_track.measures[canvas.cursor_measure]
        if canvas.cursor_beat == len(measure.beats) - 1:
            bar = canvas.cursor_measure
            if bar == len(canvas.tab_track.measures) - 1:
                canvas.tab_track.add_measure(after=bar)
            following = canvas.tab_track.measures[bar + 1]
            grid = measure.grid()
            if grid is not None and grid <= following.quarters() and not any(b.notes for b in following.beats):
                following.set_grid(grid)
            canvas.commit_edit()
        canvas.move_cursor(d_beat=1)

    def _on_vu_level_changed(self, recorder, level_fraction):
        self.progress_vu.set_fraction(level_fraction)

    def _on_input_meter_toggled(self, btn):
        if btn.get_active():
            self.interface_recorder.start_input_monitor()
        else:
            self.interface_recorder.stop_input_monitor()

    def _insert_dead_note(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        canvas.set_fret_at_cursor(0, "x")

        s_idx = canvas.cursor_string
        play_pluck_sound(s_idx, 0, "x", canvas.tab_track)

        self._advance_after_write(canvas)

    def _on_visual_fret_clicked(self, picker, string_idx, fret, technique_mode):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        play_pluck_sound(string_idx, fret, technique_mode, canvas.tab_track)
        if self.btn_neck_write.get_active():
            canvas.cursor_string = string_idx
            tech, b_val, b_type = self._armed_technique()
            canvas.set_fret_at_cursor(fret, tech, bend_val=b_val, bend_type=b_type)
            self._advance_after_write(canvas)

    def _apply_technique(self, tech_code):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        m_idx = canvas.cursor_measure
        b_idx = canvas.cursor_beat
        s_idx = canvas.cursor_string

        m = canvas.tab_track.measures[m_idx]
        b = m.beats[b_idx]
        if s_idx in b.notes:
            note = b.notes[s_idx]
            tokens = note.technique.split()
            if tech_code in tokens:
                tokens.remove(tech_code)
            elif tokens == ["x"]:
                tokens = [tech_code]
            else:
                tokens.append(tech_code)
            note.technique = " ".join(tokens)
            canvas.commit_edit()
            canvas.queue_draw()
        else:
            self.active_technique = "" if self.active_technique == tech_code else tech_code
            self._sync_edit_buttons(canvas)

    def _apply_grace_note(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        m_idx = canvas.cursor_measure
        b_idx = canvas.cursor_beat
        s_idx = canvas.cursor_string

        m = canvas.tab_track.measures[m_idx]
        b = m.beats[b_idx]
        if s_idx in b.notes:
            note = b.notes[s_idx]
            note.grace_fret = None if note.grace_fret is not None else int(self.spin_grace_fret.get_value())
            canvas.commit_edit()
            canvas.queue_draw()
        else:
            self.show_toast("Write a note first, then add its grace note")

    def _clear_current_beat(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        m_idx = canvas.cursor_measure
        b_idx = canvas.cursor_beat
        m = canvas.tab_track.measures[m_idx]
        b = m.beats[b_idx]
        b.notes.clear()
        canvas.commit_edit()
        canvas.queue_draw()

    def _setup_shortcuts(self):
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    # --- Keyboard shortcuts ---

    def _on_key_pressed(self, controller, keyval, keycode, state):
        canvas = self._get_active_canvas()
        if not canvas:
            return False

        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if is_ctrl:
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                canvas.set_zoom(canvas.zoom_level + ZOOM_STEP)
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                canvas.set_zoom(canvas.zoom_level - ZOOM_STEP)
            elif keyval == Gdk.KEY_0:
                canvas.set_zoom(1.0)
            elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self._on_remove_measure(None)
            elif keyval == Gdk.KEY_Left:
                canvas.prev_page()
            elif keyval == Gdk.KEY_Right:
                canvas.next_page()
            elif keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self._undo(is_shift)
            elif keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self._undo(True)
            elif keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self._copy_bars()
            elif keyval in (Gdk.KEY_x, Gdk.KEY_X):
                self._copy_bars()
                self._on_remove_measure(None)
            elif keyval in (Gdk.KEY_v, Gdk.KEY_V):
                self._paste_bars()
            elif keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._on_save_clicked(None)
            elif keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self._on_open_clicked(None)
            elif keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self.add_new_song_tab()
            elif keyval in (Gdk.KEY_w, Gdk.KEY_W):
                self.tab_view.close_page(self.tab_view.get_selected_page())
            else:
                return False
            return True

        if state & Gdk.ModifierType.ALT_MASK:
            if Gdk.KEY_1 <= keyval < Gdk.KEY_1 + len(NOTE_DENOMINATORS):
                self._set_note_length(4.0 / NOTE_DENOMINATORS[keyval - Gdk.KEY_1])
                return True
            return False

        if is_shift and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self._commit_typed_fret_immediately()
            canvas.extend_selection(1 if keyval == Gdk.KEY_Right else -1)
            return True

        if keyval == Gdk.KEY_Escape:
            canvas.clear_selection()
            canvas.grab_focus()
            self._on_cursor_changed(None, canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string)
            return True
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_End):
            canvas.cursor_measure = 0 if keyval == Gdk.KEY_Home else len(canvas.tab_track.measures) - 1
            canvas.cursor_beat = 0
            canvas.move_cursor()
            return True
        elif keyval == Gdk.KEY_bracketleft:
            self._scale_note_length(0.5)
            return True
        elif keyval == Gdk.KEY_bracketright:
            self._scale_note_length(2.0)
            return True
        elif keyval == Gdk.KEY_period:
            self._toggle_dotted()
            return True
        elif keyval == Gdk.KEY_semicolon:
            self._split_triplet()
            return True
        elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.btn_loop.set_active(not self.btn_loop.get_active())
            return True
        elif keyval in (Gdk.KEY_k, Gdk.KEY_K):
            self.btn_metronome.set_active(not self.btn_metronome.get_active())
            return True
        elif keyval == Gdk.KEY_Page_Up:
            canvas.prev_page()
            return True
        elif keyval == Gdk.KEY_Page_Down:
            canvas.next_page()
            return True
        elif keyval == Gdk.KEY_Up:
            self._commit_typed_fret_immediately()
            canvas.move_cursor(d_string=-1)
            return True
        elif keyval == Gdk.KEY_Down:
            self._commit_typed_fret_immediately()
            canvas.move_cursor(d_string=1)
            return True
        elif keyval == Gdk.KEY_Left:
            self._commit_typed_fret_immediately()
            canvas.move_cursor(d_beat=-1)
            return True
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            just_typed = bool(self.typed_fret_buffer)
            self._commit_typed_fret_immediately()
            if just_typed:
                self._advance_after_write(canvas)
            else:
                canvas.move_cursor(d_beat=1)
            return True
        elif keyval == Gdk.KEY_space:
            self._commit_typed_fret_immediately()
            self._on_play_clicked(None)
            return True
        elif keyval in (Gdk.KEY_x, Gdk.KEY_X):
            self._insert_dead_note()
            return True
        elif keyval in (Gdk.KEY_g, Gdk.KEY_G):
            self._apply_grace_note()
            return True
        elif keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._apply_technique("PM")
            return True
        elif keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self._apply_technique("h")
            return True
        elif keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self._apply_technique("p")
            return True
        elif keyval in (Gdk.KEY_b, Gdk.KEY_B):
            self._apply_bend(1.0, "bend")
            return True
        elif keyval in (Gdk.KEY_slash, Gdk.KEY_s, Gdk.KEY_S):
            self._apply_technique("/")
            return True
        elif keyval == Gdk.KEY_backslash:
            self._apply_technique("\\")
            return True
        elif keyval in (Gdk.KEY_asciitilde, Gdk.KEY_grave):
            self._apply_technique("~")
            return True
        elif keyval in (Gdk.KEY_t, Gdk.KEY_T):
            self._apply_technique("t")
            return True
        elif keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.typed_fret_buffer = ""
            canvas.set_fret_at_cursor(-1)
            return True

        if Gdk.KEY_0 <= keyval <= Gdk.KEY_9:
            digit = str(keyval - Gdk.KEY_0)
            self._handle_typed_digit(digit)
            return True
        elif Gdk.KEY_KP_0 <= keyval <= Gdk.KEY_KP_9:
            digit = str(keyval - Gdk.KEY_KP_0)
            self._handle_typed_digit(digit)
            return True

        return False

    def _handle_typed_digit(self, digit):
        canvas = self._get_active_canvas()
        if not canvas:
            return

        if self.commit_timer_id > 0:
            GLib.source_remove(self.commit_timer_id)
            self.commit_timer_id = 0

        new_buf = self.typed_fret_buffer + digit
        if int(new_buf) > 22:
            # "35" can't be one fret: the 3 stays here and the 5 starts the next slot.
            self._advance_after_write(canvas)
            new_buf = digit
        self.typed_fret_buffer = new_buf
        val = int(new_buf)

        tech, b_val, b_type = self._armed_technique()
        canvas.set_fret_at_cursor(val, tech, bend_val=b_val, bend_type=b_type)
        self._syncing = True
        self.spin_direct_fret.set_value(val)
        self._syncing = False

        s_idx = canvas.cursor_string
        threading.Thread(target=play_pluck_sound, args=(s_idx, val, tech, canvas.tab_track), daemon=True).start()

        self.commit_timer_id = GLib.timeout_add(550, self._auto_commit_typed_fret_timeout)

    def _auto_commit_typed_fret_timeout(self):
        canvas = self._get_active_canvas()
        if canvas:
            self._advance_after_write(canvas)
        self.commit_timer_id = 0
        self.typed_fret_buffer = ""
        return False

    def _commit_typed_fret_immediately(self):
        if self.commit_timer_id > 0:
            GLib.source_remove(self.commit_timer_id)
            self.commit_timer_id = 0
        self.typed_fret_buffer = ""

    # --- Actions ---

    def _undo(self, redo):
        canvas = self._get_active_canvas()
        if canvas:
            self._commit_typed_fret_immediately()
            done = canvas.redo() if redo else canvas.undo()
            if done:
                self._restart_playback_soon()

    def _copy_bars(self):
        canvas = self._get_active_canvas()
        if canvas:
            first, last = canvas.selected_bars()
            self.bar_clipboard = [m.to_dict() for m in canvas.tab_track.measures[first:last + 1]]
            self.show_toast(f"Copied {len(self.bar_clipboard)} bar(s)")

    def _paste_bars(self):
        canvas = self._get_active_canvas()
        if canvas and self.bar_clipboard:
            at = canvas.cursor_measure + 1
            canvas.tab_track.measures[at:at] = [Measure.from_dict(d) for d in self.bar_clipboard]
            canvas.selection_anchor = None
            canvas.cursor_measure = at
            canvas.cursor_beat = 0
            self._after_structure_change(canvas)

    def _on_add_measure(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        canvas.tab_track.add_measure(after=canvas.cursor_measure)
        self._after_structure_change(canvas)

    def _on_remove_measure(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        track = canvas.tab_track
        first, last = canvas.selected_bars()
        if last - first + 1 >= len(track.measures):
            self.show_toast("Cannot remove every bar")
            return
        del track.measures[first:last + 1]
        canvas.selection_anchor = None
        canvas.cursor_measure = first
        canvas.cursor_beat = 0
        self._after_structure_change(canvas)

    def _on_bpm_changed(self, spin):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        canvas.tab_track.bpm = int(spin.get_value())
        self._refresh_tab_title(canvas)
        self._restart_playback_soon()

    def _on_speed_changed(self, combo, param):
        canvas = self._get_active_canvas()
        if self._syncing or not canvas:
            return
        canvas.tab_track.speed_multiplier = SPEEDS[combo.get_selected()]
        self._refresh_tab_title(canvas)
        self._restart_playback_soon()

    def _on_record_clicked(self, btn):
        if self.interface_recorder.is_recording:
            saved_file = self.interface_recorder.stop_recording()
            self.record_content.set_label("Record")
            self.record_content.set_icon_name("media-record-symbolic")
            self.btn_record.remove_css_class("destructive-action")
            self.btn_record.add_css_class("record-btn")
            if saved_file:
                self.show_toast(f"Saved recording take to {os.path.basename(saved_file)}")
        else:
            rec_file = self.interface_recorder.start_recording()
            if not rec_file:
                self.show_toast("Could not start recording: check your audio input")
            else:
                self.btn_input.set_active(True)
                self.record_content.set_label("Stop")
                self.record_content.set_icon_name("media-playback-stop-symbolic")
                self.btn_record.remove_css_class("record-btn")
                self.btn_record.add_css_class("destructive-action")
                self.show_toast(f"Recording guitar take to {os.path.basename(rec_file)}")

    def _on_export_ascii_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        name = safe_file_name(canvas.tab_track.title, "Guitar_Tab")
        export_path = os.path.join(TAB_LIBRARY_DIR, f"{name}.txt")
        with open(export_path, "w") as f:
            f.write(canvas.tab_track.export_ascii())
        self.show_toast(f"Exported ASCII tab to ~/My_Guitar_Tabs/{name}.txt")

    def _on_save_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        if not canvas.is_dirty():
            self.show_toast("Already saved" if canvas.file_path else "Nothing to save yet")
            return
        saved = self._save_canvas(canvas)
        if saved:
            self.show_toast(f"Saved {saved}")
