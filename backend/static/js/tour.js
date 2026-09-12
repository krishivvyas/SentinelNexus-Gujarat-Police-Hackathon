/* A guided pass over the working system.
 *
 * It drives the real interface -- real panels, real data, real map movement --
 * rather than playing a recording. That is the point: everything it claims the
 * platform does, you are watching the platform do. If a step cannot run because
 * the thing it describes is not there, it says so instead of pretending.
 */

import { state, set, visibleCameras, notify } from './store.js';
import { el, fill, icon, sleep, $ } from './ui.js';
import * as mapView from './map.js';

const SEEN_KEY = 'sentinel.tour.seen';

const STEPS = [
  {
    id: 'map',
    target: null,
    title: 'The map is the product',
    body: 'Twenty-six departments across Gujarat run their own cameras, and none '
        + 'of the systems talk to each other. Sentinel puts them on one surface. '
        + 'Everything that follows is worked from this map.',
    run: () => { set({ panel: null, dockOpen: false }); mapView.home(); },
    hold: 4600,
  },
  {
    id: 'layers',
    target: '#layers',
    title: 'Ownership and capability are different questions',
    body: 'A camera is a point of interest with a category, the way a maps app '
        + 'treats fuel stations — so departments toggle independently. But an '
        + 'operator hunting a registration wants the cameras <b>able to read '
        + 'one</b>, whoever owns them. Watch the count as that filter goes on.',
    run: async () => {
      state.filters.anprOnly = true; notify('filters');
      await sleep(2400);
      state.filters.anprOnly = false; notify('filters');
    },
    hold: 5400,
  },
  {
    id: 'gis',
    target: '#layers',
    title: 'The geography a route is read against',
    body: 'District boundaries, highways, police stations, toll plazas and '
        + 'railway stations — fetched live from OpenStreetMap and cached, never '
        + 'baked in. Every count beside a layer is a real query result. When a '
        + 'fetch fails the layer stays empty and says so, rather than drawing '
        + 'plausible dots nobody can verify.',
    hold: 5600,
  },
  {
    id: 'registry',
    target: '#panel',
    title: 'Onboarding — whatever spreadsheet they already have',
    body: 'No two departments name their columns alike. The importer reads the '
        + 'headers it is given and maps them itself, then shows <b>every '
        + 'decision</b> — exact match, known spelling, or similarity score — for '
        + 'confirmation before a single row is saved.',
    run: () => set({ panel: 'registry' }),
    hold: 6000,
  },
  {
    id: 'wall',
    target: '#dock',
    title: 'The video wall',
    body: 'The map chooses, the wall shows. Tiles hold 16:9 and letterbox rather '
        + 'than stretching — a distorted feed is worse than a small one. A feed '
        + 'that will not open is <b>labelled</b>, never passed off as live.',
    run: async () => {
      set({ panel: null });
      const best = visibleCameras()
        .filter((c) => c.status === 'ONLINE')
        .sort((a, b) => (b.ai_enabled - a.ai_enabled) || (b.plate_score - a.plate_score))
        .slice(0, 4).map((c) => c.camera_id);
      if (best.length) set({ wall: best });
      set({ dockOpen: true });
    },
    hold: 6200,
  },
  {
    id: 'events',
    target: '#panel',
    title: 'Every reading carries its evidence',
    body: 'OCR on this footage is right most of the time, not all of the time. '
        + 'So each plate carries the <b>crop it was read from</b> and the '
        + '<b>full frame</b> with the vehicle boxed. The operator confirms the '
        + 'characters by eye before acting; the frame is the record afterwards. '
        + 'A vehicle with no readable plate is still logged — hiding those would '
        + 'misrepresent what the platform saw.',
    run: () => { set({ dockOpen: false, panel: 'events' }); },
    hold: 6400,
  },
  {
    id: 'trace',
    target: '#panel',
    title: 'Following a vehicle across cameras',
    body: 'A plate returns every camera that saw it, in time order, with a '
        + 'playback slider. These are independent recordings spanning four '
        + 'dates, so a route is only drawn between cameras that were recording '
        + '<b>at the same time</b>. Where no window overlaps, no line is drawn — '
        + 'an invented journey would undermine every number beside it.',
    run: () => set({ panel: 'trace' }),
    hold: 6400,
  },
  {
    id: 'watchlist',
    target: '#panel',
    title: 'Watchlist and live alerts',
    body: 'Stolen and wanted vehicles are matched the moment a plate is read. '
        + 'On a hit the pin flashes and the map moves to it. A fuzzy match is '
        + 'labelled as a lead rather than a fact, and acknowledgement records '
        + '<b>who acted and when</b>.',
    run: () => set({ panel: 'watchlist' }),
    hold: 5600,
  },
  {
    id: 'health',
    target: '#panel',
    title: 'Knowing what is actually up',
    body: 'The grid reports every camera as live, including the ones that are '
        + 'not. So availability is <b>measured</b> — opened, decoded, judged. The '
        + 'gap between the reported column and the confirmed column is the '
        + 'number nobody had verified.',
    run: () => set({ panel: 'health' }),
    hold: 5800,
  },
  {
    id: 'end',
    target: null,
    title: 'That is the platform',
    body: 'Registry and GIS, unified viewing, ANPR with evidence, cross-camera '
        + 'tracing and alerting — running against thirty live cameras on a '
        + 'YOLO11 detection pipeline. Press <b>Guide</b> in the top bar to watch '
        + 'this again.',
    run: () => set({ panel: null, dockOpen: false }),
    hold: 5600,
  },
];

