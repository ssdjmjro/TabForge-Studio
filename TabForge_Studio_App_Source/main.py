#!/usr/bin/env python3
import sys
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio

from window import TabWriterWindow

class TabWriterApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.tabforge.studio",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        
    def do_activate(self):
        win = self.get_active_window()
        if not win:
            self._setup_icons()
            self._load_css()
            win = TabWriterWindow(application=self)
        win.present()

    def _setup_icons(self):
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            app_dir = os.path.dirname(os.path.abspath(__file__))
            theme.add_search_path(app_dir)
            icons_dir = os.path.join(app_dir, 'icons')
            if os.path.exists(icons_dir):
                theme.add_search_path(icons_dir)

    def _load_css(self):
        css_path = os.path.join(os.path.dirname(__file__), 'style.css')
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

def main():
    app = TabWriterApp()
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())
