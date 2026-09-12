/* The video wall.
 *
 * A page rather than a panel, because it is a different posture. The command
 * centre is for investigating one thing; the wall is for watching the estate.
 * Those want opposite layouts, and cramming both into the map's bottom dock
 * meant the wall got 210 px of a screen and a cap of eight cameras.
 *
 * Four rules, all of them from how this grid actually behaves rather than from
 * a preference about video walls:
 *
 *  * **Every camera is present; none of them is streaming.** All 31 federated
 *    cameras are on the wall from the moment it opens, as dark cards. A camera
 *    starts decoding only when an operator clicks it. Each viewer gets its own
 *    stream copy off this grid, so auto-playing 31 feeds would not produce a
 *    wall -- it would produce 31 cameras that all fail to open.
 *
 *  * **Only live tiles claim to be live.** A card that is not streaming says
 *    "idle" and shows no video furniture at all. Opens here have been measured
 *    from 1.8 s to 275 s, so a black rectangle is ambiguous between "slow" and
 *    "dead", and every tile states which of those it believes it is.
 *
 *  * **Concurrency is a property of the grid, not a UI preference.** The server
 *    holds a semaphore over open captures because at ten parallel opens five
 *    cameras returned no frames at all, where the same five recovered when
 *    opened one at a time. The wall reads that number from /api/config/live and
 *    shows the operator why a tile is queued instead of letting them guess.
 *
 *  * **Stopping a tile actually stops the stream.** An MJPEG response stays
 *    open for as long as the <img> holds it, so deactivating clears src before
 *    dropping the node. Leaving it to garbage collection holds a camera slot
 *    open against the cap for minutes.
 */

import { api, session, auth } from './api.js';
import { el, fill, icon, toast, $, $$, fmtNum, statusClass, debounce } from './ui.js';
import { theme, THEMES } from './theme.js';

/* How long a tile waits before it stops claiming to be connecting. Set from the
 * measured distribution of open times on this grid rather than a round number:
 * most successful opens land well inside this, and past it the honest thing to
 * say is "this has not opened", not to spin forever. */
const CONNECT_GRACE_MS = 20000;

/* Layout presets. "Auto" fills by tile width; the numbered ones pin a column
 * count, which is what a fixed wall display wants -- an operator who has
 * arranged nine cameras in a 3x3 does not want the tenth to reflow all of them. */
const DENSITIES = [
  { key: 'auto', label: 'Auto', min: '340px' },
  { key: '2', label: '2', cols: 2 },
  { key: '3', label: '3', cols: 3 },
  { key: '4', label: '4', cols: 4 },
  { key: '5', label: '5', cols: 5 },
];

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'live', label: 'Live' },
  { key: 'online', label: 'Online' },
  { key: 'anpr', label: 'ANPR-capable' },
];

const STORE_KEY = 'sentinel.wall';

const wall = {
  cameras: [],
  active: new Map(),      // camera_id -> { tile, image, timer, state }
  filter: 'all',
  density: 'auto',
  detect: true,
  query: '',
  limit: 4,               // replaced by the server's real cap on boot
  sessionSeconds: 900,
};

/* --------------------------------------------------------------- persistence
 * Which cameras were up, and how they were arranged, is the operator's working
 * set. Losing it to an accidental refresh mid-shift is the kind of small
 * cruelty that makes people stop trusting a tool. */

function remember() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      active: [...wall.active.keys()],
      density: wall.density,
      filter: wall.filter,
      detect: wall.detect,
    }));
  } catch { /* private mode: the arrangement simply does not survive a reload */ }
}

function recall() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); }
  catch { return null; }
}

/* ------------------------------------------------------------------- boot */

