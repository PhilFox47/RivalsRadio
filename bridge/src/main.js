// RivalsRadio Overwolf bridge (ow-electron).
//
// Subscribes to the Marvel Rivals Game Events Provider (GEP) and prints the
// locally-played hero to stdout as JSON lines, which the RivalsRadio Python app
// reads via its "gep" hero source:
//
//     {"type":"hero","hero":"Jeff the Land Shark"}
//     {"type":"log","message":"..."}
//
// API shape follows Overwolf's official ow-electron-packages-sample:
//   app.overwolf.packages.on('ready', ...)  -> gep package
//   gep.on('game-detected'|'game-exit'|'new-game-event'|'error', ...)
//   gep.setRequiredFeatures(gameId, features)
//
// Marvel Rivals GEP game id (from @overwolf/ow-electron-packages-types):
const MARVEL_RIVALS = 24890;

const { app, BrowserWindow } = require('electron');

function out(obj) {
  try { process.stdout.write(JSON.stringify(obj) + '\n'); } catch (_) {}
}
function log(message) { out({ type: 'log', message: String(message) }); }

let win = null;
let gep = null;
let lastHero = null;

// Overwolf requires at least one visible desktop window; keep it tiny.
function createWindow() {
  win = new BrowserWindow({ width: 360, height: 200, title: 'RivalsRadio Bridge' });
  win.loadFile('index.html');
}

app.whenReady().then(() => {
  createWindow();
  registerOverwolf();
});

app.on('window-all-closed', () => app.quit());

function registerOverwolf() {
  if (!app.overwolf || !app.overwolf.packages) {
    log('ow-electron overwolf packages unavailable — is this running under ow-electron?');
    return;
  }
  app.overwolf.packages.on('ready', (e, packageName, version) => {
    if (packageName !== 'gep') return;
    log('gep ready ' + version);
    setupGep();
  });
}

function setupGep() {
  gep = app.overwolf.packages.gep;

  gep.on('game-detected', (e, gameId, name) => {
    if (gameId !== MARVEL_RIVALS) return;
    e.enable();                       // opt in to this game's events
    log('Marvel Rivals detected: ' + name);
    try {
      // null = all features; or pass ['match_info'] to narrow it.
      gep.setRequiredFeatures(MARVEL_RIVALS, null);
    } catch (err) {
      log('setRequiredFeatures failed: ' + err);
    }
  });

  gep.on('game-exit', (e, gameId) => {
    if (gameId === MARVEL_RIVALS) { lastHero = null; log('Marvel Rivals exited'); }
  });

  gep.on('error', (e, gameId, error) => log('gep error: ' + error));

  // Live updates. Different ow-electron versions deliver info on slightly
  // different event names; listen broadly and scan the payload.
  for (const evt of ['new-game-event', 'new-info-update', 'game-event', 'info-update']) {
    try { gep.on(evt, (e, gameId, ...args) => handleUpdate(args)); } catch (_) {}
  }
}

function handleUpdate(args) {
  for (const a of args) scan(a);
}

// Walk an arbitrary GEP payload looking for selected_character.character_name.
function scan(obj, depth = 0) {
  if (!obj || typeof obj !== 'object' || depth > 6) return;

  const sc = obj.selected_character ||
             (obj.match_info && obj.match_info.selected_character);
  if (sc) emitHero(sc);

  // Flat form: { feature:'match_info', key:'selected_character', value:'{...}' }
  if (obj.key === 'selected_character' && obj.value) emitHero(obj.value);

  for (const k of Object.keys(obj)) {
    const v = obj[k];
    if (v && typeof v === 'object') scan(v, depth + 1);
  }
}

function emitHero(value) {
  let name = null;
  if (typeof value === 'string') {
    try { const p = JSON.parse(value); name = p.character_name || p.name || null; }
    catch (_) { name = value; }
  } else if (value && typeof value === 'object') {
    name = value.character_name || value.name || null;
  }
  if (!name) return;
  name = prettify(name);
  if (name !== lastHero) {
    lastHero = name;
    out({ type: 'hero', hero: name });
  }
}

// "JEFF THE LAND SHARK" -> "Jeff The Land Shark" (RivalsRadio resolves the
// exact roster spelling case-insensitively on the Python side).
function prettify(s) {
  return String(s).toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}
