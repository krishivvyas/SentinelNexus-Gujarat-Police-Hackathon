/* ANPR events, with the evidence attached to every reading.
 *
 * OCR on this footage is right most of the time, not all of the time. So a
 * plate is never presented as a bare string: each reading carries the plate
 * crop it was made from, the full frame with the vehicle boxed, and the
 * confidence it was read at. The operator confirms the characters by eye
 * before acting, and the frame is the accountability record afterwards.
 *
 * A sighting with no readable plate is still shown. On this grid most vehicles
 * have none, and an attribute-only sighting still supports cross-camera
 * correlation -- hiding them would misrepresent how much the platform actually
 * saw.
 */

import { api } from '../api.js';
import { set } from '../store.js';
import { el, fill, icon, fmtTime, plateChip, into, toast } from '../ui.js';
import * as mapView from '../map.js';
import { openTrace } from './trace.js';

let plateOnly = false;

export function render(host) {
  const list = el('div');

  const controls = el('div.panel-section', {
    style: { display: 'flex', gap: '8px', alignItems: 'center' } },
    el('button.btn.sm' + (plateOnly ? '.primary' : ''), {
      onclick: () => { plateOnly = !plateOnly; render(host); },
    }, plateOnly ? 'Readable plates only' : 'All sightings'),
    el('button.btn.sm.ghost', {
      style: { marginLeft: 'auto' },
      title: 'Export the detection log as CSV',
      onclick: () => api.download(api.csvUrl({ with_plate_only: plateOnly }),
                                  'sentinel-detections.csv')
        .catch((error) => toast('Export failed', error.message, 'crit')),
    }, icon('download', 12), 'CSV'),
  );

  fill(host, controls, list);

  into(list, async () => {
    const sightings = await api.recent(80, plateOnly);
    if (!sightings.length) return null;
    return sightings.map(eventRow);
  }, { empty: plateOnly
    ? 'No plate has been read yet. On this grid most vehicles carry no legible plate — '
      + 'switch to all sightings to see what was detected.'
    : 'No sightings yet. Start ingest to begin processing cameras.' });
}

function eventRow(sighting) {
  return el('button.row', {
    onclick: () => showEvidence(sighting),
  },
    sighting.evidence
      ? el('img.thumb', { src: api.evidenceUrl(sighting.evidence), alt: '', loading: 'lazy' })
      : el('div.thumb', { style: { display: 'grid', placeItems: 'center' } },
          el('span.faint', { style: { fontSize: '9px' }, text: 'no frame' })),
    el('div.row-main', {},
      el('div', { style: { marginBottom: '3px' } },
        plateChip(sighting.plate, sighting.plate_confidence)),
      el('div.row-sub', {},
        el('span.mono', { text: sighting.camera_id }),
        ' · ', sighting.vehicle_type || 'vehicle',
        sighting.direction ? ` · ${sighting.direction}` : '',
        ' · ', fmtTime(sighting.event_ts || sighting.timestamp)),
    ),
  );
}

/* --------------------------------------------------------------- evidence */

/** The evidence view for one reading.
 *
 *  Both images are shown, always, and labelled for what they are: the crop is
 *  what the OCR actually saw, the full frame is what an operator needs to
 *  judge whether the reading belongs to the vehicle they care about.
 */
export function showEvidence(sighting) {
  set({ panel: 'evidence', evidence: sighting });
}

export function renderEvidence(host, sighting) {
  if (!sighting) { fill(host, el('div.empty', { text: 'No reading selected' })); return; }

  const votes = sighting.plate_frames_voted || 0;

  fill(host,
    el('div.panel-section', {},
      el('div', { style: { marginBottom: '14px' } },
        plateChip(sighting.plate, sighting.plate_confidence)),

      sighting.plate
        ? el('div.note' + (votes > 1 ? '' : '.warn'), {},
            votes > 1
              ? `Read on ${votes} frames and resolved by majority vote. `
                + 'Multi-frame voting is what makes a reading survivable on this '
                + 'footage; a single-frame read is not trusted.'
              : 'Read from a single frame — no corroborating vote. Confirm the '
                + 'characters against the crop below before acting on this.')
        : el('div.note.warn', {},
            'No plate could be read from this vehicle. It is recorded anyway: '
            + 'type and direction still support cross-camera correlation, and '
            + 'a sighting is never dropped just because the plate was illegible.'),

      sighting.plate_crop
        ? el('div', { style: { marginTop: '16px' } },
            el('div.label', { style: { marginBottom: '7px' }, text: 'Plate crop — what OCR read' }),
            el('img', { src: api.evidenceUrl(sighting.plate_crop), alt: 'Plate crop',
              style: { width: '100%', imageRendering: 'pixelated',
                       borderRadius: '6px', border: '1px solid var(--line)',
                       background: '#000' } }))
        : null,

      sighting.evidence
        ? el('div', { style: { marginTop: '16px' } },
            el('div.label', { style: { marginBottom: '7px' },
              text: 'Full frame — the accountability record' }),
            el('img', { src: api.evidenceUrl(sighting.evidence), alt: 'Evidence frame',
              style: { width: '100%', borderRadius: '6px', border: '1px solid var(--line)' } }))
        : null,
    ),

    el('div.panel-section', {},
      el('dl.kv', {},
        kv('Camera', el('span.mono', { text: sighting.camera_id })),
        kv('Location', sighting.location || '—'),
        kv('Event time', el('span.mono', { text: fmtTime(sighting.event_ts || sighting.timestamp) })),
        kv('Clock source', sighting.event_ts_source === 'overlay'
          ? el('span.tag.ok', { text: 'burned-in overlay' })
          : el('span.tag.warn', { text: sighting.event_ts_source || 'unknown' })),
        kv('Vehicle', sighting.vehicle_type || 'unclassified'),
        kv('Direction', sighting.direction || '—'),
        kv('Detector conf.', sighting.detection_confidence
          ? el('span.mono', { text: `${Math.round(sighting.detection_confidence * 100)}%` })
          : '—'),
      ),
      el('div.note', { style: { marginTop: '12px' } },
        'The event clock comes from the timestamp burned into the frame, not '
        + 'from when the server ingested it. These are replayed recordings, so '
        + 'ingest order says nothing about what happened when.'),
    ),

    el('div.panel-section', { style: { display: 'flex', gap: '8px' } },
      sighting.plate
        ? el('button.btn.primary', { onclick: () => openTrace(sighting.plate) },
            icon('trace', 13), 'Trace this vehicle')
        : null,
      el('button.btn', { onclick: () => mapView.selectCamera(sighting.camera_id) },
        icon('camera', 13), 'Open camera'),
    ),
  );
}

function kv(term, value) {
  return [el('dt', { text: term }), el('dd', {}, value instanceof Node ? value : String(value))];
}
