'use client';

import React, { useEffect, useState, useMemo } from 'react';
import {
  Camera as CameraIcon,
  Upload,
  Search,
  RefreshCw,
  Edit2,
  MapPin,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FileSpreadsheet,
  Download,
} from 'lucide-react';
import { api, type Camera } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';
import { Tabs } from '@/components/ui/Tabs';

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [editingCamera, setEditingCamera] = useState<Camera | null>(null);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);

  // Edit form state
  const [editName, setEditName] = useState('');
  const [editLocation, setEditLocation] = useState('');
  const [editLat, setEditLat] = useState('');
  const [editLng, setEditLng] = useState('');
  const [editStatus, setEditStatus] = useState('ONLINE');

  const fetchCameras = async () => {
    try {
      setLoading(true);
      const data = await api.getCameras();
      setCameras(data);
    } catch (e) {
      console.error('Failed to load cameras', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCameras();
  }, []);

  const handleEditClick = (cam: Camera) => {
    setEditingCamera(cam);
    setEditName(cam.name);
    setEditLocation(cam.location_name);
    setEditLat(cam.latitude ? String(cam.latitude) : '');
    setEditLng(cam.longitude ? String(cam.longitude) : '');
    setEditStatus(cam.status);
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingCamera) return;

    try {
      const updated = await api.updateCamera(editingCamera.camera_id, {
        name: editName,
        location_name: editLocation,
        latitude: editLat ? parseFloat(editLat) : null,
        longitude: editLng ? parseFloat(editLng) : null,
        status: editStatus,
      });

      setCameras((prev) =>
        prev.map((c) => (c.camera_id === updated.camera_id ? updated : c))
      );
      setEditingCamera(null);
    } catch (e) {
      alert('Failed to update camera metadata');
    }
  };

  const handleImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importFile) return;

    try {
      setImporting(true);
      await api.importCommit(importFile);
      setIsImportOpen(false);
      setImportFile(null);
      fetchCameras();
    } catch (e) {
      alert('Failed to import cameras from file');
    } finally {
      setImporting(false);
    }
  };

  const filteredCameras = useMemo(() => {
    return cameras.filter((c) => {
      if (statusFilter === 'ONLINE' && c.status !== 'ONLINE') return false;
      if (statusFilter === 'DEGRADED' && c.status !== 'DEGRADED') return false;
      if (statusFilter === 'OFFLINE' && c.status !== 'OFFLINE') return false;
      if (statusFilter === 'UNPLACED' && c.latitude && c.longitude) return false;

      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          c.camera_id.toLowerCase().includes(q) ||
          c.name.toLowerCase().includes(q) ||
          c.location_name.toLowerCase().includes(q) ||
          c.district.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [cameras, statusFilter, searchQuery]);

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <CameraIcon className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Federated Camera Registry
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-blue-50 text-blue-800 border border-blue-200">
              {cameras.length} NODES
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            CCTV inventory, geospatial coordinates, plate score metrics & bulk metadata importer
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2.5 flex-wrap">
          <Tabs
            activeId={statusFilter}
            onChange={setStatusFilter}
            tabs={[
              { id: 'ALL', label: 'All' },
              { id: 'ONLINE', label: 'Online' },
              { id: 'DEGRADED', label: 'Degraded' },
              { id: 'UNPLACED', label: 'No Pos' },
            ]}
          />
          <div className="w-44">
            <Input
              type="text"
              placeholder="Search camera..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              icon={<Search className="w-3.5 h-3.5 text-slate-400" />}
            />
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setIsImportOpen(true)}
            icon={<Upload className="w-3.5 h-3.5" />}
          >
            Import CSV
          </Button>
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

      {/* Camera Inventory Datagrid */}
      <div className="bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-600 uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4">Node ID</th>
                <th className="py-3 px-4">Location & Name</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Plate Score</th>
                <th className="py-3 px-4">Coordinates</th>
                <th className="py-3 px-4">Codec / FPS</th>
                <th className="py-3 px-4 text-right">Edit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredCameras.map((cam) => {
                const isPlaced = cam.latitude && cam.longitude;
                return (
                  <tr key={cam.camera_id} className="hover:bg-slate-50/70 transition-colors">
                    {/* ID */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <span className="font-bold text-slate-900 bg-slate-100 px-2 py-0.5 rounded border border-slate-200 shadow-xs">
                        {cam.camera_id}
                      </span>
                    </td>

                    {/* Location */}
                    <td className="py-3 px-4">
                      <div className="flex flex-col max-w-xs truncate">
                        <span className="font-semibold text-slate-800 text-xs truncate">
                          {cam.location_name || cam.name}
                        </span>
                        <span className="text-[11px] text-slate-500 font-sans truncate">
                          {cam.district || 'Ahmedabad'} · {cam.department || 'Traffic'}
                        </span>
                      </div>
                    </td>

                    {/* Status */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <Badge
                        variant={
                          cam.status === 'ONLINE'
                            ? 'ok'
                            : cam.status === 'DEGRADED'
                            ? 'warn'
                            : 'crit'
                        }
                      >
                        {cam.status}
                      </Badge>
                    </td>

                    {/* Plate Score */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <span className="font-bold text-purple-700 bg-purple-50 px-2 py-0.5 rounded border border-purple-200 text-[11px]">
                        {cam.plate_score}/100
                      </span>
                    </td>

                    {/* Coordinates */}
                    <td className="py-3 px-4 whitespace-nowrap text-slate-600">
                      {isPlaced ? (
                        <span>
                          {cam.latitude?.toFixed(4)}, {cam.longitude?.toFixed(4)}
                        </span>
                      ) : (
                        <span className="text-amber-600 font-bold bg-amber-50 px-1.5 py-0.5 rounded border border-amber-200 text-[10px]">
                          NO POS
                        </span>
                      )}
                    </td>

                    {/* Stream Specs */}
                    <td className="py-3 px-4 whitespace-nowrap text-slate-500">
                      {cam.codec || 'H.264'} · {cam.fps || 10} FPS
                    </td>

                    {/* Edit Button */}
                    <td className="py-3 px-4 whitespace-nowrap text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleEditClick(cam)}
                        icon={<Edit2 className="w-3.5 h-3.5 text-slate-500" />}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Camera Modal */}
      {editingCamera && (
        <Modal
          isOpen={Boolean(editingCamera)}
          onClose={() => setEditingCamera(null)}
          title={`Edit Camera — ${editingCamera.camera_id}`}
          spec="METADATA CONFIG"
        >
          <form onSubmit={handleSaveEdit} className="space-y-4">
            <Input
              label="Location / Junction Name"
              value={editLocation}
              onChange={(e) => setEditLocation(e.target.value)}
              required
            />

            <Input
              label="Camera Display Name"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              required
            />

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Latitude"
                type="number"
                step="any"
                value={editLat}
                onChange={(e) => setEditLat(e.target.value)}
              />
              <Input
                label="Longitude"
                type="number"
                step="any"
                value={editLng}
                onChange={(e) => setEditLng(e.target.value)}
              />
            </div>

            <div>
              <label className="block text-xs font-mono font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
                Node Status
              </label>
              <select
                value={editStatus}
                onChange={(e) => setEditStatus(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-50 border border-slate-200/90 text-slate-900 focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 shadow-xs"
              >
                <option value="ONLINE">ONLINE</option>
                <option value="DEGRADED">DEGRADED</option>
                <option value="OFFLINE">OFFLINE</option>
              </select>
            </div>

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
              <Button
                variant="outline"
                size="sm"
                type="button"
                onClick={() => setEditingCamera(null)}
              >
                Cancel
              </Button>
              <Button variant="primary" size="sm" type="submit">
                Save Changes
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* Bulk CSV Importer Modal */}
      <Modal
        isOpen={isImportOpen}
        onClose={() => setIsImportOpen(false)}
        title="Bulk Camera Registry Importer"
        spec="CSV / JSON INGEST"
      >
        <form onSubmit={handleImportSubmit} className="space-y-4">
          <p className="text-xs text-slate-600 font-medium">
            Upload a CSV or JSON file containing camera stream URLs, latitude, longitude, and department mapping.
          </p>

          <div className="p-6 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50 flex flex-col items-center justify-center text-center">
            <FileSpreadsheet className="w-8 h-8 text-[#2563EB] mb-2" />
            <input
              type="file"
              accept=".csv,.json"
              onChange={(e) => setImportFile(e.target.files?.[0] || null)}
              className="text-xs font-mono text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#2563EB] file:text-white file:cursor-pointer"
            />
          </div>

          <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => setIsImportOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="submit"
              disabled={!importFile || importing}
            >
              {importing ? 'Importing...' : 'Commit Import'}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
