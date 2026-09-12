'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search,
  MapPin,
  LayoutGrid,
  ScanLine,
  Route,
  Bell,
  Camera,
  Activity,
  FileText,
  ArrowRight,
  Shield,
  Video,
} from 'lucide-react';
import { api, type Camera as CameraType } from '@/lib/api';
import { Kbd } from '@/components/ui/Kbd';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

export function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [cameras, setCameras] = useState<CameraType[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      api.getCameras().then(setCameras).catch(() => {});
    }
  }, [isOpen]);

  const quickNav = [
    { label: 'Command Centre Map Grid', href: '/', icon: MapPin, spec: 'GIS' },
    { label: 'Multi-Camera Video Wall', href: '/wall_page', icon: LayoutGrid, spec: 'LIVE' },
    { label: 'Real-time ANPR Sightings', href: '/events_page', icon: ScanLine, spec: 'OCR' },
    { label: 'Vehicle Journey Trace', href: '/trace_page', icon: Route, spec: 'TRACE' },
    { label: 'Hotlist / Watchlist Targets', href: '/watchlist_page', icon: Bell, spec: 'HOTLIST' },
    { label: 'Camera Inventory & Importer', href: '/cameras_page', icon: Camera, spec: 'REGISTRY' },
    { label: 'System Health & Diagnostics', href: '/health_page', icon: Activity, spec: 'METRICS' },
    { label: 'Audit Trail & Compliance Logs', href: '/audit_page', icon: FileText, spec: 'LOGS' },
  ];

  const filteredNav = quickNav.filter((item) =>
    item.label.toLowerCase().includes(query.toLowerCase()) ||
    item.spec.toLowerCase().includes(query.toLowerCase())
  );

  const filteredCameras = cameras.filter((cam) =>
    cam.camera_id.toLowerCase().includes(query.toLowerCase()) ||
    cam.name.toLowerCase().includes(query.toLowerCase()) ||
    cam.location_name.toLowerCase().includes(query.toLowerCase())
  ).slice(0, 6);

  const totalResults = filteredNav.length + filteredCameras.length;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % Math.max(1, totalResults));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + totalResults) % Math.max(1, totalResults));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        executeSelection();
      }
    };

    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, totalResults, selectedIndex, filteredNav, filteredCameras]);

  const executeSelection = () => {
    if (selectedIndex < filteredNav.length) {
      const item = filteredNav[selectedIndex];
      if (item) {
        router.push(item.href);
        onClose();
      }
    } else {
      const camIndex = selectedIndex - filteredNav.length;
      const cam = filteredCameras[camIndex];
      if (cam) {
        router.push(`/?camera=${encodeURIComponent(cam.camera_id)}`);
        onClose();
      }
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-start justify-center pt-20 p-4 animate-in fade-in duration-100">
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />
      
      <div className="relative w-full max-w-xl bg-white rounded-2xl border border-slate-200/90 shadow-modal-pop overflow-hidden z-10 animate-in zoom-in-95 duration-100">
        {/* Search Input Bar */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-slate-100 bg-slate-50/50">
          <Search className="w-4 h-4 text-slate-400 flex-none" />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            placeholder="Type a command, page, camera ID, or location..."
            className="w-full bg-transparent text-sm text-slate-900 placeholder:text-slate-400 font-mono focus:outline-none"
          />
          <Kbd>ESC</Kbd>
        </div>

        {/* Results List */}
        <div className="max-h-[380px] overflow-y-auto p-2 space-y-4">
          {/* Navigation Section */}
          {filteredNav.length > 0 && (
            <div>
              <div className="px-3 py-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-400">
                Navigation & Views
              </div>
              <div className="space-y-0.5 mt-1">
                {filteredNav.map((item, idx) => {
                  const Icon = item.icon;
                  const isSelected = selectedIndex === idx;
                  return (
                    <button
                      key={item.href}
                      onClick={() => {
                        router.push(item.href);
                        onClose();
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all text-left ${
                        isSelected
                          ? 'bg-[#2563EB] text-white shadow-tactile-btn'
                          : 'text-slate-700 hover:bg-slate-100'
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <Icon className={`w-4 h-4 ${isSelected ? 'text-white' : 'text-slate-400'}`} />
                        <span>{item.label}</span>
                      </div>
                      <span
                        className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                          isSelected
                            ? 'bg-blue-700/80 text-white'
                            : 'bg-slate-100 text-slate-600 border border-slate-200'
                        }`}
                      >
                        {item.spec}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Cameras Section */}
          {filteredCameras.length > 0 && (
            <div>
              <div className="px-3 py-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-400">
                Cameras ({filteredCameras.length})
              </div>
              <div className="space-y-0.5 mt-1">
                {filteredCameras.map((cam, idx) => {
                  const overallIdx = filteredNav.length + idx;
                  const isSelected = selectedIndex === overallIdx;
                  return (
                    <button
                      key={cam.camera_id}
                      onClick={() => {
                        router.push(`/?camera=${encodeURIComponent(cam.camera_id)}`);
                        onClose();
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all text-left ${
                        isSelected
                          ? 'bg-[#2563EB] text-white shadow-tactile-btn'
                          : 'text-slate-700 hover:bg-slate-100'
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <Video className={`w-4 h-4 flex-none ${isSelected ? 'text-white' : 'text-slate-400'}`} />
                        <span className="font-mono font-semibold flex-none">
                          {cam.camera_id}
                        </span>
                        <span className="truncate text-slate-500 font-sans">
                          {cam.location_name || cam.name}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5 flex-none">
                        {cam.ai_enabled && (
                          <span
                            className={`px-1.5 py-0.2 rounded text-[10px] font-mono font-bold ${
                              isSelected ? 'bg-blue-700 text-white' : 'bg-purple-50 text-purple-700 border border-purple-200'
                            }`}
                          >
                            ANPR
                          </span>
                        )}
                        <span
                          className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                            isSelected
                              ? 'bg-blue-700 text-white'
                              : cam.status === 'ONLINE'
                              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                              : 'bg-amber-50 text-amber-700 border border-amber-200'
                          }`}
                        >
                          {cam.status}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {totalResults === 0 && (
            <div className="p-8 text-center text-slate-500 text-xs font-mono">
              No matching commands or cameras found for &quot;{query}&quot;
            </div>
          )}
        </div>

        {/* Footer Shortcut Hints */}
        <div className="flex items-center justify-between px-4 py-2 bg-slate-50 border-t border-slate-100 text-[11px] font-mono text-slate-500">
          <div className="flex items-center gap-3">
            <span>
              <Kbd>↑</Kbd> <Kbd>↓</Kbd> Navigate
            </span>
            <span>
              <Kbd>↵</Kbd> Select
            </span>
          </div>
          <span>Sentinel Nexus Ultra UI</span>
        </div>
      </div>
    </div>
  );
}
