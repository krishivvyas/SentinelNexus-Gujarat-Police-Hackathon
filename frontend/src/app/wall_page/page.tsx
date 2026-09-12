'use client';

import React, { useEffect, useState, useMemo } from 'react';
import {
  LayoutGrid,
  Maximize2,
  X,
  Play,
  Square,
  Sparkles,
  RefreshCw,
  SlidersHorizontal,
  Layers,
  ShieldAlert,
  Video,
  Eye,
} from 'lucide-react';
import { api, type Camera } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Toggle } from '@/components/ui/Toggle';
import { Modal } from '@/components/ui/Modal';
import { Tabs } from '@/components/ui/Tabs';

export default function VideoWallPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [activeStreams, setActiveStreams] = useState<Set<string>>(new Set());
  const [streamLimit, setStreamLimit] = useState(4);
  const [columns, setColumns] = useState('auto');
  const [detect, setDetect] = useState(true);
  const [focusCamera, setFocusCamera] = useState<Camera | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('ALL');

  const fetchWallData = async () => {
    try {
      setLoading(true);
      const [cams, cfg] = await Promise.all([
        api.getCameras(),
        api.getLiveConfig().catch(() => ({ limit: 4, profile: 'balanced', detect_default: true })),
      ]);
      setCameras(cams);
      setStreamLimit(cfg.limit || 4);

      // Auto start first camera
      if (cams.length > 0 && activeStreams.size === 0) {
        setActiveStreams(new Set([cams[0].camera_id]));
      }
    } catch (e) {
      console.error('Failed to load wall data', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWallData();
  }, []);

  const toggleStream = (id: string) => {
    setActiveStreams((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        if (next.size >= streamLimit) {
          alert(`Stream limit reached (${streamLimit} concurrent streams max). Stop a feed first.`);
          return prev;
        }
        next.add(id);
      }
      return next;
    });
  };

  const fillWithBest = () => {
    const online = cameras
      .filter((c) => c.status === 'ONLINE')
      .sort((a, b) => Number(b.ai_enabled) - Number(a.ai_enabled) || b.plate_score - a.plate_score);

    const next = new Set<string>();
    for (let i = 0; i < Math.min(streamLimit, online.length); i++) {
      next.add(online[i].camera_id);
    }
    setActiveStreams(next);
  };

  const stopAll = () => {
    setActiveStreams(new Set());
  };

  const filteredCameras = useMemo(() => {
    return cameras.filter((c) => {
      if (filter === 'LIVE') return activeStreams.has(c.camera_id);
      if (filter === 'ONLINE') return c.status === 'ONLINE';
      if (filter === 'ANPR') return c.ai_enabled;
      return true;
    });
  }, [cameras, filter, activeStreams]);

  const gridColsClass = {
    auto: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4',
    '2': 'grid-cols-1 md:grid-cols-2',
    '3': 'grid-cols-1 md:grid-cols-3',
    '4': 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4',
    '5': 'grid-cols-1 md:grid-cols-3 lg:grid-cols-5',
  }[columns] || 'grid-cols-1 md:grid-cols-3';

  return (
    <div className="space-y-6">
      {/* Wall Header & Command Bar */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 p-4 bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)]">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <LayoutGrid className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-lg font-bold text-slate-900 tracking-tight">
              Federated Video Wall
            </h1>
          </div>

          {/* Active Slot Budget Badge */}
          <div
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-bold border ${
              activeStreams.size >= streamLimit
                ? 'bg-amber-50 text-amber-800 border-amber-200'
                : 'bg-emerald-50 text-emerald-800 border-emerald-200'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                activeStreams.size > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'
              }`}
            />
            <span>
              {activeStreams.size} / {streamLimit} STREAMING
            </span>
          </div>
        </div>

        {/* Tactical Controls */}
        <div className="flex items-center gap-3 flex-wrap">
          <Tabs
            activeId={filter}
            onChange={setFilter}
            tabs={[
              { id: 'ALL', label: 'All' },
              { id: 'LIVE', label: 'Live', count: activeStreams.size },
              { id: 'ONLINE', label: 'Online' },
              { id: 'ANPR', label: 'ANPR' },
            ]}
          />

          <div className="flex items-center gap-2 border-l border-slate-200 pl-3">
            <Toggle
              checked={detect}
              onChange={setDetect}
              label="YOLO11 AI"
            />
          </div>

          <div className="flex items-center gap-2 border-l border-slate-200 pl-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fillWithBest}
              icon={<Sparkles className="w-3.5 h-3.5 text-blue-600" />}
            >
              Auto-Fill Best
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={stopAll}
              icon={<Square className="w-3.5 h-3.5 text-rose-600" />}
            >
              Stop All
            </Button>
          </div>
        </div>
      </div>

      {/* Video Wall Bento Grid */}
      <div className={`grid ${gridColsClass} gap-4`}>
        {filteredCameras.map((cam) => {
          const isLive = activeStreams.has(cam.camera_id);
          return (
            <div
              key={cam.camera_id}
              className={`group relative bg-white rounded-xl border transition-all overflow-hidden shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] ${
                isLive
                  ? 'border-emerald-400 ring-2 ring-emerald-500/20 shadow-md'
                  : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              {/* Tile Header */}
              <div className="flex items-center justify-between px-3.5 py-2 border-b border-slate-100 bg-slate-50/70 text-xs font-mono">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`w-2 h-2 rounded-full flex-none ${
                      cam.status === 'ONLINE'
                        ? 'bg-emerald-500'
                        : cam.status === 'DEGRADED'
                        ? 'bg-amber-500'
                        : 'bg-rose-500'
                    }`}
                  />
                  <span className="font-bold text-slate-900 truncate">
                    {cam.camera_id}
                  </span>
                  <span className="text-slate-500 truncate font-sans text-[11px]">
                    {cam.location_name || cam.name}
                  </span>
                </div>

                <div className="flex items-center gap-1 flex-none">
                  {cam.ai_enabled && (
                    <span className="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold bg-purple-50 text-purple-700 border border-purple-200">
                      ANPR
                    </span>
                  )}
                </div>
              </div>

              {/* Video Stage Container */}
              <div
                onClick={() => toggleStream(cam.camera_id)}
                className="relative aspect-video bg-slate-950 flex items-center justify-center cursor-pointer overflow-hidden select-none"
              >
                {isLive ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={api.liveUrl(cam.camera_id, detect)}
                    alt={cam.name}
                    className="w-full h-full object-contain"
                  />
                ) : (
                  <div className="relative w-full h-full flex flex-col items-center justify-center text-slate-400">
                    {/* eslint-disable-next-line @next/next/no-img-element */ }
                    <img
                      src={api.thumbnailUrl(cam.camera_id)}
                      alt={cam.name}
                      className="absolute inset-0 w-full h-full object-cover opacity-25 group-hover:opacity-40 transition-opacity"
                    />
                    <div className="relative z-10 w-10 h-10 rounded-full bg-slate-800/80 backdrop-blur-xs border border-slate-600 flex items-center justify-center text-white shadow-lg group-hover:scale-105 group-hover:bg-[#2563EB] transition-all">
                      <Play className="w-4 h-4 ml-0.5" />
                    </div>
                    <span className="relative z-10 text-[11px] font-mono text-slate-300 mt-2 font-medium">
                      Click to start feed
                    </span>
                  </div>
                )}

                {/* Live Indicator Pill */}
                {isLive && (
                  <div className="absolute top-2.5 left-2.5 flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900/80 backdrop-blur-xs text-[10px] font-mono font-bold text-emerald-400 border border-emerald-500/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    LIVE
                  </div>
                )}

                {/* Hover Action Bar */}
                <div className="absolute top-2.5 right-2.5 flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity z-20">
                  {/* Fullscreen Expand Button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!isLive) toggleStream(cam.camera_id);
                      setFocusCamera(cam);
                    }}
                    title="Full screen focus view"
                    className="w-7 h-7 rounded-lg bg-slate-900/80 backdrop-blur-md border border-slate-700 text-slate-200 hover:text-white hover:bg-[#2563EB] flex items-center justify-center transition-all cursor-pointer shadow-lg"
                  >
                    <Maximize2 className="w-3.5 h-3.5" />
                  </button>

                  {/* Stop Feed Button */}
                  {isLive && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleStream(cam.camera_id);
                      }}
                      title="Stop this feed"
                      className="w-7 h-7 rounded-lg bg-slate-900/80 backdrop-blur-md border border-slate-700 text-slate-200 hover:text-white hover:bg-rose-600 flex items-center justify-center transition-all cursor-pointer shadow-lg"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Bottom Metadata Bar */}
              <div className="flex items-center justify-between px-3 py-1.5 bg-slate-50 border-t border-slate-100 text-[11px] font-mono text-slate-500">
                <span className="truncate">{cam.location_name || cam.name}</span>
                <span className="flex-none font-semibold">
                  {cam.fps || 10} FPS · {cam.codec || 'H.264'}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Fullscreen Focus Modal */}
      {focusCamera && (
        <Modal
          isOpen={Boolean(focusCamera)}
          onClose={() => setFocusCamera(null)}
          title={`${focusCamera.camera_id} — ${focusCamera.location_name || focusCamera.name}`}
          spec="FULL SCREEN FOCUS"
          maxWidth="6xl"
        >
          <div className="space-y-4">
            <div className="relative aspect-video w-full rounded-xl overflow-hidden bg-black border border-slate-800 shadow-2xl flex items-center justify-center">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={api.liveUrl(focusCamera.camera_id, detect)}
                alt={focusCamera.name}
                className="w-full h-full object-contain"
              />
              <div className="absolute top-4 left-4 flex items-center gap-2 px-3 py-1 rounded-lg bg-slate-900/85 backdrop-blur-md text-xs font-mono font-bold text-emerald-400 border border-emerald-500/30">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                HIGH-RESOLUTION STREAM · YOLO11 DETECTIONS
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-[10px] text-slate-400 block">STATUS</span>
                <span className="font-bold text-emerald-700">{focusCamera.status}</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-[10px] text-slate-400 block">ANPR SCORE</span>
                <span className="font-bold text-purple-700">{focusCamera.plate_score}/100</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-[10px] text-slate-400 block">COORDINATES</span>
                <span className="font-bold text-slate-800">
                  {focusCamera.latitude?.toFixed(4)}, {focusCamera.longitude?.toFixed(4)}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-[10px] text-slate-400 block">DEPARTMENT</span>
                <span className="font-bold text-slate-800">{focusCamera.department || 'Gujarat Police'}</span>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
