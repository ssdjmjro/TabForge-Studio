# TabForge-Studio
Guitar tab writer

Windows Ver.
  1. Download / Copy TabForge_Studio_Windows_Portable.zip to your Windows   
  computer.
  2. Right-click the .zip file and select Extract All...
  3. Inside the extracted folder, double-click Start_TabForge_Studio.bat (or
  double-click index.html).
  
  That's it! It will instantly launch TabForge Studio in Microsoft Edge or  
  Chrome with zero setup or installation required.

Linux Ver.

   Step 1: Extract the Zip File (if not already extracted)
  
   Open a terminal in the folder where you placed TabForge_Studio_App.zip and run:
          
        unzip TabForge_Studio_App_Linux.zip
        cd TabForge_Studio_App_Linux
   
   ──────
   
   Step 2: Install System Dependencies (Run Once)
   
  Run this command to install the Python, GTK4, Libadwaita, and GStreamer graphics & audio libraries required by TabForge       Studio:
   
     sudo apt update && sudo apt install -y \
       python3 \
       python3-gi \
       python3-gi-cairo \
       python3-cairo \
       gir1.2-gtk-4.0 \
       gir1.2-adw-1 \
       gir1.2-gstreamer-1.0 \
       gstreamer1.0-plugins-base \
       gstreamer1.0-plugins-good \
       gstreamer1.0-plugins-bad \
       gstreamer1.0-plugins-ugly
      
   
   ──────
   
   Step 3: Run / Activate the Application
  
   Navigate into the source folder and launch main.py:
  
     cd TabForge_Studio_App_Source
   
     python3 main.py

   ______
   Step 4: Make the APP icon

       #!/usr/bin/env bash
    set -e

    APP_SRC="/home/ssdjmjro/TabForge_Studio_App/TabForge_Studio_App_Source"
    SVG_SRC="$APP_SRC/stratocaster.svg"
    ICONS_DIR="$HOME/.local/share/icons"
    APPS_DIR="$HOME/.local/share/applications"
    DESKTOP_DIR="$HOME/Desktop"

    echo "1. Creating standard XDG directories..."
    mkdir -p "$APPS_DIR"
    mkdir -p "$ICONS_DIR/hicolor/scalable/apps"
    mkdir -p "$ICONS_DIR/hicolor/"{16x16,24x24,32x32,48x48,64x64,128x128,256x256,
    512x512}/apps
    mkdir -p "$DESKTOP_DIR"

    echo "2. Installing scalable SVG icons..."
    cp "$SVG_SRC" "$APP_SRC/io.github.tabforge.studio.svg"
    cp "$SVG_SRC" "$APP_SRC/tabforge.svg"
    cp "$SVG_SRC" "$ICONS_DIR/io.github.tabforge.studio.svg"
    cp "$SVG_SRC" "$ICONS_DIR/tabforge.svg"
    cp "$SVG_SRC" "$ICONS_DIR/hicolor/scalable/apps/io.github.tabforge.studio.svg"
    cp "$SVG_SRC" "$ICONS_DIR/hicolor/scalable/apps/tabforge.svg"

    echo "3. Generating multi-resolution PNG icons (16x16 to 512x512)..."
    python3 -c "
    import os, gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf

    src = '$SVG_SRC'
    pb = GdkPixbuf.Pixbuf.new_from_file(src)
    base = os.path.expanduser('~/.local/share/icons/hicolor')

    for size in [16, 24, 32, 48, 64, 128, 256, 512]:
        d = f'{base}/{size}x{size}/apps'
        scaled = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if scaled:
            scaled.savev(f'{d}/io.github.tabforge.studio.png', 'png', [], [])
            scaled.savev(f'{d}/tabforge.png', 'png', [], [])
    "

    echo "4. Creating desktop entry..."
    cat << 'EOF' > "$APPS_DIR/io.github.tabforge.studio.desktop"
    [Desktop Entry]
    Version=1.5
    Type=Application
    Name=TabForge Studio
    GenericName=Guitar Tablature Studio
    Comment=Songsterr-Style 22-Fret Guitar Tablature & Audio Studio
    Exec=/usr/bin/python3
    /home/ssdjmjro/TabForge_Studio_App/TabForge_Studio_App_Source/main.py
    Icon=io.github.tabforge.studio
    Path=/home/ssdjmjro/TabForge_Studio_App/TabForge_Studio_App_Source
    Terminal=false
    StartupNotify=true
    StartupWMClass=io.github.tabforge.studio
    Categories=AudioVideo;Audio;Music;Sequencer;
    Keywords=Guitar;Tab;Tablature;Music;Fender;Stratocaster;Songsterr;Studio;Audio;
    EOF

    chmod +x "$APPS_DIR/io.github.tabforge.studio.desktop"

    # Copy alias and desktop shortcut
    cp "$APPS_DIR/io.github.tabforge.studio.desktop" "$APPS_DIR/tabforge.desktop"
    chmod +x "$APPS_DIR/tabforge.desktop"

    cp "$APPS_DIR/io.github.tabforge.studio.desktop" "$DESKTOP_DIR/TabForge Studio.
    desktop"
    chmod +x "$DESKTOP_DIR/TabForge Studio.desktop"
    gio set -t string "$DESKTOP_DIR/TabForge Studio.desktop" metadata::trusted true
    2>/dev/null || true

    echo "5. Refreshing system icon caches and desktop databases..."
    gtk-update-icon-cache -f -t "$ICONS_DIR/hicolor" 2>/dev/null || true
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true

    echo "6. Validating desktop file..."
    desktop-file-validate "$APPS_DIR/io.github.tabforge.studio.desktop"

    echo "Done!"
MacOS Ver.
  1. Extract TabForge_Studio_macOS.zip
  2. Double-click TabForge_Studio_macOS.command in Finder (or run ./TabForge_Studio_macOS.command in Terminal).
  3. (Optional) Run ./setup_mac.sh to automatically install GTK4, Libadwaita & GStreamer via Homebrew.         
  4. (Optional) Run ./build_mac_app.sh to compile a native TabForgeStudio.app bundle.
