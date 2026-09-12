/* Application shell: sign-in gate, rail, panel routing, live alert socket. */

import { api, session, auth } from './api.js';
import { state, set, setCameras, subscribe, notify, visibleCameras } from './store.js';
import { el, fill, clear, icon, toast, $, debounce, fmtNum } from './ui.js';
import * as mapView from './map.js';
import * as wall from './wall.js';
import * as tour from './tour.js';
import * as layersPanel from './panels/layers.js';
import * as unplacedTray from './panels/unplaced.js';
import * as cameraPanel from './panels/camera.js';
import * as eventsPanel from './panels/events.js';
import * as tracePanel from './panels/trace.js';
import * as watchPanel from './panels/watchlist.js';
import * as healthPanel from './panels/health.js';
import * as registryPanel from './panels/registry.js';

/* ---------------------------------------------------------------- panels */

const PANELS = {
  cameras:  { title: 'Cameras',      icon: 'camera',
              sub: () => `${fmtNum(visibleCameras().length)} of ${fmtNum(state.cameras.length)} shown`,
              render: (host) => cameraPanel.renderList(host) },
  camera:   { title: () => state.selectedCamera || 'Camera', icon: 'camera',
              sub: () => { const c = state.camerasById.get(state.selectedCamera);
                           return c ? (c.location_name || c.name) : ''; },
              render: (host) => cameraPanel.renderDetail(host, state.selectedCamera) },
  registry: { title: 'Camera registry', icon: 'registry',
              sub: () => 'Bulk import and onboarding',
              render: (host) => registryPanel.render(host) },
  events:   { title: 'ANPR events', icon: 'events',
              sub: () => 'Every reading with its evidence',
              render: (host) => eventsPanel.render(host) },
  evidence: { title: 'Evidence', icon: 'events',
              sub: () => 'One reading, in full',
              render: (host) => eventsPanel.renderEvidence(host, state.evidence) },
  trace:    { title: 'Vehicle tracing', icon: 'trace',
              sub: () => 'Follow a vehicle across cameras',
              render: (host) => tracePanel.render(host) },
  watchlist:{ title: 'Watchlist & alerts', icon: 'watch',
              sub: () => `${state.unacknowledged} unacknowledged`,
              render: (host) => watchPanel.render(host) },
  health:   { title: 'Camera health', icon: 'health',
              sub: () => 'Measured, not reported',
              render: (host) => healthPanel.render(host) },
  audit:    { title: 'Audit trail', icon: 'audit',
              sub: () => 'Every action, attributable',
              render: (host) => renderAudit(host) },
};

/* Panels reachable from the rail, in order. Evidence and camera detail are
 * reached by clicking something rather than from the rail, so they are not
 * listed here. */
const RAIL = ['cameras', 'registry', 'events', 'trace', 'watchlist', 'health'];

/* ------------------------------------------------------------------ boot */

async function boot() {
  if (!session.token) { showGate(); return; }
  try {
    await api.stats();          // cheap probe: is the token still good?
  } catch {
    showGate();
    return;
  }
  showApp();
}

auth.addEventListener('expired', () => {
  toast('Session expired', 'Sign in again to continue.', 'warn');
  showGate();
});

/* ------------------------------------------------------------------ gate */

/* Where to go once the operator is in.
 *
 * Allowlisted rather than validated. A `next` that is merely checked for
 * "starts with /" still admits `//evil.example.com`, which browsers read as a
 * protocol-relative URL and follow off-site -- an open redirect on a sign-in
 * page, which is the one page where it is worth most to an attacker. The set of
 * places this application can send somebody after sign-in is two, so it is
 * written out rather than parsed.
 */
const NEXT_ALLOWED = new Set(['/', '/wall']);

/* What each destination is called when the gate has to name it. Kept beside the
 * allowlist so adding a page cannot add a redirect target without also giving
 * it a name -- an unnamed one would silently fall back to a bare form again. */
const NEXT_NAMES = { '/wall': 'The video wall' };

function safeNext() {
  const value = new URLSearchParams(location.search).get('next');
  return NEXT_ALLOWED.has(value) ? value : null;
}

/* Why the operator is looking at a sign-in form when they asked for the wall.
 * Without this the redirect is indistinguishable from the wall being broken. */
function gateReason(next, why) {
  const name = NEXT_NAMES[next];
  if (!name) return null;              // '/' is the ordinary case; say nothing
  if (why === 'expired') {
    return `Your session expired while you were on ${name.toLowerCase()}. Sign in `
         + 'again and you will be taken back to it.';
  }
  return `${name} needs a signed-in session. Sign in and you will be taken `
       + 'straight there.';
}

