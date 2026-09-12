/* Knowing what is actually up.
 *
 * The grid reports every camera as live, including the ones that are not. So
 * this panel puts the reported number and the measured number side by side, and
 * the gap between them is the whole point: it is the count of cameras nobody has
 * verified.
 *
 * Probing follows the video wall rather than sweeping the estate. This grid
 * permits roughly one session per address and measurably degrades under
 * parallel opens -- at ten concurrent opens, five cameras returned no frames at
 * all, while serially four of them recovered. A "check everything" button would
 * therefore make the estate look worse than it is, so there is not one.
 */

import { api, session } from '../api.js';
import { state, visibleCameras } from '../store.js';
import { el, fill, icon, fmtNum, fmtTime, fmtDuration, toast, into } from '../ui.js';
import * as mapView from '../map.js';

export function render(host) {
  into(host, async () => {
    const { summary, results } = await api.health();
    const byId = new Map(results.map((r) => [r.camera_id, r]));

    return [
      el('div.panel-section', {},
        el('div.metrics', {},
          metric('Reported up', summary.reported_online, 'by the grid', ''),
          metric('Confirmed', summary.confirmed_online, 'opened and decoded', 'ok'),
          metric('Unverified', summary.unverified, 'never probed',
            summary.unverified ? 'warn' : ''),
          metric('Degraded', summary.degraded, 'up but unusable',
            summary.degraded ? 'warn' : ''),
          metric('Failed', summary.failed, 'did not open',
            summary.failed ? 'crit' : ''),
        ),
        el('div.note', { style: { marginTop: '12px' },
          text: `${fmtNum(summary.reported_online)} cameras report themselves online. `
              + `${fmtNum(summary.confirmed_online)} have actually been opened and decoded `
              + `by this process. The rest is an assumption, not a measurement.` }),
      ),

      session.canAct ? probeControls(host) : null,

      el('div.panel-section', {},
        el('div.label', { style: { marginBottom: '10px' }, text: 'Per camera' }),
        ...visibleCameras().map((cam) => cameraRow(cam, byId.get(cam.camera_id))),
      ),
    ];
  });
}

function metric(label, value, sub, kind) {
  return el(`div.metric${kind ? '.' + kind : ''}`, {},
    el('div.m-k', { text: label }),
    el('div.m-v', { text: fmtNum(value) }),
    el('div.m-s', { text: sub }));
}

function probeControls(host) {
  const targets = () => (state.wall.length ? state.wall
    : visibleCameras().slice(0, 6).map((c) => c.camera_id));

  return el('div.panel-section', {},
    el('div.label', { style: { marginBottom: '8px' }, text: 'Measure' }),
    el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      el('button.btn.primary', {
        onclick: async (event) => {
          const button = event.currentTarget;
          const ids = targets();
          if (!ids.length) { toast('Nothing to probe', 'Add cameras to the wall first.', 'warn'); return; }
          button.disabled = true;
          fill(button, 'Probing…');
          try {
            const result = await api.probe(ids, true);
            const up = result.results.filter((r) => r.status === 'ONLINE').length;
            toast('Probe complete',
              `${up} of ${result.results.length} confirmed online.`,
              up === result.results.length ? 'ok' : 'warn');
            render(host);
          } catch (error) {
            toast('Probe failed', error.message, 'crit');
            button.disabled = false;
            fill(button, icon('health', 13), 'Probe');
          }
        },
      }, icon('health', 13),
         state.wall.length ? `Probe the ${state.wall.length} on the wall` : 'Probe 6 cameras'),
    ),
    el('div.faint', { style: { fontSize: '10px', marginTop: '9px', lineHeight: '1.55' },
      text: 'Probing opens a real stream per camera and blocks for up to 25 s each. '
          + 'It is serialised behind the same concurrency cap the ingest workers '
          + 'use, because this grid returns no frames at all under parallel opens.' }),
  );
}

function cameraRow(cam, probe) {
  const measured = probe?.status;
  const disagrees = measured && measured !== cam.status;

  return el('button.row', {
    onclick: () => mapView.selectCamera(cam.camera_id),
    title: probe?.note || 'Never probed',
  },
    el('div.row-main', {},
      el('div.row-title', { text: cam.location_name || cam.camera_id }),
      el('div.row-sub', {},
        el('span.mono', { text: cam.camera_id }),
        probe ? ` · probed ${fmtTime(probe.checked_at)}` : ' · never probed'),
    ),
    el('div.row-side', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
      el('span.tag', { text: cam.status, title: 'Reported by the grid' }),
      measured
        ? el('span.tag.' + tagClass(measured), { text: measured, title: 'Measured here' })
        : el('span.tag', { style: { opacity: '0.5' }, text: '—' }),
      probe?.open_latency_s !== null && probe?.open_latency_s !== undefined
        ? el('span.faint.mono', { style: { fontSize: '10px', minWidth: '42px' },
            text: fmtDuration(probe.open_latency_s) })
        : null,
    ),
    /* A disagreement between the two columns is the finding, so it is marked
     * rather than left for the reader to spot. */
    disagrees ? el('span.dot', { style: { color: 'var(--warn)' },
      title: `Grid says ${cam.status}, measurement says ${measured}` }) : null,
  );
}

const tagClass = (status) => ({ ONLINE: 'ok', DEGRADED: 'warn', OFFLINE: 'crit' }[status] || '');
