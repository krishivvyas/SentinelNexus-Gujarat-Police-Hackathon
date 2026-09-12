/* Watchlist and live alerts.
 *
 * Stolen and wanted vehicles are matched at the moment a plate is read. On a
 * hit the camera pin flashes, the map moves to it, and an alert card carries
 * the snapshot and the reason. Acknowledgement records who acted and when --
 * that record is the point, not the notification.
 */

import { api, session } from '../api.js';
import { set } from '../store.js';
import { el, fill, icon, fmtTime, plateChip, severityClass, toast, into } from '../ui.js';
import * as mapView from '../map.js';
import { openTrace } from './trace.js';

let tab = 'alerts';

export function render(host) {
  const body = el('div');

  const tabs = el('div.panel-section', {
    style: { display: 'flex', gap: '6px', paddingBottom: '0', borderBottom: 'none' } },
    tabBtn('alerts', 'Alerts', body, host),
    tabBtn('watchlist', 'Watchlist', body, host),
  );

  fill(host, tabs, body);
  (tab === 'alerts' ? renderAlerts : renderWatchlist)(body);
}

function tabBtn(key, label, body, host) {
  return el('button.btn.sm' + (tab === key ? '.primary' : '.ghost'), {
    onclick: () => { tab = key; render(host); },
  }, label);
}

/* ------------------------------------------------------------------ alerts */

function renderAlerts(host) {
  into(host, async () => {
    const alerts = await api.alerts({ limit: 100 });
    set({ alerts, unacknowledged: alerts.filter((a) => a.status === 'NEW').length });
    if (!alerts.length) return null;
    return alerts.map(alertCard);
  }, { empty: 'No alerts. A watchlist plate has not been seen on any camera yet.' });
}

function alertCard(alert) {
  const isNew = alert.status === 'NEW';

  return el('div', { style: {
    padding: '14px 16px', borderBottom: '1px solid var(--line)',
    background: isNew ? 'var(--critical-soft)' : 'transparent',
  } },
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px',
                         marginBottom: '10px', flexWrap: 'wrap' } },
      el('span.tag.' + severityClass(alert.severity), { text: alert.severity }),
      el('span.tag' + (isNew ? '.crit' : ''), { text: alert.status }),
      el('span.faint.mono', { style: { fontSize: '10px', marginLeft: 'auto' },
        text: fmtTime(alert.event_ts || alert.created_at) }),
    ),

    el('div', { style: { display: 'flex', gap: '12px' } },
      alert.evidence
        ? el('img.thumb', { src: api.evidenceUrl(alert.evidence), alt: '',
            style: { width: '84px', height: '48px' }, loading: 'lazy' })
        : null,
      el('div', { style: { flex: '1', minWidth: '0' } },
        plateChip(alert.plate, alert.match_confidence),
        el('div', { style: { fontSize: '11px', color: 'var(--text-mute)', marginTop: '6px' } },
          el('span.mono', { text: alert.camera_id }),
          alert.location ? ` · ${alert.location}` : ''),
        alert.category
          ? el('div', { style: { fontSize: '11px', color: 'var(--text-dim)', marginTop: '4px' },
              text: alert.category })
          : null,
      ),
    ),

    alert.message
      ? el('div.note', { style: { marginTop: '10px' }, text: alert.message })
      : null,

    /* A fuzzy plate match is a lead, not a fact, and is labelled that way. On
     * this footage a single-character OCR confusion is common enough that
     * hiding the distinction would put the wrong vehicle on a wanted list. */
    alert.match_confidence !== null && alert.match_confidence < 1
      ? el('div.note.warn', { style: { marginTop: '8px' },
          text: `Fuzzy match at ${Math.round(alert.match_confidence * 100)}% — the `
              + 'plate read differs from the watchlist entry by at least one '
              + 'character. Confirm against the frame before acting.' })
      : null,

    el('div', { style: { display: 'flex', gap: '7px', marginTop: '12px', flexWrap: 'wrap' } },
      el('button.btn.sm', { onclick: () => { mapView.selectCamera(alert.camera_id); } },
        icon('target', 12), 'Locate'),
      alert.plate
        ? el('button.btn.sm', { onclick: () => openTrace(alert.plate) },
            icon('trace', 12), 'Trace')
        : null,
      session.canAct && isNew
        ? el('button.btn.sm.primary', {
            onclick: async (event) => {
              try {
                await api.ackAlert(alert.id);
                toast('Acknowledged', `Recorded against ${session.user.username}.`, 'ok');
                render(event.target.closest('.panel-body') || document.body);
              } catch (error) { toast('Failed', error.message, 'crit'); }
            },
          }, icon('check', 12), 'Acknowledge')
        : null,
      session.canAct && alert.status === 'ACKNOWLEDGED'
        ? el('button.btn.sm', {
            onclick: async (event) => {
              try {
                await api.resolveAlert(alert.id);
                toast('Resolved', '', 'ok');
                render(event.target.closest('.panel-body') || document.body);
              } catch (error) { toast('Failed', error.message, 'crit'); }
            },
          }, 'Resolve')
        : null,
      alert.acknowledged_by
        ? el('span.faint', { style: { fontSize: '10px', alignSelf: 'center' },
            text: `by ${alert.acknowledged_by}` })
        : null,
    ),
  );
}

/* --------------------------------------------------------------- watchlist */

function renderWatchlist(host) {
  const list = el('div');

  const form = session.canAct ? el('form.panel-section', {
    onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      try {
        await api.addWatch({
          plate: data.get('plate'),
          category: data.get('category'),
          severity: data.get('severity'),
          notes: data.get('notes') || '',
        });
        event.target.reset();
        toast('Added to watchlist', 'Matched against every plate read from now on.', 'ok');
        renderWatchlist(host);
      } catch (error) { toast('Could not add', error.message, 'crit'); }
    },
  },
    el('div.label', { style: { marginBottom: '10px' }, text: 'Add a vehicle' }),
    el('div', { style: { display: 'grid', gap: '8px' } },
      el('input.field.mono', { name: 'plate', placeholder: 'GJ01AB1234', required: true }),
      el('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' } },
        el('select.field', { name: 'category' },
          ...['STOLEN', 'WANTED', 'SUSPECT', 'BLACKLIST', 'OTHER']
            .map((v) => el('option', { value: v, text: v }))),
        el('select.field', { name: 'severity' },
          ...['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
            .map((v) => el('option', { value: v, text: v, selected: v === 'HIGH' })))),
      el('input.field', { name: 'notes', placeholder: 'Reference or note (optional)' }),
      el('button.btn.primary', { type: 'submit' }, icon('plus', 13), 'Add to watchlist'),
    ),
  ) : null;

  fill(host, form, list);

  into(list, async () => {
    const entries = await api.watchlist();
    if (!entries.length) return null;
    return entries.map((entry) => el('div.row', {},
      el('div.row-main', {},
        el('div', {}, plateChip(entry.plate, null)),
        el('div.row-sub', { text: `${entry.category}${entry.notes ? ' · ' + entry.notes : ''}` })),
      el('div.row-side', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
        el('span.tag.' + severityClass(entry.severity), { text: entry.severity }),
        session.canAct
          ? el('button.icon-btn', {
              title: 'Remove',
              onclick: async () => {
                try {
                  await api.removeWatch(entry.id);
                  renderWatchlist(host);
                } catch (error) { toast('Could not remove', error.message, 'crit'); }
              },
            }, icon('close', 13))
          : null,
      ),
    ));
  }, { empty: 'The watchlist is empty.' });
}
