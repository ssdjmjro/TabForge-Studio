import shutil
#!/usr/bin/env python3
import os
import json
import time
import zipfile
import threading
from datetime import datetime
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib, Gio

from tab_engine import TabTrack, TabAudioPlayer, TUNINGS
from audio_recorder import AudioInterfaceRecorder
from tab_canvas import TabCanvasWidget
from visual_picker import VisualGuitarPicker, STRING_NAMES, play_pluck_sound

TAB_LIBRARY_DIR = os.path.expanduser("~/My_Guitar_Tabs")
PDF_EXPORT_DIR = os.path.expanduser("~/My_Guitar_PDFs")
STRAT_ICON_PATH = os.path.join(os.path.dirname(__file__), 'stratocaster.svg')

def disable_spin_scroll(spin_widget):
    ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
    ctrl.connect("scroll", lambda c, dx, dy: True)
    spin_widget.add_controller(ctrl)

def make_scrolled_bar(box):
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scrolled.set_child(box)

    ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
    def _on_scroll(c, dx, dy):
        adj = scrolled.get_hadjustment()
        if adj:
            delta = (dy if dy != 0 else dx) * 35.0
            new_val = max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(), adj.get_value() + delta))
            adj.set_value(new_val)
            return True
        return False

    ctrl.connect("scroll", _on_scroll)
    scrolled.add_controller(ctrl)
    return scrolled

