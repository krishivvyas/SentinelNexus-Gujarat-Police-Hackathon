'use client';

import React, { useEffect, useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Route,
  Search,
  MapPin,
  Clock,
  ArrowRight,
  ShieldAlert,
  Car,
  Calendar,
  Sparkles,
  ChevronRight,
} from 'lucide-react';
import { api, type VehicleTrace } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';

function TraceContent() {
  const searchParams = useSearchParams();
  const initialPlate = searchParams.get('plate') || '';
  const [plate, setPlate] = useState(initialPlate);
  const [traceData, setTraceData] = useState<VehicleTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleTrace = async (searchPlate: string) => {
    if (!searchPlate.trim()) return;
    try {
      setLoading(true);
      setError('');
      const data = await api.getVehicleTrace(searchPlate.trim());
      setTraceData(data);
    } catch (e: any) {
      setError('No vehicle journey hops found for this license plate.');
      setTraceData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (initialPlate) {
      setPlate(initialPlate);
      handleTrace(initialPlate);
    }
  }, [initialPlate]);

  return (
    <div className="space-y-6">
      {/* Header & Search */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Route className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Cross-Camera Vehicle Journey Trace
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-blue-50 text-blue-800 border border-blue-200">
              RECONSTRUCTION
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            Chronological multi-camera trajectory mapping & route interpolation
          </p>
        </div>

        {/* Plate Search Form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleTrace(plate);
          }}
          className="flex items-center gap-2"
        >
          <div className="w-56">
            <Input
              type="text"
              placeholder="Enter Plate (e.g. GJ01AB1234)..."
              value={plate}
              onChange={(e) => setPlate(e.target.value.toUpperCase())}
              icon={<Search className="w-3.5 h-3.5 text-slate-400" />}
            />
          </div>
          <Button variant="primary" size="sm" type="submit" disabled={loading}>
            {loading ? 'Tracing...' : 'Run Trace'}
          </Button>
        </form>
      </div>

      {/* Trace Results */}
      {traceData ? (
        <div className="space-y-6">
          {/* Summary Bento Header Card */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <Card header="Target Vehicle" spec="REGISTRATION">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center text-[#2563EB]">
                  <Car className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-lg font-mono font-bold text-slate-900">
                    {traceData.plate_number}
                  </span>
                  <span className="text-[11px] text-slate-500 block">
                    Verified Vehicle Plate
                  </span>
                </div>
              </div>
            </Card>

            <Card header="Total Sightings" spec="GRID HOPS">
              <span className="text-2xl font-mono font-bold text-slate-900">
                {traceData.points?.length || traceData.total_sightings || 0}
              </span>
              <span className="text-[11px] text-slate-500 block mt-1">
                Distinct camera points
              </span>
            </Card>

            <Card header="First Captured" spec="START POINT">
              <span className="text-xs font-mono font-bold text-slate-800 block truncate">
                {traceData.first_seen ? new Date(traceData.first_seen).toLocaleString() : 'N/A'}
              </span>
              <span className="text-[11px] text-slate-500 block mt-1">
                Initial sighting node
              </span>
            </Card>

            <Card header="Last Seen" spec="LATEST NODE">
              <span className="text-xs font-mono font-bold text-slate-800 block truncate">
                {traceData.last_seen ? new Date(traceData.last_seen).toLocaleString() : 'N/A'}
              </span>
              <span className="text-[11px] text-emerald-600 font-bold block mt-1">
                Most recent sighting
              </span>
            </Card>
          </div>

          {/* Chronological Waypoints Timeline */}
          <Card header="Chronological Hop Reconstruction" spec="TIMELINE">
            <div className="relative pl-6 space-y-6 before:absolute before:left-2.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-200">
              {traceData.points && traceData.points.length > 0 ? (
                traceData.points.map((pt, idx) => (
                  <div key={idx} className="relative flex items-start gap-4 group">
                    {/* Node Dot */}
                    <div className="absolute -left-6 top-1 w-5 h-5 rounded-full bg-white border-2 border-[#2563EB] flex items-center justify-center shadow-xs">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB]" />
                    </div>

                    {/* Timeline Item Card */}
                    <div className="flex-1 p-4 bg-slate-50/80 rounded-xl border border-slate-200 hover:bg-white hover:border-slate-300 transition-all shadow-xs">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-bold text-slate-900 text-sm">
                            Hop #{idx + 1}: {pt.camera_id}
                          </span>
                          <Badge variant="blue">
                            {Math.round((pt.confidence || 0.92) * 100)}% CONF
                          </Badge>
                        </div>
                        <div className="flex items-center gap-1.5 text-xs font-mono text-slate-500">
                          <Clock className="w-3.5 h-3.5" />
                          <span>{new Date(pt.timestamp).toLocaleString()}</span>
                        </div>
                      </div>

                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <span className="text-xs font-semibold text-slate-800">
                            {pt.location_name || 'Gujarat Police Surveillance Node'}
                          </span>
                          {pt.latitude && pt.longitude && (
                            <span className="text-[11px] font-mono text-slate-500 block mt-0.5">
                              GPS: {pt.latitude.toFixed(4)}, {pt.longitude.toFixed(4)}
                            </span>
                          )}
                        </div>

                        {pt.crop_path && (
                          /* eslint-disable-next-line @next/next/no-img-element */
                          <img
                            src={api.evidenceUrl(pt.crop_path)}
                            alt="Crop"
                            className="h-10 w-28 object-cover rounded border border-slate-200 shadow-xs flex-none bg-slate-900"
                          />
                        )}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-xs font-mono text-slate-500">No trajectory points found.</p>
              )}
            </div>
          </Card>
        </div>
      ) : (
        <div className="p-16 text-center bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)]">
          <Route className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <h3 className="text-base font-bold text-slate-800 mb-1">
            Search Vehicle Journey
          </h3>
          <p className="text-xs font-mono text-slate-500 max-w-md mx-auto">
            {error || 'Enter any vehicle registration number to reconstruct its multi-camera trajectory across the Gujarat surveillance grid.'}
          </p>
        </div>
      )}
    </div>
  );
}

export default function TracePage() {
  return (
    <Suspense
      fallback={
        <div className="p-12 text-center font-mono text-xs text-slate-400">
          Loading vehicle trace parameters...
        </div>
      }
    >
      <TraceContent />
    </Suspense>
  );
}
