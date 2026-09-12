/* The map.
 *
 * The map is the product. Twenty-six departments run their own cameras and none
 * of the systems talk to each other; the single thing this platform adds is one
 * surface where all of them exist together. So the map is not a tab -- it is the
 * background of the whole application, and every panel floats over it.
 *
 * Rendered with MapLibre GL over the keyless vector basemap built in
 * basemap.js. See buildStyle() below for the order the three basemap options
 * are tried in, and basemap.js for why the styling is ours rather than a
 * vendor's dark theme.
 */

import { api } from './api.js';
import { state, set, subscribe, visibleCameras, camera as getCamera, isPlaced }
  from './store.js';
import { el, icon, fmtTime, plateChip, toast } from './ui.js';
import { vectorStyle, fallbackStyle, insertionPoint, setPalette } from './basemap.js';

/* Ahmedabad. The primary time cluster -- the nine cameras whose recordings
 * actually overlap and can therefore be used for cross-camera tracking -- is
 * all within a few km of here. */
const HOME = { center: [72.5714, 23.0225], zoom: 11.4 };

/* The basemap.
 *
 * Built in basemap.js -- see the long note at the top of that file for why the
 * geography is vector tiles styled by us rather than somebody's dark theme, and
 * why an operator's own raster server still wins when one is configured.
 *
 * Precedence, highest first:
 *   1. SENTINEL_BASEMAP_URL      -- the operator's own raster tile server
 *   2. the keyless vector basemap (default)
 *   3. neither, and the cached OSM geography carries the map on its own
 */
function buildStyle(config, themeName) {
  // The palette is global to basemap.js and read at construction, so it is
  // pointed at the right table immediately before the style is built rather
  // than threaded through every layer function.
  setPalette(themeName);
  if (config?.basemap_url) return fallbackStyle(config);
  return vectorStyle(config) || fallbackStyle(config);
}

/** True when the map already has geography of its own, so the heavy cached GIS
 *  road layers should stay off until an operator asks for them. Those files run
 *  to several megabytes each and duplicate what the basemap already draws. */
export function hasBasemap(config) {
  return Boolean(config?.basemap_url || config?.vector_tiles_url);
}

let mapConfig = null;
let mapTheme = 'dark';
let map = null;
let ready = false;
let basemapFailed = false;
/* Callbacks run once if the basemap turns out to be unreachable. The shell
 * registers one that switches the cached geography layers on, so an isolated
 * network still gets a map rather than an empty canvas. */
const onBasemapLost = [];
export const whenBasemapLost = (fn) => onBasemapLost.push(fn);
const pending = [];
const cameraMarkers = new Map();     // camera_id -> maplibregl.Marker
const poiMarkers = new Map();        // layer key -> Marker[]
/* Line and area layers belong to the *style*, so setStyle destroys them. Their
 * GeoJSON is kept here so a theme change can put them back without re-fetching
 * several megabytes from the API. POI layers are DOM markers and survive on
 * their own, which is why only these are tracked. */
const gisLineData = new Map();       // layer key -> GeoJSON
let tracePopup = null;

/** Queue work until the style has loaded. Adding a source before 'load' throws,
 *  and layer toggles can legitimately arrive during startup. */
function whenReady(fn) {
  if (ready) fn();
  else pending.push(fn);
}