function showGate() {
  $('#shell').hidden = true;
  const gate = $('#gate');
  gate.hidden = false;

  const next = safeNext();
  const why = new URLSearchParams(location.search).get('why');
  const reason = gateReason(next, why);

  const error = el('div.gate-err');
  const form = el('form', {
    onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      const button = event.target.querySelector('button');
      button.disabled = true;
      fill(button, 'Signing in…');
      fill(error);
      try {
        await api.login(data.get('username'), data.get('password'));
        // Hand the operator back to what they actually asked for. Dropping them
        // on the map instead means they have to go find the wall link again,
        // having already told us twice where they were going.
        if (next && next !== location.pathname) { location.replace(next); return; }
        gate.hidden = true;
        history.replaceState(null, '', location.pathname);
        showApp();
      } catch (exception) {
        fill(error, exception.message);
        button.disabled = false;
        fill(button, 'Sign in');
      }
    },
  },
    el('input.field', { name: 'username', placeholder: 'Username',
      autocomplete: 'username', required: true, autofocus: true }),
    el('input.field', { name: 'password', type: 'password', placeholder: 'Password',
      autocomplete: 'current-password', required: true }),
    el('button.btn.primary', { type: 'submit' }, 'Sign in'),
    error,
  );

  fill(gate, el('div.gate-card', {},
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } },
      el('div.brand-mark', {}, icon('shield', 15)),
      el('div', {},
        el('div.brand-name', { text: 'Sentinel Nexus' }),
        el('div.brand-sub', { text: 'Command centre' }))),
    el('h1', { text: 'Sign in' }),
    el('p.sub', { text: 'Unified CCTV interoperability and intelligence' }),
    reason ? el('div.gate-note', {}, icon('alert', 13), el('span', { text: reason })) : null,
    form,
    el('div.gate-hint', {},
      'Demo accounts: ', el('code', { text: 'admin' }), ', ',
      el('code', { text: 'operator' }), ', ', el('code', { text: 'analyst' }),
      ' — password ', el('code', { text: 'sentinel-<role>' }), '. ',
      'ANALYST is read-only; OPERATOR and ADMIN may act.'),
  ));
}

/* ------------------------------------------------------------------- app */

async function showApp() {
  $('#gate').hidden = true;
  $('#shell').hidden = false;

  buildTopbar();
  buildRail();

  // The basemap configuration decides how the map is built, so it is fetched
  // before the map rather than applied to it afterwards -- changing a style's
  // sources after construction means tearing the whole style down again.
  let mapConfig = null;
  try { mapConfig = await api.mapConfig(); } catch { /* fall back to no basemap */ }

  // Dark, always. The command centre has no theme control: see the note over
  // buildRail(). Passed explicitly rather than left to default so the call site
  // says which palette the map is on.
  mapView.init($('#map'), mapConfig, 'dark');
  layersPanel.mount($('#layers'));
  unplacedTray.mount($('#unplaced'));
  wall.mount($('#dock'));

  subscribe('panel', renderPanel);
  subscribe(['selectedCamera', 'evidence'], renderPanel);
  subscribe('dockOpen', () => $('#dock').classList.toggle('open', state.dockOpen));
  subscribe(['cameras', 'filters'], () => {
    if (state.panel === 'cameras') renderPanel();
  });
  subscribe('unacknowledged', updateAlertBadge);

  await Promise.all([loadCameras(), layersPanel.load(), loadStats()]);

  // With no basemap at all, the cached OSM geography *is* the map, so it is
  // switched on rather than left for the operator to discover. With a basemap
  // those layers are several megabytes that duplicate what is already drawn,
  // so they stay off until asked for -- unless the basemap turns out to be
  // unreachable, which on an isolated network is the expected case.
  const enableCachedGeography = () => layersPanel.enableBaseGeography(
    mapConfig?.base_geography || ['districts', 'highways', 'trunk', 'city']);

  if (mapView.hasBasemap(mapConfig)) mapView.whenBasemapLost(enableCachedGeography);
  else enableCachedGeography();

  connectAlerts();
  pollStats();

  // Deep link from the video wall: /?camera=CAM-04 opens that camera here.
  // Selecting before the cameras have loaded would centre on nothing, so this
  // runs after loadCameras() above.
  const wanted = new URLSearchParams(location.search).get('camera');
  if (wanted && state.camerasById.has(wanted)) {
    mapView.selectCamera(wanted);
    history.replaceState(null, '', location.pathname);
    return;                    // never open the tour over a requested camera
  }

  if (tour.shouldAutoStart()) setTimeout(() => tour.open(), 900);
}

