'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Bell, X, ShieldAlert, Route, ChevronRight } from 'lucide-react';
import { socket, type LiveAlert } from '@/lib/socket';
import { api } from '@/lib/api';

export function AlertToast() {
  const router = useRouter();
  const [alerts, setAlerts] = useState<LiveAlert[]>([]);

  useEffect(() => {
    const unsub = socket.onAlert((alert) => {
      setAlerts((prev) => [alert, ...prev.slice(0, 3)]);
      // Play subtle chime sound if desired or browser audio enabled
    });

    return () => unsub();
  }, []);

  const dismiss = (id: string) => {
    setAlerts((prev) => prev.filter((a) => a.id !== id));
  };

  if (alerts.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none">
      {alerts.map((alert) => {
        const isCritical = alert.priority === 'CRITICAL' || alert.type === 'HOTLIST_MATCH';
        return (
          <div
            key={alert.id || `${alert.plate_number}-${alert.timestamp}`}
            className={`pointer-events-auto relative w-full bg-white rounded-xl border p-4 shadow-modal-pop transition-all animate-in slide-in-from-bottom-5 duration-200 ${
              isCritical
                ? 'border-rose-300 ring-2 ring-rose-500/20'
                : 'border-slate-200'
            }`}
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="flex items-center gap-2">
                <div
                  className={`w-6 h-6 rounded-lg flex items-center justify-center text-white flex-none ${
                    isCritical ? 'bg-rose-600' : 'bg-[#2563EB]'
                  }`}
                >
                  <ShieldAlert className="w-3.5 h-3.5" />
                </div>
                <div className="flex flex-col">
                  <span className="text-xs font-bold text-slate-900 tracking-tight">
                    {alert.type === 'HOTLIST_MATCH' ? 'HOTLIST BOLO MATCH' : 'ANPR SIGHTING'}
                  </span>
                  <span className="text-[10px] font-mono text-slate-500">
                    {new Date(alert.timestamp).toLocaleTimeString()} · {alert.camera_id}
                  </span>
                </div>
              </div>
              <button
                onClick={() => dismiss(alert.id)}
                className="p-1 text-slate-400 hover:text-slate-700 rounded-md transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Content & Plate Crop */}
            <div className="flex items-center justify-between gap-3 my-2.5 p-2 bg-slate-50/80 rounded-lg border border-slate-200/80">
              <div className="flex flex-col">
                <span className="text-base font-mono font-bold text-slate-900 tracking-wider">
                  {alert.plate_number}
                </span>
                <span className="text-[11px] text-slate-600 font-medium">
                  {alert.location_name || alert.camera_name || alert.camera_id}
                </span>
              </div>
              {alert.crop_path ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src={api.evidenceUrl(alert.crop_path)}
                  alt={alert.plate_number}
                  className="h-10 w-24 object-cover rounded border border-slate-300 shadow-xs flex-none"
                />
              ) : (
                <div className="px-2 py-1 bg-blue-100/70 border border-blue-200 rounded text-[11px] font-mono font-bold text-blue-900">
                  {Math.round((alert.confidence || 0.95) * 100)}% CONF
                </div>
              )}
            </div>

            {/* Action Bar */}
            <div className="flex items-center justify-between pt-1">
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-rose-50 text-rose-800 border border-rose-200">
                {alert.category || 'WANTED'}
              </span>
              <button
                onClick={() => {
                  router.push(`/trace_page?plate=${encodeURIComponent(alert.plate_number)}`);
                  dismiss(alert.id);
                }}
                className="inline-flex items-center gap-1 text-xs font-semibold text-[#2563EB] hover:text-[#1D4ED8] transition-colors"
              >
                <span>Trace Vehicle</span>
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
