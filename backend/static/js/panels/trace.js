/* Following one vehicle across cameras.
 *
 * A plate returns every camera that saw it, in time order, drawn as a route
 * with a playback slider.
 *
 * One constraint governs this whole panel and is worth stating plainly. The
 * cameras on this grid are independent recordings spanning four dates, and only
 * some of their windows overlap. A "journey" may therefore only be built from
 * sightings whose cameras were recording at the same time -- joining sightings
 * across recording days would draw a trip that never happened. The backend
 * splits a trace into segments on exactly that rule, and this panel draws each
 * segment separately rather than connecting them into one convincing line.
 */

import { api } from '../api.js';
import { state, set } from '../store.js';
import { el, fill, icon, fmtTime, plateChip, toast } from '../ui.js';
import * as mapView from '../map.js';

let lastQuery = '';

export function openTrace(plate) {
  lastQuery = plate;
  set({ panel: 'trace' });
  runSearch({ plate });
}

export function render(host) {
  const results = el('div');

  const form = el('form.panel-section', {
    onsubmit: (event) => {
      event.preventDefault();
      const value = form.querySelector('input').value.trim();
      if (value) { lastQuery = value; runSearch(guessQuery(value)); }
    },
  },
    el('div.label', { style: { marginBottom: '8px' }, text: 'Find a vehicle' }),
    el('div', { style: { display: 'flex', gap: '8px' } },
      el('input.field.mono', {
        placeholder: 'Plate, camera, or free text',
        value: lastQuery,
        style: { flex: '1' },
      }),
      el('button.btn.primary', { type: 'submit' }, icon('search', 13), 'Trace'),
    ),
    el('div.faint', { style: { fontSize: '10px', marginTop: '8px', lineHeight: '1.55' },
      text: 'A plate traces exactly; anything else is matched against camera, '
          + 'vehicle type and direction — which is what carries this grid, '
          + 'where most plates are unreadable.' }),
  );

  fill(host, form, results);
  renderResults(results);

  // Re-render the results block whenever a new trace lands, without rebuilding
  // the form and losing what the operator is typing.
  state._traceHost = results;
}

function guessQuery(value) {
  const upper = value.toUpperCase().replace(/\s+/g, '');
  // An Indian registration normalises to two letters, one or two digits, one
  // or two letters, then up to four digits. Anything shaped like that is a
  // plate; anything else is free text.
  if (/^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{1,4}$/.test(upper)) return { plate: upper };
  if (/^CAM-?\d+$/i.test(value)) return { camera_id: value.toUpperCase() };
  return { q: value };
}

async function runSearch(params) {
  const host = state._traceHost;
  if (host) fill(host, el('div.empty', { text: 'Searching…' }));
  try {
    const trace = await api.search(params);
    set({ trace, tracePosition: 0 });
    if (host) renderResults(host);
    if (!trace.points?.length) {
      toast('No sightings', 'Nothing in the event store matches that.', 'warn');
    }
  } catch (error) {
    if (host) fill(host, el('div.empty', { text: error.message }));
  }
}

/* ------------------------------------------------------------------ results */

function renderResults(host) {
  const trace = state.trace;
  if (!trace) {
    fill(host, el('div.empty', { text: 'Search for a vehicle to draw its route.' }));
    return;
  }
  if (!trace.points?.length) {
    fill(host, el('div.empty', {},
      el('div', { text: `Nothing found for "${trace.query}".` }),
      el('div', { style: { marginTop: '8px', fontSize: '11px' },
        text: 'Detections are never fabricated. If a plate was not read, it is '
            + 'not in the store.' })));
    return;
  }

  const segments = trace.route_segments || [];
  const placed = trace.points.filter((p) => p.latitude !== null);

  fill(host,
    el('div.panel-section', {},
      el('div.metrics', {},
        metric('Sightings', trace.sighting_count),
        metric('Cameras', trace.camera_count),
        metric('Journeys', segments.length,
          segments.length > 1 ? 'separate recording windows' : null),
      ),

      el('div', { style: { marginTop: '14px', display: 'flex', gap: '8px',
                           alignItems: 'center', flexWrap: 'wrap' } },
        plateChip(trace.match_type === 'plate' ? trace.query : null, null),
        el('span.tag' + (trace.match_type === 'plate' ? '.ok' : '.warn'),
          { text: trace.match_type === 'plate' ? 'plate match' : 'attribute match' }),
        trace.match_type === 'plate'
          ? el('button.btn.sm.ghost', {
              style: { marginLeft: 'auto' },
              onclick: () => api.download(api.tracePdfUrl(trace.query),
                                          `trace_${trace.query}.pdf`)
                .catch((error) => toast('Export failed', error.message, 'crit')),
            }, icon('download', 12), 'PDF report')
          : null,
      ),

      trace.notes?.length
        ? el('div', { style: { marginTop: '12px', display: 'grid', gap: '8px' } },
            ...trace.notes.map((note) => el('div.note.warn', { text: note })))
        : null,
    ),

    /* Playback. The slider is not decoration -- stepping through the sightings
     * in time order is how an operator judges whether a route is plausible. */
    placed.length > 1 ? playback(placed) : null,

    el('div.panel-section', {},
      el('div.label', { style: { marginBottom: '12px' }, text: 'Movement' }),
      ...segments.map((segment, index) => segmentBlock(segment, index, segments.length)),
    ),
  );
}