export function init(container, config = null, themeName = 'dark') {
  mapConfig = config;
  mapTheme = themeName;
  if (!window.maplibregl) {
    container.append(el('div.empty', {},
      el('div', { style: { color: 'var(--warn)' }, text: 'Map library unavailable' }),
      el('div', { style: { marginTop: '6px', maxWidth: '380px' },
        text: 'maplibre-gl could not be loaded from the CDN. Every panel still '
            + 'works; only the map surface is missing.' })));
    return null;
  }

  map = new maplibregl.Map({
    container,
    style: buildStyle(config, themeName),
    center: HOME.center,
    zoom: HOME.zoom,
    // Suppressed here and added explicitly below. Leaving the built-in one on
    // as well produced two stacked attribution boxes in the corner, one of them
    // crediting the same OpenStreetMap data twice.
    attributionControl: false,
    // Pitch is available but not on by default: a tilted map looks impressive
    // and makes comparing two pin positions harder, which is the actual task.
    pitch: 0,
    maxZoom: 18,
    minZoom: 4,
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  // The geography layers are OpenStreetMap data under ODbL whether or not a
  // raster basemap is configured, so the credit is unconditional.
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution:
      'Geography &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
      + 'contributors (ODbL)',
  }));

  map.on('load', () => {
    ready = true;
    addTraceSource();
    while (pending.length) pending.shift()();
    renderCameras();
  });

  map.on('error', (event) => {
    // Tile 404s are noisy and harmless; anything else is worth knowing about.
    if (event?.error?.status === 404) return;
    // The basemap is fetched from the public internet, and this platform is
    // specified to run on networks where that may simply not resolve. Losing
    // the tile server must degrade the map, never break it: fall back to the
    // geography we cached for ourselves and say so once.
    if (event?.sourceId === 'openmaptiles' && !basemapFailed) {
      basemapFailed = true;
      console.warn('basemap unreachable; falling back to cached GIS geography');
      onBasemapLost.forEach((fn) => fn());
      return;
    }
    console.warn('map error', event?.error?.message || event);
  });

  subscribe(['cameras', 'filters', 'selectedCamera', 'wall'], renderCameras);
  subscribe('trace', renderTrace);

  return map;
}

export const instance = () => map;

/** Rebuild the basemap for a new theme.
 *
 *  A MapLibre style bakes its colours into its layers at construction, so there
 *  is no repainting it in place -- the style is rebuilt and swapped. That is
 *  cheap for the basemap (the vector tiles are already in cache; only the paint
 *  changes) and destructive for everything this application added on top, which
 *  is the part that needs care:
 *
 *    * Camera pins, POI pins and trace waypoints are DOM `Marker`s. Markers are
 *      attached to the *map*, not to the style, so they survive untouched. This
 *      is the main reason the swap is tolerable at all.
 *    * The trace source and its two layers, and any GIS line layers, are style
 *      objects and are destroyed. They are re-added on `style.load`, the trace
 *      data re-set from `state` and the GIS geometry replayed from the cache in
 *      `gisLineData` -- never re-fetched, since those files run to megabytes.
 *
 *  Guarded on the resolved theme rather than the preference: switching from
 *  "dark" to "auto" on a dark OS resolves to the same map, and rebuilding it
 *  would blink the basemap for no visible change.
 */
export function restyle(themeName) {
  if (!map || themeName === mapTheme) return;
  mapTheme = themeName;
  ready = false;

  /* Put back everything the style swap destroyed.
   *
   * Waiting on the right signal here is the whole trick, and the two obvious
   * choices are both wrong on MapLibre 4.7.1. Measured on this version:
   *
   *   * **`style.load` never fires for a `setStyle`** -- only for the map's
   *     initial style. A `once('style.load')` here simply never runs, and the
   *     failure is silent and total: the basemap recolours correctly, so it all
   *     looks fine, while the trace and every GIS layer are quietly gone until
   *     the next full reload.
   *   * **`styledata` fires exactly once, and `isStyleLoaded()` is still false
   *     when it does.** So the usual "re-arm until the style is ready" loop
   *     waits for a second `styledata` that never comes, and hangs.
   *
   * What does arrive in a usable state is `idle`. Both events are therefore
   * listened to and the handler is made idempotent and self-detaching, so
   * whichever one first turns up with the style actually loaded does the work
   * and the other is a no-op. Guessing which single event to trust is what
   * broke this twice.
   */
  let rebuilt = false;
  const rebuild = () => {
    if (rebuilt || !map.isStyleLoaded()) return;
    rebuilt = true;
    map.off('styledata', rebuild);
    map.off('idle', rebuild);

    ready = true;
    addTraceSource();
    for (const [key, data] of gisLineData) {
      const spec = state.gisLayers.find((l) => l.key === key);
      if (!spec || map.getSource(`gis-${key}`)) continue;
      map.addSource(`gis-${key}`, { type: 'geojson', data });
      map.addLayer({
        id: `gis-${key}`, type: 'line', source: `gis-${key}`,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: linePaint(spec),
      }, firstMarkerBeforeLayer());
    }
    while (pending.length) pending.shift()();
    // Pin classes encode status and selection, neither of which changed -- but
    // the markers were built against the old ground and their halos are sized
    // for it, so they are re-rendered rather than left.
    renderCameras();
    renderTrace();
  };

  map.on('styledata', rebuild);
  map.on('idle', rebuild);
  map.setStyle(buildStyle(mapConfig, themeName));
}

