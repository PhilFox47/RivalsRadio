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
- Keep the game at a **consistent resolution**; references are resolution- and
  position-sensitive. Re-capture if you change resolution.
- Tune in **Settings → Detection tuning**:
  - **Match threshold** (0–1): raise it if heroes get confused for each other,
    lower it if switches are missed. ~0.7 is a good start.
  - **Confirm count**: higher = steadier (won't switch on a brief kill-cam of an
    enemy hero), but slower to react.
  - **Poll interval**: how often it reads the screen.

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
