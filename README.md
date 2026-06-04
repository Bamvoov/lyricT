# lyricT 🎵

A responsive, centered, dynamically-themed CLI lyrics syncer for Linux media players. It connects to active players (Spotify, VLC, Audacious, Firefox, Chrome, etc.) via MPRIS/playerctl, fetches synced lyrics, and displays them beautifully in your terminal with smooth transitions and cover-art-matching background gradients.

---

https://github.com/user-attachments/assets/4b8cb312-f139-4971-a252-b143f3fc866a


## Features

- ✨ **Dynamic Theming:** Automatically extracts the dominant color palette from your current track's album art. If your music player does not provide cover art URLs (e.g., Spotify Web in Firefox/Chrome), lyricT performs an instantaneous background query against the iTunes Search API to fetch the official artwork.
- 🚀 **Butter-Smooth Scrolling:** Features an asynchronous query engine and a Phase-Locked Loop (PLL) filter that matches MPRIS events to local monotonic system clocks. This eliminates the text-highlight jitter and visual stutter common in other CLI lyric decoders.
- 📏 **Adaptive Layout:** Dynamically adjusts padding and scaling (automatically applying spacing on larger terminal windows to fill the screen).
- 🧠 **Smart Caching:** Local caching of lyrics under `~/.cache/lyricT` to ensure instant loading of previously played tracks.

---

## Quick Installation

Run the following command in your terminal to install `lyricT` to your local user binary directory (`~/.local/bin/`):

```bash
curl -sSL https://raw.githubusercontent.com/Bamvoov/lyricT/main/install.sh | bash
```

> [!NOTE]
> Make sure `~/.local/bin` is in your shell's environment path. If it isn't, add the following to your `~/.bashrc` or `~/.zshrc`:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

---

## Manual Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Bamvoov/lyricT.git
   cd lyricT
   ```

2. **Make the script executable:**
   ```bash
   chmod +x lyricT.py
   ```

3. **Install Python dependencies:**
   ```bash
   pip install requests pillow
   ```

4. **Copy the script to your local bin directory:**
   ```bash
   mkdir -p ~/.local/bin
   cp lyricT.py ~/.local/bin/lyricT
   ```

---

## System Requirements

- **Linux Operating System**
- **Python 3.8+** (with standard `pip` setup)
- **playerctl:** Required to fetch media data from MPRIS-compatible players.
  - *Arch Linux:* `sudo pacman -S playerctl`
  - *Debian/Ubuntu:* `sudo apt install playerctl`
  - *Fedora:* `sudo dnf install playerctl`

---

## Usage

Simply play your music from any MPRIS-compatible player (Spotify client, web browser playing YouTube/Spotify, VLC, etc.) and launch the syncer:

```bash
lyricT
```

Use `Ctrl + C` at any time to exit.

---

## License

This project is licensed under the MIT License.
