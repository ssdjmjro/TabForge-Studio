# TabForge-Studio
Guitar tab writer

Windows Ver.
  1. Open the extracted TabForge_Studio_Windows_x64 folder.
  2. Double-click Setup_Windows_x64.bat (or QUICK_START.bat).
  3. The setup script will automatically install all necessary runtime      
  dependencies:
      • GTK4 & Libadwaita graphics runtime
      • Python PyGObject bindings
      • GStreamer audio synthesizer engine
      • Desktop Shortcut creation
     
  Once setup finishes, you can start the application anytime by:            
  
  • Double-clicking the TabForge Studio Desktop shortcut.
  • Or double-clicking QUICK_START.bat / TabForge_Studio.bat inside the     
  folder.
  
  -[!TIP]-
    Optional (Standalone Executable Build):
    If you prefer a single .exe file without needing Python script wrappers,
    double-click Build_EXE_Windows.bat inside the folder. It will generate a
    standalone executable in dist\TabForgeStudio_Win64\.


Linux Ver.
  1. Extract TabForge_Studio_Linux.zip
  2. Open terminal in the extracted folder and run:
    ./TabForge_Studio.sh
  
  3. (Optional) Run ./setup_linux.sh for 1-click dependency installation (Debian, Ubuntu, Arch, Fedora).
  4. (Optional) Run ./install_desktop_shortcut.sh to create an Application Menu & Desktop shortcut.

MacOS Ver.
  1. Extract TabForge_Studio_macOS.zip
  2. Double-click TabForge_Studio_macOS.command in Finder (or run ./TabForge_Studio_macOS.command in Terminal).
  3. (Optional) Run ./setup_mac.sh to automatically install GTK4, Libadwaita & GStreamer via Homebrew.         
  4. (Optional) Run ./build_mac_app.sh to compile a native TabForgeStudio.app bundle.
