'use client';

import React, { useEffect, useState, useMemo } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import {
  Camera as CameraIcon,
  Video,
  Activity,
  Shield,
  Layers,
  MapPin,
  ExternalLink,
  Navigation,
  RefreshCw,
  Search,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Sparkles,
} from 'lucide-react';
import { api, type Camera } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tabs } from '@/components/ui/Tabs';

// Dynamically import GisMap to avoid SSR Leaflet issues
const GisMap = dynamic(() => import('@/components/map/GisMap'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full min-h-[500px] rounded-xl bg-slate-100 flex items-center justify-center text-slate-400 font-mono text-xs border border-slate-200">
      Loading GIS Map Grid...
    </div>
  ),
});

export default function CommandCentrePage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [isPlacing, setIsPlacing] = useState(false);
  const [showLiveStream, setShowLiveStream] = useState(true);

  const fetchCameras = async () => {
    try {
      setLoading(true);
      const data = await api.getCameras();
      setCameras(data);
      if (data.length > 0 && !selectedCamera) {
        setSelectedCamera(data[0]);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCameras();
  }, []);

  const stats = useMemo(() => {
    const total = cameras.length;
    const online = cameras.filter((c) => c.status === 'ONLINE').length;
    const degraded = cameras.filter((c) => c.status === 'DEGRADED').length;
    const offline = cameras.filter((c) => c.status === 'OFFLINE').length;
    const aiEnabled = cameras.filter((c) => c.ai_enabled).length;
    const unplaced = cameras.filter((c) => !c.latitude || !c.longitude).length;
    return { total, online, degraded, offline, aiEnabled, unplaced };
  }, [cameras]);

  const filteredCameras = useMemo(() => {
    return cameras.filter((c) => {
      if (statusFilter === 'ONLINE' && c.status !== 'ONLINE') return false;
      if (statusFilter === 'DEGRADED' && c.status !== 'DEGRADED') return false;
      if (statusFilter === 'OFFLINE' && c.status !== 'OFFLINE') return false;
      if (statusFilter === 'ANPR' && !c.ai_enabled) return false;
      if (statusFilter === 'UNPLACED' && c.latitude && c.longitude) return false;

      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          c.camera_id.toLowerCase().includes(q) ||
          c.name.toLowerCase().includes(q) ||
          c.location_name.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [cameras, statusFilter, searchQuery]);

  const handlePlaceCamera = async (lat: number, lng: number) => {
    if (!selectedCamera) return;
    try {
      const updated = await api.updateCamera(selectedCamera.camera_id, {
        latitude: lat,
        longitude: lng,
        location_accuracy: 'ACCURATE_FIELD',
      });
      setCameras((prev) =>
        prev.map((c) => (c.camera_id === updated.camera_id ? updated : c))
      );
      setSelectedCamera(updated);
      setIsPlacing(false);
    } catch (e) {
      console.error('Failed to update position', e);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Section Header with Quick Stats */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">
              Command Centre GIS Grid
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-blue-50 text-blue-800 border border-blue-200">
              {stats.total} NODES
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            Federated camera topology, live telemetry & position accuracy mapping
          </p>
        </div>

        {/* Quick Filter Segmented Controller */}
        <div className="flex items-center gap-2 flex-wrap">
          <Tabs
            activeId={statusFilter}
            onChange={setStatusFilter}
            tabs={[
              { id: 'ALL', label: 'All', count: stats.total },
              { id: 'ONLINE', label: 'Online', count: stats.online },
              { id: 'DEGRADED', label: 'Degraded', count: stats.degraded },
              { id: 'ANPR', label: 'ANPR', count: stats.aiEnabled },
              { id: 'UNPLACED', label: 'No Pos', count: stats.unplaced },
            ]}
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={fetchCameras}
            icon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />}
          >
            Sync
          </Button>
        </div>
      </div>

      {/* Main Grid Layout: Map + Bento Telemetry Sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-[640px]">
        {/* Left GIS Map Card (8 cols) */}
        <div className="lg:col-span-8 flex flex-col bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] overflow-hidden">
          {/* Map Sub-header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50/50">
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono font-semibold text-slate-700">
                GEOSPATIAL TOPOLOGY
              </span>
              <span className="text-[11px] font-mono text-slate-500">
                Showing {filteredCameras.filter((c) => c.latitude && c.longitude).length} mapped cameras
              </span>
            </div>

            {/* Unplaced Tool button */}
            {selectedCamera && (!selectedCamera.latitude || isPlacing) && (
              <Button
                variant={isPlacing ? 'danger' : 'primary'}
                size="sm"
                onClick={() => setIsPlacing(!isPlacing)}
                icon={<MapPin className="w-3.5 h-3.5" />}
              >
                {isPlacing ? 'Cancel Placing' : `Place ${selectedCamera.camera_id} on Map`}
              </Button>
            )}
          </div>

          {/* Interactive Map */}
          <div className="flex-1 w-full min-h-[500px] p-2">
            <GisMap
              cameras={filteredCameras}
              selectedCamera={selectedCamera}
              onSelectCamera={setSelectedCamera}
              onPlaceLocation={handlePlaceCamera}
              isPlacing={isPlacing}
            />
          </div>
        </div>

        {/* Right Bento Sidebar: Selected Node Telemetry (4 cols) */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          {selectedCamera ? (
            <Card
              header={
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-bold text-slate-900">
                    {selectedCamera.camera_id}
                  </span>
                  <Badge
                    variant={
                      selectedCamera.status === 'ONLINE'
                        ? 'ok'
                        : selectedCamera.status === 'DEGRADED'
                        ? 'warn'
                        : 'crit'
                    }
                  >
                    {selectedCamera.status}
                  </Badge>
                </div>
              }
              spec="NODE TELEMETRY"
            >
              {/* Location and Department */}
              <div className="space-y-3 mb-4">
                <div>
                  <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">
                    Location Name
                  </span>
                  <span className="text-sm font-semibold text-slate-900">
                    {selectedCamera.location_name || selectedCamera.name}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                  <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="text-[10px] text-slate-400 block">DISTRICT</span>
                    <span className="font-semibold text-slate-800 truncate block">
                      {selectedCamera.district || 'Ahmedabad'}
                    </span>
                  </div>
                  <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="text-[10px] text-slate-400 block">DEPT</span>
                    <span className="font-semibold text-slate-800 truncate block">
                      {selectedCamera.department || 'Traffic'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Live MJPEG Mini Player Preview */}
              <div className="relative rounded-lg overflow-hidden border border-slate-200 bg-slate-900 aspect-video mb-4 shadow-inner">
                {showLiveStream ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={api.liveUrl(selectedCamera.camera_id, true)}
                    alt={selectedCamera.name}
                    className="w-full h-full object-contain"
                  />
                ) : (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={api.thumbnailUrl(selectedCamera.camera_id)}
                    alt={selectedCamera.name}
                    className="w-full h-full object-cover opacity-60"
                  />
                )}
                <div className="absolute top-2 left-2 flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900/80 backdrop-blur-xs text-[10px] font-mono font-bold text-emerald-400 border border-emerald-500/30">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  LIVE PREVIEW
                </div>
              </div>

              {/* Technical Specifications Bento Grid */}
              <div className="grid grid-cols-3 gap-2 text-center text-xs font-mono mb-4">
                <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
                  <span className="text-[10px] text-slate-400 block">FPS</span>
                  <span className="font-bold text-slate-900">
                    {selectedCamera.fps || 10}
                  </span>
                </div>
                <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
                  <span className="text-[10px] text-slate-400 block">CODEC</span>
                  <span className="font-bold text-slate-900">
                    {selectedCamera.codec || 'H.264'}
                  </span>
                </div>
                <div className="p-2 bg-slate-50 rounded-lg border border-slate-100">
                  <span className="text-[10px] text-slate-400 block">AI SCORE</span>
                  <span className="font-bold text-purple-700">
                    {selectedCamera.plate_score || 0}/100
                  </span>
                </div>
              </div>

              {/* Actions Footer */}
              <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
                <Link
                  href={`/wall_page?camera=${encodeURIComponent(selectedCamera.camera_id)}`}
                  className="flex-1"
                >
                  <Button variant="primary" size="sm" className="w-full" icon={<Video className="w-3.5 h-3.5" />}>
                    Open in Video Wall
                  </Button>
                </Link>
                <Link
                  href={`/events_page?camera=${encodeURIComponent(selectedCamera.camera_id)}`}
                  className="flex-1"
                >
                  <Button variant="secondary" size="sm" className="w-full" icon={<Sparkles className="w-3.5 h-3.5" />}>
                    Sightings
                  </Button>
                </Link>
              </div>
            </Card>
          ) : (
            <Card header="Select a Node">
              <p className="text-xs text-slate-500 font-mono">
                Click any camera marker on the map to inspect live stream and telemetry.
              </p>
            </Card>
          )}

          {/* Quick Node Finder List */}
          <div className="bg-white rounded-xl border border-slate-200/90 p-4 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] flex-1">
            <div className="flex items-center gap-2 mb-3">
              <Search className="w-3.5 h-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="Filter node list..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full text-xs font-mono bg-transparent focus:outline-none placeholder:text-slate-400"
              />
            </div>
            <div className="max-h-[220px] overflow-y-auto space-y-1 pr-1">
              {filteredCameras.map((cam) => {
                const isSelected = selectedCamera?.camera_id === cam.camera_id;
                return (
                  <button
                    key={cam.camera_id}
                    onClick={() => setSelectedCamera(cam)}
                    className={`w-full flex items-center justify-between p-2 rounded-lg text-xs font-mono transition-all text-left ${
                      isSelected
                        ? 'bg-blue-50 text-blue-900 border border-blue-200'
                        : 'hover:bg-slate-50 text-slate-700'
                    }`}
                  >
                    <div className="truncate">
                      <span className="font-bold mr-2">{cam.camera_id}</span>
                      <span className="text-slate-500">{cam.location_name || cam.name}</span>
                    </div>
                    <span
                      className={`w-2 h-2 rounded-full flex-none ${
                        cam.status === 'ONLINE'
                          ? 'bg-emerald-500'
                          : cam.status === 'DEGRADED'
                          ? 'bg-amber-500'
                          : 'bg-rose-500'
                      }`}
                    />
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
