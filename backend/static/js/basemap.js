/* The Sentinel dark basemap.
 *
 * Why this file exists
 * --------------------
 * Until now the map had no basemap at all. The reasoning was sound -- every
 * free *raster* dark basemap either watermarks anonymous requests (CARTO stamps
 * "API KEY REQUIRED" across every tile) or forbids application traffic outright
 * (the OSM volunteer servers answer 418) -- but the result was a near-black
 * canvas with a few thousand unstyled OpenStreetMap line segments on it.
 * Defensible, and genuinely hard to read.
 *
 * The missing option was vector tiles. OpenFreeMap serves the full OpenMapTiles
 * planet with no API key, no account, no watermark and no rate limit, which
 * means the geography can come from a real basemap *and* stay off anyone's
 * billing account. And because the tiles are vector, the styling is ours: this
 * is not somebody else's dark theme, it is a palette built to sit underneath
 * the five signal hues in tokens.css without competing with any of them.
 *
 * The rule the palette follows
 * ----------------------------
 * The basemap may use greys, one desaturated blue for water and one desaturated
 * green for parkland. It may not use accent blue, signal green, warn amber,
 * critical red or trace purple, because in this interface each of those means
 * exactly one operational thing. A road drawn in trace purple would read as a
 * vehicle's journey.
 *
 * Offline fallback
 * ----------------
 * An isolated operator network may not resolve tiles.openfreemap.org at all.
 * When the basemap is unset or unreachable the map falls back to the cached
 * OpenStreetMap geography this platform fetched for itself -- the behaviour
 * that used to be the only behaviour. Nothing here is load-bearing.
 */

/* Two palettes, one geometry.
 *
 * Everything below the fold of this block -- which layers exist, what they
 * filter on, how wide a road is at which zoom -- is shared. Only the colours
 * fork, which is the whole reason a light basemap was tractable at all: the
 * style is built from a `P` object, so adding a theme meant adding a second
 * table of values rather than a second style.
 *
 * The palette rule survives both themes unchanged: **neutrals do the work, one
 * desaturated blue for water, one desaturated green for parkland, and never a
 * signal hue.** Accent blue, signal green, warn amber, critical red and trace
 * purple each mean exactly one operational thing in this interface. A road
 * drawn in trace purple would read as a vehicle's journey.
 */

/* Dark: the default, and the one tuned against the real deployment.
 *
 * Ground is kept a touch above pure black -- a true #000 basemap makes the
 * translucent panels float on nothing and destroys the depth the layout is
 * built on.
 *
 * Luminance, not hue, is what was wrong with the first pass of this palette.
 * Every colour sat within about 12 L* of the ground, which is a defensible
 * choice on a calibrated monitor in a dark room and unreadable on the display
 * this actually runs on -- a wall panel, at an angle, in a room kept dim but
 * not black. The ladder has been lifted roughly 8-14 L* per step.
 */
const DARK = {
  ground: '#0a0e14',
  water: '#10293f',
  waterDeep: '#0d2033',
  park: '#0e1a13',
  residential: '#10151d',
  industrial: '#131821',
  building: '#1a222e',
  buildingHi: '#212b38',

  /* Road hierarchy. Six steps, each a legible increment lighter than the last,
   * so the network reads as a network at state zoom and as streets at junction
   * zoom. The steps are even in *luminance* rather than even in hex: a motorway
   * has to be separable from a trunk road at a glance while an operator is
   * reading a trace across it, and that separation is what the ladder buys. */
  road: {
    motorway:  '#6a82a1',
    trunk:     '#5a6f8b',
    primary:   '#4a5c73',
    secondary: '#3b4a5d',
    minor:     '#2e3947',
    service:   '#232c38',
    rail:      '#39465a',
  },
  casing: '#0b0f16',

  boundary: '#5c7398',
  label: '#c2d1e3',
  labelDim: '#8b9db4',
  labelWater: '#7098bb',
  halo: '#070a10',
};

/* Light: for a lit office, a projector, or a screenshot pasted into a report.
 *
 * Not an inversion. Inverting the dark ladder puts the *roads* darker than the
 * ground, which is right, but it also puts motorways at near-black and service
 * roads at pale grey -- exactly backwards, because under light the heaviest
 * road should be the darkest and the ladder has to run the other way. So the
 * road ramp is rebuilt rather than flipped, and it is compressed: on white,
 * six evenly-spaced greys separate far more clearly than they do on black, so
 * the steps can be closer together without the network turning into a mat.
 *
 * The ground is a warm off-white rather than #fff. Pure white behind a
 * translucent panel leaves the panel with nothing to be translucent against,
 * which is the same mistake as a #000 ground under the dark theme.
 */
