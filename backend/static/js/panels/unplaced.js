/* The unplaced estate.
 *
 * Why this exists
 * ---------------
 * Twenty-two of this grid's thirty-one cameras have no position anyone has
 * vouched for. The map refuses to draw them, which is right -- a pin an operator
 * might dispatch against must not be a guess -- but the consequence was that two
 * thirds of the estate simply did not exist on the primary surface. The map read
 * as a nine-camera deployment. That is the failure mode this platform is
 * supposed to prevent: an estate quietly losing the cameras nobody is
 * accountable for.
 *
 * So the unplaced cameras get their own surface, on the map, permanently
 * visible while any remain. Not a warning banner -- a way in.
 *
 * The thumbnail is the point
 * --------------------------
 * "Dethali Char Rasta" and "CAM-23" are both unplaceable from the name alone.
 * What an operator can place is a *picture*: a junction they recognise, a
 * flyover, a hoarding. Every card leads with the camera's own last frame, which
 * is the only piece of evidence that actually answers "where is this".
 *
 * Placement is two clicks and it is audited. Clicking a card arms the map;
 * clicking the map writes the position through PUT /api/cameras/{id}/location
 * with accuracy VERIFIED and the operator's name against it, into
 * camera_metadata_history. Escape cancels. Nothing is ever placed automatically
 * and nothing is estimated -- a camera stays in this tray until a person says
 * where it is.
 */

import { api, session } from '../api.js';
import { state, set, notify, subscribe, isPlaced } from '../store.js';
import { el, fill, icon, toast, $, statusClass } from '../ui.js';
import * as mapView from '../map.js';

const STORE_KEY = 'sentinel.unplaced.collapsed';

/** Cameras with no position the map is willing to draw, worst first.
 *
 *  Ordered by whether the camera can read a plate and then by its plate score:
 *  an unplaced ANPR camera is a hole in the tracking network, while an unplaced
 *  low-score overview camera is only a hole in the inventory. Both matter; they
 *  do not matter equally.
 */
function unplaced() {
  return state.cameras
    .filter((camera) => !isPlaced(camera))
    .sort((a, b) => (b.ai_enabled - a.ai_enabled) || (b.plate_score - a.plate_score));
}

let armed = null;      // camera_id currently waiting for a map click

export function mount(host) {
  const body = el('div.unplaced-body');
  const head = el('div.float-head', {},
    el('span.float-title', { text: 'Not on the map' }),
    el('span#unplaced-count.tag.warn'),
    el('button.icon-btn', {
      title: 'Collapse',
      onclick: () => {
        host.classList.toggle('collapsed');
        try { localStorage.setItem(STORE_KEY, host.classList.contains('collapsed') ? '1' : '0'); }
        catch { /* the preference simply does not survive a reload */ }
      },
    }, icon('chevron', 14)),
  );
  fill(host, head, body);

  try { if (localStorage.getItem(STORE_KEY) === '1') host.classList.add('collapsed'); }
  catch { /* no stored preference */ }

  const render = () => renderInto(host, body);
  subscribe(['cameras', 'selectedCamera'], render);
  render();
}

function renderInto(host, body) {
  const cameras = unplaced();

  // The tray disappears the moment the estate is fully placed. A panel that
  // says "0 cameras need attention" is a panel taking up map for nothing.
  host.hidden = cameras.length === 0;
  if (!cameras.length) return;

  const chip = $('#unplaced-count');
  if (chip) chip.textContent = `${cameras.length} of ${state.cameras.length}`;

  fill(body,
    el('div.unplaced-why', {},
      session.canAct
        ? 'These cameras are not drawn rather than pinned at a guess. Pick one, '
          + 'then click where it is — recorded against your name.'
        : 'These cameras have no position anyone has vouched for, so they are '
          + 'not drawn. An OPERATOR or ADMIN can place them.'),

    el('div.unplaced-list', {}, ...cameras.map(card)),
  );
}

