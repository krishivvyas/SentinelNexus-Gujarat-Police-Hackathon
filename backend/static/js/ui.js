/* Shared UI helpers: DOM building, formatting, icons, toasts.
 *
 * There is no framework here on purpose -- this whole application is served as
 * static files from the same Python process that runs the API, with no Node
 * toolchain and no build step, which is the deployment constraint that matters
 * for this platform. So the few things a framework would have given us are
 * written out once, here, rather than reached for ad hoc in every panel.
 */

/* --------------------------------------------------------------------- DOM */

/** el('div.row', {onclick}, child, child) -- tag.class#id shorthand. */
export function el(spec, props = null, ...children) {
  const [head, ...classes] = String(spec).split('.');
  const [tag, id] = head.split('#');
  const node = document.createElement(tag || 'div');
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(' ');

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className += (node.className ? ' ' : '') + value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : value);
  }

  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.firstChild.remove(); return node; }
export function fill(node, ...children) { return append(clear(node), children); }
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* ---------------------------------------------------------------- icons */

/* Inline 24x24 stroke paths. Inlined rather than loaded from an icon CDN so
 * the interface is complete on an isolated operator network. */
/* Every glyph in the left rail has to be distinguishable from every other one
 * at 18 px, by shape alone, by somebody who is not looking directly at it. That
 * was not true: `cameras`, `events` and the preview-dock button all rendered the
 * *same* video-camera path, so three unrelated controls were one symbol repeated
 * three times, and `watch` was the same shield as the brand mark in the corner.
 *
 * The rule applied here is that a glyph should depict the *subject* of its
 * panel rather than a generic idea of surveillance -- almost everything in this
 * application is about cameras, so a camera outline distinguishes nothing. So
 * ANPR events are a registration plate, the registry is a table, a trace is two
 * points with a path between them, and the alert panel is a bell (which also
 * gives the unacknowledged-count badge something to hang off).
 *
 * Shapes are kept to one or two closed forms plus at most three short strokes.
 * Anything finer turns to mud at 18 px with a 1.8 stroke, which is the size and
 * weight the rail actually renders at. */
const PATHS = {
  layers:   'M12 2 2 7l10 5 10-5-10-5ZM2 17l10 5 10-5M2 12l10 5 10-5',
  /* Records in a register: a marker and a row, three times.
   *
   * This was a table -- an outer box with a header rule and a column divider --
   * and on screen that was indistinguishable from `dock`, which is also an
   * outer box with a smaller shape inside it. Both read as "rectangle
   * containing a rectangle" at 18 px, which is the size that matters. Distinct
   * path data is not distinct *shape*: the check that counts is looking at the
   * rendered rail, not diffing the `d` attributes.
   *
   * So this one drops the enclosing box entirely. Nothing else in the rail is
   * an unboxed row of lines, which makes it separable from `events` (a plate),
   * `dock` (an inset panel) and `wall` (a grid) by silhouette alone. */
  registry: 'M4 6.5h.01M4 12h.01M4 17.5h.01M9 6.5h11M9 12h11M9 17.5h11',
  wall:     'M3 4h8v7H3zM13 4h8v7h-8zM3 13h8v7H3zM13 13h8v7h-8z',
  // A registration plate. This panel is plate readings, and a plate is the one
  // thing in this interface that only ever means that.
  events:   'M2.5 6.5h19v11h-19zM7 10.5v3M12 10.5v3M17 10.5v3',
  // Two waypoints and the path between them, rather than a bare diagonal arrow
  // -- a journey has endpoints, and the endpoints are what an operator reads.
  trace:    'M6 18.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 8.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z'
          + 'M7.5 15.1C9.5 12 12.5 9.6 16.4 8.2',
  // A bell. The shield this replaced was the brand mark at 18 px, so the rail
  // appeared to carry the logo twice.
  watch:    'M18 8.5a6 6 0 1 0-12 0c0 6-2.5 8-2.5 8h17s-2.5-2-2.5-8M13.7 21a2 2 0 0 1-3.4 0',
  health:   'M22 12h-4l-3 9L9 3l-3 9H2',
  // Picture-in-picture: the preview dock is a small view inset over the main
  // one, which is exactly what it does to the map.
  dock:     'M3 5h18v14H3zM12.5 12.5h6.5v5h-6.5z',
  reports:  'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13h6M9 17h4',
  audit:    'M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  search:   'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM21 21l-4.35-4.35',
  close:    'M18 6 6 18M6 6l12 12',
  chevron:  'm6 9 6 6 6-6',
  play:     'm5 3 14 9-14 9V3Z',
  pause:    'M6 4h4v16H6zM14 4h4v16h-4z',
  guide:    'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20ZM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01',
  camera:   'M23 7l-7 5 7 5V7zM1 5h15v14H1z',
  logout:   'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  refresh:  'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  upload:   'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  expand:   'M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7',
  fullscreen: 'M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7',
  pin:      'M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0Z',
  target:   'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12ZM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z',
  alert:    'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0ZM12 9v4M12 17h.01',
  shield:   'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z',
  check:    'm20 6-11 11-5-5',
  plus:     'M12 5v14M5 12h14',
  minus:    'M5 12h14',
  cursor:   'm3 3 7.5 18 2.4-7.6L20.5 11 3 3Z',
  /* Half-filled circle: the standard light/dark mark, and the only glyph here
   * that is about the interface rather than about the estate. */
  theme:    'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 3v18'
          + 'M12 6.5a5.5 5.5 0 0 1 0 11',
};