function metric(label, value, sub) {
  return el('div.metric', {},
    el('div.m-k', { text: label }),
    el('div.m-v', { text: String(value) }),
    sub ? el('div.m-s', { text: sub }) : null);
}

function playback(placed) {
  let timer = null;

  const slider = el('input.field', {
    type: 'range', min: '0', max: String(placed.length - 1), value: '0',
    style: { flex: '1', padding: '0', height: '28px', accentColor: 'var(--trace)' },
    oninput: () => step(Number(slider.value)),
  });

  const readout = el('div.faint.mono', { style: { fontSize: '10px', marginTop: '8px' } });

  const step = (index) => {
    slider.value = String(index);
    set({ tracePosition: index });
    mapView.focusWaypoint(index);
    const point = placed[index];
    fill(readout, `${index + 1} / ${placed.length}  ·  ${point.camera_id}  ·  `
      + `${fmtTime(point.timestamp)}`);
  };

  const playBtn = el('button.btn.sm', {
    onclick: () => {
      if (timer) { clearInterval(timer); timer = null; fill(playBtn, icon('play', 12), 'Play'); return; }
      fill(playBtn, icon('pause', 12), 'Pause');
      timer = setInterval(() => {
        const next = Number(slider.value) + 1;
        if (next >= placed.length) {
          clearInterval(timer); timer = null; fill(playBtn, icon('play', 12), 'Play');
          return;
        }
        step(next);
      }, 1400);
    },
  }, icon('play', 12), 'Play');

  step(0);

  return el('div.panel-section', {},
    el('div.label', { style: { marginBottom: '10px' }, text: 'Playback' }),
    el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center' } },
      playBtn, slider),
    readout,
  );
}

function segmentBlock(segment, index, total) {
  const first = segment[0];
  const last = segment[segment.length - 1];

  return el('div', { style: { marginBottom: index < total - 1 ? '22px' : '0' } },
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px',
                         marginBottom: '10px' } },
      el('span.tag.trace', { text: `Journey ${index + 1}` }),
      el('span.faint', { style: { fontSize: '10px' },
        text: `${segment.length} sighting${segment.length === 1 ? '' : 's'}` }),
      first.time_cluster !== null && first.time_cluster !== undefined
        ? el('span.tag', { text: `cluster ${first.time_cluster}` })
        : null,
    ),

    el('ul.timeline', {}, ...segment.map((point, i) => el('li', {},
      el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px',
                           marginBottom: '4px' } },
        el('span.mono', { style: { fontSize: '11px', fontWeight: '600' },
          text: point.camera_id }),
        point.direction ? el('span.tag', { text: point.direction }) : null),
      el('div', { style: { fontSize: '12px', color: 'var(--text-dim)' },
        text: point.location || '—' }),
      el('div.faint.mono', { style: { fontSize: '10px', marginTop: '2px' },
        text: fmtTime(point.timestamp) }),
      point.evidence
        ? el('img', {
            src: api.evidenceUrl(point.evidence), alt: '', loading: 'lazy',
            style: { width: '100%', maxWidth: '220px', marginTop: '8px',
                     borderRadius: '5px', border: '1px solid var(--line)', cursor: 'pointer' },
            onclick: () => mapView.focusWaypoint(i),
          })
        : null,
    ))),

    index < total - 1
      ? el('div.note.warn', { style: { marginTop: '12px' },
          text: 'The next journey is from a different recording window. No route '
              + 'is drawn between them — these cameras were not recording at the '
              + 'same time, so any line between them would be invented.' })
      : null,
  );
}