async function boot() {
  if (!session.token) { toSignIn('signin'); return; }

  let config = null;
  try {
    const [cameras, live] = await Promise.all([api.cameras(), api.liveConfig()]);
    wall.cameras = cameras;
    config = live;
  } catch (error) {
    if (error.status === 401) { toSignIn('expired'); return; }
    fill($('#wall-grid'), el('div.empty', {},
      el('div', { style: { color: 'var(--critical)' }, text: 'Could not load the estate' }),
      el('div', { style: { marginTop: '6px' }, text: error.message })));
    return;
  }

  wall.limit = config?.max_concurrent_streams || 4;
  wall.sessionSeconds = config?.session_seconds || 900;

  const saved = recall();
  if (saved) {
    wall.density = saved.density || wall.density;
    wall.filter = saved.filter || wall.filter;
    wall.detect = saved.detect ?? wall.detect;
  }

  buildChrome();
  render();

  // Restore the previous working set, but never more than the grid will carry.
  // Restoring twelve tiles into a four-slot cap would reproduce exactly the
  // failure the cap exists to prevent.
  for (const id of (saved?.active || []).slice(0, wall.limit)) {
    if (wall.cameras.some((c) => c.camera_id === id)) activate(id, { quiet: true });
  }
  updateCounts();
}

/* Leaving the wall because there is no usable session.
 *
 * This used to be a bare `location.href = '/'`, and that was the single worst
 * thing about this page. An operator clicked "Video wall", landed silently back
 * on the map, and had no way to tell a signed-out session from a broken wall --
 * so it read as a broken wall. It is not enough to be correct here; the bounce
 * has to explain itself.
 *
 * `next` carries the intent across the redirect so the gate can say what it is
 * gating and hand the operator back to the wall once they are in, rather than
 * dropping them on the map to find the link again. The command centre
 * allowlists the value rather than trusting it -- see `safeNext` in app.js.
 */
function toSignIn(why = 'expired') {
  const params = new URLSearchParams({ next: '/wall', why });
  location.replace('/?' + params);
}

auth.addEventListener('expired', () => {
  stopAll({ quiet: true });
  toast('Session expired', 'Returning to sign-in.', 'warn');
  setTimeout(() => toSignIn('expired'), 1200);
});

/* ----------------------------------------------------------------- chrome */

function buildChrome() {
  fill($('#wall-top'),
    el('a.brand', { href: '/', title: 'Back to the command centre' },
      el('div.brand-mark', {}, icon('shield', 15)),
      el('div', {},
        el('div.brand-name', { text: 'Sentinel Nexus' }),
        el('div.brand-sub', { text: 'Video wall' }))),

    el('div.vr'),

    el('div#wall-search', {},
      el('span.omni-icon', {}, icon('search', 14)),
      el('input', {
        placeholder: 'Filter by camera, location or district',
        'aria-label': 'Filter cameras',
        oninput: debounce((event) => { wall.query = event.target.value; render(); }, 160),
      })),

    el('div.wall-top-right', {},
      el('span#slot-chip.tag', { title: 'Open stream slots on this grid' }),
      el('button.btn.sm', {
        title: 'Open the highest-scoring ANPR cameras until the wall is full',
        onclick: fillWithBest,
      }, icon('plus', 12), 'Auto-fill'),
      el('button.btn.sm.danger', { onclick: () => stopAll() },
        icon('pause', 12), 'Stop all'),
      el('div.vr'),
      el('a.btn.sm.ghost', { href: '/', title: 'Back to the map' },
        icon('pin', 12), 'Command centre'),
      el('button.icon-btn', {
        title: 'Sign out',
        onclick: () => { stopAll({ quiet: true }); session.clear(); location.href = '/'; },
      }, icon('logout', 15)),
    ),
  );

  fill($('#wall-bar'),
    segmented('Show', FILTERS, () => wall.filter,
      (key) => { wall.filter = key; remember(); render(); }),
    el('div.vr'),
    segmented('Columns', DENSITIES, () => wall.density,
      (key) => { wall.density = key; remember(); applyDensity(); }),
    el('div.vr'),
    /* Theme sits with Columns rather than in the header because it is the same
     * kind of thing: how this operator wants the wall drawn, not what the wall
     * is showing. Note what it does *not* change -- the tiles keep their dark
     * ground under both themes, because the pictures are the point and a
     * night-time feed in a white frame is unreadable. See --video-ground. */
    segmented('Theme', THEMES, () => theme.preference,
      (key) => { theme.set(key); }),
    el('div.vr'),
    el('label.wall-check', {},
      el('input', {
        type: 'checkbox', checked: wall.detect,
        onchange: (event) => {
          wall.detect = event.target.checked;
          remember();
          // Detection is baked into the stream URL, so every live tile has to
          // be reopened. Say so rather than silently paying the reconnect.
          const open = [...wall.active.keys()];
          if (open.length) {
            stopAll({ quiet: true });
            open.forEach((id) => activate(id, { quiet: true }));
            toast('Reopening tiles',
              `Detection ${wall.detect ? 'on' : 'off'} changes the stream, so `
              + `${open.length} tile(s) reconnected.`, 'info');
          }
          updateCounts();
        },
      }),
      el('span', { text: 'Draw detections' })),
    el('span.wall-hint.faint', {
      text: 'Detection costs about 230 ms a frame. Off gives smoother video.' }),
  );

  fill($('#wall-foot'),
    el('span#foot-count.faint.mono'),
    el('span.faint', {
      text: 'Click a card to start its feed. Opens on this grid have been '
          + 'measured from 1.8 s to 275 s.' }),
  );

  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeFocus();
  });
  window.addEventListener('beforeunload', () => stopAll({ quiet: true }));

  applyDensity();
}

