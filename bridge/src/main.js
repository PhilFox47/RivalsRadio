// RivalsRadio Overwolf bridge (ow-electron).
//
// Subscribes to the Marvel Rivals Game Events Provider (GEP) and writes the
// locally-played hero (and best-effort match/KDA) as newline-delimited JSON to
// an events file that the RivalsRadio Python app tails:
//
//     {"type":"hero","hero":"Jeff the Land Shark"}
//     {"type":"log","message":"..."}
//
// A file is used rather than stdout because a packaged *windowed* Electron app
// on Windows has no usable stdout pipe — stdout writes silently vanish.
//
// API shape follows Overwolf's official ow-electron-packages-sample:
//   app.overwolf.packages.on('ready', ...)  -> gep package
//   gep.on('game-detected'|'game-exit'|'new-game-event'|'error', ...)
//   gep.setRequiredFeatures(gameId, features)
//
// Marvel Rivals GEP game id (from @overwolf/ow-electron-packages-types):
const MARVEL_RIVALS = 24890;
// Candidate GEP features for Marvel Rivals. null = "all" on some versions; we
// also try an explicit list since ow-electron can require named features.
const FEATURES = ['game_info', 'match_info', 'kill', 'death', 'roster', 'me',
                  'match_state', 'game_state'];

const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');

// Primary IPC: append structured messages to the events file the app tails.
const EVENTS_LOG = process.env.RIVALSRADIO_GEP_EVENTS || '';

function out(obj) {
  const line = JSON.stringify(obj) + '\n';
  if (EVENTS_LOG) {
    try { fs.appendFileSync(EVENTS_LOG, line); } catch (_) {}
  } else {
    try { process.stdout.write(line); } catch (_) {}
  }
}

// ---- Debug dump --------------------------------------------------------
// When enabled, every raw GEP payload AND every status line is appended to a
// log file so the exact field names / lifecycle can be confirmed from a live
// game. Toggled by the Python app via RIVALSRADIO_GEP_DEBUG / *_LOG, or --debug.
const DEBUG = process.env.RIVALSRADIO_GEP_DEBUG === '1' ||
              process.argv.includes('--debug');
const DEBUG_LOG = process.env.RIVALSRADIO_GEP_LOG ||
                  path.join(process.cwd(), 'gep-debug.log');
function dbg(tag, data) {
  if (!DEBUG) return;
  try {
    const line = `${new Date().toISOString()} ${tag} ` +
      (typeof data === 'string' ? data : JSON.stringify(data)) + '\n';
    fs.appendFileSync(DEBUG_LOG, line);
  } catch (_) {}
}

// Status logging goes to BOTH the events file (so the app shows it) and the
// debug log (so we have a record even if the app isn't reading).
function log(message) { out({ type: 'log', message: String(message) }); dbg('log', String(message)); }

let win = null;
let gep = null;
let gepReady = false;
let lastHero = null;
let lastResult = null;

// Overwolf requires at least one visible desktop window; keep it tiny.
function createWindow() {
  win = new BrowserWindow({ width: 360, height: 200, title: 'RivalsRadio Bridge' });
  win.loadFile('index.html');
}

app.whenReady().then(() => {
  createWindow();
  if (DEBUG) {
    try { fs.writeFileSync(DEBUG_LOG, `# RivalsRadio GEP debug log ${new Date().toISOString()}\n`); } catch (_) {}
  }
  log('bridge started (ow-electron ' + process.versions.electron + ')');
  registerOverwolf();
});

app.on('window-all-closed', () => app.quit());

function registerOverwolf() {
  log('overwolf api: ' + (app.overwolf ? 'present' : 'MISSING') +
      ', packages: ' + (app.overwolf && app.overwolf.packages ? 'present' : 'MISSING'));
  if (!app.overwolf || !app.overwolf.packages) {
    log('ow-electron overwolf packages unavailable — the bridge is not running under ow-electron.');
    return;
  }
  app.overwolf.packages.on('ready', (e, packageName, version) => {
    log('package ready: ' + packageName + ' v' + version);
    if (packageName === 'gep') setupGep();
    else if (packageName === 'overlay') setupOverlay();
  });
  log('registered overwolf ready handler; waiting for the gep package…');
  // Heartbeat so a stalled init is visible in the log.
  setTimeout(() => {
    if (!gepReady) log('still waiting for the gep package after 20s (is Overwolf installed/allowed?)');
  }, 20000);
}

// ow-electron needs games registered for tracking; registerGames lives on the
// overlay package. Register Marvel Rivals so GEP starts detecting it.
function setupOverlay() {
  try {
    const ov = app.overwolf.packages.overlay;
    if (ov && typeof ov.registerGames === 'function') {
      ov.registerGames([MARVEL_RIVALS]);
      log('overlay.registerGames([' + MARVEL_RIVALS + ']) called');
    } else {
      log('overlay ready but registerGames missing; keys: ' +
          (ov ? Object.keys(ov).join(',') : 'none'));
    }
  } catch (err) {
    log('overlay.registerGames failed: ' + err);
  }
}

