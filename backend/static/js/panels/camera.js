/* Camera inspector and the camera list.
 *
 * Two things one panel: a scrollable estate list, and the detail for whichever
 * camera is selected. They share a panel because they are the same task at two
 * zoom levels -- find the camera, then work it.
 */

import { api, session } from '../api.js';
import { state, set, camera as getCamera, toggleWall, visibleCameras, isPlaced,
         WALL_LIMIT } from '../store.js';
import { el, fill, icon, fmtNum, fmtTime, statusClass, toast, into } from '../ui.js';
import * as mapView from '../map.js';

/* ------------------------------------------------------------------- list */

export function renderList(host) {
  const cameras = visibleCameras();
  if (!cameras.length) {
    fill(host, el('div.empty', { text: 'No camera matches the current layers.' }));
    return;
  }

  fill(host, ...cameras.map((cam) => {
    const onWall = state.wall.includes(cam.camera_id);
    return el('button.row' + (state.selectedCamera === cam.camera_id ? '.selected' : ''), {
      onclick: () => mapView.selectCamera(cam.camera_id),
    },
      el('span.dot', { style: { color: statusColour(cam.status) } }),
      el('div.row-main', {},
        el('div.row-title', { text: cam.location_name || cam.name || cam.camera_id }),
        el('div.row-sub', {},
          el('span.mono', { text: cam.camera_id }),
          ' · ', cam.district || 'unknown district',
          cam.time_cluster !== null ? ` · cluster ${cam.time_cluster}` : ''),
      ),
      el('div.row-side', { style: { display: 'flex', gap: '5px', alignItems: 'center' } },
        cam.ai_enabled ? el('span.tag.info', { text: 'ANPR' }) : null,
        onWall ? el('span.tag.ok', { text: 'WALL' }) : null,
        // Say so on the row rather than only in the detail panel. On a
        // map-first interface an unplaced camera is otherwise invisible.
        isPlaced(cam) ? null : el('span.tag.warn', { text: 'NO POSITION' }),
      ),
    );
  }));
}

const statusColour = (status) => ({
  ONLINE: 'var(--signal)', DEGRADED: 'var(--warn)', OFFLINE: 'var(--critical)',
}[status] || 'var(--text-faint)');

/* ----------------------------------------------------------------- detail */