function buildTopbar() {
  const search = el('input', {
    placeholder: 'Search cameras, or type a plate to trace',
    'aria-label': 'Search',
    oninput: debounce((event) => {
      state.filters.query = event.target.value;
      notify('filters');
    }, 200),
    onkeydown: (event) => {
      if (event.key !== 'Enter') return;
      const value = event.target.value.trim();
      if (!value) return;
      // A registration goes straight to a trace; anything else filters the map.
      if (/^[A-Z]{2}\s?[0-9]{1,2}\s?[A-Z]{0,3}\s?[0-9]{1,4}$/i.test(value)) {
        tracePanel.openTrace(value.toUpperCase().replace(/\s+/g, ''));
      } else {
        set({ panel: 'cameras' });
      }
    },
  });

  window.addEventListener('keydown', (event) => {
    if (event.key === '/' && document.activeElement?.tagName !== 'INPUT') {
      event.preventDefault();
      search.focus();
    }
  });

  fill($('#topbar'),
    el('div.brand', {},
      el('div.brand-mark', {}, icon('shield', 15)),
      el('div', {},
        el('div.brand-name', { text: 'Sentinel Nexus' }),
        el('div.brand-sub', { text: 'Command centre' }))),
    el('div.vr'),
    el('div#omni', {},
      el('span.omni-icon', {}, icon('search', 14)),
      search,
      el('kbd', { text: '/' })),

    el('div.topbar-right', {},
      el('span#conn.tag', {}, el('span.dot', {}), 'connecting'),
      el('span#model.tag', { title: 'Detection model in use' }),
      el('button.btn.sm', { onclick: () => tour.open(), title: 'Replay the walkthrough' },
        icon('guide', 13), 'Guide'),
      el('div.vr'),
      el('div', { style: { textAlign: 'right', lineHeight: '1.25' } },
        el('div', { style: { fontSize: '12px', fontWeight: '600' },
          text: session.user?.full_name || session.user?.username || '' }),
        el('div.faint', { style: { fontSize: '10px' }, text: session.user?.role || '' })),
      el('button.icon-btn', {
        title: 'Sign out',
        onclick: () => { session.clear(); location.reload(); },
      }, icon('logout', 15)),
    ),
  );
}

/* A rail button, with its name on hover.
 *
 * `data-label` drives the CSS flyout; `aria-label` carries the same string for
 * assistive technology. There is deliberately no `title` -- the native tooltip
 * would arrive a second after the flyout and render a second copy of the name
 * on top of it, in the operating system's style rather than this one.
 */
function railButton(label, iconName, { panel = null, onclick = null, href = null } = {}) {
  const spec = {
    'aria-label': label,
    dataset: { label, ...(panel ? { panel } : {}) },
  };
  if (href) return el('a.rail-btn', { ...spec, href }, icon(iconName, 18));
  return el('button.rail-btn', {
    ...spec,
    onclick: onclick || (() => set({ panel: state.panel === panel ? null : panel })),
  }, icon(iconName, 18));
}

/* The rail.
 *
 * There is deliberately **no theme control here.** The video wall has one --
 * it is read on a laptop, projected in briefings and screenshotted into
 * reports, and a dark page is miserable for all three. The command centre is
 * not that: it is a map-first surface watched for whole shifts in a room kept
 * dim so camera feeds stay readable, and a light map next to a night-time feed
 * destroys the adaptation an operator needs to see the feed. So this page is
 * dark, full stop, and does not read the stored theme preference at all -- an
 * operator who sets the wall to light still gets a dark command centre, which
 * is the intended result rather than an inconsistency.
 *
 * The light map palette in basemap.js and `mapView.restyle()` are what a theme
 * control here would have driven. Nothing calls them now; they are kept because
 * they are working, tested, and cheap to leave, and reinstating the control is
 * a matter of putting this button back.
 */
function buildRail() {
  const rail = $('#rail');
  fill(rail, ...RAIL.map((key) => {
    const spec = PANELS[key];
    const label = typeof spec.title === 'function' ? spec.title() : spec.title;
    const button = railButton(label, spec.icon, { panel: key });
    if (key === 'watchlist') button.append(el('span.badge', { id: 'alert-badge', hidden: true }));
    return button;
  }),
    el('div.rail-spacer'),
    session.isAdmin ? railButton('Audit trail', 'audit', { panel: 'audit' }) : null,
    // Two different things, deliberately kept apart. The dock is a few tiles
    // beside the map for the camera you are working; the wall is the whole
    // estate on its own page, which an operator typically puts on a second
    // display. Collapsing them into one control is what made the wall a
    // 210 px strip in the first place.
    railButton('Preview dock', 'dock', { onclick: () => set({ dockOpen: !state.dockOpen }) }),
    railButton('Video wall', 'wall', { href: '/wall' }),
  );
}

