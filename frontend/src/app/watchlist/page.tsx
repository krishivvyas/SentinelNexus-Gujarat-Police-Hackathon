'use client';

import React, { useEffect, useState } from 'react';
import {
  Bell,
  Plus,
  Trash2,
  ShieldAlert,
  Search,
  RefreshCw,
  AlertTriangle,
  Clock,
  User,
} from 'lucide-react';
import { api, type WatchlistTarget } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input, Textarea } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';

export default function WatchlistPage() {
  const [targets, setTargets] = useState<WatchlistTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [isAddOpen, setIsAddOpen] = useState(false);

  // Form State
  const [newPlate, setNewPlate] = useState('');
  const [newReason, setNewReason] = useState('');
  const [newCategory, setNewCategory] = useState('STOLEN');
  const [newPriority, setNewPriority] = useState('CRITICAL');
  const [submitting, setSubmitting] = useState(false);

  const fetchWatchlist = async () => {
    try {
      setLoading(true);
      const data = await api.getWatchlist();
      setTargets(data);
    } catch (e) {
      console.error('Failed to load watchlist', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWatchlist();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newPlate.trim() || !newReason.trim()) return;

    try {
      setSubmitting(true);
      await api.addToWatchlist({
        plate_number: newPlate.trim().toUpperCase(),
        reason: newReason.trim(),
        category: newCategory,
        priority: newPriority,
      });
      setIsAddOpen(false);
      setNewPlate('');
      setNewReason('');
      fetchWatchlist();
    } catch (e) {
      alert('Failed to register target plate');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (plate: string) => {
    if (!confirm(`Remove ${plate} from active hotlist?`)) return;
    try {
      await api.removeFromWatchlist(plate);
      setTargets((prev) => prev.filter((t) => t.plate_number !== plate));
    } catch (e) {
      alert('Failed to remove target');
    }
  };

  const filteredTargets = targets.filter(
    (t) =>
      t.plate_number.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.reason.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.category.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Bell className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Hotlist & Watchlist Target Registry
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-rose-50 text-rose-800 border border-rose-200">
              {targets.length} BOLO TARGETS
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            Real-time automatic sighting triggers, stolen vehicle tracking & wanted suspect alerts
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2.5">
          <div className="w-48">
            <Input
              type="text"
              placeholder="Search target..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              icon={<Search className="w-3.5 h-3.5 text-slate-400" />}
            />
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setIsAddOpen(true)}
            icon={<Plus className="w-3.5 h-3.5" />}
          >
            Add Target
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={fetchWatchlist}
            icon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />}
          >
            Sync
          </Button>
        </div>
      </div>

      {/* Target Bento Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredTargets.map((target) => {
          const isCritical = target.priority === 'CRITICAL';
          return (
            <div
              key={target.id || target.plate_number}
              className={`bg-white rounded-xl border p-5 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] flex flex-col justify-between transition-all hover:shadow-[0_8px_24px_-4px_rgba(0,0,0,0.08)] ${
                isCritical ? 'border-rose-200 ring-1 ring-rose-500/10' : 'border-slate-200/90'
              }`}
            >
              <div>
                {/* Header with Plate and Badges */}
                <div className="flex items-center justify-between gap-2 mb-3 pb-3 border-b border-slate-100">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-base font-bold text-slate-900 tracking-wider">
                      {target.plate_number}
                    </span>
                    <Badge variant={isCritical ? 'crit' : 'warn'}>
                      {target.priority}
                    </Badge>
                  </div>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-100 text-slate-700 border border-slate-200">
                    {target.category}
                  </span>
                </div>

                {/* Reason text */}
                <p className="text-xs text-slate-700 font-medium leading-relaxed mb-4">
                  {target.reason}
                </p>
              </div>

              {/* Footer details */}
              <div className="flex items-center justify-between pt-3 border-t border-slate-100 text-[11px] font-mono text-slate-500">
                <div className="flex items-center gap-1.5">
                  <Clock className="w-3 h-3 text-slate-400" />
                  <span>{new Date(target.created_at).toLocaleDateString()}</span>
                </div>
                <button
                  onClick={() => handleDelete(target.plate_number)}
                  className="p-1 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                  title="Remove Target"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          );
        })}

        {filteredTargets.length === 0 && !loading && (
          <div className="col-span-full p-12 text-center text-slate-400 font-mono text-xs bg-white rounded-xl border border-slate-200">
            No watchlist targets found. Click &quot;Add Target&quot; to register a vehicle for real-time alerts.
          </div>
        )}
      </div>

      {/* Add Target Modal */}
      <Modal
        isOpen={isAddOpen}
        onClose={() => setIsAddOpen(false)}
        title="Register Hotlist / BOLO Target"
        spec="SECURITY RULE"
      >
        <form onSubmit={handleAdd} className="space-y-4">
          <Input
            label="License Plate Number"
            placeholder="e.g. GJ01AA9999"
            value={newPlate}
            onChange={(e) => setNewPlate(e.target.value.toUpperCase())}
            required
          />

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-mono font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
                Target Category
              </label>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-50 border border-slate-200/90 text-slate-900 focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 shadow-xs"
              >
                <option value="STOLEN">Stolen Vehicle</option>
                <option value="WANTED">Wanted Suspect</option>
                <option value="EXPIRED_RC">Expired RC / Fitness</option>
                <option value="SUSPECT">Suspect Pattern</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-mono font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
                Alert Priority
              </label>
              <select
                value={newPriority}
                onChange={(e) => setNewPriority(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg bg-slate-50 border border-slate-200/90 text-slate-900 focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 shadow-xs"
              >
                <option value="CRITICAL">Critical (Immediate Chime)</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
            </div>
          </div>

          <Textarea
            label="Incident Reason & Notes"
            placeholder="FIR / Case Number, suspect description, police jurisdiction..."
            value={newReason}
            onChange={(e) => setNewReason(e.target.value)}
            rows={3}
            required
          />

          <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => setIsAddOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="submit"
              disabled={submitting}
            >
              {submitting ? 'Registering...' : 'Register Target'}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
