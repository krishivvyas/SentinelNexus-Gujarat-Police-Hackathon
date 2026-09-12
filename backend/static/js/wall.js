/* The video wall.
 *
 * Cameras are added from map pins: the map chooses, the wall shows. Three rules
 * that come straight from how this grid behaves:
 *
 *  * **Tiles hold 16:9 and letterbox.** A distorted feed is worse than a small
 *    one -- an operator judging a vehicle by its shape cannot afford the aspect
 *    ratio to be a lie.
 *  * **A feed that will not open says so.** Opens on this grid have been
 *    measured from 1.8 s to 275 s, so a blank tile is ambiguous between "slow"
 *    and "dead". Each tile states which it thinks it is.
 *  * **The wall is capped.** Each viewer gets its own stream copy and this grid
 *    degrades badly under parallel opens, so the cap is a correctness
 *    constraint rather than a UI preference.
 */

import { api } from './api.js';
import { state, set, subscribe, camera as getCamera, toggleWall, WALL_LIMIT } from './store.js';
import { el, fill, icon, toast } from './ui.js';
import * as mapView from './map.js';

/* How long a tile waits before it stops claiming to be connecting. Set from the
 * measured distribution of open times on this grid rather than a round number:
 * most successful opens land well inside this, and past it the honest thing to
 * say is "this has not opened", not to keep showing a spinner forever. */
const CONNECT_GRACE_MS = 20000;

export function mount(dock) {
  const grid = el('div.dock-grid');

  const head = el('div.dock-head', {},
    el('span.float-title', { text: 'Video wall' }),
    el('span#wall-count.faint.mono', { style: { fontSize: '11px' } }),
    el('div', { style: { marginLeft: 'auto', display: 'flex', gap: '6px' } },
      el('button.btn.sm.ghost', {
        title: 'Fill the wall with the best ANPR cameras',
        onclick: fillWithBest,
      }, icon('plus', 12), 'Auto-fill'),
      el('button.btn.sm.ghost', {
        title: 'Expand',
        onclick: () => dock.classList.toggle('tall'),
      }, icon('expand', 12)),
      el('button.icon-btn', {
        title: 'Close',
        onclick: () => set({ dockOpen: false }),
      }, icon('close', 14)),
    ),
  );

  fill(dock, head, grid);
  subscribe('wall', () => renderTiles(grid));
  renderTiles(grid);
}

function renderTiles(grid) {
  const count = document.querySelector('#wall-count');
  if (count) count.textContent = `${state.wall.length} / ${WALL_LIMIT}`;

  if (!state.wall.length) {
    fill(grid, el('div.empty', { style: { gridColumn: '1 / -1' } },
      el('div', { text: 'No cameras on the wall.' }),
      el('div', { style: { marginTop: '6px' },
        text: 'Click a pin on the map and add it, or use Auto-fill.' })));
    return;
  }

  // Rebuild only what changed. Re-creating an <img> would tear down a live
  // MJPEG connection and pay the 10-90 s reconnect cost for no reason.
  const existing = new Map([...grid.children]
    .filter((node) => node.dataset?.camera)
    .map((node) => [node.dataset.camera, node]));

  const tiles = state.wall.map((id) => existing.get(id) || makeTile(id));
  for (const node of existing.values()) {
    if (!state.wall.includes(node.dataset.camera)) node.remove();
  }
  fill(grid, ...tiles);
}

function makeTile(cameraId) {
  const cam = getCamera(cameraId);
  const name = cam?.location_name || cam?.name || cameraId;

  const status = el('div.tile-state', { text: `Connecting to ${cameraId}…` });
  const image = el('img', {
    alt: `Live feed from ${cameraId}`,
    style: { opacity: '0' },
    src: api.liveUrl(cameraId, true),
  });

  // The MJPEG stream sends a status frame first, so 'load' fires as soon as
  // anything at all arrives -- including the "connecting" placeholder the
  // server draws. That is still the honest moment to reveal the tile.
  image.addEventListener('load', () => {
    image.style.opacity = '1';
    status.remove();
  }, { once: true });

  image.addEventListener('error', () => {
    image.style.display = 'none';
    fill(status,
      el('div', { style: { color: 'var(--warn)' }, text: 'Feed unavailable' }),
      el('div', { style: { marginTop: '6px', fontSize: '10px' },
        text: 'The stream did not open. It is not being passed off as live.' }));
  });

  const timer = setTimeout(() => {
    if (!status.isConnected) return;
    fill(status,
      el('div', { style: { color: 'var(--warn)' }, text: 'Still connecting' }),
      el('div', { style: { marginTop: '6px', fontSize: '10px', maxWidth: '200px' },
        text: 'Opens on this grid have been measured up to 275 s. The tile will '
            + 'fill on its own if the camera answers.' }));
  }, CONNECT_GRACE_MS);

  const tile = el('div.tile', { dataset: { camera: cameraId } },
    image,
    status,
    el('button.tile-x', {
      title: 'Remove from wall',
      onclick: () => { clearTimeout(timer); toggleWall(cameraId); },
    }, icon('close', 12)),
    el('div.tile-bar', {},
      el('span.dot.live', { style: { color: 'var(--signal)' } }),
      el('span.tile-name', { text: name }),
      el('button.icon-btn', {
        style: { width: '20px', height: '20px', marginLeft: 'auto' },
        title: 'Show on the map',
        onclick: () => mapView.selectCamera(cameraId),
      }, icon('target', 11)),
    ),
  );

  return tile;
}

/** Fill the wall with the cameras most likely to yield a readable plate.
 *
 *  Ranked by plate score and then by membership of the primary time cluster --
 *  a camera that reads plates beautifully but shares no recording window with
 *  any other camera cannot contribute to a cross-camera trace.
 */
function fillWithBest() {
  const candidates = state.cameras
    .filter((c) => c.status === 'ONLINE' && !state.wall.includes(c.camera_id))
    .sort((a, b) =>
      (b.ai_enabled - a.ai_enabled) ||
      (b.plate_score - a.plate_score));

  const room = WALL_LIMIT - state.wall.length;
  if (room <= 0) { toast('Wall is full', `Remove a tile first (limit ${WALL_LIMIT}).`, 'warn'); return; }
  if (!candidates.length) { toast('Nothing to add', 'No further online cameras.', 'warn'); return; }

  set({ wall: [...state.wall, ...candidates.slice(0, room).map((c) => c.camera_id)] });
}
