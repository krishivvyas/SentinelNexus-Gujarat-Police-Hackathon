/**
 * Sentinel Nexus WebSocket Client for Real-time ANPR & Hotlist Alerts
 */

export interface LiveAlert {
  id: string;
  type: 'HOTLIST_MATCH' | 'ANPR_SIGHTING' | 'SYSTEM_ALERT';
  plate_number: string;
  camera_id: string;
  camera_name?: string;
  location_name?: string;
  confidence: number;
  crop_path?: string;
  priority?: 'CRITICAL' | 'HIGH' | 'MEDIUM';
  category?: string;
  timestamp: string;
}

type AlertListener = (alert: LiveAlert) => void;
type StatusListener = (connected: boolean) => void;

class SocketService {
  private ws: WebSocket | null = null;
  private alertListeners: Set<AlertListener> = new Set();
  private statusListeners: Set<StatusListener> = new Set();
  private reconnectTimer: NodeJS.Timeout | null = null;
  private isExplicitlyClosed = false;

  connect() {
    if (typeof window === 'undefined') return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.isExplicitlyClosed = false;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.port === '3000' ? `${window.location.hostname}:8000` : window.location.host;
    const token = localStorage.getItem('sentinel_token') || '';
    const url = `${protocol}//${host}/api/ws/alerts?token=${encodeURIComponent(token)}`;

    try {
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        this.notifyStatus(true);
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.notifyAlert(data);
        } catch (e) {
          console.error('Failed to parse websocket message', e);
        }
      };

      this.ws.onclose = () => {
        this.notifyStatus(false);
        if (!this.isExplicitlyClosed) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        this.notifyStatus(false);
        this.ws?.close();
      };
    } catch (e) {
      this.notifyStatus(false);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 4000);
  }

  onAlert(listener: AlertListener): () => void {
    this.alertListeners.add(listener);
    return () => this.alertListeners.delete(listener);
  }

  onStatusChange(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  private notifyAlert(alert: LiveAlert) {
    this.alertListeners.forEach((fn) => fn(alert));
  }

  private notifyStatus(connected: boolean) {
    this.statusListeners.forEach((fn) => fn(connected));
  }

  disconnect() {
    this.isExplicitlyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

export const socket = new SocketService();
