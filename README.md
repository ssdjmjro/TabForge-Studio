# 🎸 TabForge Studio

An open-source, Songsterr-style **22-Fret Guitar Tablature & Audio Studio** for Linux built with Python, GTK4, Libadwaita, and Cairo.

## ✨ Features

- **Interactive 22-Fret Stratocaster Neck & Visual Picker**: Click frets directly on the visual fretboard or enter fret numbers on the 6-string tablature canvas.
- **Guitar Techniques & Articulations**: Supports hammer-ons (`h`), pull-offs (`p`), slides (`/`, `\`), bends (`b`), vibrato (`~`), palm muting (`P.M.`), harmonics, dead notes (`X`), and ghost notes.
- **Practice Playback**: Built-in guitar synthesizer with sample-accurate tempo, time signatures, practice speed, tap tempo, bar looping, metronome, and count-in.
- **Real Rhythm**: Pick a note length and keep typing: the empty space ahead follows it. Drag a note's end to resize it, or right-click for a length menu. Mix whole notes down to 32nds, dotted notes and triplets, with a time signature per bar. Note width on screen matches its length, with beat numbers and beamed rhythm notation. Keep your own lengths as buttons, and choose how lengths are shown under Length Display in the menu.
- **Fast Editing**: Undo/redo, bar copy & paste, and automatic saving (see Keyboard Shortcuts in the menu for the full list).
- **Built-in Audio Recorder**: Record live guitar takes to `~/guitar_recordings/`.
- **Vector PDF Sheet Music Export**: Export crisp, printable vector PDF guitar tabs to `~/My_Guitar_PDFs/`.
- **Native Desktop Integration**: Installs a desktop shortcut and system application menu icon with one command.

---

## 🚀 Quick Install (`git clone` + Desktop Icon)

Clone the repository and run `bash install.sh` to install dependencies, create your workspace folders, and add the **TabForge Studio** icon to your Desktop and Application Menu:

```bash
git clone https://github.com/ssdjmjro/TabForge-Studio.git ~/TabForge_Studio_App
cd ~/TabForge_Studio_App
bash install.sh
```

### Launching
- **From Desktop / App Menu**: Click the **TabForge Studio** icon on your Desktop or in your system application launcher.
- **From Terminal**:
  ```bash
  ~/TabForge_Studio_App/TabForge_Studio_App_Source/launch.sh
  ```

---

## 🛠️ Requirements

- Linux with **GTK 4** and **Libadwaita**
- Python 3 with NumPy and GStreamer (`python3-gi`, `python3-gi-cairo`, `python3-numpy`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-gstreamer-1.0`, `gstreamer1.0-plugins-good`, `gstreamer1.0-pulseaudio`) — automatically checked and installed by `./install.sh` on Debian/Ubuntu/Parrot systems.

## 📄 License

Released under the [MIT License](LICENSE).