/* ==================================================================== cameras */

function markerClasses(cam) {
  const classes = ['cam'];
  classes.push((cam.status || 'unknown').toLowerCase());
  if (cam.ai_enabled) classes.push('anpr');
  if (cam.location_accuracy === 'APPROXIMATE' || cam.location_accuracy === 'UNKNOWN') {
    classes.push('approx');
  }
  if (state.selectedCamera === cam.camera_id) classes.push('selected');
  return classes.join(' ');
}

export function renderCameras() {
  if (!map) return;
  const visible = new Set(visibleCameras().map((c) => c.camera_id));

  for (const cam of state.cameras) {
    // A camera without a trustworthy position is not drawn at all. Placing it
    // at a guessed coordinate would put a pin an operator might dispatch
    // against somewhere nobody chose. Note this tests accuracy, not just the
    // presence of numbers -- an UNKNOWN position is not a position.
    const show = isPlaced(cam) && visible.has(cam.camera_id);
    let marker = cameraMarkers.get(cam.camera_id);

    if (!show) {
      if (marker) { marker.remove(); cameraMarkers.delete(cam.camera_id); }
      continue;
    }

    if (!marker) {
      const node = el('button.cam', {
        title: `${cam.camera_id} — ${cam.location_name || cam.name}`,
        'aria-label': `Camera ${cam.camera_id}`,
      });
      node.addEventListener('click', (event) => {
        event.stopPropagation();
        selectCamera(cam.camera_id);
      });
      marker = new maplibregl.Marker({ element: node })
        .setLngLat([cam.longitude, cam.latitude])
        .addTo(map);
      cameraMarkers.set(cam.camera_id, marker);
    } else {
      marker.setLngLat([cam.longitude, cam.latitude]);
    }
    marker.getElement().className = markerClasses(cam);
  }
}

export function selectCamera(cameraId) {
  set({ selectedCamera: cameraId, panel: 'camera' });
  const cam = getCamera(cameraId);
  if (cam && cam.latitude !== null && map) {
    map.easeTo({
      center: [cam.longitude, cam.latitude],
      zoom: Math.max(map.getZoom(), 14),
      // Nudge left so the pin does not end up underneath the slide-over.
      offset: [-180, 0],
      duration: 700,
    });
  }
}

/** Flash a camera pin and move to it — used when a watchlist alert lands. */
export function flashCamera(cameraId) {
  const marker = cameraMarkers.get(cameraId);
  if (!marker) return;
  const node = marker.getElement();
  node.classList.add('hit');
  setTimeout(() => node.classList.remove('hit'), 9000);
  const cam = getCamera(cameraId);
  if (cam?.latitude != null && map) {
    map.flyTo({ center: [cam.longitude, cam.latitude], zoom: 14.5, duration: 1400 });
  }
}

export function fitToCameras() {
  if (!map) return;
  const placed = visibleCameras().filter((c) => c.latitude !== null);
  if (!placed.length) { map.easeTo({ ...HOME, duration: 600 }); return; }
  const bounds = placed.reduce(
    (acc, c) => acc.extend([c.longitude, c.latitude]),
    new maplibregl.LngLatBounds(
      [placed[0].longitude, placed[0].latitude],
      [placed[0].longitude, placed[0].latitude]));
  map.fitBounds(bounds, { padding: { top: 80, bottom: 80, left: 320, right: 440 }, maxZoom: 14 });
}

export const home = () => map?.easeTo({ ...HOME, duration: 700 });

/* ================================================================ GIS layers */

/** Add or remove one context layer. Lines and areas become GL layers; points
 *  become DOM markers, which keeps them clickable and consistent with cameras. */
