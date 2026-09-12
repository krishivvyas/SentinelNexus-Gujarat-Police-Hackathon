/**
 * Sentinel Nexus REST API Client
 * Proxies calls to FastAPI backend with JWT authorization
 */

const API_BASE = '/api';

export interface Camera {
  camera_id: string;
  name: string;
  department: string;
  district: string;
  location_name: string;
  latitude: number | null;
  longitude: number | null;
  location_accuracy: string;
  location_source: string;
  protocol: string;
  codec: string;
  width: number | null;
  height: number | null;
  fps: number | null;
  status: 'ONLINE' | 'DEGRADED' | 'OFFLINE' | string;
  plate_score: number;
  triage_note: string;
  ai_enabled: boolean;
  time_cluster: number | null;
  overlay_ts: string | null;
  hls_url: string | null;
}

export interface Sighting {
  id: number;
  plate_number: string;
  camera_id: string;
  timestamp: string;
  confidence: number;
  crop_path: string | null;
  vehicle_type?: string;
  vehicle_color?: string;
  hotlist_match?: boolean;
}

export interface WatchlistTarget {
  id: number;
  plate_number: string;
  reason: string;
  category: 'STOLEN' | 'WANTED' | 'EXPIRED_RC' | 'SUSPECT' | string;
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  created_at: string;
  added_by: string;
  notes?: string;
}

export interface HealthMetrics {
  status: string;
  cameras_total: number;
  cameras_online: number;
  cameras_degraded: number;
  cameras_offline: number;
  active_streams: number;
  stream_limit: number;
  inference_fps: number;
  worker_queue_depth: number;
  disk_usage_gb: number;
  uptime_seconds: number;
  anpr_enabled_cameras: number;
  today_sightings: number;
}

export interface AuditLog {
  id: number;
  timestamp: string;
  username: string;
  role: string;
  action: string;
  details: string;
  ip_address: string;
}

export interface TracePoint {
  camera_id: string;
  location_name: string;
  latitude: number;
  longitude: number;
  timestamp: string;
  confidence: number;
  crop_path: string | null;
}

export interface VehicleTrace {
  plate_number: string;
  total_sightings: number;
  first_seen: string;
  last_seen: string;
  points: TracePoint[];
  estimated_distance_km?: number;
}

class ApiService {
  private token: string | null = null;

  constructor() {
    if (typeof window !== 'undefined') {
      this.token = localStorage.getItem('sentinel_token') || 'mock_dev_token';
    }
  }

  getToken(): string | null {
    if (typeof window !== 'undefined' && !this.token) {
      this.token = localStorage.getItem('sentinel_token');
    }
    return this.token;
  }

  setToken(token: string) {
    this.token = token;
    if (typeof window !== 'undefined') {
      localStorage.setItem('sentinel_token', token);
    }
  }

  clearToken() {
    this.token = null;
    if (typeof window !== 'undefined') {
      localStorage.removeItem('sentinel_token');
    }
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };

    if (token && !headers['Authorization']) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }

    const res = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (!res.ok) {
      const errorText = await res.text().catch(() => 'Network error');
      throw new Error(`API Error ${res.status}: ${errorText}`);
    }

    return res.json();
  }

  // --- Auth
  async login(username: string, password: string):Promise<{ access_token: string; role: string; full_name: string }> {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    const res = await fetch(`${API_BASE}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString(),
    });

    if (!res.ok) {
      throw new Error('Authentication failed');
    }

    const data = await res.json();
    this.setToken(data.access_token);
    return data;
  }

  // --- Cameras
  async getCameras(): Promise<Camera[]> {
    return this.request<Camera[]>('/cameras');
  }

  async getCamera(id: string): Promise<Camera> {
    return this.request<Camera>(`/cameras/${encodeURIComponent(id)}`);
  }

  async updateCamera(id: string, updates: Partial<Camera>): Promise<Camera> {
    return this.request<Camera>(`/cameras/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
  }

  async getCameraGeoFeatures(): Promise<any> {
    return this.request<any>('/cameras/geo/features');
  }

  // --- Sightings & Trace
  async getSightings(params: { limit?: number; camera_id?: string; plate?: string } = {}): Promise<Sighting[]> {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', String(params.limit));
    if (params.camera_id) q.set('camera_id', params.camera_id);
    if (params.plate) q.set('plate', params.plate);
    return this.request<Sighting[]>(`/sightings?${q.toString()}`);
  }

  async getVehicleTrace(plate: string): Promise<VehicleTrace> {
    return this.request<VehicleTrace>(`/sightings/trace?plate=${encodeURIComponent(plate)}`);
  }

  // --- Watchlist
  async getWatchlist(): Promise<WatchlistTarget[]> {
    return this.request<WatchlistTarget[]>('/watchlist');
  }

  async addToWatchlist(target: { plate_number: string; reason: string; category: string; priority: string; notes?: string }): Promise<WatchlistTarget> {
    return this.request<WatchlistTarget>('/watchlist', {
      method: 'POST',
      body: JSON.stringify(target),
    });
  }

  async removeFromWatchlist(plate: string): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>(`/watchlist/${encodeURIComponent(plate)}`, {
      method: 'DELETE',
    });
  }

  // --- Health & Config
  async getHealth(): Promise<HealthMetrics> {
    return this.request<HealthMetrics>('/health');
  }

  async getLiveConfig(): Promise<{ limit: number; profile: string; detect_default: boolean }> {
    return this.request<{ limit: number; profile: string; detect_default: boolean }>('/config/live');
  }

  // --- Audit Logs
  async getAuditLogs(limit = 100): Promise<AuditLog[]> {
    return this.request<AuditLog[]>(`/audit?limit=${limit}`);
  }

  // --- Importer
  async importPreview(file: File): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    return this.request<any>('/registry/import/preview', {
      method: 'POST',
      body: form,
    });
  }

  async importCommit(file: File, overrides: any = {}): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    form.append('overrides', JSON.stringify(overrides));
    return this.request<any>('/registry/import/commit', {
      method: 'POST',
      body: form,
    });
  }

  // --- URLs
  liveUrl(cameraId: string, detect = true): string {
    const token = this.getToken() || '';
    const params = new URLSearchParams({ detect: String(detect), token });
    return `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/live?${params.toString()}`;
  }

  thumbnailUrl(cameraId: string): string {
    return `${API_BASE}/cameras/${encodeURIComponent(cameraId)}/thumbnail`;
  }

  evidenceUrl(path: string | null): string {
    if (!path) return '';
    const filename = path.split(/[\\/]/).pop() || '';
    return `${API_BASE}/evidence/${encodeURIComponent(filename)}`;
  }
}

export const api = new ApiService();
