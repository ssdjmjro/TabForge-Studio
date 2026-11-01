#!/usr/bin/env python3
import json
import math
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GObject

from tab_engine import EPSILON, TabTrack, tech_tokens, note_value, flag_count, length_text

MAX_UNDO = 200
STAFF_ROOM = 126  # height of one staff with its rhythm marks, before zoom
PAGE_TURNER_ROOM = 44  # kept clear at the bottom so the page buttons never cover notes
ACCENT = (0.16, 0.62, 0.56)
SLOT_PAD = 16     # screen space before a bar's first note, before zoom
PDF_SLOT_PAD = 8  # the same in the PDF, in points
PDF_SLOT_MIN = 8.5  # narrowest printed slot, in points
PDF_MAX_STRETCH = 1.4  # how far a short last row may widen
DRAG_REACH = 5    # how close to a ribbon's end a drag can start, before zoom
SNAP_LENGTHS = (6.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.125)

# rhythm: "notation", "numbers" or "off"
DEFAULT_DISPLAY = {"rhythm": "notation", "ribbons": False, "beats": True}


def tech_badge(technique):
    """Text shown above a note. Dead notes and harmonics already show in the fret
    number; slides, bends, hammer-ons and pull-offs have their own drawings."""
    labels = {"PM": "P.M.", "t": "T", "T": "T", "~": "~~~"}
    return " ".join(labels[t] for t in technique.split() if t in labels)


def slur_label(technique):
    """The letter over a slur: h for a hammer-on, p for a pull-off, "" for neither."""
    tokens = tech_tokens(technique)
    return "h" if "h" in tokens else "p" if "p" in tokens else ""


