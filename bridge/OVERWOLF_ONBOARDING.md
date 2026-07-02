# Getting the ow-electron bridge working (whitelisting, UIDs, console)

Status: the app proposal **is whitelisted** (confirmation email received), but the
bridge still needs its runtime identity to match the whitelisted one before
Overwolf's servers will serve the real GEP package. This document explains the
whole mechanism and the exact steps left.

## How ow-electron whitelisting actually works

There are **no keys, no tokens, no downloads**. The entire mechanism is:

1. Every ow-electron app has a **UID** — a hash derived from two fields of its
   `package.json`: the top-level **`productName`** (falling back to `name` if
   missing) and **`author.name`** (the `name` field *inside* the `author`
   object).
2. When Overwolf approves a proposal, they add that app's UID (derived from the
   name/author **you wrote in the proposal form**) to a server-side allowlist.
3. At runtime, ow-electron's package manager identifies itself by UID. If the
   UID is allowlisted, the real `gep`/`overlay` packages download; if not, you
   get the empty **v0.0.0 stub** packages (exactly the failure we saw).

So "getting whitelisted" changes nothing on your machine — it only matters that
the bridge's `package.json` identity **exactly matches** (case, spacing) what
was submitted in the proposal.

You can compute the UID for any identity locally:

```bash
npm i -D @overwolf/ow-cli
npx ow client calc-uid -n "<productName>" -a "<author name>"
```

And the bridge logs its actual runtime UID on startup (`app uid: …` — from the
`OVERWOLF_APP_UID` env var ow-electron sets).

## About console.overwolf.com ("Something went wrong")

The Developers Console is **not self-service**. Per Overwolf's docs, your
DevRel contact must first provision access for the **specific account you gave
them** (typically a Google account). Logging in with any other account — even a
valid Overwolf account — fails with exactly the generic "Something went wrong"
error. It is not a browser problem.

→ **Reply to the whitelisting email** and ask them to:
1. provision Developers Console access for your account (tell them which email
   / Google account to use), and
2. confirm the **exact app name and author name** they whitelisted (or the UID
   itself), and
3. confirm the whitelist covers **Marvel Rivals (game id 24890) GEP**.

That thread is your DevRel contact — it's the fastest (and basically only)
path.

## The identity test loop (no console needed)

You don't need the console to verify the whitelist works. The feedback loop is
local:

1. Edit `bridge/package.json`: set top-level `productName` and `author.name`
   to a candidate identity (whatever you wrote in the proposal — app name and
   your name/studio name).
2. `npm install` (first time only), then `npm start` in `bridge/`.
3. Watch the RivalsRadio activity log (or `gep-debug.log` with debug on):
   - `package ready: gep v0.0.0` → **not** whitelisted under this identity.
   - `package ready: gep v3.x.x` (any real version) → **match!** Done.

Try the exact strings from your proposal first. Capitalisation and spacing
matter — "RivalsRadio" ≠ "Rivals Radio".

## Fixed in this repo (previous silent UID bugs)

- `author` was a plain string (`"author": "RivalsRadio"`). ow-electron reads
  **`author.name`**, so the author was effectively empty and the UID wrong.
  It is now the object form.
- `productName` only existed under `build` (electron-builder), which affects
  the **packaged** exe but not `npm start` — so dev runs and packaged runs had
  **different UIDs**. A top-level `productName` now makes them identical.
- `@overwolf/ow-electron` was pinned to `31.7.12` (2024-era). Now `39.6.1`
  (latest stable) — an ancient client against current package servers is
  another possible stub cause.
- The QA packages URL workaround is no longer needed; leave
  "GEP packages URL" empty in RivalsRadio's settings (PROD).

## Support channels

- Reply directly to the whitelisting email (your DevRel contact).
- Overwolf developers Discord — the ow-electron channel is where package/UID
  issues get answered fastest.
- https://support.overwolf.com for account-level issues.

## Once it works

Set RivalsRadio's detection source to `gep` (Status tab). The app launches the
bridge automatically and reads heroes from it. The native Overwolf app remains
the default and keeps working regardless.
