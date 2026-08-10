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

   1. Open terminal and run this command:
    nano ~/.local/share/applications/tabforge-studio.desktop
  
  2. Copy and paste the following content inside:
    [Desktop Entry]
    Name=TabForge Studio
    Comment=Guitar Tab Writer & Editor
    Exec=python3 /home/ssdjmjro/tab_writer/main.py
    Icon=/home/ssdjmjro/tab_writer/stratocaster.svg
    Terminal=false
    Type=Application
    Categories=AudioVideo;Audio;Utility;

MacOS Ver.
  1. Extract TabForge_Studio_macOS.zip
  2. Double-click TabForge_Studio_macOS.command in Finder (or run ./TabForge_Studio_macOS.command in Terminal).
  3. (Optional) Run ./setup_mac.sh to automatically install GTK4, Libadwaita & GStreamer via Homebrew.         
  4. (Optional) Run ./build_mac_app.sh to compile a native TabForgeStudio.app bundle.
