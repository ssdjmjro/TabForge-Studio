# TabForge-Studio
Guitar tab writer

Windows Ver.
  1. Extract the ZIP file:                                                                                     
      • Transfer TabForge_Studio_Windows_x64.zip to your Windows PC.                                           
      • Right-click TabForge_Studio_Windows_x64.zip and select Extract All...                                  
  2. Open the App:                                                                                             
      • Double-click QUICK_START.bat (or TabForge_Studio.bat).                                                 
      • Build_EXE_Windows.bat: Compiles TabForge Studio into a standalone TabForgeStudio.exe binary.           
      • tabforge_studio.spec: PyInstaller bundle specification configured for GTK4 & audio engines.            
  4. Documentation                                                                                             
      • README_WINDOWS.txt                                                                                     
                                                                                                               
      • First-time setup: If GTK4 is not installed on your Windows PC, select Option [2] in QUICK_START.bat to 
      run the 1-Click Installer.                                                                               
  3. Silent / Desktop Launch:                                                                                  
      • Double-click Start_TabForge_Studio.vbs to launch without showing a command window, or use option [4] in
      QUICK_START.bat to create a Desktop shortcut.

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
