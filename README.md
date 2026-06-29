# RivalsRadio 🎵🦸

Automatically switch Spotify playlists based on the **Marvel Rivals** hero
you're currently playing. Pick Spider-Man → your Spider-Man playlist starts.
Swap to Luna Snow → the music follows. There's also a themed, audio-reactive
**Stage** view for a second monitor or your stream.

## How it works

RivalsRadio detects your current hero through **Overwolf**, which exposes
Marvel Rivals' supported Game Events:

1. A small companion **native Overwolf app** (bundled in `overwolf-app/`) reads
   the game's events and reports your current hero to RivalsRadio over
   localhost.
2. When your hero changes, RivalsRadio starts that hero's Spotify playlist on
   your active device.
3. You can also **switch heroes manually** from the Status tab at any time —
   handy on a second monitor, and it works without any detection set up.

> **No anti-cheat risk.** Hero data comes from Overwolf's official Game Events
> Provider — RivalsRadio never reads or modifies game memory, so it does not
> interact with the game's anti-cheat.

## Detection source

Hero detection is **pluggable** (Settings → *Hero detection source*):

| Mode | Behaviour |
|---|---|
| **`native`** (default) | The companion native Overwolf app POSTs hero data to RivalsRadio over `http://127.0.0.1:8771`. Load it unpacked in Overwolf (dev mode). |
| `gep` | The optional standalone [ow-electron](bridge/README.md) bridge, launched as a child process. Requires building it once (Node) and a whitelisted Overwolf account. |

Everything downstream (Spotify, Stage, OBS overlay, manual switch) is identical
regardless of source.

## Requirements

- **Windows** gaming PC (where the game, Overwolf and Spotify all run).
- **Python 3.9+** (only if running from source — the prebuilt `.exe` bundles it).
- **Spotify Premium** — the Spotify Web API only allows apps to control playback
  on Premium accounts.
- The Spotify desktop or mobile app open as an active playback device.
- **Overwolf** installed, for automatic hero detection (optional — manual
  switching works without it).

## Setup

### 1. Install (from source)

```bash
git clone <this-repo>
cd RivalsRadio
python -m venv .venv
# Windows:  .venv\Scripts\activate
pip install -r requirements.txt
```

(Or just download the prebuilt `.exe` — see *Running as an .exe* below.)

### 2. Create a Spotify app (for API access)

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. **Create app**. Give it any name.
3. Set the **Redirect URI** to exactly: `http://127.0.0.1:8888/callback`
   (Spotify rejects `localhost` — you must use the loopback IP `127.0.0.1`.)
4. Copy the **Client ID** and **Client Secret**.

### 3. Run

```bash
python main.py
```

Then in the app:

1. **Settings tab** — paste your Client ID / Secret, then save settings.
2. Click **Connect Spotify** (a browser window authorizes the app once).
3. **Heroes tab** — for each hero you want, paste its Spotify **playlist URI**
   (in Spotify: right-click a playlist → Share → *Copy Spotify URI*, e.g.
   `spotify:playlist:37i9dQZF1DX...`). Optionally set the Stage art and the two
   per-hero colours (see *Stage view* below).
4. Set up detection (optional): install the native Overwolf app (see below),
   **or** just use the **manual hero switch** on the Status tab.
5. Back on the **Status tab**, click **Start monitoring** and play — or pick a
   hero from the manual switch to change the music instantly.

### Native Overwolf app (automatic detection)

1. Install **Overwolf** and enable **developer mode** (Overwolf Settings →
   *Support* → *Development options*, or the dev tools panel).
2. **Load unpacked** the `overwolf-app/` folder and launch it.
3. Start Marvel Rivals — the app reports your hero to RivalsRadio on
   `http://127.0.0.1:8771`. Keep RivalsRadio's source set to `native`.

## Stage view (second screen)

The **Stage** is a large, themed "now playing" window built for a second
monitor. The hero's **logo** sits front and centre and **pulses with the
audio**, the hero's **signature** sits top-right, **now playing** (album art +
track + progress) sits top-left, and an **audio-reactive visualizer** runs along
the bottom.

- Click **Status → Open Stage view**, drag the window to your second screen, and
  press **F11** (or double-click) for fullscreen — it goes fullscreen **on the
  monitor the window is currently on** (Esc to exit).
- **Art:** in the **Heroes tab**, click **Art…** next to a hero and pick a
  transparent **logo** (shown centred, pulses), a **signature** (top-right) and a
  **portrait**. The portrait isn't shown on the Stage (yet) — it's the source for
  automatic colour extraction. Files are copied into `~/.rivalsradio/logos/`,
  `signatures/` and `portraits/`.
- **Per-hero colours:** each hero has a **Main** colour (the background glow) and
  an **Accent** colour (the visualizer bars). Click the **Main** / **Accent**
  swatches in the Heroes tab to pick them. Leave them unset to auto-derive from
  the portrait/logo.
- **Visualizer FPS:** the bars run at up to **160 FPS** for a smooth look. Set
  the target in **Settings → Stage & overlay → Visualizer FPS**.
- **Visualizer audio:** reacts to your PC's actual audio via WASAPI loopback (the
  `soundcard` package, Windows). If that backend isn't available, the Stage still
  runs — the bars just idle instead of reacting.
- **Now playing:** with a track playing, the Stage shows the current title,
  artist, album art and a progress bar (toggle in Settings).

### OBS / streaming (browser source)

In **Settings → Stage & overlay**, click **Start OBS overlay**. Then in OBS add a
**Browser Source** pointing at `http://localhost:8770/` (the port is
configurable). It renders the same hero / colours / now-playing / visualizer as
the desktop Stage, so you can drop it straight into a stream layout.

### First-run wizard

On first launch a short **setup wizard** walks you through Spotify → detection →
hero playlists.

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
overwolf-app/                   ← native Overwolf app (default hero source)
bridge/RivalsRadioBridge.exe    ← optional ow-electron GEP bridge (best-effort)
```

Keep the folder contents together. Load `overwolf-app/` unpacked in Overwolf for
automatic detection, or just use the manual hero switch.

Tagged versions (e.g. pushing a `v1.0.0` tag) also attach the zip to a GitHub
**Release** for easy linking.

### Option B — build it locally

On your Windows PC, just run **`build.bat`** (double-click it). It sets up a
virtual environment, installs everything, and produces `dist\RivalsRadio.exe`.

> The exe is windowed (no console pops up). It writes its config and artwork to
> `~/.rivalsradio/` as usual.

## Configuration storage

Config and artwork live in `~/.rivalsradio/` (override with the
`RIVALSRADIO_HOME` env var): `config.json` plus `logos/`, `signatures/`,
`portraits/` and `avatars/`. The hero roster starts empty and auto-populates as
heroes are detected in-game.

## Project layout

```
main.py                      # entry point
rivalsradio/
  app.py                     # CustomTkinter desktop UI
  config.py                  # JSON config + hero roster
  hero_source.py             # native Overwolf / ow-electron GEP sources
  monitor.py                 # detection → playlist-switch loop
  spotify_controller.py      # Spotipy playback control
  stage.py / stage_render.py # second-screen Stage view
  audio_visualizer.py        # WASAPI-loopback FFT spectrum
  web_overlay.py             # OBS browser-source overlay
overwolf-app/                # native Overwolf app (default hero source)
bridge/                      # optional ow-electron GEP bridge
```

## Limitations

- Automatic detection requires Overwolf and a supported game build; manual
  switching always works.
- Spotify playback control requires Premium and an active device.
- Tested target platform is the Windows PC where you play; it is not a console
  solution.
