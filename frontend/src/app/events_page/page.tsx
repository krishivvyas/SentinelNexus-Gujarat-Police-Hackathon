'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ScanLine,
  Search,
  RefreshCw,
  Route,
  Filter,
  Camera as CameraIcon,
  Clock,
  Sparkles,
  ExternalLink,
} from 'lucide-react';
import { api, type Sighting, type Camera } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';

export default function EventsPage() {
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [plateQuery, setPlateQuery] = useState('');
  const [selectedCamera, setSelectedCamera] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchSightings = async () => {
    try {
      setLoading(true);
      const [sights, cams] = await Promise.all([
        api.getSightings({ limit: 100, camera_id: selectedCamera || undefined, plate: plateQuery || undefined }),
        api.getCameras().catch(() => []),
      ]);
      setSightings(sights);
      setCameras(cams);
    } catch (e) {
      console.error('Failed to load sightings', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSightings();
    const interval = setInterval(fetchSightings, 8000);
    return () => clearInterval(interval);
  }, [selectedCamera]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchSightings();
  };

  return (
    <div className="space-y-6">
      {/* Header & Filter Controls */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <ScanLine className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Real-time ANPR Intelligence Stream
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-blue-50 text-blue-800 border border-blue-200">
              {sightings.length} SIGHTINGS
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            OCR plate detections, vehicle crops & optical confidence telemetry
          </p>
        </div>

        {/* Filter Bar */}
        <form onSubmit={handleSearch} className="flex items-center gap-2.5 flex-wrap">
          <div className="w-48">
            <Input
              type="text"
              placeholder="Filter plate (e.g. GJ01)..."
              value={plateQuery}
              onChange={(e) => setPlateQuery(e.target.value)}
              icon={<Search className="w-3.5 h-3.5 text-slate-400" />}
            />
          </div>

          <select
            value={selectedCamera}
            onChange={(e) => setSelectedCamera(e.target.value)}
            className="px-3 py-2 text-xs font-mono rounded-lg bg-slate-50 border border-slate-200/90 text-slate-900 focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 shadow-xs"
          >
            <option value="">All Cameras</option>
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                {c.camera_id} — {c.location_name || c.name}
              </option>
            ))}
          </select>

          <Button variant="primary" size="sm" type="submit">
            Search
          </Button>

          <Button
            variant="secondary"
            size="sm"
            type="button"
            onClick={fetchSightings}
            icon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />}
          >
            Refresh
          </Button>
        </form>
      </div>

      {/* Sightings Datagrid Table */}
      <div className="bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-600 uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4">Timestamp</th>
                <th className="py-3 px-4">License Plate</th>
                <th className="py-3 px-4">Confidence</th>
                <th className="py-3 px-4">Camera ID & Node</th>
                <th className="py-3 px-4">Plate Crop Image</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sightings.map((s) => {
                const isHighConf = (s.confidence || 0) >= 0.85;
                return (
                  <tr key={s.id} className="hover:bg-slate-50/70 transition-colors">
                    {/* Timestamp */}
                    <td className="py-3 px-4 whitespace-nowrap text-slate-500">
                      <div className="flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-slate-400" />
                        <span>{new Date(s.timestamp).toLocaleString()}</span>
                      </div>
                    </td>

                    {/* Plate */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <span className="px-2.5 py-1 rounded bg-slate-100 border border-slate-300 font-mono font-bold text-slate-900 text-sm tracking-wider shadow-xs">
                          {s.plate_number}
                        </span>
                        {s.hotlist_match && (
                          <Badge variant="crit">HOTLIST</Badge>
                        )}
                      </div>
                    </td>

                    {/* OCR Confidence */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold ${
                          isHighConf
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : 'bg-amber-50 text-amber-700 border border-amber-200'
                        }`}
                      >
                        {Math.round((s.confidence || 0.9) * 100)}%
                      </span>
                    </td>

                    {/* Camera */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <div className="flex flex-col">
                        <span className="font-bold text-slate-800">{s.camera_id}</span>
                        <span className="text-[11px] font-sans text-slate-500">
                          {cameras.find((c) => c.camera_id === s.camera_id)?.location_name || 'Grid Node'}
                        </span>
                      </div>
                    </td>

                    {/* Crop Image */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      {s.crop_path ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img
                          src={api.evidenceUrl(s.crop_path)}
                          alt={s.plate_number}
                          className="h-9 w-24 object-cover rounded border border-slate-200 shadow-xs hover:scale-110 transition-transform bg-slate-900"
                        />
                      ) : (
                        <span className="text-slate-400 text-[11px]">—</span>
                      )}
                    </td>

                    {/* Trace Action */}
                    <td className="py-3 px-4 whitespace-nowrap text-right">
                      <Link href={`/trace_page?plate=${encodeURIComponent(s.plate_number)}`}>
                        <Button variant="secondary" size="sm" icon={<Route className="w-3.5 h-3.5 text-[#2563EB]" />}>
                          Trace
                        </Button>
                      </Link>
                    </td>
                  </tr>
                );
              })}

              {sightings.length === 0 && !loading && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-400 font-mono">
                    No ANPR sightings matching filter criteria.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
