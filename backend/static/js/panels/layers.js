/* The layer control.
 *
 * Two questions that look like one and are not:
 *
 *   "Which cameras am I responsible for?"  -> owning department
 *   "Which cameras can read a plate?"      -> ANPR capability
 *
 * A single combined filter can answer either one but never both, so they are
 * separate groups here. An operator hunting a registration wants every camera
 * able to read one, whoever owns it.
 *
 * Every context layer states why it is worth the clutter. A layer nobody
 * understands is a layer nobody turns on.
 */

import { api } from '../api.js';
import { state, set, notify, subscribe, visibleCameras, unplacedCount }
  from '../store.js';
import { el, fill, icon, toast, fmtNum } from '../ui.js';
import * as mapView from '../map.js';

export function mount(host) {
  const body = el('div.layer-body');
  const head = el('div.float-head', {},
    el('span.float-title', { text: 'Layers' }),
    el('button.icon-btn', {
      title: 'Collapse',
      onclick: () => host.classList.toggle('collapsed'),
    }, icon('chevron', 14)),
  );
  fill(host, head, body);

  const render = () => renderInto(body);
  subscribe(['cameras', 'facets', 'filters', 'gisLayers'], render);
  render();
}

function renderInto(body) {
  const facets = state.facets;
  const shown = visibleCameras().length;
  const total = state.cameras.length;
  const unplaced = unplacedCount();

  fill(body,
    /* --- Capability ---------------------------------------------------- */
    group('Plate-reading capability',
      toggle({
        label: 'ANPR-capable only',
        colour: 'var(--accent)',
        count: facets ? facets.anpr_capable : null,
        on: state.filters.anprOnly,
        onclick: () => {
          state.filters.anprOnly = !state.filters.anprOnly;
          notify('filters');
        },
      }),
      why('Ownership and capability are different questions. Switch this on '
        + 'and the estate drops to the cameras that can actually read a '
        + 'registration, whoever owns them.'),
    ),

    /* --- Ownership ----------------------------------------------------- */
    facets?.departments?.length
      ? group('Owning department',
          ...facets.departments.map((entry) => toggle({
            label: entry.value,
            colour: 'var(--signal)',
            count: entry.count,
            on: state.filters.departments.has(entry.value),
            onclick: () => {
              const set_ = state.filters.departments;
              set_.has(entry.value) ? set_.delete(entry.value) : set_.add(entry.value);
              notify('filters');
            },
          })),
          why('A camera is a point of interest with a category, the way a maps '
            + 'app treats fuel stations. Each department toggles independently.'))
      : null,

    /* --- Health -------------------------------------------------------- */
    facets?.statuses?.length
      ? group('Reported status',
          ...facets.statuses.map((entry) => toggle({
            label: entry.value,
            colour: { ONLINE: 'var(--signal)', DEGRADED: 'var(--warn)',
                      OFFLINE: 'var(--critical)' }[entry.value] || 'var(--text-mute)',
            count: entry.count,
            on: state.filters.statuses.has(entry.value),
            onclick: () => {
              const set_ = state.filters.statuses;
              set_.has(entry.value) ? set_.delete(entry.value) : set_.add(entry.value);
              notify('filters');
            },
          })),
          why('What the grid claims. The Health panel measures it instead — '
            + 'this grid reports every camera as live, including the ones that '
            + 'are not.'))
      : null,

    /* --- GIS context --------------------------------------------------- */
    group('Geography',
      ...state.gisLayers.map(gisToggle),
      why('The geography a route is read against. Fetched live from '
        + 'OpenStreetMap and cached — never baked in, so every count is a real '
        + 'query result rather than a decorative number.')),

    /* --- Unplaced estate ------------------------------------------------
     * An unplaced camera is invisible on a map-first interface, which is
     * exactly how an estate loses track of cameras nobody is accountable for.
     * So the count is stated, and it is a way in rather than a warning. */
    unplaced ? group('Not on the map',
      toggle({
        label: 'Cameras without a position',
        colour: 'var(--warn)',
        count: unplaced,
        on: state.filters.placement === 'unplaced',
        onclick: () => {
          state.filters.placement =
            state.filters.placement === 'unplaced' ? 'all' : 'unplaced';
          notify('filters');
          set({ panel: 'cameras' });
        },
      }),
      why(`${unplaced} of ${total} cameras have no position anyone has vouched `
        + 'for, so they are not drawn rather than pinned at a guess. Open the '
        + 'list to place them — each correction is recorded against your name.'))
      : null,

    /* --- Footer -------------------------------------------------------- */
    el('div', { style: { padding: '10px 8px 4px', borderTop: '1px solid var(--line)',
                         marginTop: '12px', display: 'flex', alignItems: 'center',
                         gap: '8px' } },
      el('span.mono', { style: { fontSize: '11px', color: 'var(--text-dim)' },
        text: `${fmtNum(shown)} / ${fmtNum(total)} cameras` }),
      el('button.btn.sm.ghost', {
        style: { marginLeft: 'auto' },
        onclick: () => {
          state.filters.departments.clear();
          state.filters.statuses.clear();
          state.filters.anprOnly = false;
          state.filters.placement = 'all';
          state.filters.query = '';
          notify('filters');
        },
      }, 'Reset'),
      el('button.btn.sm.ghost', { title: 'Zoom to the visible cameras',
        onclick: () => mapView.fitToCameras() }, icon('expand', 12)),
    ),
  );
}