export function renderDetail(host, cameraId) {
  const cam = getCamera(cameraId);
  if (!cam) { fill(host, el('div.empty', { text: 'Camera not found' })); return; }

  const onWall = state.wall.includes(cam.camera_id);
  const approximate = cam.location_accuracy === 'APPROXIMATE'
                   || cam.location_accuracy === 'UNKNOWN';

  fill(host,
    el('div.panel-section', {},
      /* Live preview. A plain <img> against the MJPEG endpoint -- no player
       * library, no WebRTC, no HLS transcode. On this hardware the limiting
       * factor is detection latency, not transport. */
      el('div.tile', { style: { marginBottom: '14px' } },
        el('img', {
          src: api.liveUrl(cam.camera_id, true),
          alt: `Live preview of ${cam.camera_id}`,
          onerror: (event) => { event.target.style.display = 'none'; },
        }),
        el('div.tile-bar', {},
          el('span.dot.live', { style: { color: 'var(--signal)' } }),
          el('span.tile-name', { text: 'Live, detections drawn on' })),
      ),

      el('div', { style: { display: 'flex', gap: '8px', marginBottom: '14px' } },
        el('button.btn' + (onWall ? '' : '.primary'), {
          onclick: () => {
            const result = toggleWall(cam.camera_id);
            if (result.full) toast('Wall is full', `Remove a tile first (limit ${WALL_LIMIT}).`, 'warn');
            else set({ dockOpen: true });
          },
        }, icon('wall', 13), onWall ? 'Remove from wall' : 'Add to wall'),
        isPlaced(cam)
          ? el('button.btn', {
              onclick: () => mapView.selectCamera(cam.camera_id),
            }, icon('target', 13), 'Centre')
          : session.canAct
            ? el('button.btn', { onclick: () => placeByHand(cam) },
                icon('pin', 13), 'Place on map')
            : null,
      ),

      el('dl.kv', {},
        row('Camera ID', el('span.mono', { text: cam.camera_id })),
        row('Location', cam.location_name || '—'),
        row('District', cam.district || '—'),
        row('Department', cam.department || '—'),
        row('Status', el('span.tag.' + statusClass(cam.status), { text: cam.status })),
        row('Plate score', el('span.mono', { text: `${fmtNum(cam.plate_score, 0)} / 100` })),
        row('Time cluster', cam.time_cluster === null
          ? el('span.faint', { text: 'not clustered' })
          : el('span.mono', { text: String(cam.time_cluster) })),
        row('Overlay clock', el('span.mono', { text: fmtTime(cam.overlay_ts) })),
        row('Stream', `${cam.protocol || '—'} · ${cam.codec || '—'}`),
        row('Resolution', cam.width ? `${cam.width} × ${cam.height}` : '—'),
      ),
    ),

    /* The triage note is the camera's own honest self-assessment and belongs
     * next to the plate score, not buried in a tooltip. */
    cam.triage_note
      ? el('div.panel-section', {},
          el('div.label', { style: { marginBottom: '8px' }, text: 'Triage' }),
          el('div.note', { text: cam.triage_note }))
      : null,

    /* Provenance of the pin. An APPROXIMATE position is a landmark-level
     * estimate from the camera's overlay label, not a surveyed coordinate, and
     * it must never be read as one -- an operator may dispatch against it. */
    el('div.panel-section', {},
      el('div.label', { style: { marginBottom: '8px' }, text: 'Position' }),
      el('div.note' + (approximate ? '.warn' : ''), {},
        el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px',
                             marginBottom: '6px' } },
          el('span.tag' + (approximate ? '.warn' : '.ok'), { text: cam.location_accuracy }),
          cam.latitude !== null
            ? el('span.mono.faint', { style: { fontSize: '10px' },
                text: `${cam.latitude.toFixed(5)}, ${cam.longitude.toFixed(5)}` })
            : el('span.faint', { text: 'not placed' })),
        el('div', { text: approximate
          ? (cam.location_source || 'Estimated from the camera\'s overlay label. '
             + 'This is a landmark-level guess, not a surveyed position.')
          : (cam.location_source || 'From the operator catalogue.') })),

      session.canAct
        ? el('div', { style: { marginTop: '10px' } },
            el('button.btn.sm', { onclick: () => placeByHand(cam) },
              icon('pin', 12), 'Correct this position'))
        : null,
    ),

    /* Recent sightings on this camera. */
    (() => {
      const box = el('div.panel-section');
      fill(box, el('div.label', { style: { marginBottom: '10px' },
        text: 'Recent sightings here' }));
      const list = el('div');
      box.append(list);
      into(list, async () => {
        const trace = await api.search({ camera_id: cam.camera_id });
        const points = (trace.points || []).slice(-12).reverse();
        if (!points.length) return null;
        return points.map((point) => el('div', {
          style: { display: 'flex', alignItems: 'center', gap: '10px',
                   padding: '7px 0', borderBottom: '1px solid var(--line)' } },
          point.evidence
            ? el('img.thumb', { src: api.evidenceUrl(point.evidence), alt: '', loading: 'lazy' })
            : el('div.thumb'),
          el('div', { style: { flex: '1', minWidth: '0' } },
            el('div', { style: { fontSize: '12px' }, text: point.vehicle_type || 'vehicle' }),
            el('div.faint.mono', { style: { fontSize: '10px' },
              text: fmtTime(point.timestamp) })),
        ));
      }, { empty: 'No sightings recorded on this camera yet.' });
      return box;
    })(),
  );
}

function row(term, value) {
  return [el('dt', { text: term }),
          el('dd', {}, value instanceof Node ? value : String(value))];
}

/* --------------------------------------------------------- manual placement */

/** Click the map to set a camera's true position. Every change is written to
 *  camera_metadata_history with the operator's name against it. */
function placeByHand(cam) {
  const map = mapView.instance();
  if (!map) return;
  toast('Click the map', `Place ${cam.camera_id} at its true position. Press Escape to cancel.`,
    'info', 12000);
  map.getCanvas().style.cursor = 'crosshair';

  const finish = () => {
    map.getCanvas().style.cursor = '';
    map.off('click', onClick);
    window.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => { if (event.key === 'Escape') finish(); };
  const onClick = async (event) => {
    finish();
    try {
      const updated = await api.setLocation(cam.camera_id, {
        latitude: event.lngLat.lat,
        longitude: event.lngLat.lng,
        accuracy: 'VERIFIED',
        note: `placed by hand by ${session.user?.username}`,
      });
      const index = state.cameras.findIndex((c) => c.camera_id === cam.camera_id);
      if (index >= 0) state.cameras[index] = updated;
      state.camerasById.set(cam.camera_id, updated);
      set({ cameras: [...state.cameras] });
      toast('Position updated', 'Recorded in the metadata audit trail.', 'ok');
    } catch (error) {
      toast('Could not update', error.message, 'crit');
    }
  };
  map.on('click', onClick);
  window.addEventListener('keydown', onKey);
}