function setupGep() {
  gep = app.overwolf.packages.gep;
  gepReady = true;
  log('gep package ready — subscribing to game events');

  gep.on('game-detected', (e, gameId, name) => {
    log('game-detected: ' + gameId + ' (' + name + ')');
    dbg('game-detected', { gameId, name });
    if (gameId !== MARVEL_RIVALS) { log('  not Marvel Rivals (' + MARVEL_RIVALS + '); ignoring'); return; }
    try { e.enable(); } catch (err) { log('enable() failed: ' + err); }
    log('Marvel Rivals detected — enabling events');
    setRequired();
  });

  // Fires (instead of game-detected) when the game runs elevated and we don't.
  // Marvel Rivals uses kernel anti-cheat, so this is the common failure mode.
  gep.on('elevated-privileges-required', (e, gameId, ...a) => {
    dbg('elevated-privileges-required', { gameId, a });
    out({ type: 'needs_admin' });
    log('ELEVATED PRIVILEGES REQUIRED: the game is running as administrator. ' +
        'Restart RivalsRadio as administrator so it can read game events.');
  });

  gep.on('game-exit', (e, gameId) => {
    if (gameId === MARVEL_RIVALS) { lastHero = null; lastResult = null; log('Marvel Rivals exited'); }
  });

  gep.on('error', (e, gameId, error) => { dbg('error', String(error)); log('gep error: ' + error); });

  // Live updates. Different ow-electron versions deliver info on slightly
  // different event names; listen broadly and scan the payload.
  let firstUpdateLogged = false;
  for (const evt of ['new-game-event', 'new-info-update', 'game-event', 'info-update']) {
    try {
      gep.on(evt, (e, gameId, ...args) => {
        if (!firstUpdateLogged) { firstUpdateLogged = true; log('receiving GEP updates (' + evt + ')'); }
        handleUpdate(evt, args);
      });
    } catch (_) {}
  }

  // Proactively register features now — ow-electron needs this up front so it
  // starts tracking a game that is already running (game-detected won't fire on
  // its own otherwise).
  setRequired();
}

// Try the known GEP registration APIs across ow-electron versions and log which
// one the installed runtime accepts, so detection starts regardless of version.
function setRequired() {
  const attempts = [
    ['setRequiredFeatures(gameId, FEATURES)', () => gep.setRequiredFeatures(MARVEL_RIVALS, FEATURES)],
    ['setRequiredFeatures(gameId, null)', () => gep.setRequiredFeatures(MARVEL_RIVALS, null)],
    ['setRequiredFeatures(FEATURES)', () => gep.setRequiredFeatures(FEATURES)],
    ['gep.registerGames([gameId])',
      () => (typeof gep.registerGames === 'function') ? gep.registerGames([MARVEL_RIVALS]) : '(absent)'],
  ];
  for (const [label, fn] of attempts) {
    try {
      const r = fn();
      log('register: ' + label + ' → ok' + (r && typeof r.then === 'function' ? ' (async)' : ''));
      if (r && typeof r.then === 'function') {
        r.then(() => log('register: ' + label + ' resolved'))
         .catch((err) => log('register: ' + label + ' rejected: ' + err));
      }
    } catch (err) {
      log('register: ' + label + ' → failed: ' + err);
    }
  }
}

function handleUpdate(evt, args) {
  for (const a of args) { dbg(evt, a); scan(a); }
}

// Walk an arbitrary GEP payload looking for the data we care about:
// selected_character (hero), match result (victory/defeat), and local KDA.
function scan(obj, depth = 0) {
  if (!obj || typeof obj !== 'object' || depth > 6) return;

  const sc = obj.selected_character ||
             (obj.match_info && obj.match_info.selected_character);
  if (sc) emitHero(sc);

  // Flat form: { feature:'match_info', key:'selected_character', value:'{...}' }
  if (obj.key === 'selected_character' && obj.value) emitHero(obj.value);

  scanMatch(obj);
  scanKda(obj);

  for (const k of Object.keys(obj)) {
    const v = obj[k];
    if (v && typeof v === 'object') scan(v, depth + 1);
  }
}

// Best-effort match result. Field names are confirmed via the debug log; we
// look for common shapes (victory flag / match_outcome / game_end result).
function scanMatch(obj) {
  let raw = null;
  if (obj.match_outcome != null) raw = obj.match_outcome;
  else if (obj.match_result != null) raw = obj.match_result;
  else if (obj.victory != null) raw = obj.victory;
  else if (obj.game_victory != null) raw = obj.game_victory;
  else if ((obj.key === 'match_outcome' || obj.key === 'victory') && obj.value != null) raw = obj.value;
  if (raw == null) return;

  const s = String(raw).toLowerCase();
  let result = null;
  if (s.includes('win') || s.includes('victory') || s === 'true' || s === '1') result = 'victory';
  else if (s.includes('los') || s.includes('defeat') || s === 'false' || s === '0') result = 'defeat';
  if (result && result !== lastResult) {
    lastResult = result;
    out({ type: 'match', result });
  }
}

// Best-effort local-player KDA from a roster entry flagged as the local player.
function scanKda(obj) {
  const isLocal = obj.is_local || obj.is_local_player || obj.local || obj.me;
  if (isLocal && (obj.kills != null || obj.deaths != null || obj.assists != null)) {
    out({
      type: 'stats',
      kills: Number(obj.kills) || 0,
      deaths: Number(obj.deaths) || 0,
      assists: Number(obj.assists) || 0,
    });
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