/* Drawn when a glyph is asked for by a name this module does not have.
 *
 * A hollow dashed square, deliberately ugly, and never a real glyph. It
 * replaces `PATHS[name] || PATHS.target`, which substituted a *valid, existing*
 * icon for a missing one and so turned a broken build into a silently wrong
 * interface. That is not hypothetical: a browser holding a stale ui.js beside a
 * fresh app.js asks for `dock`, gets `target`, and the operator sees two
 * buttons wearing the same camera while nothing anywhere reports a problem.
 * A missing icon should look missing. */
const MISSING = 'M4 4h16v16H4z';

export function icon(name, size = 16) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  const known = Object.prototype.hasOwnProperty.call(PATHS, name);
  if (!known) {
    // Loud, because the usual cause is a stale module rather than a typo, and
    // the operator cannot be expected to diagnose that from a wrong picture.
    console.warn(`icon(): no glyph named "${name}". `
      + 'If the interface was just updated, reload with cache disabled.');
    path.setAttribute('stroke-dasharray', '3 2');
  }
  path.setAttribute('d', known ? PATHS[name] : MISSING);
  svg.append(path);
  return svg;
}

/* ----------------------------------------------------------- formatting */

/* Event times are the burned-in overlay clock, which is the authoritative
 * event timestamp on this grid -- not when the server happened to ingest the
 * frame. Rendered in full rather than as "3 minutes ago": these are replayed
 * recordings spanning four dates, so a relative time would be a lie. */
export function fmtTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(+date)) return '—';
  return date.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit',
    minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function fmtClock(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(+date)) return '—';
  return date.toLocaleTimeString('en-GB', { hour12: false });
}

export function fmtNum(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return Number(value).toLocaleString('en-GB',
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export const severityClass = (severity) => ({
  CRITICAL: 'crit', HIGH: 'high', MEDIUM: 'warn', LOW: 'info',
}[severity] || '');

export const statusClass = (status) => ({
  ONLINE: 'ok', DEGRADED: 'warn', OFFLINE: 'crit',
}[status] || '');

/** A plate reading with its confidence, or an explicit statement that there
 *  is none. Never renders an empty cell: "no plate read" is a finding. */
export function plateChip(plate, confidence) {
  if (!plate) return el('span.plate.none', { text: 'no plate read' });
  const chip = el('span.plate', { text: plate });
  if (confidence === null || confidence === undefined) return chip;
  const percent = Math.round(confidence * 100);
  const bar = el('span.conf' + (percent < 55 ? '.bad' : percent < 75 ? '.low' : ''),
    {}, el('i', { style: { width: `${Math.max(4, percent)}%` } }));
  return el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '7px' } },
    chip, bar, el('span.faint.mono', { style: { fontSize: '10px' }, text: `${percent}%` }));
}

/* -------------------------------------------------------------- toasts */

let toastHost = null;

export function toast(title, body = '', kind = '', ms = 5200) {
  toastHost ??= $('#toasts');
  if (!toastHost) return;
  const node = el(`div.toast${kind ? '.' + kind : ''}`, {},
    el('div', { style: { flex: '1', minWidth: '0' } },
      el('div.toast-title', { text: title }),
      body ? el('div.toast-body', { text: body }) : null),
    el('button.icon-btn', { onclick: () => node.remove(), title: 'Dismiss' }, icon('close', 13)),
  );
  toastHost.append(node);
  // Newest at the bottom, and never more than five: a wall display that fills
  // with toasts stops showing the map, which is the thing being watched.
  while (toastHost.children.length > 5) toastHost.firstChild.remove();
  if (ms) setTimeout(() => node.remove(), ms);
  return node;
}

/* ------------------------------------------------------------- helpers */

/** Wraps a load into a placeholder-then-content swap with an honest error. */
export async function into(host, loader, { empty = 'Nothing to show' } = {}) {
  fill(host, el('div.empty', { text: 'Loading…' }));
  try {
    const content = await loader();
    if (!content || (Array.isArray(content) && !content.length)) {
      fill(host, el('div.empty', { text: empty }));
    } else {
      fill(host, content);
    }
  } catch (error) {
    fill(host, el('div.empty', {},
      el('div', { style: { color: 'var(--critical)' }, text: 'Could not load' }),
      el('div', { style: { marginTop: '6px' }, text: error.message })));
  }
}

export function debounce(fn, ms = 220) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
