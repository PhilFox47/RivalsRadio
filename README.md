# RivalsRadio 🎵🦸

Automatically switch Spotify playlists based on the **Marvel Rivals** hero
you're currently playing. Pick Spider-Man → your Spider-Man playlist starts.
Swap to Luna Snow → the music follows.

## How it works

There's no public Marvel Rivals API that reports your live hero, so RivalsRadio
**watches your screen** instead:

1. You record a one-time **reference snapshot** of a small HUD region per hero
   (the hero portrait in the bottom-left corner — your hero's face — is the best
   anchor, since it's unique per hero and doesn't change during play).
2. While you play, the app captures that region every couple of seconds and
   matches it against your saved references.
3. When it confirms you've switched heroes, it starts that hero's Spotify
   playlist on your active device.

> **No anti-cheat risk.** RivalsRadio only captures the screen — the same thing
> OBS and streaming tools do. It never reads or modifies game memory, so it does
> not interact with the game's anti-cheat. (Avoid any tool that claims to read
> the game's memory; that's what gets accounts banned.)

## Detection source: screen capture vs Overwolf GEP

Hero detection is **pluggable** (Settings → *Hero detection source*). The
default **`auto`** prefers the exact Overwolf GEP bridge and **falls back to
screen capture** automatically if the bridge isn't installed — or if it stops
working mid-session.

| Mode | Behaviour |
|---|---|
| **`auto`** (default) | Use the Overwolf GEP bridge if present; otherwise (or on bridge failure) use screen capture |
| `gep` | Overwolf bridge only — exact, no screen capture |
| `screen` | Screen capture only — no bridge needed |

| | Overwolf GEP | Screen capture |
|---|---|---|
| Accuracy | **Exact** — the real hero from the game's events | Good (depends on calibration) |
| Setup | Build the ow-electron bridge once (Node + Overwolf dev account) | Calibrate references per hero |
| Screen capture | **No** | Yes |

The GEP source uses a small **standalone [ow-electron](bridge/README.md)
bridge** — *white-label, no separate Overwolf client to run*. RivalsRadio
launches it as a child process on Start and closes it on exit. Marvel Rivals
(`24890`) is in Overwolf's supported-games list. See
[`bridge/README.md`](bridge/README.md) to build it. Everything downstream
(Spotify, Stage, OBS overlay) is identical regardless of source.

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

- **Best anchor: the bottom-left hero portrait** (your hero's face). It's always
  on screen, unique per hero, and — unlike the right-side ability icons — it
  doesn't change with cooldowns, ammo, or ult charge, so matching stays stable.
  Keep the box tight on the face, away from the frame edges (which can glow when
  your ultimate is ready). Avoid the crosshair and health bar.
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
  runs — the bars just idle instead of reacting. Pick a style in **Settings →
  Stage & overlay**: `bars`, `mirror`, or `radial`.
- **Now playing:** with a track playing, the Stage shows the current title,
  artist, album art and a progress bar (toggle in Settings).

### OBS / streaming (browser source)

In **Settings → Stage & overlay**, click **Start OBS overlay**. Then in OBS add a
**Browser Source** pointing at `http://localhost:8770/` (the port is
configurable). It renders the same hero / accent / now-playing / visualizer as
the desktop Stage, so you can drop it straight into a stream layout.

### First-run wizard & auto-detect

On first launch a short **setup wizard** walks you through Spotify → capture
region → calibration. You can re-find the region any time with **Settings → HUD
capture region → Auto-detect game** (locates the Marvel Rivals window and
suggests a HUD box to fine-tune).

## Running as an .exe (no terminal)

You don't have to launch from a terminal. There are two ways to get a
standalone `RivalsRadio.exe`:

### Option A — download it from GitHub Actions (no tools needed)

Every push to the dev branch builds a Windows executable automatically.

1. Go to the repo's **Actions** tab → **Build Windows EXE** → the latest run.
2. Download the **RivalsRadio-windows** artifact at the bottom.
3. Unzip it **keeping the folder intact**, then double-click `RivalsRadio.exe`.

The zip is a self-contained bundle:

```
RivalsRadio.exe                 ← the app
bridge/RivalsRadioBridge.exe    ← the Overwolf GEP bridge (built automatically)
```

Keep `RivalsRadio.exe` and the `bridge/` folder together — the app looks for
the bridge right next to itself and uses it as the primary (exact) hero source,
falling back to screen capture if it isn't there. The bridge is built
**best-effort**: if the Overwolf build ever fails, the zip still contains a
working `RivalsRadio.exe` (screen-capture mode), just without `bridge/`.

Tagged versions (e.g. pushing a `v1.0.0` tag) also attach the zip to a GitHub
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