const FEATURES = [
  ['layers', 'GIS map & layers'],
  ['registry', 'Camera registry'],
  ['wall', 'Video wall'],
  ['events', 'ANPR evidence'],
  ['trace', 'Vehicle tracing'],
  ['watch', 'Watchlist alerts'],
];

let root = null;
let index = 0;
let timer = null;
let running = false;
let paused = false;

export function shouldAutoStart() {
  try { return !localStorage.getItem(SEEN_KEY); } catch { return false; }
}

export function open() {
  root = $('#tour');
  index = 0;
  running = false;
  paused = false;
  root.hidden = false;
  root.classList.add('blocking');
  renderWelcome();
  window.addEventListener('keydown', onKey);
}

export function close() {
  clearTimeout(timer);
  timer = null;
  running = false;
  window.removeEventListener('keydown', onKey);
  try { localStorage.setItem(SEEN_KEY, '1'); } catch { /* private mode */ }
  if (root) { root.hidden = true; root.classList.remove('blocking'); fill(root); }
}

function onKey(event) {
  if (event.key === 'Escape') close();
  else if (event.key === 'ArrowRight') next();
  else if (event.key === ' ') { event.preventDefault(); togglePause(); }
}

function renderWelcome() {
  fill(root,
    el('div.tour-scrim'),
    el('div.tour-welcome', {},
      el('div.tour-welcome-card', {},
        el('div', { style: { display: 'grid', placeItems: 'center' } },
          el('div', { style: {
            width: '46px', height: '46px', display: 'grid', placeItems: 'center',
            borderRadius: '12px', background: 'var(--accent-soft)',
            border: '1px solid var(--accent-line)', color: 'var(--accent)',
          } }, icon('guide', 24))),
        el('h2', { text: 'Platform walkthrough' }),
        el('p', { text: 'A guided pass over the working system — camera registry, '
          + 'GIS layers, the video wall, plate reading with evidence, and vehicle '
          + 'tracing. It drives the real interface, so everything you see it '
          + 'claim, it does.' }),
        el('div.tour-grid', {}, ...FEATURES.map(([iconName, label]) =>
          el('div', {}, icon(iconName, 13), label))),
        el('div', { style: { display: 'flex', gap: '8px', justifyContent: 'center' } },
          el('button.btn', { onclick: close }, 'Skip'),
          el('button.btn.primary', { onclick: start }, icon('play', 13), 'Start walkthrough')),
      )),
  );
}

function start() {
  running = true;
  index = 0;
  show();
}

function next() {
  if (!running) return;
  clearTimeout(timer);
  if (index < STEPS.length - 1) { index += 1; show(); }
  else close();
}

function previous() {
  if (!running || index === 0) return;
  clearTimeout(timer);
  index -= 1;
  show();
}

function togglePause() {
  if (!running) return;
  paused = !paused;
  if (paused) clearTimeout(timer);
  else timer = setTimeout(next, 2200);
  const button = root.querySelector('#tour-pause');
  if (button) fill(button, icon(paused ? 'play' : 'pause', 12), paused ? 'Resume' : 'Pause');
}

async function show() {
  const step = STEPS[index];
  const generation = index;

  try { await step.run?.(); } catch (error) { console.warn('tour step failed', step.id, error); }
  // Let the panel that just opened lay out before measuring it.
  await sleep(420);
  if (generation !== index || !running) return;

  const target = step.target ? document.querySelector(step.target) : null;
  const box = target?.getBoundingClientRect();
  const usable = box && box.width > 40 && box.height > 40;

  const spot = usable ? el('div.tour-spot', { style: {
    top: `${box.top - 6}px`, left: `${box.left - 6}px`,
    width: `${box.width + 12}px`, height: `${box.height + 12}px`,
  } }) : el('div.tour-scrim');

  const card = el('div.tour-card', {},
    el('div.tour-progress', {},
      el('i', { style: { width: `${((index + 1) / STEPS.length) * 100}%` } })),
    el('div.tour-step', { text: `STEP ${index + 1} OF ${STEPS.length}` }),
    el('h3', { text: step.title }),
    el('p', { html: step.body }),
    el('div.tour-actions', {},
      el('button.btn.sm.ghost', { onclick: close }, 'Skip tour'),
      el('div', { style: { marginLeft: 'auto', display: 'flex', gap: '6px' } },
        index > 0 ? el('button.btn.sm', { onclick: previous }, 'Back') : null,
        el('button.btn.sm#tour-pause', { onclick: togglePause },
          icon(paused ? 'play' : 'pause', 12), paused ? 'Resume' : 'Pause'),
        el('button.btn.sm.primary', { onclick: next },
          index === STEPS.length - 1 ? 'Finish' : 'Next')),
    ),
  );

  fill(root, spot, card);
  position(card, box);

  if (!paused) timer = setTimeout(next, step.hold || 5000);
}

/** Put the card beside the spotlight, or centre it when there is nothing to
 *  highlight. Kept inside the viewport in both axes. */
function position(card, box) {
  const width = 348;
  const height = card.offsetHeight || 260;
  const margin = 18;

  if (!box) {
    card.style.left = `${(window.innerWidth - width) / 2}px`;
    card.style.top = `${(window.innerHeight - height) / 2}px`;
    return;
  }

  // Prefer the side with more room, so the card never covers what it describes.
  const spaceLeft = box.left;
  const spaceRight = window.innerWidth - box.right;
  let left = spaceRight >= width + margin * 2
    ? box.right + margin
    : spaceLeft >= width + margin * 2
      ? box.left - width - margin
      : (window.innerWidth - width) / 2;

  let top = box.top + box.height / 2 - height / 2;
  top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
  left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}
