# RivalsRadio — native Overwolf GEP app

A tiny **native Overwolf** app that reads the current Marvel Rivals hero (and
match result / KDA) via `overwolf.games.events` and sends it to the RivalsRadio
desktop app over localhost. This is the path that works with a **whitelisted
Overwolf developer account** — no app-store approval and no ow-electron package
provisioning.

## How it talks to RivalsRadio

It POSTs newline JSON to `http://127.0.0.1:8771/event`:

```
{"type":"game","running":true}
{"type":"hero","hero":"Hela"}
{"type":"match","result":"victory"}
{"type":"stats","kills":12,"deaths":3,"assists":7}
{"type":"log","message":"…"}     ← raw GEP payloads, shown in RivalsRadio's log
```

RivalsRadio's **`native`** detection source runs that listener.

## Setup (one time)

1. In **RivalsRadio → Settings → Detection source**, choose **`native`**, Save,
   then **Start monitoring** (this starts the localhost listener on port 8771).
2. In the **Overwolf** client: **Settings → Support → Development options →
   Load unpacked extension**, and select this `overwolf-app` folder.
   (You must be **logged in** to Overwolf with your **whitelisted** developer
   account, or you'll get an "Unauthorized App" error.)
3. Launch **Marvel Rivals**. In RivalsRadio's Activity log you should see
   `[overwolf] ✅ Marvel Rivals running …`, then `game: Running ✓` and your hero.

## Notes

- The app logs raw `info:`/`event:` GEP payloads to RivalsRadio's Activity log on
  purpose — that's how we confirm the exact field names. Once detection is solid
  we can quiet those down.
- The port must match RivalsRadio's `native_port` (default **8771**). Change it in
  both places if 8771 is taken.
- Marvel Rivals GEP data is NetEase-compliance-limited (no enemy stats); the local
  player's hero, KDA, and match result are within the allowed feature set.