function renderPanel() {
  const panel = $('#panel');
  const key = state.panel;

  for (const button of $('#rail').children) {
    if (button.dataset?.panel) {
      button.classList.toggle('active', button.dataset.panel === key);
    }
  }

  if (!key || !PANELS[key]) { panel.classList.remove('open'); return; }

  const spec = PANELS[key];
  const title = typeof spec.title === 'function' ? spec.title() : spec.title;
  const sub = typeof spec.sub === 'function' ? spec.sub() : spec.sub;
  const body = el('div.panel-body');

  fill(panel,
    el('div.panel-head', {},
      el('div', { style: { flex: '1', minWidth: '0' } },
        el('div.panel-title', { text: title }),
        sub ? el('div.panel-sub', { text: sub }) : null),
      el('button.icon-btn', { title: 'Close', onclick: () => set({ panel: null }) },
        icon('close', 15))),
    body,
  );

  panel.classList.toggle('wide', key === 'registry');
  panel.classList.add('open');
  spec.render(body);
}

/* ------------------------------------------------------------------ data */

export async function loadCameras() {
  try {
    const [cameras, facets] = await Promise.all([api.cameras(), api.facets()]);
    setCameras(cameras);
    set({ facets });
  } catch (error) {
    toast('Could not load cameras', error.message, 'crit');
  }
}

async function loadStats() {
  try {
    const stats = await api.stats();
    set({ stats });
    const chip = $('#model');
    const vehicle = stats.models?.vehicle;
    if (chip && vehicle) {
      fill(chip, `${vehicle.model} · ${vehicle.runtime === 'onnxruntime' ? 'ONNX' : 'DNN'}`);
      chip.classList.toggle('warn', vehicle.runtime !== 'onnxruntime');
      chip.title = `Detector: ${vehicle.model} at ${vehicle.input}px via `
                 + `${vehicle.runtime}. Plate localisation: ${stats.models.plate.backend}.`;
    }
  } catch { /* the chip stays empty; nothing else depends on it */ }
}

function pollStats() {
  // Slow on purpose. This polls a database aggregate, and the things that
  // actually change second to second arrive over the WebSocket instead.
  setInterval(loadStats, 30000);
}

/* ---------------------------------------------------------- live alerts */

let socket = null;
let backoff = 1000;

function connectAlerts() {
  socket = new WebSocket(api.alertSocketUrl());

  socket.addEventListener('open', () => {
    backoff = 1000;
    set({ connected: true });
    setConnChip(true);
  });

  socket.addEventListener('message', (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'connected') return;
    handleAlert(message);
  });

  socket.addEventListener('close', () => {
    set({ connected: false });
    setConnChip(false);
    // Exponential backoff to 30 s, matching the stream worker's policy. A
    // tight reconnect loop against a restarting server is how you turn one
    // outage into two.
    setTimeout(connectAlerts, backoff);
    backoff = Math.min(backoff * 2, 30000);
  });

  socket.addEventListener('error', () => socket.close());
}

function setConnChip(connected) {
  const chip = $('#conn');
  if (!chip) return;
  chip.className = 'tag ' + (connected ? 'ok' : 'warn');
  fill(chip, el('span.dot' + (connected ? '.live' : ''), {}),
       connected ? 'live' : 'reconnecting');
}

function handleAlert(message) {
  const alert = message.alert || message;
  if (!alert.camera_id) return;

  set({ unacknowledged: state.unacknowledged + 1 });
  mapView.flashCamera(alert.camera_id);

  toast(`${alert.severity || 'ALERT'} — ${alert.plate || 'watchlist hit'}`,
    `${alert.camera_id}${alert.location ? ' · ' + alert.location : ''}`
    + `${alert.category ? ' · ' + alert.category : ''}`,
    'crit', 12000);

  if (state.panel === 'watchlist') renderPanel();
}

function updateAlertBadge() {
  const badge = $('#alert-badge');
  if (!badge) return;
  badge.hidden = state.unacknowledged === 0;
  badge.textContent = String(Math.min(state.unacknowledged, 99));
}

/* ----------------------------------------------------------------- audit */

function renderAudit(host) {
  fill(host, el('div.empty', { text: 'Loading…' }));
  api.audit(300).then((entries) => {
    if (!entries.length) { fill(host, el('div.empty', { text: 'No audit entries.' })); return; }
    fill(host, ...entries.map((entry) => el('div.row', {},
      el('div.row-main', {},
        el('div.row-title', { text: entry.action }),
        el('div.row-sub', {},
          el('span.mono', { text: entry.username }),
          entry.entity ? ` · ${entry.entity} ${entry.entity_id || ''}` : '',
          entry.detail ? ` · ${entry.detail}` : '')),
      el('div.row-side.faint.mono', { style: { fontSize: '10px' },
        text: new Date(entry.ts).toLocaleString('en-GB', { hour12: false }) }),
    )));
  }).catch((error) => {
    fill(host, el('div.empty', { text: error.message }));
  });
}

boot();