class TabWriterWindow(Adw.ApplicationWindow):
    """
    Songsterr-Style GTK4 22-Fret Guitar Studio featuring Fender Stratocaster Guitar Branding,
    One-Click Strat Backup Archiver (zip), Dedicated PDF Storage (~/My_Guitar_PDFs/), and Specialized Tab Storage.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("TabForge Studio - Stratocaster Guitar Studio")
        self.set_icon_name("io.github.tabforge.studio")
        
        # Calculate optimal initial window size based on display dimensions (Guarantees room for neck panel)
        def_w, def_h = 1040, 720
        display = Gdk.Display.get_default()
        if display:
            monitors = display.get_monitors()
            if monitors.get_n_items() > 0:
                geom = monitors.get_item(0).get_geometry()
                def_w = min(1180, max(750, int(geom.width * 0.85)))
                def_h = min(780, max(620, int(geom.height * 0.78)))
        self.set_default_size(def_w, def_h)

        os.makedirs(TAB_LIBRARY_DIR, exist_ok=True)
        os.makedirs(PDF_EXPORT_DIR, exist_ok=True)

        self.audio_player = TabAudioPlayer()
        self.audio_player.on_start_cb = self._on_audio_started
        self.audio_player.on_stop_cb = self._stop_playback
        self.interface_recorder = AudioInterfaceRecorder()
        self.interface_recorder.connect('level-changed', self._on_vu_level_changed)

        self.typed_fret_buffer = ""
        self.commit_timer_id = 0

        self.play_anim_timer_id = 0
        self.play_start_time = 0.0

        self._build_ui()
        self._setup_shortcuts()

        # Start Automatic Save Periodic Timer (Every 10 Minutes = 600 Seconds)
        GLib.timeout_add_seconds(600, self._periodic_auto_save)

    def _build_ui(self):
        self.toast_overlay = Adw.ToastOverlay()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Initialize labels first before creating tabs
        self.lbl_position = Gtk.Label(label="📍 Bar 1 | Beat 1")
        self.lbl_position.add_css_class("caption-heading")

        self.lbl_autosave = Gtk.Label(label="💾 Auto-Saves every 10 mins")
        self.lbl_autosave.add_css_class("dim-label")

        self.lbl_page_num = Gtk.Label(label="Page 1 / 1")
        self.lbl_page_num.add_css_class("caption-heading")

        # 1. HeaderBar
        header = Adw.HeaderBar()

        # App Brand Icon in HeaderBar
        if os.path.exists(STRAT_ICON_PATH):
            app_icon = Gtk.Image.new_from_file(STRAT_ICON_PATH)
            app_icon.set_pixel_size(24)
            app_icon.set_tooltip_text("TabForge Studio - Stratocaster Edition")
            app_icon.set_margin_start(6)
            app_icon.set_margin_end(2)
            header.pack_start(app_icon)

        # 📁 OPEN SPECIALIZED FOLDER IN FILE MANAGER BUTTON
        btn_open_folder = Gtk.Button.new_from_icon_name("folder-music-symbolic")
        btn_open_folder.set_tooltip_text("Open Specialized Folder (~/My_Guitar_Tabs) in File Manager")
        btn_open_folder.connect("clicked", self._on_open_specialized_folder_clicked)
        header.pack_start(btn_open_folder)

        # 📦 FULL APP BACKUP BUTTON
        btn_backup = Gtk.Button(label="📦 Backup App")
        btn_backup.add_css_class("pill")
        btn_backup.set_tooltip_text("One-Click Backup: Creates complete zip archive of TabForge Studio App, Source Code, Tabs & PDFs")
        btn_backup.connect("clicked", self._on_backup_clicked)
        header.pack_start(btn_backup)

        btn_open = Gtk.Button.new_from_icon_name("document-open-symbolic")
        btn_open.set_tooltip_text("Open Saved Tab (.json)")
        btn_open.connect("clicked", self._on_open_clicked)
        header.pack_start(btn_open)

        btn_save = Gtk.Button.new_from_icon_name("document-save-symbolic")
        btn_save.set_tooltip_text("Save Tab to Specialized Folder (~/My_Guitar_Tabs)")
        btn_save.connect("clicked", self._on_save_clicked)
        header.pack_start(btn_save)

        # Song Title Entry for Naming Tabs
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_song_title = Gtk.Label(label="Song:")
        lbl_song_title.add_css_class("dim-label")
        title_box.append(lbl_song_title)

        self.entry_song_title = Gtk.Entry()
        self.entry_song_title.set_text("Song 1")
        self.entry_song_title.set_width_chars(12)
        self.entry_song_title.connect("changed", self._on_song_title_changed)
        title_box.append(self.entry_song_title)

        header.set_title_widget(title_box)

        # Zoom Controls
        btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        btn_zoom_out.set_tooltip_text("Zoom Out (Ctrl -)")
        btn_zoom_out.connect("clicked", lambda b: self._get_active_canvas().set_zoom(self._get_active_canvas().zoom_level - 0.15) if self._get_active_canvas() else None)
        header.pack_end(btn_zoom_out)

        btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        btn_zoom_in.set_tooltip_text("Zoom In (Ctrl +)")
        btn_zoom_in.connect("clicked", lambda b: self._get_active_canvas().set_zoom(self._get_active_canvas().zoom_level + 0.15) if self._get_active_canvas() else None)
        header.pack_end(btn_zoom_in)

        # Toggle Dark / Paper Sheet Theme Button
        self.btn_theme_toggle = Gtk.Button(label="📄 Paper")
        self.btn_theme_toggle.set_tooltip_text("Switch between Dark Studio & Classic Paper Sheet tab view")
        self.btn_theme_toggle.connect("clicked", self._on_toggle_theme)
        header.pack_end(self.btn_theme_toggle)

        # Toggle Visual Fretboard Neck Panel Button
        self.btn_toggle_neck = Gtk.ToggleButton(label="🎸 Neck")
        self.btn_toggle_neck.set_active(True)
        self.btn_toggle_neck.set_tooltip_text("Show or Hide 22-Fret Guitar Neck Panel")
        self.btn_toggle_neck.connect("toggled", self._on_toggle_neck)
        header.pack_end(self.btn_toggle_neck)

        # 🗑️ REMOVE MEASURE BAR BUTTON
        btn_remove_measure = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        btn_remove_measure.set_tooltip_text("Remove Measure Bar (Delete Bar)")
        btn_remove_measure.connect("clicked", self._on_remove_measure)
        header.pack_end(btn_remove_measure)

        # ➕ ADD MEASURE BAR BUTTON
        btn_add_measure = Gtk.Button.new_from_icon_name("list-add-symbolic")
        btn_add_measure.set_tooltip_text("Add Measure Bar (+)")
        btn_add_measure.connect("clicked", self._on_add_measure)
        header.pack_end(btn_add_measure)

        # 📄 EXPORT PDF BUTTON
        btn_export_pdf = Gtk.Button(label="📄 PDF")
        btn_export_pdf.add_css_class("suggested-action")
        btn_export_pdf.set_tooltip_text("Export Printable Vector PDF Sheet Music to ~/My_Guitar_PDFs/")
        btn_export_pdf.connect("clicked", self._on_export_pdf_clicked)
        header.pack_end(btn_export_pdf)

        btn_export_ascii = Gtk.Button(label="ASCII")
        btn_export_ascii.connect("clicked", self._on_export_ascii_clicked)
        header.pack_end(btn_export_ascii)

        main_box.append(header)

        # 2. GOOGLE CHROME STYLE MULTI-SONG TAB BAR
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar()
        self.tab_bar.set_view(self.tab_view)
        self.tab_view.connect('notify::selected-page', self._on_selected_page_changed)
        main_box.append(self.tab_bar)

        # 3. Controls & Transport Bar
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top_bar.set_margin_start(8)
        top_bar.set_margin_end(8)
        top_bar.set_margin_top(4)
        top_bar.set_margin_bottom(4)

        # Chrome Style New Song Tab Button
        btn_new_tab = Gtk.Button(label="➕ New Song Tab")
        btn_new_tab.add_css_class("pill")
        btn_new_tab.set_tooltip_text("Create a new song tab like Google Chrome")
        btn_new_tab.connect("clicked", lambda b: self.add_new_song_tab())
        top_bar.append(btn_new_tab)

        top_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Play / Stop
        self.btn_play = Gtk.Button(label=" ▶ Play")
        self.btn_play.add_css_class("suggested-action")
        self.btn_play.add_css_class("transport-btn")
        self.btn_play.add_css_class("pill")
        self.btn_play.connect("clicked", self._on_play_clicked)
        top_bar.append(self.btn_play)

        # BPM
        lbl_bpm = Gtk.Label(label="BPM:")
        lbl_bpm.add_css_class("dim-label")
        top_bar.append(lbl_bpm)

        self.spin_bpm = Gtk.SpinButton.new_with_range(30, 400, 1)
        self.spin_bpm.set_value(120)
        self.spin_bpm.connect("value-changed", self._on_bpm_changed)
        disable_spin_scroll(self.spin_bpm)
        top_bar.append(self.spin_bpm)

        # ⚡ Speed Multiplier
        lbl_speed = Gtk.Label(label="⚡ Speed:")
        lbl_speed.add_css_class("dim-label")
        top_bar.append(lbl_speed)

        self.speed_options = ["0.5x", "0.75x", "1.0x (Norm)", "1.25x", "1.5x (Fast)", "2.0x (Double)", "2.5x"]
        self.speed_values = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]
        self.combo_speed = Gtk.DropDown.new_from_strings(self.speed_options)
        self.combo_speed.set_selected(2)
        self.combo_speed.set_tooltip_text("Adjust Playback Speed Multiplier")
        self.combo_speed.connect("notify::selected", self._on_speed_changed)
        top_bar.append(self.combo_speed)

        top_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # 🎸 GUITAR TUNING SELECTOR
        lbl_tuning = Gtk.Label(label="🎸 Tuning:")
        lbl_tuning.add_css_class("dim-label")
        top_bar.append(lbl_tuning)

        tuning_keys = list(TUNINGS.keys()) + ["⚙️ Custom Tuning..."]
        self.combo_tuning = Gtk.DropDown.new_from_strings(tuning_keys)
        self.combo_tuning.set_selected(0)
        self.combo_tuning.set_tooltip_text("Select Guitar Tuning or Build Custom Tuning")
        self.combo_tuning.connect("notify::selected", self._on_tuning_changed)
        top_bar.append(self.combo_tuning)

        btn_custom_tuning = Gtk.Button(label="⚙️ Custom")
        btn_custom_tuning.add_css_class("pill")
        btn_custom_tuning.set_tooltip_text("Build & Save Custom 6-String Guitar Tuning")
        btn_custom_tuning.connect("clicked", lambda b: self._open_custom_tuning_dialog())
        top_bar.append(btn_custom_tuning)

        # 🪗 CAPO CONTROL
        lbl_capo = Gtk.Label(label="🪗 Capo:")
        lbl_capo.add_css_class("dim-label")
        top_bar.append(lbl_capo)

        self.spin_capo = Gtk.SpinButton.new_with_range(0, 12, 1)
        self.spin_capo.set_value(0)
        self.spin_capo.set_tooltip_text("Set Guitar Capo Fret Position (0 = No Capo)")
        self.spin_capo.connect("value-changed", self._on_capo_changed)
        disable_spin_scroll(self.spin_capo)
        top_bar.append(self.spin_capo)

        top_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Page System Navigation Controls (Previous Page / Page Number / Next Page)
        btn_prev_p = Gtk.Button(label="◀")
        btn_prev_p.add_css_class("pill")
        btn_prev_p.set_tooltip_text("Previous Page (PageUp / Ctrl+Left)")
        btn_prev_p.connect("clicked", lambda b: self._get_active_canvas().prev_page() if self._get_active_canvas() else None)
        top_bar.append(btn_prev_p)

        top_bar.append(self.lbl_page_num)

        btn_next_p = Gtk.Button(label="▶")
        btn_next_p.add_css_class("pill")
        btn_next_p.set_tooltip_text("Next Page (PageDown / Ctrl+Right)")
        btn_next_p.connect("clicked", lambda b: self._get_active_canvas().next_page() if self._get_active_canvas() else None)
        top_bar.append(btn_next_p)

        top_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Beats Per Bar Spinner
        lbl_beats = Gtk.Label(label="Beats/Bar:")
        lbl_beats.add_css_class("dim-label")
        top_bar.append(lbl_beats)

        self.spin_beats = Gtk.SpinButton.new_with_range(1, 32, 1)
        self.spin_beats.set_value(8)
        self.spin_beats.connect("value-changed", self._on_beats_per_bar_changed)
        disable_spin_scroll(self.spin_beats)
        top_bar.append(self.spin_beats)

        top_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        top_bar.append(self.lbl_position)
        top_bar.append(self.lbl_autosave)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_bar.append(spacer)

        # Live Input Volume Meter
        lbl_vu = Gtk.Label(label="Input:")
        lbl_vu.add_css_class("dim-label")
        top_bar.append(lbl_vu)

        self.progress_vu = Gtk.ProgressBar()
        self.progress_vu.set_size_request(50, 12)
        self.progress_vu.set_fraction(0.0)
        top_bar.append(self.progress_vu)

        # Record Button
        self.btn_record = Gtk.Button(label=" 🎙️ Record")
        self.btn_record.add_css_class("destructive-action")
        self.btn_record.add_css_class("pill")
        self.btn_record.connect("clicked", self._on_record_clicked)
        top_bar.append(self.btn_record)

        top_scroll = make_scrolled_bar(top_bar)
        main_box.append(top_scroll)

        # 4. Tab View Container for Chrome Tabs
        self.tab_view.set_vexpand(True)
        main_box.append(self.tab_view)

        # Add initial Chrome Song Tab after labels are ready
        self.add_new_song_tab("Song 1")

        # 5. Techniques & Fret Entry Bar
        tech_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tech_bar.set_margin_start(8)
        tech_bar.set_margin_end(8)
        tech_bar.set_margin_top(4)

        # Type Fret # Spinner
        lbl_type_fret = Gtk.Label(label="Fret #:")
        lbl_type_fret.add_css_class("dim-label")
        tech_bar.append(lbl_type_fret)

        self.spin_direct_fret = Gtk.SpinButton.new_with_range(0, 22, 1)
        self.spin_direct_fret.set_value(0)
        self.spin_direct_fret.connect("value-changed", self._on_direct_fret_changed)
        disable_spin_scroll(self.spin_direct_fret)
        tech_bar.append(self.spin_direct_fret)

        btn_apply_fret = Gtk.Button(label="Set")
        btn_apply_fret.add_css_class("pill")
        btn_apply_fret.connect("clicked", lambda b: self._commit_direct_fret_entry())
        tech_bar.append(btn_apply_fret)

        tech_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Harmonic Modes
        self.btn_mode_normal = Gtk.ToggleButton(label="Normal")
        self.btn_mode_normal.set_active(True)
        self.btn_mode_normal.add_css_class("pill")
        self.btn_mode_normal.connect("toggled", lambda b: self._set_harmonic_mode("", b))
        tech_bar.append(self.btn_mode_normal)

        self.btn_mode_nh = Gtk.ToggleButton(label="Harmonics <NH>")
        self.btn_mode_nh.add_css_class("pill")
        self.btn_mode_nh.connect("toggled", lambda b: self._set_harmonic_mode("NH", b))
        tech_bar.append(self.btn_mode_nh)

        self.btn_mode_ah = Gtk.ToggleButton(label="Pinch [AH]")
        self.btn_mode_ah.add_css_class("pill")
        self.btn_mode_ah.connect("toggled", lambda b: self._set_harmonic_mode("AH", b))
        tech_bar.append(self.btn_mode_ah)

        btn_dead = Gtk.Button(label="Dead (X)")
        btn_dead.add_css_class("pill")
        btn_dead.connect("clicked", lambda b: self._insert_dead_note())
        tech_bar.append(btn_dead)

        techs = [
            ("PM", "PM"),
            ("h", "h"),
            ("p", "p"),
            ("/", "/"),
            ("~", "~"),
        ]

        for label_str, tech_code in techs:
            btn = Gtk.Button(label=label_str)
            btn.add_css_class("pill")
            btn.connect("clicked", lambda b, t=tech_code: self._apply_technique(t))
            tech_bar.append(btn)

        # ⤴️ Dedicated Note Bend Popover Menu Button
        popover_bend = Gtk.Popover()
        bend_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bend_box.set_margin_start(8)
        bend_box.set_margin_end(8)
        bend_box.set_margin_top(8)
        bend_box.set_margin_bottom(8)

        lbl_b_title = Gtk.Label(label="⤴️ Note Bend Options")
        lbl_b_title.add_css_class("heading")
        bend_box.append(lbl_b_title)

        bend_options = [
            ("1/2 Bend (1 Semitone)", 0.5, "bend"),
            ("Full Bend (2 Semitones)", 1.0, "bend"),
            ("1 ½ Bend (3 Semitones)", 1.5, "bend"),
            ("2 Bend (4 Semitones)", 2.0, "bend"),
            ("Bend & Release", 1.0, "bend_release"),
            ("Pre-Bend", 1.0, "prebend"),
            ("Pre-Bend & Release", 1.0, "prebend_release"),
        ]

        for label_str, b_val, b_type in bend_options:
            b_btn = Gtk.Button(label=label_str)
            b_btn.add_css_class("flat")
            b_btn.connect("clicked", lambda btn, v=b_val, t=b_type, pop=popover_bend: (pop.popdown(), self._apply_bend(v, t)))
            bend_box.append(b_btn)

        popover_bend.set_child(bend_box)
        btn_bend = Gtk.MenuButton(label="⤴️ Bend")
        btn_bend.add_css_class("pill")
        btn_bend.set_tooltip_text("Apply or customize pitch bends (Hotkey: B)")
        btn_bend.set_popover(popover_bend)
        tech_bar.append(btn_bend)

        tech_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        lbl_grace = Gtk.Label(label="Deco Grace:")
        lbl_grace.add_css_class("dim-label")
        tech_bar.append(lbl_grace)

        self.spin_grace_fret = Gtk.SpinButton.new_with_range(0, 22, 1)
        self.spin_grace_fret.set_value(5)
        disable_spin_scroll(self.spin_grace_fret)
        tech_bar.append(self.spin_grace_fret)

        btn_grace = Gtk.Button(label="✨ Grace")
        btn_grace.add_css_class("pill")
        btn_grace.set_tooltip_text("Add decorative grace note small number to selected note (Hotkey: G)")
        btn_grace.connect("clicked", lambda b: self._apply_grace_note())
        tech_bar.append(btn_grace)

        btn_clear = Gtk.Button(label="Clear")
        btn_clear.add_css_class("pill")
        btn_clear.connect("clicked", lambda b: self._clear_current_beat())
        tech_bar.append(btn_clear)

        tech_scroll = make_scrolled_bar(tech_bar)
        main_box.append(tech_scroll)

        # 6. Guitar Neck Panel (Guaranteed Visible Fretboard)
        self.neck_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.neck_panel.set_vexpand(False)
        self.neck_panel.set_size_request(-1, 120)

        self.visual_picker = VisualGuitarPicker()
        self.visual_picker.connect("fret-clicked", self._on_visual_fret_clicked)

        picker_frame = Gtk.Frame()
        picker_frame.set_vexpand(False)
        picker_frame.set_size_request(-1, 118)
        picker_frame.set_margin_start(8)
        picker_frame.set_margin_end(8)
        picker_frame.set_margin_top(4)
        picker_frame.set_margin_bottom(8)
        picker_frame.set_child(self.visual_picker)
        self.neck_panel.append(picker_frame)

        main_box.append(self.neck_panel)

        self.toast_overlay.set_child(main_box)
        self.set_content(self.toast_overlay)

    def add_new_song_tab(self, title=None):
        n_pages = self.tab_view.get_n_pages()
        if not title:
            title = f"Song {n_pages + 1}"

        canvas = TabCanvasWidget()
        canvas.tab_track.title = title
        canvas.connect('cursor-changed', self._on_cursor_changed)
        canvas.connect('zoom-changed', lambda widget, z: self.show_toast(f"🔍 Zoom: {int(z * 100)}%"))
        canvas.connect('page-changed', self._on_page_changed)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(canvas)

        page = self.tab_view.append(scrolled)
        page.set_title(f"🎸 {title}")

        self.tab_view.set_selected_page(page)
        self.show_toast(f"Song Tab '{title}' set to 10-min auto-save")

    def _get_active_canvas(self):
        page = self.tab_view.get_selected_page()
        if page:
            scrolled = page.get_child()
            if scrolled:
                child = scrolled.get_child()
                if isinstance(child, TabCanvasWidget):
                    return child
                elif hasattr(child, 'get_child'):
                    return child.get_child()
        return None

    def _on_song_title_changed(self, entry):
        new_title = entry.get_text().strip()
        if not new_title:
            return

        page = self.tab_view.get_selected_page()
        if page:
            page.set_title(f"🎸 {new_title}")

        canvas = self._get_active_canvas()
        if canvas:
            canvas.tab_track.title = new_title
            canvas.queue_draw()
            self.audio_player.request_precompile(canvas.tab_track)

    def _on_tuning_changed(self, dropdown, param):
        selected_idx = dropdown.get_selected()
        tuning_keys = list(TUNINGS.keys())
        if selected_idx == len(tuning_keys):
            self._open_custom_tuning_dialog()
            return

        if 0 <= selected_idx < len(tuning_keys):
            tuning_name = tuning_keys[selected_idx]
            canvas = self._get_active_canvas()
            if canvas:
                canvas.tab_track.tuning_name = tuning_name
                freqs, names = TUNINGS[tuning_name]
                canvas.tab_track.custom_open_freqs = freqs
                canvas.tab_track.custom_string_names = names
                canvas.queue_draw()
                self.audio_player.invalidate_cache()
                self.audio_player.request_precompile(canvas.tab_track)
            if hasattr(self, 'visual_picker'):
                self.visual_picker.set_tuning(tuning_name)
            _, names = TUNINGS[tuning_name]
            names_str = " ".join(names)
            self.show_toast(f"Guitar Tuning set to {tuning_name} ({names_str})")

    def _open_custom_tuning_dialog(self):
        dialog = Adw.Window(title="Custom Guitar Tuning Builder", transient_for=self, modal=True)
        dialog.set_default_size(440, 520)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        lbl_hdr = Gtk.Label(label="🎸 Create Custom Guitar Tuning")
        lbl_hdr.add_css_class("title-2")
        box.append(lbl_hdr)

        lbl_sub = Gtk.Label(label="Customize pitch note and octave for each string (1 to 6)")
        lbl_sub.add_css_class("dim-label")
        box.append(lbl_sub)

        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_name = Gtk.Label(label="Tuning Name:")
        name_box.append(lbl_name)
        entry_name = Gtk.Entry()
        entry_name.set_text("Custom Tuning 1")
        entry_name.set_hexpand(True)
        name_box.append(entry_name)
        box.append(name_box)

        notes = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        default_strings = [
            ("High e", 4, 4),  # E4 (String 0)
            ("B", 3, 11),      # B3 (String 1)
            ("G", 3, 7),       # G3 (String 2)
            ("D", 3, 2),       # D3 (String 3)
            ("A", 2, 9),       # A2 (String 4)
            ("Low E", 2, 4),   # E2 (String 5)
        ]

        string_combos = []
        octave_spins = []

        for s in range(6):
            s_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_s = Gtk.Label(label=f"String {s + 1} ({default_strings[s][0]}):")
            lbl_s.set_width_chars(16)
            s_box.append(lbl_s)

            combo_note = Gtk.DropDown.new_from_strings(notes)
            combo_note.set_selected(default_strings[s][2])
            s_box.append(combo_note)
            string_combos.append(combo_note)

            lbl_oct = Gtk.Label(label="Octave:")
            s_box.append(lbl_oct)

            spin_oct = Gtk.SpinButton.new_with_range(0, 6, 1)
            spin_oct.set_value(default_strings[s][1])
            disable_spin_scroll(spin_oct)
            s_box.append(spin_oct)
            octave_spins.append(spin_oct)

            box.append(s_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_margin_top(12)

        btn_apply = Gtk.Button(label="Apply Custom Tuning")
        btn_apply.add_css_class("suggested-action")
        btn_apply.add_css_class("pill")

        def _on_apply(b):
            t_name = entry_name.get_text().strip() or "Custom Tuning"
            calc_freqs = []
            calc_names = []
            for s in range(6):
                n_idx = string_combos[s].get_selected()
                oct_val = int(octave_spins[s].get_value())
                n_name = notes[n_idx]
                midi = 12 * (oct_val + 1) + n_idx
                freq = round(440.0 * (2.0 ** ((midi - 69) / 12.0)), 2)
                calc_freqs.append(freq)
                calc_names.append(n_name)

            TUNINGS[t_name] = (calc_freqs, calc_names)

            tuning_keys = list(TUNINGS.keys()) + ["⚙️ Custom Tuning..."]
            if hasattr(self, 'combo_tuning'):
                model = Gtk.StringList.new(tuning_keys)
                self.combo_tuning.set_model(model)
                self.combo_tuning.set_selected(list(TUNINGS.keys()).index(t_name))

            canvas = self._get_active_canvas()
            if canvas:
                canvas.tab_track.tuning_name = t_name
                canvas.tab_track.custom_open_freqs = calc_freqs
                canvas.tab_track.custom_string_names = calc_names
                canvas.queue_draw()
                self.audio_player.request_precompile(canvas.tab_track)

            if hasattr(self, 'visual_picker'):
                self.visual_picker.set_tuning(t_name)

            names_str = " ".join(calc_names)
            self.show_toast(f"Custom Tuning '{t_name}' ({names_str}) applied!")
            dialog.destroy()

        btn_apply.connect("clicked", _on_apply)
        btn_box.append(btn_apply)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.add_css_class("pill")
        btn_cancel.connect("clicked", lambda b: dialog.destroy())
        btn_box.append(btn_cancel)

        box.append(btn_box)
        dialog.set_content(box)
        dialog.present()

    def _on_selected_page_changed(self, tab_view, param_spec):
        canvas = self._get_active_canvas()
        if canvas:
            self.audio_player.request_precompile(canvas.tab_track)
        page = tab_view.get_selected_page()
        if page:
            canvas = self._get_active_canvas()
            if canvas:
                title = canvas.tab_track.title
                self.entry_song_title.set_text(title)
                t_name = canvas.tab_track.tuning_name
                tuning_keys = list(TUNINGS.keys())
                if hasattr(self, 'combo_tuning') and t_name in tuning_keys:
                    self.combo_tuning.set_selected(tuning_keys.index(t_name))
                if hasattr(self, 'spin_bpm'):
                    self.spin_bpm.set_value(canvas.tab_track.bpm)
                if hasattr(self, 'combo_speed'):
                    cur_mult = getattr(canvas.tab_track, 'speed_multiplier', 1.0)
                    if cur_mult in self.speed_values:
                        self.combo_speed.set_selected(self.speed_values.index(cur_mult))
                if hasattr(self, 'spin_capo'):
                    self.spin_capo.set_value(canvas.tab_track.capo)
                if hasattr(self, 'visual_picker'):
                    self.visual_picker.set_tuning(t_name)
                    self.visual_picker.set_capo(canvas.tab_track.capo)
                if hasattr(self, 'lbl_position'):
                    self.lbl_position.set_label(f"📍 Bar {canvas.cursor_measure + 1} | Beat {canvas.cursor_beat + 1}")

    def show_toast(self, text):
        pass

    # --- STRATOCASTER BACKUP ENGINE & AUTOMATIC SAVE ---

    def _on_backup_clicked(self, btn):
        """Creates a complete timestamped backup zip archive of the TabForge Studio App (source code, tabs, and PDFs)."""
        self._trigger_auto_save()
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_filename = f"TabForge_Studio_App_Backup_{date_str}.zip"
        backup_path = os.path.expanduser(f"~/{backup_filename}")

        app_dir = os.path.dirname(os.path.abspath(__file__))
        repo_dir = os.path.dirname(app_dir)

        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 0. Backup root installer & README
                for root_file in ["install.sh", "README.md", ".gitignore"]:
                    rf_path = os.path.join(repo_dir, root_file)
                    if os.path.exists(rf_path):
                        zipf.write(rf_path, arcname=os.path.join("TabForge_Studio_App", root_file))

                # 1. Backup App Source Code & Files
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

                # 2. Backup JSON Tab Library Files
                if os.path.exists(TAB_LIBRARY_DIR):
                    for root, dirs, files in os.walk(TAB_LIBRARY_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, TAB_LIBRARY_DIR)
                            zipf.write(full_path, arcname=os.path.join("TabForge_Studio_App", "My_Guitar_Tabs", rel_path))

                # 3. Backup PDF Export Files
                if os.path.exists(PDF_EXPORT_DIR):
                    for root, dirs, files in os.walk(PDF_EXPORT_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, PDF_EXPORT_DIR)
                            zipf.write(full_path, arcname=os.path.join("TabForge_Studio_App", "My_Guitar_PDFs", rel_path))

            self.show_toast(f"📦 Created Complete App Backup at ~/{backup_filename}")
            Gio.AppInfo.launch_default_for_uri(f"file://{os.path.expanduser('~')}", None)
        except Exception as e:
            print("Backup Error:", e)
            self.show_toast("Failed to create App backup archive.")

    def _trigger_auto_save(self):
        try:
            canvas = self._get_active_canvas()
            if not canvas:
                return

            song_title = canvas.tab_track.title or "Untitled_Riff"
            safe_name = "".join(c for c in song_title if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
            if not safe_name:
                safe_name = "Untitled_Riff"

            song_file_path = os.path.join(TAB_LIBRARY_DIR, f"{safe_name}.json")
            data = canvas.tab_track.to_dict()

            with open(song_file_path, "w") as f:
                json.dump(data, f, indent=2)

            with open(os.path.expanduser("~/my_riff.json"), "w") as f:
                json.dump(data, f, indent=2)

            if hasattr(self, 'lbl_autosave'):
                self.lbl_autosave.set_label(f"💾 Auto-Saved to {safe_name}.json")
        except Exception as e:
            print("Auto-save error:", e)

    def _periodic_auto_save(self):
        self._trigger_auto_save()
        return True

    def _on_export_pdf_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return

        song_title = canvas.tab_track.title or "Guitar_Tab"
        safe_name = "".join(c for c in song_title if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        pdf_path = os.path.join(PDF_EXPORT_DIR, f"{safe_name}.pdf")

        try:
            canvas.export_pdf(pdf_path)
            self.show_toast(f"Exported PDF to ~/My_Guitar_PDFs/{safe_name}.pdf")
            Gio.AppInfo.launch_default_for_uri(f"file://{pdf_path}", None)
        except Exception as e:
            print("PDF Export Error:", e)
            self.show_toast(f"Exported PDF to {safe_name}.pdf")

    def _on_open_specialized_folder_clicked(self, btn):
        try:
            Gio.AppInfo.launch_default_for_uri(f"file://{TAB_LIBRARY_DIR}", None)
            self.show_toast("Opened ~/My_Guitar_Tabs/ in File Manager")
        except Exception as e:
            self._on_library_clicked(btn)

    def _on_library_clicked(self, btn):
        dialog = Gtk.FileChooserDialog(
            title="Specialized Guitar Tabs Folder (~/My_Guitar_Tabs)",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Open Song", Gtk.ResponseType.ACCEPT)

        dialog.set_current_folder(Gio.File.new_for_path(TAB_LIBRARY_DIR))

        filter_json = Gtk.FileFilter()
        filter_json.set_name("Guitar Tab Files (*.json)")
        filter_json.add_pattern("*.json")
        dialog.add_filter(filter_json)

        def _on_response(dialog, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = dialog.get_file()
                if gfile:
                    file_path = gfile.get_path()
                    if os.path.exists(file_path):
                        with open(file_path, "r") as f:
                            data = json.load(f)
                            track = TabTrack.from_dict(data)
                            song_name = track.title or os.path.splitext(os.path.basename(file_path))[0]
                            self.add_new_song_tab(song_name)
                            canvas = self._get_active_canvas()
                            if canvas:
                                canvas.set_tab_track(track)
                            self.show_toast(f"Opened '{song_name}' from Specialized Folder")
            dialog.destroy()

        dialog.connect("response", _on_response)
        dialog.show()

    def _on_page_changed(self, widget, curr_page, total_pages):
        if hasattr(self, 'lbl_page_num'):
            self.lbl_page_num.set_label(f"Page {curr_page} / {total_pages}")

    def _on_audio_started(self):
        canvas = self._get_active_canvas()
        if canvas:
            canvas.is_playing = True
            canvas.set_playhead_position(0, 0.0)
        self.play_start_time = time.monotonic()
        self.last_anim_m = -1
        self.last_anim_b = -1.0

        if hasattr(self, 'btn_play'):
            self.btn_play.set_label(" ⏹ Stop")
            self.btn_play.remove_css_class("suggested-action")
            self.btn_play.add_css_class("destructive-action")

        if self.play_anim_timer_id > 0:
            GLib.source_remove(self.play_anim_timer_id)

        self.play_anim_timer_id = GLib.timeout_add(30, self._update_playhead_animation)

    def _on_play_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return

        if canvas.is_playing or self.audio_player.is_playing or self.audio_player.is_compiling:
            self._stop_playback()
            return

        # Check if tab has notes; if empty, insert a demo riff so Play ALWAYS works!
        has_notes = False
        for m in canvas.tab_track.measures:
            for b in m.beats:
                if b.notes:
                    has_notes = True
                    break
            if has_notes:
                break

        if not has_notes:
            # Auto-insert a 4-note sample riff
            m0 = canvas.tab_track.measures[0]
            m0.beats[0].set_note(0, 0)
            m0.beats[2].set_note(0, 3)
            m0.beats[4].set_note(0, 5)
            m0.beats[6].set_note(0, 3)
            canvas.queue_draw()
            self.show_toast("Added sample riff & starting playback...")

        if hasattr(self, 'btn_play'):
            self.btn_play.set_label(" ⏳ Loading")
            self.btn_play.remove_css_class("suggested-action")
            self.btn_play.add_css_class("destructive-action")

        self.show_toast("Synthesizing & Playing Tab Sweep...")

        # Background audio synthesis thread
        threading.Thread(target=self.audio_player.compile_and_play, args=(canvas.tab_track,), daemon=True).start()

    def _update_playhead_animation(self):
        canvas = self._get_active_canvas()
        if not canvas or not canvas.is_playing:
            return False

        quarter_sec = (60.0 / float(max(1, canvas.tab_track.bpm)))
        sys_elapsed = max(0.0, time.monotonic() - self.play_start_time)
        elapsed = sys_elapsed

        if hasattr(self, 'audio_player') and self.audio_player.player:
            try:
                success, pos_nanos = self.audio_player.player.query_position(Gst.Format.TIME)
                if success and pos_nanos >= 0:
                    gst_elapsed = pos_nanos / 1e9
                    if abs(gst_elapsed - sys_elapsed) > 0.15:
                        self.play_start_time = time.monotonic() - gst_elapsed
                        elapsed = gst_elapsed
            except Exception:
                pass

        total_track_duration = 0.0
        for m in canvas.tab_track.measures:
            m_sum_dur = sum(b.duration for b in m.beats)
            m_scale = 4.0 / float(max(0.001, m_sum_dur)) if m_sum_dur > 0 else 1.0
            total_track_duration += sum((b.duration * m_scale) * quarter_sec for b in m.beats)

        accum_time = 0.0
        m_idx = 0
        b_prog = 0.0
        curr_beat_num = 1

        for idx, m in enumerate(canvas.tab_track.measures):
            m_sum_dur = sum(b.duration for b in m.beats)
            m_scale = 4.0 / float(max(0.001, m_sum_dur)) if m_sum_dur > 0 else 1.0
            m_duration = sum((b.duration * m_scale) * quarter_sec for b in m.beats)
            if accum_time + m_duration > elapsed:
                m_idx = idx
                rem_time = elapsed - accum_time

                beat_accum = 0.0
                for b_idx, beat in enumerate(m.beats):
                    b_dur_sec = (beat.duration * m_scale) * quarter_sec
                    if beat_accum + b_dur_sec > rem_time:
                        b_rem = rem_time - beat_accum
                        b_prog = b_idx + (b_rem / b_dur_sec if b_dur_sec > 0 else 0.0)
                        curr_beat_num = b_idx + 1
                        break
                    beat_accum += b_dur_sec
                else:
                    b_prog = float(len(m.beats))
                    curr_beat_num = len(m.beats)
                break
            accum_time += m_duration
        else:
            if canvas.tab_track.measures:
                m_idx = len(canvas.tab_track.measures) - 1
                b_prog = float(len(canvas.tab_track.measures[-1].beats))
                curr_beat_num = len(canvas.tab_track.measures[-1].beats)
            if elapsed >= total_track_duration + 2.0:
                self._stop_playback()
                return False

        # Live location readout in header/status bar
        if hasattr(self, 'lbl_position'):
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            ms = int((elapsed % 1) * 10)
            tot_secs = int(total_track_duration)
            t_mins = tot_secs // 60
            t_secs = tot_secs % 60
            total_m = len(canvas.tab_track.measures)
            self.lbl_position.remove_css_class("caption-heading")
            self.lbl_position.add_css_class("playback-badge")
            self.lbl_position.set_label(
                f"▶ PLAYING  |  Bar {m_idx + 1} / {total_m}  |  Beat {curr_beat_num}  |  {mins:02d}:{secs:02d}.{ms} / {t_mins:02d}:{t_secs:02d}"
            )

        last_m = getattr(self, "last_anim_m", -1)
        last_b = getattr(self, "last_anim_b", -1.0)
        if m_idx != last_m or abs(b_prog - last_b) >= 0.02:
            self.last_anim_m = m_idx
            self.last_anim_b = b_prog
            canvas.set_playhead_position(m_idx, b_prog)
        return True

    def _stop_playback(self):
        self.audio_player.stop()
        canvas = self._get_active_canvas()
        if canvas:
            canvas.is_playing = False
            canvas.set_playhead_position(0, 0.0)

        if hasattr(self, 'btn_play'):
            self.btn_play.set_label(" ▶ Play")
            self.btn_play.remove_css_class("destructive-action")
            self.btn_play.add_css_class("suggested-action")

        if self.play_anim_timer_id > 0:
            GLib.source_remove(self.play_anim_timer_id)
            self.play_anim_timer_id = 0

        if hasattr(self, 'lbl_position'):
            self.lbl_position.remove_css_class("playback-badge")
            self.lbl_position.add_css_class("caption-heading")

        if canvas:
            self._on_cursor_changed(None, canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string)

    def _on_toggle_theme(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return

        canvas.dark_mode = not canvas.dark_mode
        canvas.queue_draw()
        if canvas.dark_mode:
            self.btn_theme_toggle.set_label("📄 Sheet Paper")
            self.show_toast("Switched to Songsterr Dark Theme")
        else:
            self.btn_theme_toggle.set_label("🌙 Dark Studio")
            self.show_toast("Switched to Paper Sheet Theme")

    def _on_toggle_neck(self, button):
        visible = button.get_active()
        self.neck_panel.set_visible(visible)

    def _on_cursor_changed(self, widget, measure_idx, beat_idx, string_idx):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        total_beats = len(canvas.tab_track.measures[measure_idx].beats) if measure_idx < len(canvas.tab_track.measures) else 8
        if hasattr(self, 'lbl_position'):
            self.lbl_position.set_label(f"📍 Bar {measure_idx + 1} | Beat {beat_idx + 1} of {total_beats}")

    def _on_direct_fret_changed(self, spin):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        fret_val = int(spin.get_value())
        tech = self.visual_picker.harmonic_mode
        canvas.set_fret_at_cursor(fret_val, tech)

    def _on_capo_changed(self, spin):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        val = int(spin.get_value())
        canvas.tab_track.capo = val
        if hasattr(self, 'visual_picker'):
            self.visual_picker.set_capo(val)
        canvas.queue_draw()
        if val > 0:
            self.show_toast(f"🪗 Capo set to Fret {val}")
        else:
            self.show_toast("Capo Removed (Fret 0)")

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
            if "b" in note.technique and getattr(note, "bend_val", None) == bend_val and getattr(note, "bend_type", None) == bend_type:
                note.technique = note.technique.replace("b", "").replace("  ", " ").strip()
                note.bend_val = None
                note.bend_type = None
                self.show_toast("Removed Note Bend")
            else:
                if "b" not in note.technique:
                    note.technique = (note.technique + " b").strip()
                note.bend_val = bend_val
                note.bend_type = bend_type

                if bend_val == 0.25: b_str = "1/4"
                elif bend_val == 0.5: b_str = "1/2"
                elif bend_val == 0.75: b_str = "3/4"
                elif bend_val == 1.0: b_str = "Full"
                elif bend_val == 1.5: b_str = "1 ½"
                elif bend_val == 2.0: b_str = "2"
                else: b_str = f"{bend_val:g}"

                if bend_type == "bend_release": b_str += " Release"
                elif bend_type == "prebend": b_str = "Pre-Bend " + b_str
                elif bend_type == "prebend_release": b_str = "Pre-Bend " + b_str + " Release"

                self.show_toast(f"Applied {b_str} Bend to Fret {note.fret}")
            canvas.queue_draw()
        else:
            self.active_technique = "b"
            self.active_bend_val = bend_val
            self.active_bend_type = bend_type
            self.show_toast(f"Active Bend set to {bend_val} step ({bend_type})")

    def _commit_direct_fret_entry(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        fret_val = int(self.spin_direct_fret.get_value())
        tech = self.visual_picker.harmonic_mode
        b_val = getattr(self, "active_bend_val", None) if getattr(self, "active_technique", "") == "b" else None
        b_type = getattr(self, "active_bend_type", None) if getattr(self, "active_technique", "") == "b" else None
        canvas.set_fret_at_cursor(fret_val, tech, bend_val=b_val, bend_type=b_type)

        s_idx = canvas.cursor_string
        play_pluck_sound(s_idx, fret_val, tech, canvas.tab_track.tuning_name, canvas.tab_track.capo)

        canvas.move_cursor(d_beat=1)
        self.show_toast(f"Set Fret {fret_val}")

    def _on_beats_per_bar_changed(self, spin):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        val = int(spin.get_value())
        canvas.tab_track.set_beats_per_bar_all(val)
        canvas.queue_draw()
        self._on_cursor_changed(None, canvas.cursor_measure, canvas.cursor_beat, canvas.cursor_string)
        self._on_page_changed(None, canvas.current_page + 1, canvas.get_total_pages())
        self.show_toast(f"Set Beats/Bar to {val}")
        self.audio_player.invalidate_cache()
        if canvas.is_playing or self.audio_player.is_playing:
            self._stop_playback()
            self._on_play_clicked(None)
        else:
            self.audio_player.request_precompile(canvas.tab_track)

    def _on_vu_level_changed(self, recorder, level_fraction):
        GLib.idle_add(self.progress_vu.set_fraction, level_fraction)

    def _set_harmonic_mode(self, mode_str, button):
        if not button.get_active():
            return
        
        if mode_str == "":
            self.btn_mode_nh.set_active(False)
            self.btn_mode_ah.set_active(False)
        elif mode_str == "NH":
            self.btn_mode_normal.set_active(False)
            self.btn_mode_ah.set_active(False)
        elif mode_str == "AH":
            self.btn_mode_normal.set_active(False)
            self.btn_mode_nh.set_active(False)

        self.visual_picker.set_harmonic_mode(mode_str)

        if mode_str == "NH":
            self.show_toast("Natural Harmonics <NH>")
        elif mode_str == "AH":
            self.show_toast("Pinch Harmonics [AH]")
        else:
            self.show_toast("Normal Frets Mode")

    def _insert_dead_note(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        canvas.set_fret_at_cursor(0, "x")

        s_idx = canvas.cursor_string
        play_pluck_sound(s_idx, 0, "x", canvas.tab_track.tuning_name, canvas.tab_track.capo)

        canvas.move_cursor(d_beat=1)
        self.show_toast("Added Dead Note (X)")

    def _on_visual_fret_clicked(self, picker, string_idx, fret, technique_mode):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        string_name = canvas.tab_track.get_string_names()[string_idx]
        play_pluck_sound(string_idx, fret, technique_mode, canvas.tab_track.tuning_name, canvas.tab_track.capo)
        self.show_toast(f"🎸 Fret {fret} Sound Preview on String {string_name}")

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
            cur = note.technique or ""

            if cur == tech_code:
                note.technique = ""
                self.show_toast(f"Removed '{tech_code}'")
            elif tech_code in cur:
                new_t = cur.replace(tech_code, "").replace("  ", " ").strip()
                note.technique = new_t
                self.show_toast(f"Removed '{tech_code}'")
            else:
                if cur and cur != "x":
                    if tech_code in ("/", "\\", "~", "b") and cur in ("t", "PM", "h", "p", "T"):
                        note.technique = f"{cur} {tech_code}"
                    elif cur in ("/", "\\", "~", "b") and tech_code in ("t", "PM", "h", "p", "T"):
                        note.technique = f"{tech_code} {cur}"
                    else:
                        note.technique = f"{cur} {tech_code}"
                else:
                    note.technique = tech_code
                self.show_toast(f"Applied '{note.technique}' to Fret {note.fret}")
            canvas.queue_draw()
        else:
            if getattr(self, "active_technique", "") == tech_code:
                self.active_technique = ""
                self.show_toast("Cleared Active Technique Mode")
            else:
                self.active_technique = tech_code
                self.show_toast(f"Active Technique set to '{tech_code}'")

    def _apply_grace_note(self, fret=None):
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
            if note.grace_fret is not None:
                note.grace_fret = None
                self.show_toast("Removed Decorative Grace Note")
            else:
                target_fret = fret if fret is not None else int(self.spin_grace_fret.get_value())
                note.grace_fret = target_fret
                self.show_toast(f"Added Decorative Grace Note ({target_fret})")
            canvas.queue_draw()
        else:
            target_fret = fret if fret is not None else int(self.spin_grace_fret.get_value())
            self.show_toast(f"Set Grace Note to ({target_fret}) - Add a note to apply")

    def _clear_current_beat(self):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        m_idx = canvas.cursor_measure
        b_idx = canvas.cursor_beat
        m = canvas.tab_track.measures[m_idx]
        b = m.beats[b_idx]
        b.notes.clear()
        canvas.queue_draw()
        self.show_toast("Cleared Beat")

    def _setup_shortcuts(self):
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    # --- KEYBOARD SHORTCUTS ---

    def _on_key_pressed(self, controller, keyval, keycode, state):
        canvas = self._get_active_canvas()
        if not canvas:
            return False

        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if is_ctrl:
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                canvas.set_zoom(canvas.zoom_level + 0.15)
                return True
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                canvas.set_zoom(canvas.zoom_level - 0.15)
                return True
            elif keyval == Gdk.KEY_0:
                canvas.set_zoom(1.0)
                return True
            elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self._on_remove_measure(None)
                return True
            elif keyval == Gdk.KEY_Left:
                canvas.prev_page()
                return True
            elif keyval == Gdk.KEY_Right:
                canvas.next_page()
                return True

        if keyval == Gdk.KEY_Page_Up:
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
            self._commit_typed_fret_immediately()
            canvas.move_cursor(d_beat=1)
            return True
        elif keyval == Gdk.KEY_space:
            self._commit_typed_fret_immediately()
            if canvas.is_playing or self.audio_player.is_playing or self.audio_player.is_compiling:
                self._stop_playback()
            else:
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
            if is_ctrl:
                self._trigger_auto_save()
            else:
                self._apply_technique("/")
            return True
        elif keyval == Gdk.KEY_backslash:
            self._apply_technique("\\")
            return True
        elif keyval in (Gdk.KEY_asciitilde, Gdk.KEY_grave):
            self._apply_technique("~")
            return True
        elif keyval in (Gdk.KEY_t, Gdk.KEY_T):
            if is_ctrl:
                self.add_new_song_tab()
            else:
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
        val = int(new_buf)

        if val > 22:
            self._commit_typed_fret_immediately()
            self.typed_fret_buffer = digit
            val = int(digit)

        self.typed_fret_buffer = new_buf
        tech = getattr(self, "active_technique", "") or self.visual_picker.harmonic_mode
        b_val = getattr(self, "active_bend_val", None) if tech == "b" else None
        b_type = getattr(self, "active_bend_type", None) if tech == "b" else None

        canvas.set_fret_at_cursor(val, tech, bend_val=b_val, bend_type=b_type)
        self.spin_direct_fret.set_value(val)

        s_idx = canvas.cursor_string
        threading.Thread(target=play_pluck_sound, args=(s_idx, val, tech, canvas.tab_track.tuning_name, canvas.tab_track.capo), daemon=True).start()

        self.commit_timer_id = GLib.timeout_add(550, self._auto_commit_typed_fret_timeout)

    def _auto_commit_typed_fret_timeout(self):
        canvas = self._get_active_canvas()
        if canvas:
            canvas.move_cursor(d_beat=1)
        self.commit_timer_id = 0
        self.typed_fret_buffer = ""
        return False

    def _commit_typed_fret_immediately(self):
        if self.commit_timer_id > 0:
            GLib.source_remove(self.commit_timer_id)
            self.commit_timer_id = 0
        self.typed_fret_buffer = ""
        canvas = self._get_active_canvas()
        if canvas and canvas.tab_track:
            self.audio_player.invalidate_cache()
            self.audio_player.request_precompile(canvas.tab_track)

    # --- Actions ---

    def _on_add_measure(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        canvas.tab_track.add_measure()
        self._on_page_changed(None, canvas.current_page + 1, canvas.get_total_pages())
        canvas.queue_draw()
        self.audio_player.invalidate_cache()
        self.audio_player.request_precompile(canvas.tab_track)
        self.show_toast("Added new measure bar")

    def _on_remove_measure(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        track = canvas.tab_track
        if len(track.measures) > 1:
            curr_m = canvas.cursor_measure
            track.measures.pop(curr_m)
            canvas.cursor_measure = max(0, min(curr_m, len(track.measures) - 1))
            canvas.cursor_beat = 0
            canvas.queue_draw()
            self._on_page_changed(None, canvas.current_page + 1, canvas.get_total_pages())
            self.audio_player.invalidate_cache()
            self.audio_player.request_precompile(track)
            self.show_toast(f"Removed Measure Bar {curr_m + 1}")
        else:
            self.show_toast("Cannot remove the last remaining measure bar.")

    def _on_bpm_changed(self, spin):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        val = int(spin.get_value())
        if canvas.tab_track.bpm == val:
            return
        canvas.tab_track.bpm = val
        self.audio_player.invalidate_cache()
        if canvas.is_playing or self.audio_player.is_playing:
            self._stop_playback()
            self._on_play_clicked(None)
        else:
            self.audio_player.request_precompile(canvas.tab_track)

    def _on_speed_changed(self, combo, param):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        idx = combo.get_selected()
        if idx < len(self.speed_values):
            val = self.speed_values[idx]
            if getattr(canvas.tab_track, 'speed_multiplier', 1.0) == val:
                return
            canvas.tab_track.speed_multiplier = val
            self.audio_player.invalidate_cache()
            if canvas.is_playing or self.audio_player.is_playing:
                self._stop_playback()
                self._on_play_clicked(None)
            else:
                self.audio_player.request_precompile(canvas.tab_track)

    def _on_record_clicked(self, btn):
        if self.interface_recorder.is_recording:
            saved_file = self.interface_recorder.stop_recording()
            self.btn_record.set_label(" 🎙️ Record")
            self.btn_record.remove_css_class("suggested-action")
            self.btn_record.add_css_class("destructive-action")
            if saved_file:
                self.show_toast(f"Saved recording take to {os.path.basename(saved_file)}")
        else:
            rec_file = self.interface_recorder.start_recording()
            if rec_file:
                self.btn_record.set_label(" ⏹ Stop")
                self.btn_record.remove_css_class("destructive-action")
                self.btn_record.add_css_class("suggested-action")
                self.show_toast(f"Recording guitar take to {os.path.basename(rec_file)}")

    def _on_export_ascii_clicked(self, btn):
        canvas = self._get_active_canvas()
        if not canvas:
            return
        ascii_text = canvas.tab_track.export_ascii()
        export_path = os.path.expanduser("~/guitar_tab_export.txt")
        with open(export_path, "w") as f:
            f.write(ascii_text)
        self.show_toast(f"Exported ASCII Tab to {export_path}")

    def _on_save_clicked(self, btn):
        self._trigger_auto_save()
        canvas = self._get_active_canvas()
        if canvas:
            self.show_toast(f"Saved '{canvas.tab_track.title}' to ~/My_Guitar_Tabs/")

    def _on_open_clicked(self, btn):
        self._on_library_clicked(None)
