# RivalsRadio 🎵🦸

Automatically switch Spotify playlists based on the **Marvel Rivals** hero
you're currently playing. Pick Spider-Man → your Spider-Man playlist starts.
Swap to Luna Snow → the music follows.

## How it works

There's no public Marvel Rivals API that reports your live hero, so RivalsRadio
**watches your screen** instead:

1. You record a one-time **reference snapshot** of a small HUD region per hero
   (the ability/ultimate icons in the corner are unique to each hero).
2. While you play, the app captures that region every couple of seconds and
   matches it against your saved references.
3. When it confirms you've switched heroes, it starts that hero's Spotify
   playlist on your active device.

> **No anti-cheat risk.** RivalsRadio only captures the screen — the same thing
> OBS and streaming tools do. It never reads or modifies game memory, so it does
> not interact with the game's anti-cheat. (Avoid any tool that claims to read
> the game's memory; that's what gets accounts banned.)

## Requirements

- **Windows or macOS** gaming PC (where the game and Spotify both run).
- **Python 3.9+**.
- **Spotify Premium** — the Spotify Web API only allows apps to control playback
  on Premium accounts.
- The Spotify desktop or mobile app open as an active playback device.

## Setup

### 1. Install

```bash
git clone <this-repo>
cd RivalsRadio
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS:    source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a Spotify app (for API access)

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. **Create app**. Give it any name.
3. Set the **Redirect URI** to exactly: `http://localhost:8888/callback`
4. Copy the **Client ID** and **Client Secret**.

### 3. Run

```bash
python main.py
```

Then in the app:

1. **Settings tab** — paste your Client ID / Secret, then click **Select
   region…** and drag a box around your in-game HUD ability icons. Use
   **Preview** to confirm the box is on the right spot. Save settings.
2. Click **Connect Spotify** (a browser window authorizes the app once).
3. **Heroes tab** — for each hero you want, paste its Spotify **playlist URI**
   (in Spotify: right-click a playlist → Share → *Copy Spotify URI*, e.g.
   `spotify:playlist:37i9dQZF1DX...`).
4. **Calibrate**: jump into a match (or the practice range) on a hero, then click
   **Capture** next to that hero. Repeat for each hero. A ✓ marks calibrated
   heroes.
5. Back on the **Status tab**, click **Start monitoring** and play.

## Tips for reliable detection

- Choose a HUD region that's **always visible while playing** and **distinct per
  hero** — the ability/ultimate icon cluster works well. Avoid the crosshair,
  health bar, or anything that looks similar across heroes.
- Matching is **tolerant to small HUD drift and moderate resolution changes**
  (the template is matched at several scales inside a slightly larger search
  window), but a big resolution change can still warrant re-capturing.
- Use **Status → Test detection** while in a match to see the live top-3 match
  scores. A confident setup shows a high top score with a clear gap to the
  runner-up; if two heroes score close together, re-capture one of them using a
  more distinctive HUD region.
- Tune in **Settings → Detection tuning**:
  - **Match threshold** (0–1): raise it if heroes get confused for each other,
    lower it if switches are missed. ~0.7 is a good start.
  - **Confirm count**: higher = steadier (won't switch on a brief kill-cam of an
    enemy hero), but slower to react.
  - **Poll interval**: how often it reads the screen.

## Stage view (second screen)

The **Stage** is a large, themed "now playing" window built for a second
monitor. It shows the current hero's artwork on an accent-tinted background with
the hero/playlist names and a **live audio-reactive visualizer**.

- Click **Status → Open Stage view**, drag the window to your second screen, and
  press **F11** for fullscreen (**Esc** to exit fullscreen).
- **Avatars:** in the **Heroes tab**, click **Avatar** next to a hero and pick a
  transparent PNG/WebP (square renders look best — like the official hero
  splash art). The file is copied into `~/.rivalsradio/avatars/`.
- **Accent colour:** auto-extracted from each avatar (e.g. Doctor Strange → red,
  Luna Snow → ice blue). To override, type a `#RRGGBB` value in the hero's accent
  box and **Save mappings**.
- **Visualizer:** reacts to your PC's actual audio via WASAPI loopback (the
  `soundcard` package, Windows). If that backend isn't available, the Stage still
  runs — the bars just idle instead of reacting.

## Running as an .exe (no terminal)

You don't have to launch from a terminal. There are two ways to get a
standalone `RivalsRadio.exe`:

### Option A — download it from GitHub Actions (no tools needed)

Every push to the dev branch builds a Windows executable automatically.

1. Go to the repo's **Actions** tab → **Build Windows EXE** → the latest run.
2. Download the **RivalsRadio-windows** artifact at the bottom.
3. Unzip and double-click `RivalsRadio.exe`.

Tagged versions (e.g. pushing a `v1.0.0` tag) also attach the exe to a GitHub
**Release** for easy linking.

### Option B — build it locally

On your Windows PC, just run **`build.bat`** (double-click it). It sets up a
virtual environment, installs everything, and produces `dist\RivalsRadio.exe`.
Move that file anywhere and double-click to run — no Python or terminal needed
afterwards. (For a desktop shortcut, right-click the exe → *Send to → Desktop*.)

> The exe is windowed (no console pops up). It still writes its config and
> reference images to `~/.rivalsradio/` as usual.

## Configuration storage

Config and reference images live in `~/.rivalsradio/` (override with the
`RIVALSRADIO_HOME` env var). Reference PNGs are screen crops of your own game and
are git-ignored.

## Project layout

```
main.py                      # entry point
rivalsradio/
  app.py                     # Tkinter desktop UI
  config.py                  # JSON config + hero roster
  capture.py                 # mss screen capture
  recognizer.py              # OpenCV template matching
  spotify_controller.py      # Spotipy playback control
  monitor.py                 # detection loop (capture→match→debounce→switch)
  region_selector.py         # drag-to-select HUD overlay
```

## Limitations

- Detection is image-based, so it depends on good calibration and a stable HUD.
  It won't be perfect across UI changes or resolution swaps.
- Spotify playback control requires Premium and an active device.
- Tested target platform is the PC where you play; it is not a console solution.
