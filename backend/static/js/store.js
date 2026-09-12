/* Application state.
 *
 * A single mutable object plus a subscribe/notify pair. Panels read from it and
 * call set() to change it; anything that renders from a slice of state
 * subscribes to that slice. That is all the reactivity this application needs,
 * and it means state is inspectable from the console during a demo -- which a
 * framework's internals are not.
 */

const listeners = new Map();   // key -> Set<fn>
let nextId = 1;

export const state = {
  /* Registry */
  cameras: [],
  camerasById: new Map(),
  facets: null,
  stats: null,

  /* What is drawn on the map */
  filters: {
    departments: new Set(),      // empty means "all"
    statuses: new Set(),
    anprOnly: false,
    // 'all' | 'placed' | 'unplaced'. A camera with no trustworthy position is
    // never drawn on the map, so without a way to filter *for* those they would
    // be invisible rather than merely unplaced -- which is how an estate
    // quietly loses cameras nobody is accountable for.
    placement: 'all',
    query: '',
  },
  gisLayers: [],                 // catalogue from /api/gis/layers
  activeGis: new Set(),

  /* Selection and panels */
  selectedCamera: null,
  panel: null,                   // null | 'cameras' | 'events' | ...
  dockOpen: false,

  /* Video wall */
  wall: [],                      // camera ids, in tile order

  /* Investigation */
  trace: null,
  tracePosition: 0,

  /* Live */
  alerts: [],
  unacknowledged: 0,
  events: [],
  connected: false,
};

export function subscribe(keys, fn) {
  const id = nextId++;
  for (const key of [].concat(keys)) {
    if (!listeners.has(key)) listeners.set(key, new Map());
    listeners.get(key).set(id, fn);
  }
  return () => {
    for (const key of [].concat(keys)) listeners.get(key)?.delete(id);
  };
}

export function notify(...keys) {
  const called = new Set();
  for (const key of keys) {
    for (const [id, fn] of listeners.get(key) || []) {
      if (called.has(id)) continue;   // one call per subscriber per notify
      called.add(id);
      try { fn(state); } catch (error) { console.error('subscriber failed', key, error); }
    }
  }
}

export function set(patch, ...extraKeys) {
  Object.assign(state, patch);
  notify(...Object.keys(patch), ...extraKeys);
}

/* --------------------------------------------------------------- derived */

export function setCameras(cameras) {
  state.cameras = cameras;
  state.camerasById = new Map(cameras.map((c) => [c.camera_id, c]));
  notify('cameras');
}

export const camera = (id) => state.camerasById.get(id) || null;

/** Has this camera a position the map is willing to draw?
 *
 *  Coordinates alone are not enough. An UNKNOWN accuracy means nobody vouched
 *  for the position, and a pin an operator might dispatch against has to be
 *  better than that.
 */
export const isPlaced = (c) =>
  c.latitude !== null && c.longitude !== null && c.location_accuracy !== 'UNKNOWN';

export const unplacedCount = () => state.cameras.filter((c) => !isPlaced(c)).length;

/** The cameras currently passing the layer filters.
 *
 *  Ownership and capability are separate tests on purpose. An operator hunting
 *  a registration wants every camera able to read one, whoever owns it; an
 *  operator answering for their own estate wants the opposite. Collapsing them
 *  into one filter would make one of those two questions unaskable.
 */
export function visibleCameras() {
  const { departments, statuses, anprOnly, placement, query } = state.filters;
  const needle = query.trim().toLowerCase();

  return state.cameras.filter((c) => {
    if (departments.size && !departments.has(c.department)) return false;
    if (statuses.size && !statuses.has(c.status)) return false;
    if (anprOnly && !c.ai_enabled) return false;
    if (placement === 'placed' && !isPlaced(c)) return false;
    if (placement === 'unplaced' && isPlaced(c)) return false;
    if (needle) {
      const haystack = `${c.camera_id} ${c.name} ${c.location_name} ${c.district}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
}

/* --------------------------------------------------------------- wall */

export const WALL_LIMIT = 8;

export function toggleWall(cameraId) {
  const index = state.wall.indexOf(cameraId);
  if (index >= 0) state.wall.splice(index, 1);
  else if (state.wall.length < WALL_LIMIT) state.wall.push(cameraId);
  else return { full: true };
  set({ wall: [...state.wall] });
  return { full: false, on: index < 0 };
}