class TabCanvasWidget(Gtk.DrawingArea):
    """
    Paged tablature canvas drawn with Cairo. The score lives in a cached layer so the
    playhead can repaint every frame without redrawing notes. Also owns per-song
    editing state: bar selection, undo history and save status.
    """
    __gsignals__ = {
        'cursor-changed': (GObject.SignalFlags.RUN_FIRST, None, (int, int, int)),
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        'page-changed': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        'edited': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'length-dragged': (GObject.SignalFlags.RUN_FIRST, None, (int, int, int, float)),
        'length-menu': (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }

    def __init__(self):
        super().__init__()
        self.tab_track = TabTrack()

        self.cursor_measure = 0
        self.cursor_beat = 0
        self.cursor_string = 0
        self.selection_anchor = None  # bar a Shift-selection started from
        self.dark_mode = True
        self.display = DEFAULT_DISPLAY  # the window shares its settings here
        self.zoom_level = 1.0
        self.current_page = 0

        self.is_playing = False
        self.playhead_measure = 0
        self.playhead_beat_progress = 0.0

        self.file_path = None
        self._layer = None
        self._drag = None  # (measure, slot, string, length so far) while resizing a note
        self._reset_history()

        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._draw)
        self.connect('resize', self._on_resize)

        self._setup_events()

    def _setup_events(self):
        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_button(0)
        click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click_gesture.connect("pressed", self._on_clicked)
        self.add_controller(click_gesture)

        drag_gesture = Gtk.GestureDrag.new()
        drag_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag_gesture.connect("drag-begin", self._on_drag_begin)
        drag_gesture.connect("drag-update", self._on_drag_update)
        drag_gesture.connect("drag-end", self._on_drag_end)
        self.add_controller(drag_gesture)

        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion_controller.connect("motion", self._on_motion)
        self.add_controller(motion_controller)

        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect("scroll", self._on_scroll)
        self.add_controller(scroll_controller)

    @staticmethod
    def _slot_xs(measure, mx, width, pad):
        """Where each slot's note sits, plus the bar's right edge. A slot's cell starts
        `pad` before its note, so a note reads as the beginning of its length."""
        return [mx + pad + e * (width - pad) for e in measure.slot_edges()]

    def _layout_metrics(self):
        zl = self.zoom_level
        return int(66 * zl), int(58 * zl), max(10, int(20 * zl)), max(125, int(166 * zl))

    def rows_per_page(self):
        _, margin_y, _, system_spacing = self._layout_metrics()
        spare = max(240, self.get_height()) - PAGE_TURNER_ROOM - margin_y - int(STAFF_ROOM * self.zoom_level)
        return max(1, 1 + spare // system_spacing)

    def _calculate_all_pages(self, canvas_width):
        zl = self.zoom_level
        margin_x, margin_y, string_spacing, system_spacing = self._layout_metrics()
        avail_w = max(260, canvas_width - margin_x - int(24 * zl))

        # Fit bars into rows, then stretch each row to the full width.
        rows, row, row_w = [], [], 0
        for m_idx, m in enumerate(self.tab_track.measures):
            edges = m.slot_edges()
            narrowest = min(b - a for a, b in zip(edges, edges[1:]))
            measure_w = max(int(150 * zl), int((SLOT_PAD + 25 / narrowest) * zl))
            if row and row_w + measure_w > avail_w:
                rows.append((row, row_w))
                row, row_w = [], 0
            row.append((m_idx, measure_w))
            row_w += measure_w
        if row:
            rows.append((row, row_w))

        per_page = self.rows_per_page()
        pages = []
        for r_idx, (row, row_w) in enumerate(rows):
            row_in_page = r_idx % per_page
            if row_in_page == 0:
                pages.append({})
            stretch = avail_w / float(row_w) if row_w > 0.6 * avail_w else 1.0
            x = float(margin_x)
            for m_idx, measure_w in row:
                w = measure_w * stretch
                pages[-1][m_idx] = (x, margin_y + row_in_page * system_spacing,
                                    w, len(self.tab_track.measures[m_idx].beats), row_in_page)
                x += w
        return pages or [{}], len(pages) or 1, margin_x, margin_y, string_spacing, system_spacing

    def _on_resize(self, area, width, height):
        self._layer = None
        self.follow_measure(self.playhead_measure if self.is_playing else self.cursor_measure)

    def follow_measure(self, measure_idx):
        """Shows the page containing a bar, and keeps the page counter honest."""
        pages = self._calculate_all_pages(max(400, self.get_width()))[0]
        target = next((i for i, p in enumerate(pages) if measure_idx in p), self.current_page)
        self.current_page = max(0, min(len(pages) - 1, target))
        self.emit('page-changed', self.current_page + 1, len(pages))
        self.queue_draw()

    def get_total_pages(self):
        w = max(400, self.get_width())
        _, total_pages, _, _, _, _ = self._calculate_all_pages(w)
        return total_pages

    def set_page(self, page_num):
        total_pages = self.get_total_pages()
        self.current_page = max(0, min(total_pages - 1, page_num))
        self.emit('page-changed', self.current_page + 1, total_pages)
        self.queue_draw()

    def next_page(self):
        self.set_page(self.current_page + 1)

    def prev_page(self):
        self.set_page(self.current_page - 1)

    def set_zoom(self, zoom):
        self.zoom_level = max(0.4, min(3.0, zoom))
        self.emit('zoom-changed', self.zoom_level)
        self.follow_measure(self.cursor_measure)

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0:
                self.set_zoom(self.zoom_level + 0.12)
            elif dy > 0:
                self.set_zoom(self.zoom_level - 0.12)
            return True
        # Accumulate so a touchpad swipe flips one page, not ten.
        self._scroll_accum = getattr(self, '_scroll_accum', 0.0) + dy
        if abs(self._scroll_accum) >= 1.0:
            self.set_page(self.current_page + (1 if self._scroll_accum > 0 else -1))
            self._scroll_accum = 0.0
        return True

    def set_tab_track(self, track):
        self.tab_track = track
        self.cursor_measure = 0
        self.cursor_beat = 0
        self.cursor_string = 0
        self.current_page = 0
        self.playhead_measure = 0
        self.playhead_beat_progress = 0.0
        self.selection_anchor = None
        self._reset_history()
        self.emit('page-changed', 1, self.get_total_pages())
        self.queue_draw()

    # --- Undo history & save state ---

    def _measures_json(self):
        return json.dumps([m.to_dict() for m in self.tab_track.measures], sort_keys=True)

    def _track_json(self):
        return json.dumps(self.tab_track.to_dict(), sort_keys=True)

    def _reset_history(self):
        self._history = [self._measures_json()]
        self._history_pos = 0
        self.mark_saved()

    def mark_saved(self):
        self._saved_json = self._track_json()

    def is_dirty(self):
        return self._track_json() != self._saved_json

    def commit_edit(self):
        """Records an undo step if the notes actually changed."""
        current = self._measures_json()
        if current != self._history[self._history_pos]:
            del self._history[self._history_pos + 1:]
            self._history.append(current)
            if len(self._history) > MAX_UNDO:
                self._history.pop(0)
            self._history_pos = len(self._history) - 1
            self.emit('edited')

    def _restore_history(self, delta):
        target = self._history_pos + delta
        if not 0 <= target < len(self._history):
            return False
        self._history_pos = target
        restored = TabTrack.from_dict({"measures": json.loads(self._history[target])})
        self.tab_track.measures = restored.measures
        self.cursor_measure = min(self.cursor_measure, len(self.tab_track.measures) - 1)
        self.cursor_beat = min(self.cursor_beat, len(self.tab_track.measures[self.cursor_measure].beats) - 1)
        self.selection_anchor = None
        self.follow_measure(self.cursor_measure)
        self.emit('cursor-changed', self.cursor_measure, self.cursor_beat, self.cursor_string)
        self.emit('edited')
        return True

    def undo(self):
        return self._restore_history(-1)

    def redo(self):
        return self._restore_history(1)

    # --- Bar selection ---

    def selected_bars(self):
        """(first, last) bars of the selection; just the cursor's bar if none."""
        a = self.cursor_measure if self.selection_anchor is None else self.selection_anchor
        return min(a, self.cursor_measure), max(a, self.cursor_measure)

    def extend_selection(self, d_measure):
        if self.selection_anchor is None:
            self.selection_anchor = self.cursor_measure
        self.cursor_measure = max(0, min(len(self.tab_track.measures) - 1, self.cursor_measure + d_measure))
        self.cursor_beat = 0
        self.follow_measure(self.cursor_measure)
        self.emit('cursor-changed', self.cursor_measure, self.cursor_beat, self.cursor_string)

    def clear_selection(self):
        self.selection_anchor = None
        self.queue_draw()

    def set_fret_at_cursor(self, fret, technique="", bend_val=None, bend_type=None):
        if 0 <= self.cursor_measure < len(self.tab_track.measures):
            m = self.tab_track.measures[self.cursor_measure]
            if 0 <= self.cursor_beat < len(m.beats):
                b = m.beats[self.cursor_beat]
                b.set_note(self.cursor_string, fret, technique, bend_val=bend_val, bend_type=bend_type)
                self.commit_edit()
                self.queue_draw()

    def move_cursor(self, d_measure=0, d_beat=0, d_string=0):
        total_measures = len(self.tab_track.measures)
        if total_measures == 0:
            return

        self.cursor_string = max(0, min(5, self.cursor_string + d_string))
        new_beat = self.cursor_beat + d_beat
        m = self.tab_track.measures[self.cursor_measure]

        if new_beat >= len(m.beats):
            if self.cursor_measure < total_measures - 1:
                self.cursor_measure += 1
                self.cursor_beat = 0
        elif new_beat < 0:
            if self.cursor_measure > 0:
                self.cursor_measure -= 1
                m_prev = self.tab_track.measures[self.cursor_measure]
                self.cursor_beat = len(m_prev.beats) - 1
        else:
            self.cursor_beat = new_beat

        self.selection_anchor = None
        self.follow_measure(self.cursor_measure)
        self.emit('cursor-changed', self.cursor_measure, self.cursor_beat, self.cursor_string)

    def set_playhead_position(self, measure_idx, beat_progress):
        if measure_idx != self.playhead_measure:
            self.playhead_measure = measure_idx
            self.follow_measure(measure_idx)
        self.playhead_beat_progress = beat_progress
        super().queue_draw()  # playhead only: the score layer stays cached

    def _on_motion(self, controller, raw_x, raw_y):
        width = max(400, self.get_width())
        x, y = raw_x, raw_y

        pages, _, _, _, string_spacing, _ = self._calculate_all_pages(width)
        active_page_dict = pages[self.current_page] if self.current_page < len(pages) else {}

        over_tab = False
        for m_idx, (mx, my, mw, num_beats, row_idx) in active_page_dict.items():
            if mx <= x <= mx + mw and (my - 25) <= y <= (my + 5 * string_spacing + 35):
                over_tab = True
                break

        if self._ribbon_end_at(x, y):
            self.set_cursor(Gdk.Cursor.new_from_name("ew-resize", None))
        elif over_tab:
            self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        else:
            self.set_cursor(None)

    def _placed_bar(self, m_idx):
        """(note xs, staff top, slot pad) for a bar on the current page, or None."""
        pages = self._calculate_all_pages(max(400, self.get_width()))[0]
        placed = pages[self.current_page].get(m_idx) if self.current_page < len(pages) else None
        if placed is None:
            return None
        mx, my, width = placed[:3]
        pad = SLOT_PAD * self.zoom_level
        return self._slot_xs(self.tab_track.measures[m_idx], mx, width, pad), my, pad

    def _ribbon_end_x(self, m_idx, b_idx, xs):
        last = b_idx == len(self.tab_track.measures[m_idx].beats) - 1
        return (xs[-1] if last else xs[b_idx + 1] - 8 * self.zoom_level) - 3 * self.zoom_level

    def _ribbon_end_at(self, x, y):
        """(measure, slot, string) of the note whose length ribbon ends at this point."""
        spacing = self._layout_metrics()[2]
        pages = self._calculate_all_pages(max(400, self.get_width()))[0]
        if self.is_playing or self.current_page >= len(pages):
            return None
        reach = DRAG_REACH * self.zoom_level
        for m_idx, placed in pages[self.current_page].items():
            mx, my, width = placed[:3]
            string = int(round((y - my) / float(spacing)))
            if not (0 <= string <= 5 and abs(y - my - string * spacing) <= reach and mx <= x <= mx + width + reach):
                continue
            xs, _, pad = self._placed_bar(m_idx)
            for b_idx, beat in enumerate(self.tab_track.measures[m_idx].beats):
                if string in beat.notes and abs(x - self._ribbon_end_x(m_idx, b_idx, xs)) <= reach:
                    return m_idx, b_idx, string
        return None

    def _on_drag_begin(self, gesture, x, y):
        hit = self._ribbon_end_at(x, y)
        if hit is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        m_idx, b_idx, string = hit
        self._drag = (m_idx, b_idx, string, self.tab_track.measures[m_idx].beat_quarters()[b_idx])
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_drag_update(self, gesture, dx, dy):
        if self._drag is None:
            return
        m_idx, b_idx, string, _ = self._drag
        measure = self.tab_track.measures[m_idx]
        xs, _, pad = self._placed_bar(m_idx)
        px_per_quarter = (xs[-1] - xs[0]) / measure.quarters()
        wanted = measure.beat_quarters()[b_idx] + dx / px_per_quarter
        room = measure.quarters() - sum(measure.beat_quarters()[:b_idx])
        options = [q for q in SNAP_LENGTHS if q <= room + EPSILON]
        self._drag = (m_idx, b_idx, string, min(options, key=lambda q: abs(q - wanted)))
        super().queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        if self._drag is None:
            return
        m_idx, b_idx, string, quarters = self._drag
        self._drag = None
        super().queue_draw()
        if abs(quarters - self.tab_track.measures[m_idx].beat_quarters()[b_idx]) > EPSILON:
            self.emit('length-dragged', m_idx, b_idx, string, quarters)

    def _on_clicked(self, gesture, n_press, raw_x, raw_y):
        self.grab_focus()

        width = max(400, self.get_width())
        x, y = raw_x, raw_y

        pages, _, _, _, string_spacing, _ = self._calculate_all_pages(width)
        active_page_dict = pages[self.current_page] if self.current_page < len(pages) else {}

        best_match = None
        min_dist = float('inf')

        for m_idx, (mx, my, mw, num_beats, row_idx) in active_page_dict.items():
            if (my - 30) <= y <= (my + 5 * string_spacing + 40):
                if (mx - 20) <= x <= (mx + mw + 20):
                    pad = SLOT_PAD * self.zoom_level
                    xs = self._slot_xs(self.tab_track.measures[m_idx], mx, mw, pad)

                    b_idx = 0
                    best_bx_dist = float('inf')
                    for b in range(num_beats):
                        center_bx = (xs[b] + xs[b + 1]) / 2.0 - pad
                        d_x = abs(x - center_bx)
                        if d_x < best_bx_dist:
                            best_bx_dist = d_x
                            b_idx = b

                    rel_y = y - my
                    s_idx = max(0, min(5, int(round(rel_y / float(string_spacing)))))

                    total_dist = math.hypot(best_bx_dist, abs(rel_y - s_idx * string_spacing))
                    if total_dist < min_dist:
                        min_dist = total_dist
                        best_match = (m_idx, b_idx, s_idx)

        if best_match:
            shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
            if shift and self.selection_anchor is None:
                self.selection_anchor = self.cursor_measure
            elif not shift:
                self.selection_anchor = None
            self.cursor_measure, self.cursor_beat, self.cursor_string = best_match
            self.emit('cursor-changed', self.cursor_measure, self.cursor_beat, self.cursor_string)
            self.queue_draw()
            if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
                self.emit('length-menu', raw_x, raw_y)

    def queue_draw(self):
        """Schedules a repaint of the score as well as the playhead."""
        self._layer = None
        super().queue_draw()

    def _draw(self, area, cr, width, height):
        scale = self.get_scale_factor()
        if self._layer is None:
            self._layer = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
            self._layer.set_device_scale(scale, scale)
            self.render_tab(cairo.Context(self._layer), width, height)
        cr.set_source_surface(self._layer, 0, 0)
        cr.paint()

        if self._drag:
            self._draw_drag_preview(cr)

        if self.is_playing:
            page = self._calculate_all_pages(width)
            placed = page[0][self.current_page].get(self.playhead_measure) if self.current_page < len(page[0]) else None
            if placed:
                mx, my, measure_w, num_beats, _ = placed
                zl = self.zoom_level
                xs = self._slot_xs(self.tab_track.measures[self.playhead_measure], mx, measure_w, SLOT_PAD * zl)
                slot = min(int(self.playhead_beat_progress), num_beats - 1)
                fraction = self.playhead_beat_progress - slot
                px = xs[slot] + fraction * (xs[slot + 1] - xs[slot])
                cr.set_source_rgb(*ACCENT)
                cr.set_line_width(max(2.5, 3.2 * zl))
                cr.move_to(px, my - int(10 * zl))
                cr.line_to(px, my + 5 * page[4] + int(10 * zl))
                cr.stroke()

    def _draw_drag_preview(self, cr):
        m_idx, b_idx, string, quarters = self._drag
        placed = self._placed_bar(m_idx)
        if placed is None:
            return
        xs, my, _ = placed
        zl = self.zoom_level
        measure = self.tab_track.measures[m_idx]
        sy = my + string * self._layout_metrics()[2]
        end = xs[b_idx] + quarters / measure.quarters() * (xs[-1] - xs[0])
        cr.set_source_rgb(*ACCENT)
        cr.rectangle(xs[b_idx], sy - 4 * zl, end - xs[b_idx], 8 * zl)
        cr.fill()
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(12, int(14 * zl)))
        cr.move_to(end + 5 * zl, sy - 6 * zl)
        cr.show_text(length_text(quarters))

    def render_tab(self, cr, width, height):
        surf_w = int(width)
        surf_h = int(height)
        zl = self.zoom_level

        if self.dark_mode:
            bg_r, bg_g, bg_b = 0.08, 0.09, 0.114
            line_r, line_g, line_b, line_a = 0.55, 0.6, 0.7, 0.45
            text_r, text_g, text_b = 0.88, 0.89, 0.93
            badge_bg_r, badge_bg_g, badge_bg_b = 0.08, 0.09, 0.114
            barline_r, barline_g, barline_b = 0.55, 0.6, 0.7
        else:
            bg_r, bg_g, bg_b = 0.97, 0.96, 0.94
            line_r, line_g, line_b, line_a = 0.2, 0.2, 0.25, 0.8
            text_r, text_g, text_b = 0.1, 0.1, 0.12
            badge_bg_r, badge_bg_g, badge_bg_b = 0.97, 0.96, 0.94
            barline_r, barline_g, barline_b = 0.2, 0.2, 0.25

        cr.set_source_rgb(bg_r, bg_g, bg_b)
        cr.rectangle(0, 0, surf_w, surf_h)
        cr.fill()

        pages, _, margin_x, margin_y, string_spacing, _ = self._calculate_all_pages(surf_w)
        active_page_dict = pages[self.current_page] if self.current_page < len(pages) else {}

        cr.set_source_rgb(text_r, text_g, text_b)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(10, int(13 * zl)))
        banner_x = margin_x + int(50 * zl)  # beside the first bar's label
        t_sigs = {m.time_sig for m in self.tab_track.measures}
        if len(t_sigs) == 1:
            sig = self.tab_track.measures[0].time_sig
            cr.move_to(banner_x, int(25 * zl))
            cr.show_text(f"{sig[0]}/{sig[1]}")
            banner_x += int(50 * zl)

        if self.tab_track.capo > 0:
            cr.set_source_rgb(0.95, 0.65, 0.15)
            cr.move_to(banner_x, int(25 * zl))
            cr.show_text(f"CAPO AT FRET {self.tab_track.capo}")

        ink = (text_r, text_g, text_b)
        paper = (bg_r, bg_g, bg_b)
        last_note = {}  # string -> (row, x) of the note before, for slurs
        for m_idx, (mx, my, measure_w, num_beats, row_idx) in active_page_dict.items():
            m = self.tab_track.measures[m_idx]

            sel_first, sel_last = self.selected_bars()
            if self.selection_anchor is not None and sel_first <= m_idx <= sel_last:
                cr.set_source_rgba(*ACCENT, 0.16)
                cr.rectangle(mx, my - int(30 * zl), measure_w, 5 * string_spacing + int(46 * zl))
                cr.fill()

            cr.set_source_rgb(0.6, 0.65, 0.75)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(max(9, int(12 * zl)))
            cr.move_to(mx + int(4 * zl), my - int(33 * zl))
            cr.show_text(f"m.{m_idx + 1}{self._bar_sig_label(m_idx)}")

            if mx == margin_x:
                string_names = self.tab_track.get_string_names()
                cr.set_source_rgb(text_r, text_g, text_b)
                cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(max(11, int(15 * zl)))
                for s in range(6):
                    sy = my + s * string_spacing
                    cr.move_to(mx - int(58 * zl), sy + int(5 * zl))
                    cr.show_text(f"{string_names[s]:>2}")

                cr.set_font_size(max(10, int(13 * zl)))
                cr.move_to(mx - int(32 * zl), my + string_spacing * 1.2)
                cr.show_text("T"); cr.move_to(mx - int(32 * zl), my + string_spacing * 2.8); cr.show_text("A"); cr.move_to(mx - int(32 * zl), my + string_spacing * 4.4); cr.show_text("B")

            cr.set_source_rgba(line_r, line_g, line_b, line_a)
            cr.set_line_width(max(1.0, 1.4 * zl))
            for s in range(6):
                sy = my + s * string_spacing
                cr.move_to(mx, sy); cr.line_to(mx + measure_w, sy); cr.stroke()

            cr.set_source_rgb(barline_r, barline_g, barline_b)
            cr.set_line_width(max(1.4, 2.0 * zl))
            cr.move_to(mx, my); cr.line_to(mx, my + 5 * string_spacing); cr.stroke()
            cr.move_to(mx + measure_w, my); cr.line_to(mx + measure_w, my + 5 * string_spacing); cr.stroke()

            pad = SLOT_PAD * zl
            xs = self._slot_xs(m, mx, measure_w, pad)
            ruler_y = my - int(20 * zl)

            # Beat numbers across the top, with a tick for each slot between them.
            if self.display["beats"]:
                beat_count, beat_unit = m.click_grid()
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(max(11, int(13 * zl)))
                beat_xs = []
                for n in range(beat_count):
                    beat_x = xs[0] + n * beat_unit / m.quarters() * (xs[-1] - xs[0])
                    beat_xs.append(beat_x)
                    ext = cr.text_extents(str(n + 1))
                    cr.set_source_rgba(text_r, text_g, text_b, 1.0 if n == 0 else 0.7)
                    cr.move_to(beat_x - ext.width / 2.0, ruler_y + ext.height / 2.0)
                    cr.show_text(str(n + 1))

                cr.set_source_rgba(text_r, text_g, text_b, 0.35)
                for slot_x in xs[:-1]:
                    if min(abs(slot_x - bx) for bx in beat_xs) > 2:
                        cr.new_sub_path()
                        cr.arc(slot_x, ruler_y, max(1.2, 1.6 * zl), 0, 2 * math.pi)
                        cr.fill()

            for b_idx, beat in enumerate(m.beats):
                bx = xs[b_idx]
                slot_end = xs[b_idx + 1] - 8 * zl if b_idx + 1 < len(m.beats) else mx + measure_w

                for s_idx, note in beat.notes.items():
                    sy = my + s_idx * string_spacing

                    # A ribbon along the string shows how long the note lasts.
                    if self.display["ribbons"] and "x" not in tech_tokens(note.technique):
                        cr.set_source_rgba(*ACCENT, 0.65)
                        cr.rectangle(bx, sy - 3 * zl, max(0.0, slot_end - 3 * zl - bx), 6 * zl)
                        cr.fill()

                    tokens = tech_tokens(note.technique)
                    if "x" in tokens:
                        text_str = "X"
                    elif "NH" in tokens:
                        text_str = f"<{note.fret}>"
                    elif "AH" in tokens:
                        text_str = f"[{note.fret}]"
                    else:
                        text_str = str(note.fret)

                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                    cr.set_font_size(max(11, int(15 * zl)))
                    ext = cr.text_extents(text_str)

                    # Break the string line behind the number, as on printed tabs.
                    cr.set_source_rgb(bg_r, bg_g, bg_b)
                    cr.rectangle(bx - ext.width / 2.0 - 3 * zl, sy - ext.height / 2.0 - 2 * zl,
                                 ext.width + 6 * zl, ext.height + 4 * zl)
                    cr.fill()

                    cr.set_source_rgb(text_r, text_g, text_b)
                    cr.move_to(bx - ext.width / 2.0, sy + ext.height / 2.0); cr.show_text(text_str)

                    if note.technique:
                        if "/" in note.technique or "s" in note.technique:
                            cr.save()
                            cr.set_source_rgb(text_r, text_g, text_b)
                            cr.set_line_width(1.2 * zl)
                            cr.move_to(bx - 12 * zl, sy + 3.5 * zl)
                            cr.line_to(bx - 4 * zl, sy - 3.5 * zl)
                            cr.stroke()
                            cr.restore()
                        elif "\\" in note.technique or "S" in note.technique:
                            cr.save()
                            cr.set_source_rgb(text_r, text_g, text_b)
                            cr.set_line_width(1.2 * zl)
                            cr.move_to(bx - 12 * zl, sy - 3.5 * zl)
                            cr.line_to(bx - 4 * zl, sy + 3.5 * zl)
                            cr.stroke()
                            cr.restore()

                    if "b" in note.technique or note.bend_val is not None:
                        b_val = 1.0 if note.bend_val is None else note.bend_val
                        b_type = note.bend_type or "bend"

                        if b_val == 0.25: bend_lbl = "1/4"
                        elif b_val == 0.5: bend_lbl = "1/2"
                        elif b_val == 0.75: bend_lbl = "3/4"
                        elif b_val == 1.0: bend_lbl = "Full"
                        elif b_val == 1.5: bend_lbl = "1 ½"
                        elif b_val == 2.0: bend_lbl = "2"
                        else: bend_lbl = f"{b_val:g}"

                        if b_type == "bend_release":
                            bend_lbl += " r"
                        elif b_type == "prebend":
                            bend_lbl = "pb " + bend_lbl
                        elif b_type == "prebend_release":
                            bend_lbl = "pb " + bend_lbl + " r"

                        bend_h = 24.0 * zl
                        curve_top_y = sy - bend_h

                        cr.save()
                        cr.set_source_rgb(1.0, 0.72, 0.20)
                        cr.set_line_width(1.8 * zl)

                        if b_type in ("prebend", "prebend_release"):
                            cr.set_dash([3 * zl, 2 * zl])
                            cr.move_to(bx, sy - 11 * zl)
                            cr.line_to(bx, curve_top_y)
                            cr.stroke()
                            cr.set_dash([])
                        else:
                            cr.move_to(bx, sy - 11 * zl)
                            cr.curve_to(bx + 4 * zl, sy - 16 * zl, bx + 8 * zl, curve_top_y + 4 * zl, bx + 12 * zl, curve_top_y)
                            cr.stroke()

                        arrow_x = bx + 12 * zl if b_type not in ("prebend", "prebend_release") else bx
                        arrow_y = curve_top_y
                        cr.move_to(arrow_x - 3.5 * zl, arrow_y + 5 * zl)
                        cr.line_to(arrow_x + 3.5 * zl, arrow_y + 5 * zl)
                        cr.line_to(arrow_x, arrow_y)
                        cr.close_path()
                        cr.fill()

                        if b_type in ("bend_release", "prebend_release"):
                            cr.move_to(arrow_x, arrow_y)
                            cr.curve_to(arrow_x + 4 * zl, arrow_y + 4 * zl, arrow_x + 8 * zl, sy - 16 * zl, arrow_x + 10 * zl, sy - 11 * zl)
                            cr.stroke()

                        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                        cr.set_font_size(max(9, int(11 * zl)))
                        lbl_ext = cr.text_extents(bend_lbl)
                        lbl_x = arrow_x - lbl_ext.width / 2.0
                        lbl_y = arrow_y - 3 * zl

                        cr.set_source_rgba(badge_bg_r, badge_bg_g, badge_bg_b, 0.95)
                        cr.rectangle(lbl_x - 2 * zl, lbl_y - lbl_ext.height, lbl_ext.width + 4 * zl, lbl_ext.height + 2 * zl)
                        cr.fill()

                        cr.set_source_rgb(1.0, 0.72, 0.20)
                        cr.move_to(lbl_x, lbl_y)
                        cr.show_text(bend_lbl)
                        cr.restore()

                    if note.technique:
                        top_tech = tech_badge(note.technique)
                        if top_tech:
                            cr.save()
                            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                            cr.set_font_size(max(11, int(14 * zl)))
                            t_ext = cr.text_extents(top_tech)
                            ty = my - int(4 * zl)  # above the staff, as on printed tabs
                            tx = bx - t_ext.width / 2.0

                            cr.set_source_rgb(*ink)
                            cr.move_to(tx, ty)
                            cr.show_text(top_tech)
                            cr.restore()

                    label = slur_label(note.technique)
                    if label:
                        row, prev_x = last_note.get(s_idx, (None, 0.0))
                        start = prev_x + 7 * zl if row == row_idx else bx - 24 * zl
                        self._draw_note_slur(cr, label, start, bx - 7 * zl, sy, (9 * zl, 10 * zl, 1.3 * zl, max(10, int(12 * zl))), ink, paper)

                    if note.grace_fret is not None:
                        self._draw_grace(cr, note.grace_fret, bx - 17 * zl, bx - 6 * zl, sy,
                                         (max(9, int(11 * zl)), 8 * zl, 6 * zl, 1.1 * zl), ink, paper)

                    last_note[s_idx] = (row_idx, bx)

            cr.set_source_rgb(text_r, text_g, text_b)
            self._draw_length_marks(cr, m, xs, my + 5 * string_spacing + 6 * zl, 20 * zl, max(1.4, 1.8 * zl),
                                    8 * zl, max(11, int(12 * zl)))

            if m_idx == self.cursor_measure:
                cx = xs[self.cursor_beat] - pad
                beat_w = xs[self.cursor_beat + 1] - xs[self.cursor_beat]
                if self.cursor_beat == len(m.beats) - 1:
                    beat_w += pad
                cy = my + self.cursor_string * string_spacing
                cr.set_source_rgba(*ACCENT, 0.28)
                cr.rectangle(cx, cy - int(9 * zl), beat_w, int(18 * zl)); cr.fill()
                cr.set_source_rgb(*ACCENT)
                cr.set_line_width(max(1.4, 1.8 * zl))
                cr.rectangle(cx, cy - int(9 * zl), beat_w, int(18 * zl)); cr.stroke()

    def _bar_sig_label(self, m_idx):
        """Time signature text for a bar that changes it."""
        sig = self.tab_track.measures[m_idx].time_sig
        changed = m_idx > 0 and self.tab_track.measures[m_idx - 1].time_sig != sig
        return f"   {sig[0]}/{sig[1]}" if changed else ""

    @staticmethod
    def _draw_slur(cr, x0, x1, y, rise, line_width):
        """A curve over the string joining two notes."""
        span = x1 - x0
        cr.set_line_width(line_width)
        cr.move_to(x0, y)
        cr.curve_to(x0 + span * 0.25, y - rise, x0 + span * 0.75, y - rise, x1, y)
        cr.stroke()

    def _draw_note_slur(self, cr, label, x0, x1, sy, sizes, ink, paper):
        """Hammer-on or pull-off: a slur from the previous note, with its letter on top.
        `sizes` is (gap above the string, rise, line width, font size)."""
        gap, rise, line_width, font_size = sizes
        cr.set_source_rgb(*ink)
        self._draw_slur(cr, x0, x1, sy - gap, rise, line_width)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(font_size)
        ext = cr.text_extents(label)
        tx = (x0 + x1 - ext.width) / 2.0
        ty = sy - gap - rise * 0.75 - font_size * 0.2
        cr.set_source_rgb(*paper)
        cr.rectangle(tx - 1, ty - ext.height - 1, ext.width + 2, ext.height + 2)
        cr.fill()
        cr.set_source_rgb(*ink)
        cr.move_to(tx, ty)
        cr.show_text(label)

    def _draw_grace(self, cr, fret, x, bx, sy, sizes, ink, paper):
        """A small fret number just before the note, tied to it by a little slur.
        `sizes` is (font size, slur gap, rise, line width)."""
        font_size, gap, rise, line_width = sizes
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(font_size)
        text = str(fret)
        ext = cr.text_extents(text)
        cr.set_source_rgb(*paper)
        cr.rectangle(x - ext.width / 2.0 - 1, sy - ext.height / 2.0 - 1, ext.width + 2, ext.height + 2)
        cr.fill()
        cr.set_source_rgb(*ink)
        cr.move_to(x - ext.width / 2.0, sy + ext.height / 2.0)
        cr.show_text(text)
        self._draw_slur(cr, x, bx, sy - gap, rise, line_width)

    def _draw_length_marks(self, cr, measure, xs, top, length, line_width, beam_gap, font_size):
        """Under a staff: rhythm notation, plain numbers, or nothing, as the user chose."""
        style = self.display["rhythm"]
        if style == "notation":
            self._draw_rhythm(cr, measure, xs, top, length, line_width, beam_gap)
        elif style == "numbers":
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(font_size)
            for i, quarters in enumerate(measure.beat_quarters()):
                if measure.beats[i].notes:
                    text = length_text(quarters)
                    ext = cr.text_extents(text)
                    cr.move_to(xs[i] - ext.width / 2.0, top + ext.height + length * 0.1)
                    cr.show_text(text)

    def _draw_rhythm(self, cr, measure, xs, top, length, line_width, beam_gap):
        """Rhythm notation under a staff, using the caller's colour: stems only for a
        quarter, a short stem for a half, a ring for a whole, and flags or beams below
        that. Notes sharing a beat are beamed together."""
        lengths = measure.beat_quarters()
        beat_unit = measure.click_grid()[1]
        bottom = top + length
        cr.set_line_width(line_width)

        beams = []  # runs of (slot, flags) joined by a beam
        start = 0.0
        last_beat = None
        for i, quarters in enumerate(lengths):
            has_notes = bool(measure.beats[i].notes)
            flags = flag_count(quarters)
            if has_notes:
                value = note_value(quarters)
                den = value[0] if value else 4.0 / quarters
                if den <= 1:
                    cr.new_sub_path()
                    cr.arc(xs[i], top + length * 0.3, length * 0.2, 0, 2 * math.pi)
                    cr.stroke()
                else:
                    cr.move_to(xs[i], top)
                    cr.line_to(xs[i], top + length * 0.5 if den <= 2 else bottom)
                    cr.stroke()
                if value and value[1]:
                    cr.new_sub_path()
                    cr.arc(xs[i] + beam_gap * 0.7, bottom - length * 0.3, line_width * 1.7, 0, 2 * math.pi)
                    cr.fill()

            beat = int((start + EPSILON) / beat_unit)
            if has_notes and flags:
                if beams and last_beat == beat and beams[-1][-1][0] == i - 1:
                    beams[-1].append((i, flags))
                else:
                    beams.append([(i, flags)])
                last_beat = beat
            start += quarters

        for run in beams:
            self._draw_beam(cr, run, xs, bottom, beam_gap, line_width)

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(beam_gap * 1.5)
        for i in range(1, len(lengths) - 1):
            value = note_value(lengths[i])
            neighbours_match = all(abs(lengths[j] - lengths[i]) < EPSILON for j in (i - 1, i + 1))
            in_middle = value and value[2] and neighbours_match and self._triplet_position(lengths, i) == 1
            if in_middle and any(measure.beats[j].notes for j in (i - 1, i, i + 1)):
                ext = cr.text_extents("3")
                cr.move_to(xs[i] - ext.width / 2.0, bottom + ext.height + line_width * 2)
                cr.show_text("3")

    @staticmethod
    def _triplet_position(lengths, i):
        """Where a slot falls in its group of three, counted from the start of the run."""
        run_start = i
        while run_start > 0 and abs(lengths[run_start - 1] - lengths[i]) < EPSILON:
            run_start -= 1
        return (i - run_start) % 3

    @staticmethod
    def _draw_beam(cr, run, xs, bottom, beam_gap, line_width):
        """One note gets flags. In a group, each level joins neighbours that share it,
        and a note with an extra level alone gets a short stub towards its partner."""
        thickness = line_width * 2.0
        stub = beam_gap * 0.9
        cr.set_line_width(thickness)
        for level in range(max(flags for _, flags in run)):
            y = bottom - thickness / 2.0 - level * beam_gap * 0.6
            for pos, (slot, flags) in enumerate(run):
                if flags <= level:
                    continue
                joins_next = pos + 1 < len(run) and run[pos + 1][1] > level
                joins_prev = pos > 0 and run[pos - 1][1] > level
                if joins_next:
                    cr.move_to(xs[slot] - line_width / 2.0, y)
                    cr.line_to(xs[run[pos + 1][0]] + line_width / 2.0, y)
                    cr.stroke()
                elif not joins_prev:
                    direction = -1 if pos == len(run) - 1 and len(run) > 1 else 1
                    cr.move_to(xs[slot], y)
                    cr.line_to(xs[slot] + direction * stub, y)
                    cr.stroke()
        cr.set_line_width(line_width)

    # --- PDF export ---

    def export_pdf(self, pdf_file_path):
        """Writes the whole song as printable A4 vector sheet music. Sizes are in points."""
        page_w = 595.28
        page_h = 841.89

        margin_left = 55.0
        margin_right = 25.0
        score_w = page_w - margin_left - margin_right
        string_spacing = 8.0
        system_spacing = 68.0
        top_y_start = 78.0
        first_page_rows = int((page_h - 40 - top_y_start) / system_spacing)
        later_page_rows = int((page_h - 40 - 42) / system_spacing)

        pdf_surface = cairo.PDFSurface(pdf_file_path, page_w, page_h)
        cr = cairo.Context(pdf_surface)

        measures_by_row = []
        curr_row = []
        curr_w = 0.0

        def min_bar_width(m):
            edges = m.slot_edges()
            return max(65.0, PDF_SLOT_PAD + PDF_SLOT_MIN / min(b - a for a, b in zip(edges, edges[1:])))

        for m_idx, m in enumerate(self.tab_track.measures):
            min_mw = min_bar_width(m)

            if (curr_row and (curr_w + min_mw > score_w)) or len(curr_row) >= 6:
                measures_by_row.append(curr_row)
                curr_row = [m_idx]
                curr_w = min_mw
            else:
                curr_row.append(m_idx)
                curr_w += min_mw

        if curr_row:
            measures_by_row.append(curr_row)

        page_rows = []
        rem_rows = list(measures_by_row)

        if rem_rows:
            page_rows.append(rem_rows[:first_page_rows])
            rem_rows = rem_rows[first_page_rows:]

        while rem_rows:
            page_rows.append(rem_rows[:later_page_rows])
            rem_rows = rem_rows[later_page_rows:]

        total_pages = max(1, len(page_rows))

        for p_idx, page_row_list in enumerate(page_rows):
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(0, 0, page_w, page_h)
            cr.fill()

            string_names = self.tab_track.get_string_names()

            if p_idx == 0:
                title_str = self.tab_track.title or "Guitar Tablature"

                cr.set_source_rgb(0.08, 0.10, 0.16)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(18)
                cr.move_to(margin_left, 36)
                cr.show_text(title_str)

                meta_parts = []
                t_name = self.tab_track.tuning_name or "Standard E"
                tuning_str = " ".join(string_names)
                meta_parts.append(f"Tuning: {t_name} ({tuning_str})")
                if self.tab_track.capo > 0:
                    meta_parts.append(f"Capo: Fret {self.tab_track.capo}")
                meta_parts.append(f"BPM: {self.tab_track.bpm}")
                first_sig = self.tab_track.measures[0].time_sig
                if first_sig != (4, 4):
                    meta_parts.append(f"Time: {first_sig[0]}/{first_sig[1]}")

                cr.set_source_rgb(0.35, 0.40, 0.48)
                cr.set_font_size(8.5)
                cr.move_to(margin_left, 52)
                cr.show_text("   |   ".join(meta_parts))

                cr.set_source_rgb(0.45, 0.50, 0.58)
                cr.set_font_size(8.5)
                page_str = f"Page {p_idx + 1} of {total_pages}"
                p_ext = cr.text_extents(page_str)
                cr.move_to(page_w - margin_right - p_ext.width, 36)
                cr.show_text(page_str)

                cr.set_source_rgb(0.82, 0.85, 0.90)
                cr.set_line_width(0.9)
                cr.move_to(margin_left, 60)
                cr.line_to(page_w - margin_right, 60)
                cr.stroke()

                current_top_y = top_y_start
            else:
                cr.set_source_rgb(0.45, 0.50, 0.58)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(8.5)
                page_str = f"Page {p_idx + 1} of {total_pages}"
                p_ext = cr.text_extents(page_str)
                cr.move_to(page_w - margin_right - p_ext.width, 24)
                cr.show_text(page_str)

                cr.set_source_rgb(0.88, 0.90, 0.94)
                cr.set_line_width(0.7)
                cr.move_to(margin_left, 30)
                cr.line_to(page_w - margin_right, 30)
                cr.stroke()

                current_top_y = 42.0

            for r_idx, row_m_indices in enumerate(page_row_list):
                pdf_my = current_top_y + r_idx * system_spacing

                row_min_w = sum(min_bar_width(self.tab_track.measures[idx]) for idx in row_m_indices)

                curr_mx = margin_left
                last_note = {}  # string -> x of the note before, for slurs
                pdf_ink = (0.05, 0.08, 0.15)
                pdf_paper = (1.0, 1.0, 1.0)

                # String names and the TAB clef appear on the first line only.
                if p_idx == 0 and r_idx == 0:
                    cr.set_source_rgb(0.25, 0.30, 0.38)
                    cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                    cr.set_font_size(8.0)
                    for s in range(6):
                        sy = pdf_my + s * string_spacing
                        cr.move_to(margin_left - 38, sy + 2.5)
                        cr.show_text(f"{string_names[s]:>2}")

                    cr.set_source_rgb(0.12, 0.15, 0.22)
                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                    cr.set_font_size(9.0)
                    cr.move_to(margin_left - 18, pdf_my + string_spacing * 1.3)
                    cr.show_text("T")
                    cr.move_to(margin_left - 18, pdf_my + string_spacing * 2.8)
                    cr.show_text("A")
                    cr.move_to(margin_left - 18, pdf_my + string_spacing * 4.3)
                    cr.show_text("B")

                for m_idx in row_m_indices:
                    m = self.tab_track.measures[m_idx]
                    measure_w = min_bar_width(m) * min(score_w / row_min_w, PDF_MAX_STRETCH)

                    cr.set_source_rgb(0.40, 0.45, 0.55)
                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                    cr.set_font_size(7.5)
                    cr.move_to(curr_mx + 2, pdf_my - 9)
                    cr.show_text(f"m.{m_idx + 1}{self._bar_sig_label(m_idx)}")

                    cr.set_source_rgb(0.35, 0.40, 0.48)
                    cr.set_line_width(0.75)
                    for s in range(6):
                        sy = pdf_my + s * string_spacing
                        cr.move_to(curr_mx, sy)
                        cr.line_to(curr_mx + measure_w, sy)
                        cr.stroke()

                    cr.set_source_rgb(0.15, 0.18, 0.25)
                    cr.set_line_width(1.2)
                    cr.move_to(curr_mx, pdf_my)
                    cr.line_to(curr_mx, pdf_my + 5 * string_spacing)
                    cr.stroke()
                    cr.move_to(curr_mx + measure_w, pdf_my)
                    cr.line_to(curr_mx + measure_w, pdf_my + 5 * string_spacing)
                    cr.stroke()

                    xs = self._slot_xs(m, curr_mx, measure_w, PDF_SLOT_PAD)

                    for b_idx, beat in enumerate(m.beats):
                        bx = xs[b_idx]

                        for s_idx, note in beat.notes.items():
                            sy = pdf_my + s_idx * string_spacing

                            tokens = tech_tokens(note.technique)
                            if "x" in tokens:
                                text_str = "X"
                            elif "NH" in tokens:
                                text_str = f"<{note.fret}>"
                            elif "AH" in tokens:
                                text_str = f"[{note.fret}]"
                            else:
                                text_str = str(note.fret)

                            # White box so the string line doesn't run through the number.
                            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                            cr.set_font_size(7.5)
                            ext = cr.text_extents(text_str)

                            pad_x = 1.0
                            pad_y = 1.0
                            box_w = max(6.0, ext.width + pad_x * 2.0)
                            box_h = ext.height + pad_y * 2.0
                            box_x = bx - box_w / 2.0
                            box_y = (sy + ext.y_bearing / 2.0) - box_h / 2.0

                            cr.set_source_rgb(1.0, 1.0, 1.0)
                            cr.rectangle(box_x, box_y, box_w, box_h)
                            cr.fill()

                            cr.set_source_rgb(0.05, 0.08, 0.15)
                            text_y = sy - (ext.height / 2.0 + ext.y_bearing)
                            cr.move_to(bx - ext.width / 2.0, text_y)
                            cr.show_text(text_str)

                            if note.technique:
                                if "/" in note.technique or "s" in note.technique:
                                    cr.save()
                                    cr.set_source_rgb(0.1, 0.15, 0.25)
                                    cr.set_line_width(0.65)
                                    cr.move_to(bx - 6.0, sy + 1.8)
                                    cr.line_to(bx - 2.5, sy - 1.8)
                                    cr.stroke()
                                    cr.restore()
                                elif "\\" in note.technique or "S" in note.technique:
                                    cr.save()
                                    cr.set_source_rgb(0.1, 0.15, 0.25)
                                    cr.set_line_width(0.65)
                                    cr.move_to(bx - 6.0, sy - 1.8)
                                    cr.line_to(bx - 2.5, sy + 1.8)
                                    cr.stroke()
                                    cr.restore()

                            if "b" in note.technique or note.bend_val is not None:
                                b_val = 1.0 if note.bend_val is None else note.bend_val
                                b_type = note.bend_type or "bend"

                                if b_val == 0.25: bend_lbl = "1/4"
                                elif b_val == 0.5: bend_lbl = "1/2"
                                elif b_val == 0.75: bend_lbl = "3/4"
                                elif b_val == 1.0: bend_lbl = "Full"
                                elif b_val == 1.5: bend_lbl = "1 ½"
                                elif b_val == 2.0: bend_lbl = "2"
                                else: bend_lbl = f"{b_val:g}"

                                if b_type == "bend_release": bend_lbl += " r"
                                elif b_type == "prebend": bend_lbl = "pb " + bend_lbl
                                elif b_type == "prebend_release": bend_lbl = "pb " + bend_lbl + " r"

                                bend_h = 10.0
                                curve_top_y = sy - bend_h

                                cr.save()
                                cr.set_source_rgb(0.85, 0.45, 0.05)
                                cr.set_line_width(1.0)

                                if b_type in ("prebend", "prebend_release"):
                                    cr.set_dash([2.0, 1.0])
                                    cr.move_to(bx, sy - 5)
                                    cr.line_to(bx, curve_top_y)
                                    cr.stroke()
                                    cr.set_dash([])
                                else:
                                    cr.move_to(bx, sy - 5)
                                    cr.curve_to(bx + 2, sy - 7, bx + 3, curve_top_y + 1, bx + 5, curve_top_y)
                                    cr.stroke()

                                arrow_x = bx + 5 if b_type not in ("prebend", "prebend_release") else bx
                                arrow_y = curve_top_y
                                cr.move_to(arrow_x - 2.0, arrow_y + 2.5)
                                cr.line_to(arrow_x + 2.0, arrow_y + 2.5)
                                cr.line_to(arrow_x, arrow_y)
                                cr.close_path(); cr.fill()

                                if b_type in ("bend_release", "prebend_release"):
                                    cr.move_to(arrow_x, arrow_y)
                                    cr.curve_to(arrow_x + 2, arrow_y + 1, arrow_x + 3, sy - 7, arrow_x + 5, sy - 5)
                                    cr.stroke()

                                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                                cr.set_font_size(6.5)
                                lbl_ext = cr.text_extents(bend_lbl)
                                lbl_x = arrow_x - lbl_ext.width / 2.0
                                lbl_y = arrow_y - 1.5

                                cr.set_source_rgb(1.0, 1.0, 1.0)
                                cr.rectangle(lbl_x - 1.0, lbl_y - lbl_ext.height, lbl_ext.width + 2, lbl_ext.height + 1.0)
                                cr.fill()

                                cr.set_source_rgb(0.85, 0.45, 0.05)
                                cr.move_to(lbl_x, lbl_y)
                                cr.show_text(bend_lbl)
                                cr.restore()

                            if note.technique:
                                top_tech = tech_badge(note.technique)
                                if top_tech:
                                    cr.save()
                                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                                    cr.set_font_size(6.5)
                                    t_ext = cr.text_extents(top_tech)
                                    ty = pdf_my - 2
                                    tx = bx - t_ext.width / 2.0

                                    cr.set_source_rgb(*pdf_ink)
                                    cr.move_to(tx, ty)
                                    cr.show_text(top_tech)
                                    cr.restore()

                            label = slur_label(note.technique)
                            if label:
                                start = last_note[s_idx] + 3 if s_idx in last_note else bx - 11
                                self._draw_note_slur(cr, label, start, bx - 3, sy, (4.0, 5.0, 0.5, 5.5), pdf_ink, pdf_paper)

                            if note.grace_fret is not None:
                                self._draw_grace(cr, note.grace_fret, bx - 7.5, bx - 3, sy, (5.0, 3.5, 2.5, 0.4), pdf_ink, pdf_paper)

                            last_note[s_idx] = bx

                    cr.set_source_rgb(0.20, 0.25, 0.32)
                    self._draw_length_marks(cr, m, xs, pdf_my + 5 * string_spacing + 2, 9.0, 0.55, 3.2, 6.0)

                    curr_mx += measure_w

            cr.set_source_rgb(0.60, 0.65, 0.72)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(7.5)
            cr.move_to(margin_left, page_h - 18)
            cr.show_text("Created with TabForge Studio")
            link = "github.com/ssdjmjro/TabForge-Studio"
            cr.move_to(page_w - margin_right - cr.text_extents(link).width, page_h - 18)
            cr.show_text(link)

            cr.show_page()

        pdf_surface.finish()