function group(title, ...children) {
  return el('div.layer-group', {}, el('div.layer-group-title', { text: title }), ...children);
}

function why(text) { return el('div.layer-why', { text }); }

function toggle({ label, colour, count, on, onclick, status }) {
  // count may be a number (a tally) or a word ("fetch", "fetching…"). Running
  // fmtNum over a word yields "NaN", which reads as a broken layer rather than
  // one that simply has not been fetched yet.
  const badge = typeof count === 'number' ? fmtNum(count) : count;
  return el('button.layer' + (on ? '.on' : ''), { onclick, dataset: { status: status || '' } },
    el('span.swatch', { style: { color: colour } }),
    el('span.lname', { text: label }),
    badge === null || badge === undefined ? null : el('span.lcount', { text: badge }),
  );
}

/* ------------------------------------------------------------ GIS layers */

function gisToggle(spec) {
  const on = state.activeGis.has(spec.key);
  const fetching = spec.status === 'fetching';

  const countLabel = fetching ? 'fetching…'
    : spec.cached ? fmtNum(spec.feature_count)
    : 'fetch';

  const node = toggle({
    label: spec.label,
    colour: spec.colour,
    count: countLabel,
    on,
    status: spec.status,
    onclick: async () => {
      if (!spec.cached) { await refresh(spec); return; }
      if (on) {
        state.activeGis.delete(spec.key);
        mapView.setGisLayer(spec.key, false);
      } else {
        state.activeGis.add(spec.key);
        try { await mapView.setGisLayer(spec.key, true); }
        catch { state.activeGis.delete(spec.key); }
      }
      notify('gisLayers');
    },
  });

  // A refresh button only where refreshing is meaningful, and only for roles
  // that may act — Overpass is a shared free service and this is a real
  // outbound query, not a local recompute.
  if (spec.cached) {
    node.append(el('span.icon-btn', {
      style: { width: '20px', height: '20px' },
      title: 'Re-fetch from OpenStreetMap',
      onclick: (event) => { event.stopPropagation(); refresh(spec); },
    }, icon('refresh', 11)));
  }
  return node;
}

async function refresh(spec) {
  try {
    await api.gisRefresh(spec.key);
    toast(`Fetching ${spec.label}`,
      'Querying OpenStreetMap via Overpass. Large layers take up to a minute.', 'info');
    poll(spec.key);
  } catch (error) {
    toast('Could not start fetch', error.message, 'crit');
  }
}

/* Poll until the background fetch settles. Overpass queries for road layers
 * take tens of seconds, which is far too long to hold an HTTP request open. */
async function poll(key, attempts = 40) {
  for (let i = 0; i < attempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let layers;
    try { layers = await api.gisLayers(); } catch { return; }
    set({ gisLayers: layers });
    const layer = layers.find((l) => l.key === key);
    if (!layer || layer.status === 'fetching') continue;

    if (layer.status === 'ready') {
      toast(`${layer.label} ready`,
        `${fmtNum(layer.feature_count)} features from OpenStreetMap.`, 'ok');
    } else {
      toast(`${layer.label} unavailable`,
        layer.error || 'Overpass did not answer. The layer stays empty rather '
                     + 'than showing invented features.', 'warn', 9000);
    }
    return;
  }
}

export async function load() {
  try { set({ gisLayers: await api.gisLayers() }); }
  catch { /* the panel renders with no context layers, which is honest */ }
}

/** Switch on the geography that stands in for a basemap.
 *
 *  Only layers already cached are enabled -- this must never trigger an
 *  Overpass query on page load. Anything missing is fetched once, in the
 *  background, and appears when it lands.
 */
export async function enableBaseGeography(keys) {
  const missing = [];

  for (const key of keys) {
    const spec = state.gisLayers.find((l) => l.key === key);
    if (!spec) continue;
    if (!spec.cached) { missing.push(spec); continue; }
    state.activeGis.add(key);
    try { await mapView.setGisLayer(key, true); }
    catch { state.activeGis.delete(key); }
  }
  notify('gisLayers');

  if (!missing.length) return;

  toast('Fetching map geography',
    `${missing.map((l) => l.label).join(', ')} — one-off query to OpenStreetMap. `
    + 'The map fills in as each layer lands.', 'info', 8000);

  for (const spec of missing) {
    try {
      await api.gisRefresh(spec.key);
      pollThenEnable(spec.key);
    } catch { /* reported by the layer's own state */ }
  }
}

async function pollThenEnable(key, attempts = 40) {
  for (let i = 0; i < attempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let layers;
    try { layers = await api.gisLayers(); } catch { return; }
    set({ gisLayers: layers });
    const layer = layers.find((l) => l.key === key);
    if (!layer || layer.status === 'fetching') continue;
    if (layer.status === 'ready') {
      state.activeGis.add(key);
      try { await mapView.setGisLayer(key, true); }
      catch { state.activeGis.delete(key); }
      notify('gisLayers');
    }
    return;
  }
}
