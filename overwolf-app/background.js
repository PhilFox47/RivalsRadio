// RivalsRadio — native Overwolf GEP bridge.
//
// Reads Marvel Rivals live game data via overwolf.games.events and POSTs the
// current hero (and match/KDA) to the RivalsRadio desktop app's local listener.
// Requires only a whitelisted Overwolf account (Load unpacked) — no app-store
// approval and no ow-electron package provisioning.

const GAME_ID = 24890;                                   // Marvel Rivals class id
const FEATURES = ['match_info', 'game_info', 'gep_internal'];
const ENDPOINT = 'http://127.0.0.1:8771/event';          // RivalsRadio "native" source

let lastHero = null;
let lastResult = null;
let registered = false;

function post(obj) {
  try {
    fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj),
    }).catch(() => {});
  } catch (_) {}
}
function log(m) { post({ type: 'log', message: String(m) }); }

// Overwolf's running-game id is the class id * 10 (+ an index); GEP uses the
// class id itself. Accept both.
function isRivals(id) {
  if (id == null) return false;
  return id === GAME_ID || Math.floor(id / 10) === GAME_ID;
}

function setFeatures() {
  overwolf.games.events.setRequiredFeatures(FEATURES, (info) => {
    log('setRequiredFeatures → ' + JSON.stringify(info));
  });
}

function onRunningChanged(info) {
  const running = !!(info && info.isRunning && isRivals(info.id != null ? info.id : info.classId));
  post({ type: 'game', running: running });
  if (running && !registered) {
    registered = true;
    log('✅ Marvel Rivals running — registering GEP features');
    setFeatures();
    // The game may have launched before us; pull current state once.
    overwolf.games.events.getInfo((res) => { if (res && res.res) scan(res.res); });
  } else if (!running) {
    registered = false;
    lastHero = null;
    lastResult = null;
  }
}

// ---- wiring -----------------------------------------------------------
overwolf.games.getRunningGameInfo((info) => onRunningChanged(info));
overwolf.games.onGameInfoUpdated.addListener((res) => {
  if (res && res.gameInfo) onRunningChanged(res.gameInfo);
});
overwolf.games.events.onError.addListener((e) => log('gep error: ' + JSON.stringify(e)));
overwolf.games.events.onInfoUpdates2.addListener((data) => {
  log('info: ' + JSON.stringify(data));            // raw — lets us confirm field names
  if (data && data.info) scan(data.info);
});
overwolf.games.events.onNewEvents.addListener((data) => {
  log('event: ' + JSON.stringify(data));
  if (data && data.events) data.events.forEach(scanEvent);
});

// ---- payload scanning -------------------------------------------------
function scan(obj, depth) {
  depth = depth || 0;
  if (!obj || typeof obj !== 'object' || depth > 6) return;
  const mi = obj.match_info || obj;
  if (mi && typeof mi === 'object') {
    if (mi.selected_character) emitHero(mi.selected_character);
    Object.keys(mi).forEach((k) => { if (k.indexOf('roster') === 0) tryRoster(mi[k]); });
    if (mi.match_outcome != null) emitMatch(mi.match_outcome);
  }
  Object.keys(obj).forEach((k) => {
    if (obj[k] && typeof obj[k] === 'object') scan(obj[k], depth + 1);
  });
}

function tryRoster(v) {
  let o = v;
  if (typeof v === 'string') { try { o = JSON.parse(v); } catch (_) { return; } }
  if (!o || typeof o !== 'object') return;
  if (o.is_local === true || o.is_local === 'true') {
    if (o.character_name) emitHero({ name: o.character_name });
    if (o.kills != null || o.deaths != null || o.assists != null) {
      post({ type: 'stats', kills: +o.kills || 0, deaths: +o.deaths || 0, assists: +o.assists || 0 });
    }
  }
}

function emitHero(v) {
  let name = null;
  if (typeof v === 'string') {
    try { const p = JSON.parse(v); name = p.character_name || p.name; } catch (_) { name = v; }
  } else if (v && typeof v === 'object') {
    name = v.character_name || v.name;
  }
  if (!name) return;
  name = String(name);
  if (name !== lastHero) { lastHero = name; post({ type: 'hero', hero: name }); }
}

function emitMatch(raw) {
  const s = String(raw).toLowerCase();
  let r = null;
  if (s.indexOf('win') >= 0 || s.indexOf('victory') >= 0) r = 'victory';
  else if (s.indexOf('los') >= 0 || s.indexOf('defeat') >= 0) r = 'defeat';
  if (r && r !== lastResult) { lastResult = r; post({ type: 'match', result: r }); }
}

function scanEvent(ev) {
  if (!ev) return;
  if (ev.name === 'match_end' || ev.name === 'match_outcome') emitMatch(ev.data);
}

log('RivalsRadio Overwolf app started — waiting for Marvel Rivals');
