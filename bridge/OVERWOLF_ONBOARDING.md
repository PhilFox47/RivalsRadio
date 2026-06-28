# Getting Overwolf GEP working for RivalsRadio

## TL;DR — why the bridge currently shows `gep v0.0.0`

ow-electron's `gep` and `overlay` packages only **provision** (download a real
version) for apps whose **Overwolf developer account is whitelisted**. Until
then, every package loads as an empty `v0.0.0` stub — which is exactly what our
logs show, on every machine, even running Overwolf's own canonical setup.

This is **not** a code, packaging, version, admin, or game problem (all ruled
out). And it is **not** a Marvel Rivals problem — Marvel Rivals (game id
`24890`) is already on Overwolf's Electron-supported list. The only missing
piece is **account/app whitelisting**.

So once the steps below are done, the existing bridge should work unchanged:
the log should show `gep vX.Y.Z` (a real version) and `✅ Marvel Rivals
detected`.

## What you need to do (requires your Overwolf account)

1. **Create an Overwolf developer account** at https://dev.overwolf.com →
   "Developer onboarding". Console: https://console.overwolf.com.

2. **Phase 1 — submit the app idea** to the Overwolf DevRel team for approval.
   Describe RivalsRadio (a hero-aware Spotify controller) and state that it uses
   **ow-electron GEP for Marvel Rivals (game id 24890)**. DevRel approval is what
   triggers the **account whitelisting** email.

3. **Register the app identity.** ow-electron derives the GEP app UID from
   `productName` + `author.name` in `bridge/package.json`. Current values:
   - `productName`: `RivalsRadioBridge`
   - `author`: `RivalsRadio`
   - → **App UID:** `lpgiljdaoinadfgconkigikgigfolimbbifcpgjm`
     (also printed in the bridge log as `app uid: …`, and shown in RivalsRadio's
     Activity log).
   Register the app with this exact identity. If you change `productName`/`author`,
   the UID changes — keep them in sync with what you register.

4. **DEV vs PROD package environment.** While your game/app is in Overwolf's DEV
   stage, point the bridge at the QA package endpoint:
   RivalsRadio → Settings → **GEP packages URL** =
   `https://electronapi-qa.overwolf.com/packages`.
   Once Overwolf moves you to PROD, clear that field (blank = PROD).

5. **Verify.** With the account whitelisted, run RivalsRadio **as administrator**
   (Marvel Rivals runs elevated), turn on **Settings → Log raw GEP events**, hit
   **Start monitoring**, and launch the game. In the Activity log you should see:
   - `package ready: gep vX.Y.Z`  ← real version, not `v0.0.0`
   - `✅ Marvel Rivals detected and ENABLED — game is running`
   - then hero/match events.

If `gep` still shows `v0.0.0` after whitelisting, send Overwolf DevRel the
installer logs from `%Temp%\ow-electron` and the app UID above.

## Honest expectations

- This depends on Overwolf approving/whitelisting the app; that's their process
  and timeline, not something the code controls.
- GEP data for Marvel Rivals is **NetEase-compliance-limited** (no enemy
  damage/healing, etc.). RivalsRadio only needs the local player's selected hero
  (and optionally KDA/match result), which are within the allowed feature set.
- The app stays self-contained as a binary, but GEP still relies on the Overwolf
  runtime being present on the machine.