const LIGHT = {
  ground: '#eef1f5',
  water: '#c2d8e8',
  waterDeep: '#aec9dd',
  park: '#d8e6d4',
  residential: '#e7eaef',
  industrial: '#e2e5eb',
  building: '#dbe0e8',
  buildingHi: '#ccd4de',

  road: {
    motorway:  '#8a99ad',
    trunk:     '#98a6b8',
    primary:   '#a7b3c3',
    secondary: '#b6c0cd',
    minor:     '#c6ced8',
    service:   '#d4dae2',
    rail:      '#b0bac7',
  },
  /* Under dark the casing is darker than the road it sits beneath. Under light
   * it has to be *lighter* -- near the ground colour -- or every road gains a
   * hard black outline and the map reads as a wiring diagram. */
  casing: '#f7f9fb',

  boundary: '#9aa8bd',
  label: '#2b3646',
  labelDim: '#5a6878',
  labelWater: '#4d7a9b',
  halo: '#f7f9fb',
};

/* The live palette. Swapped by `setPalette` before a style is built; the map
 * rebuilds its style on a theme change rather than trying to repaint in place,
 * because a MapLibre style's colours are baked into its layers at construction. */
let P = DARK;

/** Point the style builders at one of the two palettes. Called by map.js
 *  immediately before `vectorStyle`/`fallbackStyle`, never on its own -- the
 *  palette only takes effect on the next style built. */
export function setPalette(themeName) {
  P = themeName === 'light' ? LIGHT : DARK;
  return P;
}

export const paletteName = () => (P === LIGHT ? 'light' : 'dark');

/* OpenFreeMap serves Noto Sans in Regular and Bold only -- Medium 404s, and a
 * missing fontstack makes MapLibre drop the whole symbol layer silently. */
const FONT = ['Noto Sans Regular'];
const FONT_BOLD = ['Noto Sans Bold'];

/** Interpolate a line width across zoom. Written once rather than inlined at
 *  each call site: every road layer needs the same shape of ramp, and a
 *  transcription slip in one of them stays invisible until someone zooms in. */
const width = (stops) => ['interpolate', ['exponential', 1.4], ['zoom'], ...stops];

const isClass = (...classes) => ['match', ['get', 'class'], classes, true, false];

/* Roads are drawn as a casing plus a fill -- a darker line underneath a lighter
 * one. Without it, two roads crossing at a shallow angle merge into a single
 * shape, and an operator reading a trace cannot tell whether the vehicle had a
 * junction available to it there. */
function roadPair(id, filter, colour, casingStops, fillStops, minzoom = 0) {
  return [
    {
      id: id + '-casing', type: 'line', source: 'openmaptiles',
      'source-layer': 'transportation', minzoom, filter,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': P.casing, 'line-width': width(casingStops) },
    },
    {
      id, type: 'line', source: 'openmaptiles',
      'source-layer': 'transportation', minzoom, filter,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': colour, 'line-width': width(fillStops) },
    },
  ];
}

/** Build the full MapLibre style document.
 *
 *  @param config  the payload from /api/config/map
 *  @returns       a style object, or null when no vector basemap is configured
 */
