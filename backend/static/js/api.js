/* REST client and session.
 *
 * One place that knows about the token, so nothing else has to. Two endpoints
 * cannot send an Authorization header -- the MJPEG preview, which is consumed
 * by an <img> tag, and the alert WebSocket -- and both take the same JWT as a
 * query parameter instead. Those URLs are built here too, so the token never
 * gets spread around the rest of the code.
 */

const TOKEN_KEY = 'sentinel.token';
const USER_KEY = 'sentinel.user';

export const session = {
  get token() {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  get user() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
    catch { return null; }
  },
  set(token, user) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    } catch { /* private mode: the session simply does not survive a reload */ }
  },
  clear() {
    try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); }
    catch { /* nothing to clear */ }
  },
  /* ADMIN and OPERATOR may act; ANALYST may only read. Checked here so every
   * call site asks the same question the same way. */
  get canAct() {
    const role = this.user?.role;
    return role === 'ADMIN' || role === 'OPERATOR';
  },
  get isAdmin() { return this.user?.role === 'ADMIN'; },
};

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.status = status;
  }
}

/* Fired when the server rejects our token. The shell listens and returns to
 * the sign-in gate rather than leaving a half-dead interface on screen. */
const onUnauthorized = new EventTarget();
export const auth = onUnauthorized;

async function request(path, { method = 'GET', body, form, raw } = {}) {
  const headers = {};
  const token = session.token;
  if (token) headers.Authorization = `Bearer ${token}`;

  let payload = form;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const response = await fetch(path, { method, headers, body: payload });

  if (response.status === 401) {
    session.clear();
    onUnauthorized.dispatchEvent(new Event('expired'));
    throw new ApiError(401, 'Session expired');
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const parsed = await response.json();
      if (parsed?.detail) detail = typeof parsed.detail === 'string'
        ? parsed.detail : JSON.stringify(parsed.detail);
    } catch { /* a non-JSON error body is not worth a second failure */ }
    throw new ApiError(response.status, detail);
  }

  if (raw) return response;
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  async login(username, password) {
    const form = new URLSearchParams({ username, password });
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new ApiError(response.status, detail.detail || 'Sign-in failed');
    }
    const data = await response.json();
    session.set(data.access_token, {
      username: data.username, role: data.role, full_name: data.full_name,
    });
    return data;
  },

  stats:        () => request('/api/stats'),
  timeline:     () => request('/api/stats/timeline'),
  facets:       () => request('/api/cameras/meta/facets'),
  cameras:      (params = {}) => request('/api/cameras?' + new URLSearchParams(params)),
  camera:       (id) => request(`/api/cameras/${encodeURIComponent(id)}`),
  cameraGeo:    () => request('/api/cameras/geo/features'),
  setLocation:  (id, body) => request(`/api/cameras/${encodeURIComponent(id)}/location`,
                                      { method: 'PUT', body }),

  recent:       (limit = 60, withPlateOnly = false) =>
                  request(`/api/sightings/recent?limit=${limit}&with_plate_only=${withPlateOnly}`),
  search:       (params) => request('/api/search?' + new URLSearchParams(params)),

  alerts:       (params = {}) => request('/api/alerts?' + new URLSearchParams(params)),
  ackAlert:     (id) => request(`/api/alerts/${id}/acknowledge`, { method: 'POST' }),
  resolveAlert: (id) => request(`/api/alerts/${id}/resolve`, { method: 'POST' }),

  watchlist:    () => request('/api/watchlist'),
  addWatch:     (body) => request('/api/watchlist', { method: 'POST', body }),
  removeWatch:  (id) => request(`/api/watchlist/${id}`, { method: 'DELETE' }),

  ingestStatus: () => request('/api/ingest/status'),
  startIngest:  (ids) => request('/api/ingest/start' + (ids?.length ? '' : '?ai=true'),
                                 { method: 'POST', body: ids || null }),
  stopIngest:   (ids) => request('/api/ingest/stop', { method: 'POST', body: ids || null }),

  mapConfig:    () => request('/api/config/map'),
  liveConfig:   () => request('/api/config/live'),
  gisLayers:    () => request('/api/gis/layers'),
  gisLayer:     (key) => request(`/api/gis/layers/${key}`),
  gisRefresh:   (key) => request(`/api/gis/layers/${key}/refresh`, { method: 'POST' }),

  health:       () => request('/api/health/cameras'),
  probe:        (ids, force = false) =>
                  request(`/api/health/probe?force=${force}`, { method: 'POST', body: ids }),

  importPreview(file) {
    const form = new FormData();
    form.append('file', file);
    return request('/api/registry/import/preview', { method: 'POST', form });
  },
  importCommit(file, overrides = {}) {
    const form = new FormData();
    form.append('file', file);
    form.append('overrides', JSON.stringify(overrides));
    return request('/api/registry/import/commit', { method: 'POST', form });
  },

  audit:        (limit = 200) => request(`/api/audit?limit=${limit}`),

  /* --- URLs that carry the token in the query string ------------------- */

  liveUrl(cameraId, detect = true) {
    const params = new URLSearchParams({ detect, token: session.token || '' });
    return `/api/cameras/${encodeURIComponent(cameraId)}/live?${params}`;
  },
  evidenceUrl(path) {
    if (!path) return '';
    const name = path.split(/[\\/]/).pop();
    return `/api/evidence/${encodeURIComponent(name)}`;
  },
  thumbnailUrl(cameraId) {
    return `/api/cameras/${encodeURIComponent(cameraId)}/thumbnail`;
  },
  alertSocketUrl() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${scheme}://${location.host}/api/ws/alerts`;
  },
  csvUrl(params = {}) { return '/api/reports/detections.csv?' + new URLSearchParams(params); },
  tracePdfUrl(plate) { return `/api/reports/trace.pdf?plate=${encodeURIComponent(plate)}`; },
  templateUrl() { return '/api/registry/template.csv'; },

  /* Downloads need the Authorization header, so they cannot be plain links.
   * Fetch the body, hand the browser a blob, and revoke the object URL --
   * otherwise every export leaks its own payload for the life of the tab. */
  async download(url, filename) {
    const response = await request(url, { raw: true });
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  },
};