function card(camera) {
  const named = camera.location_name || (camera.name !== camera.camera_id ? camera.name : '');
  const isArmed = armed === camera.camera_id;

  const shot = el('div.unplaced-shot', {},
    el('img', {
      src: api.thumbnailUrl(camera.camera_id),
      alt: `Last frame from ${camera.camera_id}`,
      loading: 'lazy',
      // No thumbnail is a fact worth stating: it means this camera has never
      // delivered a decodable frame to this platform, which is a different
      // problem from being unplaced and is usually the reason for it.
      onerror: (event) => {
        event.target.remove();
        shot.classList.add('empty');
        shot.append(el('span', { text: 'no frame' }));
      },
    }),
  );

  return el('button.unplaced-card' + (isArmed ? '.armed' : ''), {
    title: session.canAct
      ? `Place ${camera.camera_id} on the map`
      : `${camera.camera_id} — no position on record`,
    disabled: !session.canAct,
    onclick: () => (isArmed ? disarm() : arm(camera)),
  },
    shot,
    el('div.unplaced-meta', {},
      el('div.unplaced-id', {},
        el('span.dot', { style: { color: statusColour(camera.status) } }),
        el('span.mono', { text: camera.camera_id }),
        camera.ai_enabled ? el('span.tag.info', { text: 'ANPR' }) : null),
      // An OCR'd site name is a lead, not an address. Saying which of the two
      // this is stops an operator trusting "Suvidlnapar" as a place they can
      // look up.
      named
        ? el('div.unplaced-name', { title: named, text: named })
        : el('div.unplaced-name.faint', { text: 'no site name read' })),
    el('span.unplaced-action', {},
      icon(isArmed ? 'target' : 'pin', 13),
      isArmed ? 'Click the map' : 'Place'),
  );
}

const statusColour = (status) => ({
  ONLINE: 'var(--signal)', DEGRADED: 'var(--warn)', OFFLINE: 'var(--critical)',
}[status] || 'var(--text-faint)');

/* ------------------------------------------------------------- placement */

let cleanup = null;

/** Arm the map for one camera's placement. */
function arm(camera) {
  const map = mapView.instance();
  if (!map) { toast('No map surface', 'The map library did not load.', 'warn'); return; }

  disarm();
  armed = camera.camera_id;
  notify('cameras');

  map.getCanvas().style.cursor = 'crosshair';
  toast(`Placing ${camera.camera_id}`,
    `${camera.location_name || 'No site name'} — click the map at its true `
    + 'position. Escape cancels.', 'info', 14000);

  const onKey = (event) => { if (event.key === 'Escape') disarm(); };
  const onClick = async (event) => {
    const { lat, lng } = event.lngLat;
    disarm();
    try {
      const updated = await api.setLocation(camera.camera_id, {
        latitude: lat,
        longitude: lng,
        accuracy: 'VERIFIED',
        note: `placed by hand by ${session.user?.username || 'operator'}`,
      });
      const index = state.cameras.findIndex((c) => c.camera_id === camera.camera_id);
      if (index >= 0) state.cameras[index] = updated;
      state.camerasById.set(camera.camera_id, updated);
      set({ cameras: [...state.cameras] });
      toast(`${camera.camera_id} placed`,
        `${lat.toFixed(5)}, ${lng.toFixed(5)} — VERIFIED, and written to the `
        + 'metadata audit trail.', 'ok');
    } catch (error) {
      toast('Could not place the camera', error.message, 'crit');
    }
  };

  map.on('click', onClick);
  window.addEventListener('keydown', onKey);
  cleanup = () => {
    map.getCanvas().style.cursor = '';
    map.off('click', onClick);
    window.removeEventListener('keydown', onKey);
  };
}

function disarm() {
  cleanup?.();
  cleanup = null;
  if (armed) { armed = null; notify('cameras'); }
}