export function vectorStyle(config) {
  const tiles = config && config.vector_tiles_url;
  if (!tiles) return null;

  return {
    version: 8,
    glyphs: config.vector_glyphs_url,
    sources: {
      openmaptiles: {
        type: 'vector',
        url: tiles,
        attribution: config.vector_attribution
          || '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> '
           + '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': P.ground } },

      /* ---------------------------------------------------------- surfaces */

      {
        id: 'landuse-residential', type: 'fill', source: 'openmaptiles',
        'source-layer': 'landuse', minzoom: 8,
        filter: isClass('residential', 'suburb', 'neighbourhood'),
        paint: {
          'fill-color': P.residential,
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0, 11, 0.9],
        },
      },
      {
        id: 'landuse-industrial', type: 'fill', source: 'openmaptiles',
        'source-layer': 'landuse', minzoom: 10,
        filter: isClass('industrial', 'commercial', 'retail'),
        paint: { 'fill-color': P.industrial, 'fill-opacity': 0.85 },
      },
      {
        id: 'landcover', type: 'fill', source: 'openmaptiles',
        'source-layer': 'landcover',
        filter: isClass('wood', 'grass', 'farmland'),
        paint: {
          'fill-color': P.park,
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.4, 12, 0.85],
        },
      },
      {
        id: 'park', type: 'fill', source: 'openmaptiles', 'source-layer': 'park',
        minzoom: 9,
        paint: { 'fill-color': P.park, 'fill-opacity': 0.9 },
      },

      /* ------------------------------------------------------------- water */

      {
        id: 'water', type: 'fill', source: 'openmaptiles', 'source-layer': 'water',
        filter: ['!=', ['get', 'brunnel'], 'tunnel'],
        paint: {
          'fill-color': ['match', ['get', 'class'], ['ocean'], P.waterDeep, P.water],
          'fill-antialias': true,
        },
      },
      {
        // The Sabarmati runs through the middle of every camera on this grid,
        // so the river is a landmark an operator orients by, not decoration.
        id: 'waterway', type: 'line', source: 'openmaptiles',
        'source-layer': 'waterway', minzoom: 8,
        paint: {
          'line-color': P.water,
          'line-width': width([8, 0.6, 12, 1.8, 16, 5]),
        },
      },

      /* ------------------------------------------------------------- roads */

      ...roadPair('road-service', isClass('service', 'track'), P.road.service,
        [13, 1.4, 18, 8], [13, 0.6, 18, 6], 13),
      ...roadPair('road-minor', isClass('minor'), P.road.minor,
        [12, 1.6, 18, 14], [12, 0.7, 18, 11], 12),
      ...roadPair('road-secondary', isClass('secondary', 'tertiary'), P.road.secondary,
        [9, 1.2, 14, 5, 18, 22], [9, 0.5, 14, 3.2, 18, 18], 9),
      ...roadPair('road-primary', isClass('primary'), P.road.primary,
        [7, 1.2, 14, 7, 18, 28], [7, 0.6, 14, 4.6, 18, 23], 7),
      ...roadPair('road-trunk', isClass('trunk'), P.road.trunk,
        [5, 1.2, 14, 9, 18, 32], [5, 0.6, 14, 6, 18, 27]),
      ...roadPair('road-motorway', isClass('motorway'), P.road.motorway,
        [4, 1.4, 14, 11, 18, 38], [4, 0.7, 14, 7.5, 18, 32]),

      {
        id: 'rail', type: 'line', source: 'openmaptiles',
        'source-layer': 'transportation', minzoom: 9,
        filter: isClass('rail', 'transit'),
        paint: {
          'line-color': P.road.rail,
          'line-width': width([9, 0.5, 16, 2.4]),
          'line-dasharray': [3, 2],
        },
      },
      {
        id: 'aeroway', type: 'line', source: 'openmaptiles',
        'source-layer': 'aeroway', minzoom: 10,
        paint: { 'line-color': P.road.minor, 'line-width': width([10, 1, 16, 12]) },
      },

      /* --------------------------------------------------------- buildings */

      {
        // Buildings only appear past z14. Below that they are a grey smear that
        // hides the street grid, which is the thing being navigated by.
        id: 'building', type: 'fill', source: 'openmaptiles',
        'source-layer': 'building', minzoom: 14,
        paint: {
          'fill-color': ['interpolate', ['linear'], ['zoom'],
            14, P.building, 17, P.buildingHi],
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0, 15.2, 0.85],
          'fill-outline-color': P.ground,
        },
      },

      /* -------------------------------------------------------- boundaries */

      {
        // State lines. Dashed and dim at every zoom: a boundary is context, and
        // must never be mistaken for something a vehicle drove along.
        id: 'boundary-state', type: 'line', source: 'openmaptiles',
        'source-layer': 'boundary',
        filter: ['all', ['<=', ['get', 'admin_level'], 4],
                 ['!=', ['get', 'maritime'], 1]],
        layout: { 'line-join': 'round' },
        paint: {
          'line-color': P.boundary,
          'line-width': width([3, 0.6, 8, 1.4, 14, 2.4]),
          'line-opacity': 0.55,
          'line-dasharray': [4, 2],
        },
      },
      {
        // District lines. Jurisdiction: a sighting means something different
        // either side of one, because a different unit owns it.
        id: 'boundary-district', type: 'line', source: 'openmaptiles',
        'source-layer': 'boundary', minzoom: 7,
        filter: ['all', ['>', ['get', 'admin_level'], 4],
                 ['<=', ['get', 'admin_level'], 6]],
        paint: {
          'line-color': P.boundary,
          'line-width': width([7, 0.4, 14, 1.2]),
          'line-opacity': 0.32,
          'line-dasharray': [2, 3],
        },
      },

      /* ------------------------------------------------------------ labels
       * Everything above this point is geography; everything below is type.
       * Camera markers, GIS overlays and the trace line are inserted at the
       * 'label-seam' layer, so pins sit above roads and below place names -- a
       * pin that covers the name of the place it is in is a pin an operator has
       * to move the map to identify.
       */

      {
        id: 'label-seam', type: 'background', minzoom: 24,
        paint: { 'background-opacity': 0 },
      },

      {
        id: 'label-water', type: 'symbol', source: 'openmaptiles',
        'source-layer': 'water_name', minzoom: 9,
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT,
          'text-size': 11,
          'text-letter-spacing': 0.14,
          'text-max-width': 8,
        },
        paint: {
          'text-color': P.labelWater, 'text-halo-color': P.halo, 'text-halo-width': 1.1,
        },
      },
      {
        id: 'label-road', type: 'symbol', source: 'openmaptiles',
        'source-layer': 'transportation_name', minzoom: 13,
        filter: isClass('motorway', 'trunk', 'primary', 'secondary', 'tertiary'),
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT,
          'text-size': 10.5,
          'symbol-placement': 'line',
          'text-letter-spacing': 0.06,
          'text-rotation-alignment': 'map',
        },
        paint: {
          'text-color': P.labelDim, 'text-halo-color': P.halo, 'text-halo-width': 1.4,
        },
      },
      {
        id: 'label-place-minor', type: 'symbol', source: 'openmaptiles',
        'source-layer': 'place', minzoom: 11,
        filter: isClass('suburb', 'neighbourhood', 'village', 'hamlet'),
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT,
          'text-size': ['interpolate', ['linear'], ['zoom'], 11, 10, 15, 12],
          'text-letter-spacing': 0.08,
          'text-max-width': 9,
        },
        paint: {
          'text-color': P.labelDim, 'text-halo-color': P.halo, 'text-halo-width': 1.3,
        },
      },
      {
        id: 'label-place', type: 'symbol', source: 'openmaptiles',
        'source-layer': 'place',
        filter: isClass('city', 'town'),
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD,
          'text-size': ['interpolate', ['linear'], ['zoom'], 4, 10, 8, 12.5, 13, 16],
          'text-letter-spacing': 0.1,
          'text-max-width': 9,
          'text-transform': 'uppercase',
        },
        paint: {
          'text-color': P.label, 'text-halo-color': P.halo, 'text-halo-width': 1.6,
        },
      },
      {
        id: 'label-state', type: 'symbol', source: 'openmaptiles',
        'source-layer': 'place', maxzoom: 7,
        filter: ['==', ['get', 'class'], 'state'],
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD,
          'text-size': 11,
          'text-letter-spacing': 0.22,
          'text-transform': 'uppercase',
        },
        paint: {
          'text-color': P.labelDim, 'text-halo-color': P.halo, 'text-halo-width': 1.4,
        },
      },
    ],
  };
}

/** Style used when there is no vector basemap: the cached OSM geography this
 *  platform fetched for itself, over an optional raster tile layer. The
 *  original behaviour, kept intact for isolated networks. */
export function fallbackStyle(config) {
  const style = {
    version: 8,
    sources: {},
    layers: [{ id: 'background', type: 'background',
               paint: { 'background-color': P.ground } }],
  };

  if (config && config.basemap_url) {
    style.sources.base = {
      type: 'raster',
      tiles: [config.basemap_url],
      tileSize: 256,
      attribution: config.basemap_attribution
        || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    };
    style.layers.push({
      id: 'base', type: 'raster', source: 'base',
      paint: {
        // Push the basemap down so the camera layer owns the foreground without
        // losing the street grid an operator navigates by.
        'raster-brightness-max': 0.82,
        'raster-saturation': -0.22,
        'raster-contrast': 0.06,
      },
    });
  }

  return style;
}

/** The layer our own overlays are inserted before, so pins and routes sit above
 *  the geography and below the place names. undefined means "append on top",
 *  which is correct for the fallback style -- it has no labels of its own. */
export function insertionPoint(map) {
  return map.getLayer('label-seam') ? 'label-seam' : undefined;
}