function segmented(label, options, current, onpick) {
  return el('div.seg-group', {},
    el('span.seg-label', { text: label }),
    el('div.seg', {}, ...options.map((option) => el(
      'button.seg-btn' + (current() === option.key ? '.on' : ''), {
        dataset: { seg: label, key: option.key },
        onclick: (event) => {
          for (const sibling of event.target.parentElement.children) {
            sibling.classList.toggle('on', sibling === event.target);
          }
          onpick(option.key);
        },
        title: option.hint || '',
      }, option.label))),
  );
}

function applyDensity() {
  const spec = DENSITIES.find((d) => d.key === wall.density) || DENSITIES[0];
  const grid = $('#wall-grid');
  grid.style.gridTemplateColumns = spec.cols
    ? `repeat(${spec.cols}, minmax(0, 1fr))`
    : `repeat(auto-fill, minmax(${spec.min}, 1fr))`;
}

/* ------------------------------------------------------------------- list */

function shown() {
  const needle = wall.query.trim().toLowerCase();
  return wall.cameras.filter((cam) => {
    if (wall.filter === 'live' && !wall.active.has(cam.camera_id)) return false;
    if (wall.filter === 'online' && cam.status !== 'ONLINE') return false;
    if (wall.filter === 'anpr' && !cam.ai_enabled) return false;
    if (needle) {
      const hay = `${cam.camera_id} ${cam.name} ${cam.location_name} ${cam.district} `
                + `${cam.department}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

/** Rebuild the grid without disturbing any tile that is already streaming.
 *
 *  Re-creating a live <img> would tear down its MJPEG connection and pay this
 *  grid's 10-90 s reconnect cost for nothing, so an active card is moved, never
 *  rebuilt. This is the whole reason the grid is reconciled rather than
 *  re-rendered.
 */
function render() {
  const grid = $('#wall-grid');
  const cameras = shown();

  if (!cameras.length) {
    // Detach live tiles before clearing, so a filter change cannot silently
    // kill a stream the operator is watching.
    for (const entry of wall.active.values()) entry.tile.remove();
    fill(grid, el('div.wall-empty', {},
      el('div', { text: 'No camera matches this filter.' }),
      el('div.faint', { style: { marginTop: '6px' },
        text: 'Live tiles are still running and will reappear when the filter widens.' })));
    updateCounts();
    return;
  }

  const existing = new Map($$('.wcard', grid).map((node) => [node.dataset.camera, node]));
  const tiles = cameras.map((cam) => existing.get(cam.camera_id) || card(cam));
  for (const [id, node] of existing) {
    if (!cameras.some((c) => c.camera_id === id)) node.remove();
  }
  fill(grid, ...tiles);
  updateCounts();
}

/* ------------------------------------------------------------------ tiles */

/** The dark state of a tile: this camera's last decoded frame, if the platform
 *  has one, with the play affordance over it.
 *
 *  A still rather than a black rectangle, because thirty-one black rectangles
 *  tell an operator nothing about which camera to open, and the scene is the
 *  only thing that does -- a junction, a flyover, a toll plaza. The trade is
 *  that a still could be mistaken for a frozen feed, so it never is: the frame
 *  is dimmed well below live brightness, it carries a LAST FRAME badge in the
 *  same corner where a live tile carries its LIVE badge, and only a streaming
 *  tile gets the green dot. Nothing here is passed off as live.
 *
 *  404 is the ordinary case for a camera that has never delivered a decodable
 *  frame -- CAM-08 and CAM-10 on this grid -- and it falls back to the plain
 *  idle state rather than a broken-image icon.
 */
function idleStage(cam) {
  const still = el('img.wcard-still', {
    src: api.thumbnailUrl(cam.camera_id),
    alt: '',
    loading: 'lazy',
    onload: (event) => event.target.classList.add('shown'),
    onerror: (event) => event.target.remove(),
  });

  return el('div.wcard-idle', {},
    still,
    el('div.wcard-idle-inner', {},
      el('div.wcard-play', {}, icon('play', 18)),
      el('div.wcard-idle-text', { text: 'Click to open the feed' })),
    el('div.wcard-still-badge', { text: 'LAST FRAME' }),
    el('div.wcard-actions', {},
      el('button.wcard-action.wcard-expand', {
        title: 'Open in full screen focus view',
        onclick: (event) => {
          event.stopPropagation();
          activateAndFocus(cam.camera_id);
        },
      }, icon('expand', 13)),
    ),
  );
}

function card(cam) {
  const id = cam.camera_id;
  const name = cam.location_name || cam.name || id;
  const placed = cam.latitude !== null && cam.location_accuracy !== 'UNKNOWN';

  const stage = el('div.wcard-stage', {}, idleStage(cam));

  const tile = el('div.wcard', {
    dataset: { camera: id },
    onclick: (event) => {
      // The header carries its own controls; only the stage toggles the feed.
      if (event.target.closest('button.wcard-action') || event.target.closest('button.icon-btn')) return;
      wall.active.has(id) ? deactivate(id) : activate(id);
    },
    ondblclick: (event) => {
      if (event.target.closest('button.wcard-action') || event.target.closest('button.icon-btn')) return;
      if (wall.active.has(id)) openFocus(id);
      else activateAndFocus(id);
    },
  },
    el('div.wcard-head', {},
      el('span.wdot', { dataset: { status: (cam.status || 'unknown').toLowerCase() } }),
      el('span.wcard-id.mono', { text: id }),
      el('span.wcard-name', { text: name, title: name }),
      el('div.wcard-badges', {},
        cam.ai_enabled ? el('span.tag.info', { title: `Plate score ${cam.plate_score}/100`,
          text: 'ANPR' }) : null,
        placed ? null : el('span.tag.warn', { title: 'No trustworthy position; not on the map',
          text: 'NO POS' }),
        el('span.tag.' + statusClass(cam.status), { text: cam.status || 'UNKNOWN' })),
      el('button.wcard-action.icon-btn', {
        title: 'Show this camera on the map',
        onclick: () => { location.href = `/?camera=${encodeURIComponent(id)}`; },
      }, icon('target', 13)),
    ),
    stage,
  );

  return tile;
}

function stageOf(id) {
  return document.querySelector(`.wcard[data-camera="${CSS.escape(id)}"] .wcard-stage`);
}

/** Start one camera's feed. */
function activate(id, { quiet = false } = {}) {
  if (wall.active.has(id)) return;

  if (wall.active.size >= wall.limit) {
    toast('Every stream slot is in use',
      `This grid is capped at ${wall.limit} concurrent streams — it measurably `
      + 'degrades above that. Stop a tile to open another.', 'warn', 8000);
    return;
  }

  const stage = stageOf(id);
  if (!stage) return;
  const tile = stage.closest('.wcard');

  const status = el('div.wcard-state', {},
    el('div.spinner'),
    el('div', { style: { marginTop: '10px' }, text: `Opening ${id}…` }));

  const image = el('img.wcard-video', {
    alt: `Live feed from ${id}`,
    src: api.liveUrl(id, wall.detect),
  });

  // The MJPEG stream sends a status frame before any video, so 'load' fires as
  // soon as anything arrives -- including the placeholder the server draws
  // while the capture is still opening. That is still the honest moment to
  // reveal the tile: something is coming down the wire.
  image.addEventListener('load', () => {
    tile.classList.add('painted');
    status.remove();
  }, { once: true });

  image.addEventListener('error', () => {
    tile.classList.add('failed');
    fill(status,
      el('div.wcard-state-title', { style: { color: 'var(--warn)' },
        text: 'Feed did not open' }),
      el('div', { style: { marginTop: '6px' },
        text: 'The stream is not being passed off as live. Click to retry.' }));
  });

  const timer = setTimeout(() => {
    if (!status.isConnected) return;
    fill(status,
      el('div.spinner'),
      el('div.wcard-state-title', { style: { marginTop: '10px', color: 'var(--warn)' },
        text: 'Still connecting' }),
      el('div', { style: { marginTop: '4px' },
        text: 'Opens on this grid have been measured up to 275 s. The tile fills '
            + 'on its own if the camera answers.' }));
  }, CONNECT_GRACE_MS);

  fill(stage, image, status,
    el('div.wcard-live', {},
      el('span.dot.live'), 'LIVE',
      wall.detect ? el('span.wcard-live-sub', { text: 'detections on' }) : null),
    el('div.wcard-actions', {},
      el('button.wcard-action.wcard-expand', {
        title: 'Full screen focus view',
        onclick: (event) => {
          event.stopPropagation();
          openFocus(id);
        },
      }, icon('expand', 13)),
      el('button.wcard-action.wcard-stop', {
        title: 'Stop this feed',
        onclick: (event) => {
          event.stopPropagation();
          deactivate(id);
        },
      }, icon('close', 12)),
    ),
  );

  tile.classList.add('live');
  wall.active.set(id, { tile, image, timer });
  remember();
  updateCounts();
  if (!quiet && wall.active.size === wall.limit) {
    toast('Wall is full', `All ${wall.limit} stream slots are in use.`, 'info');
  }
}

/** Open camera in full screen focus mode directly. */
function activateAndFocus(id) {
  if (!wall.active.has(id)) {
    activate(id, { quiet: true });
  }
  openFocus(id);
}

/** Stop one camera's feed and give its slot back.
 *
 *  Clearing src first matters: an MJPEG response stays open for as long as the
 *  element holds it, so dropping the node and trusting garbage collection would
 *  keep a slot occupied against the server's cap for minutes.
 */
function deactivate(id, { quiet = false } = {}) {
  const entry = wall.active.get(id);
  if (!entry) return;
  clearTimeout(entry.timer);
  entry.image.src = '';
  entry.image.remove();
  wall.active.delete(id);

  entry.tile.classList.remove('live', 'painted', 'failed');
  const stage = entry.tile.querySelector('.wcard-stage');
  const cam = wall.cameras.find((c) => c.camera_id === id);
  if (stage && cam) fill(stage, idleStage(cam));

  remember();
  updateCounts();
  if (!quiet && wall.filter === 'live') render();
}

function stopAll({ quiet = false } = {}) {
  for (const id of [...wall.active.keys()]) deactivate(id, { quiet: true });
  if (!quiet) {
    toast('Wall stopped', 'Every stream slot has been released.', 'ok');
    if (wall.filter === 'live') render();
  }
}

/** Open the best ANPR cameras until the slots run out.
 *
 *  Ranked by ANPR capability and then by plate score. A camera that reads
 *  plates well but shares no recording window with any other camera cannot
 *  contribute to a cross-camera trace, which is why capability is ranked ahead
 *  of raw score.
 */
function fillWithBest() {
  const room = wall.limit - wall.active.size;
  if (room <= 0) {
    toast('Wall is full', `Stop a tile first — the cap is ${wall.limit}.`, 'warn');
    return;
  }
  const candidates = wall.cameras
    .filter((c) => c.status === 'ONLINE' && !wall.active.has(c.camera_id))
    .sort((a, b) => (b.ai_enabled - a.ai_enabled) || (b.plate_score - a.plate_score))
    .slice(0, room);

  if (!candidates.length) {
    toast('Nothing to add', 'No further online cameras are available.', 'warn');
    return;
  }
  candidates.forEach((c) => activate(c.camera_id, { quiet: true }));
  updateCounts();
}

/* ------------------------------------------------------------------ focus */

/** One tile, full screen. Reuses the existing <img> rather than opening a
 *  second stream: a focus view that quietly doubled a camera's load would eat
 *  two slots for one picture. */
function openFocus(id) {
  const entry = wall.active.get(id);
  if (!entry) return;
  const cam = wall.cameras.find((c) => c.camera_id === id);
  const host = $('#focus');

  const holder = el('div.focus-video');
  
  const spinner = el('div.wcard-state', {},
    el('div.spinner'),
    el('div', { style: { marginTop: '10px' }, text: `Loading full-screen feed for ${id}…` }));

  const focusImg = el('img.focus-live-video', {
    alt: `Live focus feed from ${id}`,
    src: api.liveUrl(id, wall.detect),
    onload: () => spinner.remove(),
    onerror: () => {
      fill(spinner,
        el('div.wcard-state-title', { style: { color: 'var(--warn)' }, text: 'Feed did not open' }),
        el('div', { style: { marginTop: '6px' }, text: 'Could not connect to live full-screen stream.' }));
    }
  });

  holder.append(spinner, focusImg);

  fill(host,
    el('div.focus-scrim', { onclick: closeFocus }),
    el('div.focus-card', {},
      el('div.focus-head', {},
        el('span.dot.live'),
        el('span.mono', { text: id }),
        el('span.focus-name', { text: cam?.location_name || cam?.name || id }),
        el('button.icon-btn', { style: { marginLeft: 'auto' },
          title: 'Close (Esc)', onclick: closeFocus }, icon('close', 15))),
      holder),
  );
  host.hidden = false;
  host.dataset.camera = id;
}

function closeFocus() {
  const host = $('#focus');
  if (host.hidden) return;
  
  const focusImg = host.querySelector('.focus-live-video');
  if (focusImg) {
    focusImg.src = '';
    focusImg.remove();
  }

  host.hidden = true;
  fill(host);
  delete host.dataset.camera;
}

/* ----------------------------------------------------------------- counts */

function updateCounts() {
  const chip = $('#slot-chip');
  if (chip) {
    const used = wall.active.size;
    fill(chip, el('span.dot' + (used ? '.live' : ''), {}),
      `${used} / ${wall.limit} streaming`);
    chip.className = 'tag ' + (used >= wall.limit ? 'warn' : used ? 'ok' : '');
    chip.title = `This grid is capped at ${wall.limit} concurrent streams `
               + `(profile: balanced). Each viewer gets its own stream copy, and `
               + `the grid degrades measurably above the cap.`;
  }
  const foot = $('#foot-count');
  if (foot) {
    foot.textContent = `${fmtNum(shown().length)} of ${fmtNum(wall.cameras.length)} `
                     + `cameras shown · ${wall.active.size} live`;
  }
}

boot();
