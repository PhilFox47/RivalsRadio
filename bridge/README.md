# RivalsRadio Overwolf Bridge (ow-electron)

A tiny standalone [ow-electron](https://dev.overwolf.com/ow-electron/) app that
reads the **current Marvel Rivals hero** from Overwolf's Game Events Provider
(GEP) and prints it to stdout for the RivalsRadio Python app to consume.

This is the **exact, screen-free** hero source. It is *standalone* (white-label
ow-electron) — there is **no separate Overwolf client** to install or keep
running; the GEP runtime is embedded in this app, and RivalsRadio launches/closes
it for you.

## Output protocol

One JSON object per line on stdout:

```json
{"type":"hero","hero":"Jeff the Land Shark"}
{"type":"log","message":"Marvel Rivals detected: ..."}
```

RivalsRadio matches the hero name to your roster case-insensitively.

## Prerequisites (one-time)

1. **Node.js 18+** installed.
2. An **Overwolf developer account** and access to the `@overwolf/ow-electron`
   packages — see the [ow-electron getting started](https://dev.overwolf.com/ow-electron/getting-started/overview/)
   guide. You may need an Overwolf npm registry/`.npmrc` entry per their docs.
3. For distribution you'll need a **code-signing certificate** (Overwolf requires
   signed ow-electron apps).

## Build & run

```bash
cd bridge
npm install
npm start        # launches the bridge; start Marvel Rivals to see hero events
```

To package a standalone exe (so RivalsRadio can auto-launch it):

```bash
npm run dist     # produces a Windows build via ow-electron-builder
```

Then point RivalsRadio at it: **Settings → Hero detection source → `gep`**, and
set **GEP bridge command** to the built exe path — or drop the built
`RivalsRadioBridge.exe` in a `bridge/` folder next to the RivalsRadio app and it
will be found automatically.

## How RivalsRadio uses it

With source = `gep`, RivalsRadio spawns this bridge as a child process on
**Start monitoring** and terminates it on stop/exit. The hero feed replaces
screen capture; everything downstream (Spotify switching, Stage, OBS overlay) is
unchanged.

## Notes / caveats

- GEP delivers `match_info.selected_character` as
  `{ "character_id": 1047, "character_name": "JEFF THE LAND SHARK" }`. Different
  ow-electron versions surface info on slightly different event names, so
  `main.js` listens broadly and scans the payload. If hero events don't appear,
  use Overwolf's **Game Events Simulator** to inspect the exact event/field
  names for game id **24890** and adjust `scan()` accordingly.
- Marvel Rivals (id `24890`) is in Overwolf's ow-electron GEP supported-games
  list, but GEP availability rolls out per game/version — verify against your
  installed `@overwolf/ow-electron-packages` version.
- Overwolf's developer terms apply to ow-electron apps; review them before
  distributing.
