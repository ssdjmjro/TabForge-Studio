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

    mkdir -p ~/.local/share/icons/hicolor/scalable/apps ~/.local/share/applications
    cp stratocaster.svg ~/.local/share/icons/hicolor/scalable/apps/tabforge.svg
  
    cat << EOF > ~/.local/share/applications/tabforge.desktop
    [Desktop Entry]
    Type=Application
    Name=TabForge Studio
    Exec=python3 "$PWD/main.py"
    Icon=tabforge
    Terminal=false
    Categories=AudioVideo;Music;
    StartupWMClass=io.github.tabforge.studio
    EOF
  
    update-desktop-database ~/.local/share/applications



MacOS Ver.
  1. Extract TabForge_Studio_macOS.zip
  2. Double-click TabForge_Studio_macOS.command in Finder (or run ./TabForge_Studio_macOS.command in Terminal).
  3. (Optional) Run ./setup_mac.sh to automatically install GTK4, Libadwaita & GStreamer via Homebrew.         
  4. (Optional) Run ./build_mac_app.sh to compile a native TabForgeStudio.app bundle.
