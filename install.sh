#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$REPO_DIR/TabForge_Studio_App_Source"

echo "==================================================="
echo "       🎸 TabForge Studio - Linux Installer        "
echo "==================================================="

# 1. Check required GTK4 / Libadwaita Python bindings on Debian/Ubuntu/Parrot
if ! python3 -c "import numpy, gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('Gst', '1.0')" 2>/dev/null; then
    echo "[*] Installing required GTK4 & Libadwaita packages..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3-gi python3-gi-cairo python3-numpy gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good gstreamer1.0-pulseaudio
    fi
fi

# 2. Create default user directories for tabs, PDFs, and recordings
mkdir -p "$HOME/My_Guitar_Tabs" "$HOME/My_Guitar_PDFs" "$HOME/guitar_recordings"

# 3. Install Desktop Icon & System Application Menu Launcher
chmod +x "$SRC_DIR/main.py" "$SRC_DIR/launch.sh" "$SRC_DIR/install_icon.py"
python3 "$SRC_DIR/install_icon.py"

echo ""
echo "✅ TabForge Studio is installed!"
echo "   • Desktop Shortcut: ~/Desktop/TabForge Studio.desktop"
echo "   • App Menu: Search 'TabForge Studio' in your application launcher"
echo "   • Run from terminal: $SRC_DIR/launch.sh"
