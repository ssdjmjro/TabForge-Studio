#!/usr/bin/env python3
"""
TabForge Studio Icon & Desktop App Installer
Installs XDG-compliant desktop entries and high-resolution icons (SVG & PNGs)
for KDE Plasma, GNOME, and standard Linux desktop environments.
"""

import os
import sys
import shutil
import subprocess
import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ID = "io.github.tabforge.studio"
APP_NAME = "TabForge Studio"
APP_COMMENT = "Songsterr-Style 22-Fret Guitar Tablature & Audio Studio"
APP_GENERIC = "Guitar Tablature Studio"
APP_CATEGORIES = "AudioVideo;Audio;Music;Sequencer;"
APP_KEYWORDS = "Guitar;Tab;Tablature;Music;Fender;Stratocaster;Songsterr;Studio;Audio;"

HOME = os.path.expanduser("~")
APPLICATIONS_DIR = os.path.join(HOME, ".local", "share", "applications")
ICONS_BASE_DIR = os.path.join(HOME, ".local", "share", "icons")
DESKTOP_DIR = os.path.join(HOME, "Desktop")
SVG_SOURCE = os.path.join(APP_DIR, "stratocaster.svg")

SIZES = [16, 24, 32, 48, 64, 128, 256, 512]


def install_icons():
    print("Installing application icons...")
    if not os.path.exists(SVG_SOURCE):
        print(f"Error: SVG source file not found at {SVG_SOURCE}")
        return False

    # 1. Local copy in source dir
    shutil.copyfile(SVG_SOURCE, os.path.join(APP_DIR, f"{APP_ID}.svg"))
    shutil.copyfile(SVG_SOURCE, os.path.join(APP_DIR, "tabforge.svg"))

    # 2. Scalable SVGs
    scalable_dir = os.path.join(ICONS_BASE_DIR, "hicolor", "scalable", "apps")
    os.makedirs(scalable_dir, exist_ok=True)
    shutil.copyfile(SVG_SOURCE, os.path.join(scalable_dir, f"{APP_ID}.svg"))
    shutil.copyfile(SVG_SOURCE, os.path.join(scalable_dir, "tabforge.svg"))

    # Direct in ~/.local/share/icons
    os.makedirs(ICONS_BASE_DIR, exist_ok=True)
    shutil.copyfile(SVG_SOURCE, os.path.join(ICONS_BASE_DIR, f"{APP_ID}.svg"))
    shutil.copyfile(SVG_SOURCE, os.path.join(ICONS_BASE_DIR, "tabforge.svg"))

    # 3. PNGs at standard sizes
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(SVG_SOURCE)
    for size in SIZES:
        size_dir = os.path.join(ICONS_BASE_DIR, "hicolor", f"{size}x{size}", "apps")
        os.makedirs(size_dir, exist_ok=True)

        scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if scaled:
            scaled.savev(os.path.join(size_dir, f"{APP_ID}.png"), "png", [], [])
            scaled.savev(os.path.join(size_dir, "tabforge.png"), "png", [], [])

    print("Icons successfully installed across all standard resolutions.")
    return True


def install_desktop_entry():
    print("Generating and installing desktop entries...")
    os.makedirs(APPLICATIONS_DIR, exist_ok=True)

    main_py_path = os.path.join(APP_DIR, "main.py")
    python_bin = sys.executable

    desktop_content = f"""[Desktop Entry]
Version=1.5
Type=Application
Name={APP_NAME}
GenericName={APP_GENERIC}
Comment={APP_COMMENT}
Exec={python_bin} {main_py_path}
Icon={APP_ID}
Path={APP_DIR}
Terminal=false
StartupNotify=true
StartupWMClass={APP_ID}
Categories={APP_CATEGORIES}
Keywords={APP_KEYWORDS}
"""

    # In source directory
    source_desktop = os.path.join(APP_DIR, f"{APP_ID}.desktop")
    with open(source_desktop, "w", encoding="utf-8") as f:
        f.write(desktop_content)
    os.chmod(source_desktop, 0o755)

    # In ~/.local/share/applications/
    app_menu_desktop = os.path.join(APPLICATIONS_DIR, f"{APP_ID}.desktop")
    with open(app_menu_desktop, "w", encoding="utf-8") as f:
        f.write(desktop_content)
    os.chmod(app_menu_desktop, 0o755)

    # Alias ~/.local/share/applications/tabforge.desktop
    alias_desktop = os.path.join(APPLICATIONS_DIR, "tabforge.desktop")
    with open(alias_desktop, "w", encoding="utf-8") as f:
        f.write(desktop_content)
    os.chmod(alias_desktop, 0o755)

    # On ~/Desktop/ if it exists
    if os.path.exists(DESKTOP_DIR):
        desktop_launcher = os.path.join(DESKTOP_DIR, f"{APP_NAME}.desktop")
        with open(desktop_launcher, "w", encoding="utf-8") as f:
            f.write(desktop_content)
        os.chmod(desktop_launcher, 0o755)

        # Mark as trusted if gio is present (KDE / GNOME desktop security)
        try:
            subprocess.run(
                ["gio", "set", "-t", "string", desktop_launcher, "metadata::trusted", "true"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass
        print(f"Created Desktop shortcut: {desktop_launcher}")

    print(f"Installed application launcher: {app_menu_desktop}")
    return True


def update_caches():
    print("Refreshing desktop databases and icon caches...")
    hicolor_dir = os.path.join(ICONS_BASE_DIR, "hicolor")

    # Update GTK icon cache
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", hicolor_dir], check=False)

    # Update desktop database
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", APPLICATIONS_DIR], check=False)

    # Refresh KDE sycoca cache
    if shutil.which("kbuildsycoca6"):
        subprocess.run(["kbuildsycoca6"], check=False)
    elif shutil.which("kbuildsycoca5"):
        subprocess.run(["kbuildsycoca5"], check=False)

    # Validate desktop file
    if shutil.which("desktop-file-validate"):
        app_menu_desktop = os.path.join(APPLICATIONS_DIR, f"{APP_ID}.desktop")
        res = subprocess.run(["desktop-file-validate", app_menu_desktop], capture_output=True, text=True)
        if res.returncode == 0:
            print("Desktop entry validation: OK")
        else:
            print("Validation warning:", res.stderr or res.stdout)


def main():
    print("=== TabForge Studio Icon App Installer ===")
    install_icons()
    install_desktop_entry()
    update_caches()
    print("=== Installation Complete! ===")
    print("TabForge Studio is now registered as an icon app in your system application menu and desktop.")


if __name__ == "__main__":
    main()