export async function setGisLayer(key, on) {
  const spec = state.gisLayers.find((l) => l.key === key);
  if (!spec) return;

  if (!on) {
    whenReady(() => {
      if (map.getLayer(`gis-${key}`)) map.removeLayer(`gis-${key}`);
      if (map.getSource(`gis-${key}`)) map.removeSource(`gis-${key}`);
    });
    (poiMarkers.get(key) || []).forEach((m) => m.remove());
    poiMarkers.delete(key);
    gisLineData.delete(key);
    return;
  }

  let data;
  try {
    data = await api.gisLayer(key);
  } catch (error) {
    if (error.status === 404) {
      toast('Layer not fetched yet', `Refresh "${spec.label}" to pull it from OpenStreetMap.`, 'warn');
    } else {
      toast('Could not load layer', error.message, 'crit');
    }
    throw error;
  }

  if (spec.kind === 'point') {
    const markers = data.features.slice(0, 3000).map((feature) => {
      const node = el('button.poi', {
        title: feature.properties.name || spec.label,
        style: { background: spec.colour },
      });
      node.addEventListener('click', (event) => {
        event.stopPropagation();
        new maplibregl.Popup({ offset: 10, closeButton: false })
          .setLngLat(feature.geometry.coordinates)
          .setDOMContent(el('div', { style: { padding: '10px 12px' } },
            el('div', { style: { fontSize: '12px', fontWeight: '600' },
              text: feature.properties.name || '(unnamed)' }),
            el('div.faint', { style: { fontSize: '10px', marginTop: '3px' }, text: spec.label })))
          .addTo(map);
      });
      return new maplibregl.Marker({ element: node })
        .setLngLat(feature.geometry.coordinates).addTo(map);
    });
    poiMarkers.set(key, markers);
    return;
  }

  gisLineData.set(key, data);
  whenReady(() => {
    if (map.getSource(`gis-${key}`)) return;
    map.addSource(`gis-${key}`, { type: 'geojson', data });
    map.addLayer({
      id: `gis-${key}`,
      type: 'line',
      source: `gis-${key}`,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: linePaint(spec),
    }, firstMarkerBeforeLayer());
  });
}

/* Road and boundary styling.
 *
 * With no raster basemap these lines *are* the map, so they scale with zoom
 * rather than sitting at one hairline width: at state zoom the highway network
 * should read as a network, and at junction zoom it should read as a road the
 * pins sit beside. Boundaries stay dashed and dim at every zoom -- a district
 * line is context, and must never be mistaken for something a vehicle drove
 * along.
 */
function linePaint(spec) {
  if (spec.kind === 'area') {
    return {
      'line-color': spec.colour,
      'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.6, 10, 1.2, 14, 1.6],
      'line-opacity': 0.45,
      'line-dasharray': [3, 2],
    };
  }
  // Three weights, so the road hierarchy is legible at a glance: national
  // highways heaviest, the urban grid lightest. Without that separation a city
  // at zoom 12 is an undifferentiated mat of lines.
  const weight = { highways: 1.0, trunk: 0.62, city: 0.4 }[spec.key] ?? 0.5;
  return {
    'line-color': spec.colour,
    'line-width': ['interpolate', ['linear'], ['zoom'],
      5, 0.7 * weight, 9, 1.7 * weight, 13, 3.4 * weight, 16, 6.5 * weight],
    'line-opacity': { highways: 0.8, trunk: 0.55, city: 0.45 }[spec.key] ?? 0.5,
  };
}

/** Where a context layer is inserted.
 *
 *  Underneath the trace line, so a route is never hidden by the roads it is
 *  drawn against; and underneath the basemap's place names, so a layer of
 *  highways cannot bury the name of the town an operator is looking for.
 */
function firstMarkerBeforeLayer() {
  if (map.getLayer('trace-glow')) return 'trace-glow';
  return insertionPoint(map);
}

/* ===================================================================== trace */

