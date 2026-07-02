# RivalsRadio 🎵🦸

Hero-aware Spotify for **Marvel Rivals**: pick Spider-Man → your Spider-Man
playlist starts. Swap to Luna Snow → the music follows. Comes with a
GPU-rendered **Stage** — an audio visualizer for your second screen themed to
the hero you're playing.

Ships with a full roster (53 heroes) including logos, portraits, signatures,
playlists and colours — ready to play out of the box.

## How it works

1. A small companion **native Overwolf app** (in `overwolf-app/`) reads Marvel
   Rivals' official game events inside Overwolf and reports your current hero
   to RivalsRadio over localhost.
2. RivalsRadio switches Spotify to that hero's playlist (shuffled, random
   starting track) and themes the Stage.
3. Heroes you play that aren't in the roster yet are **added automatically**.

> **No anti-cheat risk.** Hero data comes from Overwolf's official Game Events
> Provider. RivalsRadio never reads or modifies game memory.

## Setup

1. **Get the app**: download `RivalsRadio-windows.zip` from Releases, unzip,
   run `RivalsRadio.exe` (or run from source: `pip install -r requirements.txt`
   then `python main.py`).
2. **Spotify** (Premium required):
   - [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
     → *Create app*, Redirect URI **exactly** `http://127.0.0.1:8888/callback`
     (Spotify rejects `localhost`).
   - Paste Client ID + Secret in **Settings → Spotify → Save & connect**.
3. **Hero detection** (optional — manual switching always works):
   - Install Overwolf, enable developer options, *Load unpacked* the
     `overwolf-app/` folder, launch it, start Marvel Rivals.

## The Stage

Open it from Home and drag it to your second monitor (F11 or double-click =
fullscreen on that monitor). The hero's **logo** pulses to the beat in the
centre over a breathing glow, the **signature** sits top-right, **now
playing** (album art, title, progress) top-left, and gradient **visualizer
bars** run along the bottom — all over the hero's **background picture**
(blurred and dimmed to taste) or a colour gradient. Hero switches play a
portrait-led panel sweep.

Everything is tunable in **Settings → Stage**: FPS (up to 160), background
blur/dim, glow, pulse depth, particles, now-playing, switch animation.

## Per-hero customisation (Heroes tab)

- **Playlist** — paste any Spotify playlist link.
- **Art…** — logo (white-on-transparent works best), portrait, signature,
  background. The button shows ✓ once all four are set.
- **Main / Accent colours** — Main tints the logo, Accent drives the bars.
  Leave unset to auto-extract a palette from the portrait.

## Project layout

```
main.py                  entry point
rivalsradio/
  ui.py                  CustomTkinter shell (Home / Heroes / Settings)
  conductor.py           detection → roster → Spotify → Stage feed
  detector.py            localhost listener for the Overwolf app
  spotify.py             playback control + album-art cache
  stage.py               GPU (SDL/pygame) Stage renderer
  audio.py               WASAPI-loopback FFT + beat detection
  config.py              settings + roster, first-run seeding
  colors.py, feed.py, paths.py
assets/heroes/           shipped roster, logos, portraits, signatures
overwolf-app/            the native Overwolf companion app
```

User data lives in `~/.rivalsradio/` (override with `RIVALSRADIO_HOME`).