function addTraceSource() {
  const seam = insertionPoint(map);
  map.addSource('trace', { type: 'geojson', data: emptyCollection() });
  map.addLayer({
    id: 'trace-glow',
    type: 'line',
    source: 'trace',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#a78bfa', 'line-width': 11, 'line-opacity': 0.16, 'line-blur': 5 },
  }, seam);
  // Only corroborated journeys are ever drawn, so this layer is a single solid
  // style. The uncorroborated case is handled by not drawing a line at all:
  // where two sightings fall in different recording windows the backend splits
  // them into separate segments, and nothing joins the gap. That is deliberate
  // -- a dashed "probably went this way" line is still a claim, and this grid
  // gives us no basis for one.
  //
  // (line-dasharray is also not data-driven in MapLibre, so a per-feature dash
  // could not have been expressed here anyway.)
  map.addLayer({
    id: 'trace-line',
    type: 'line',
    source: 'trace',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#a78bfa', 'line-width': 2.4 },
  }, seam);
}

const emptyCollection = () => ({ type: 'FeatureCollection', features: [] });

const waypointMarkers = [];

function clearTrace() {
  waypointMarkers.forEach((m) => m.remove());
  waypointMarkers.length = 0;
  tracePopup?.remove();
  tracePopup = null;
  whenReady(() => map.getSource('trace')?.setData(emptyCollection()));
}

export function renderTrace() {
  if (!map) return;
  clearTrace();
  const trace = state.trace;
  if (!trace || !trace.points?.length) return;

  const features = [];
  for (const segment of trace.route_segments || []) {
    const line = segment
      .filter((p) => p.latitude !== null && p.longitude !== null)
      .map((p) => [p.longitude, p.latitude]);
    if (line.length >= 2) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: line },
        properties: { inferred: false },
      });
    }
  }

  whenReady(() => map.getSource('trace')?.setData(
    { type: 'FeatureCollection', features }));

  const placed = trace.points.filter((p) => p.latitude !== null && p.longitude !== null);
  placed.forEach((point, index) => {
    const node = el('button.waypoint', { text: String(index + 1),
      title: `${point.camera_id} — ${point.location}` });
    node.addEventListener('click', (event) => {
      event.stopPropagation();
      set({ tracePosition: index });
      showWaypoint(point, index);
    });
    waypointMarkers.push(new maplibregl.Marker({ element: node })
      .setLngLat([point.longitude, point.latitude]).addTo(map));
  });

  if (placed.length) {
    const bounds = placed.reduce(
      (acc, p) => acc.extend([p.longitude, p.latitude]),
      new maplibregl.LngLatBounds(
        [placed[0].longitude, placed[0].latitude],
        [placed[0].longitude, placed[0].latitude]));
    map.fitBounds(bounds, {
      padding: { top: 90, bottom: 90, left: 340, right: 460 },
      maxZoom: 15, duration: 900,
    });
  }
}

/** Highlight one point of a trace — driven by the playback slider. */
export function focusWaypoint(index) {
  const placed = (state.trace?.points || [])
    .filter((p) => p.latitude !== null && p.longitude !== null);
  waypointMarkers.forEach((marker, i) =>
    marker.getElement().classList.toggle('current', i === index));
  const point = placed[index];
  if (!point || !map) return;
  map.easeTo({ center: [point.longitude, point.latitude],
    zoom: Math.max(map.getZoom(), 13.5), offset: [-180, 0], duration: 620 });
  showWaypoint(point, index);
}

function showWaypoint(point, index) {
  tracePopup?.remove();
  const content = el('div', { style: { padding: '12px 14px', width: '236px' } },
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
      el('span.tag.trace', { text: `Stop ${index + 1}` }),
      el('span.mono.faint', { style: { fontSize: '10px' }, text: point.camera_id })),
    el('div', { style: { fontSize: '12px', fontWeight: '600', marginTop: '8px' },
      text: point.location || point.camera_id }),
    el('div.faint', { style: { fontSize: '10px', marginTop: '3px' },
      text: fmtTime(point.timestamp) }),
    el('div', { style: { marginTop: '9px' } }, plateChip(point.plate, point.plate_confidence)),
    point.evidence
      ? el('img', { src: api.evidenceUrl(point.evidence), alt: 'Evidence frame',
          style: { width: '100%', marginTop: '10px', borderRadius: '6px',
                   border: '1px solid var(--line)' } })
      : null,
  );
  tracePopup = new maplibregl.Popup({ offset: 16, closeButton: true, maxWidth: '260px' })
    .setLngLat([point.longitude, point.latitude])
    .setDOMContent(content)
    .addTo(map);
}
