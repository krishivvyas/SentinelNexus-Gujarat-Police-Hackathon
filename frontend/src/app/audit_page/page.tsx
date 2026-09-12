'use client';

import React, { useEffect, useState } from 'react';
import {
  FileText,
  Shield,
  Clock,
  User,
  Search,
  RefreshCw,
  Lock,
} from 'lucide-react';
import { api, type AuditLog } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';

export default function AuditPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  const fetchLogs = async () => {
    try {
      setLoading(true);
      const data = await api.getAuditLogs(100);
      setLogs(data);
    } catch (e) {
      console.error('Failed to load audit logs', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  const filteredLogs = logs.filter(
    (l) =>
      l.action.toLowerCase().includes(query.toLowerCase()) ||
      l.username.toLowerCase().includes(query.toLowerCase()) ||
      l.details.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-[#2563EB]" />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Tamper-Evident Audit Trail
            </h1>
            <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-blue-50 text-blue-800 border border-blue-200">
              COMPLIANCE LOG
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono mt-0.5">
            Immutable operator actions, camera repositioning, stream viewing & watchlist mutations
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <div className="w-56">
            <Input
              type="text"
              placeholder="Search audit trail..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              icon={<Search className="w-3.5 h-3.5 text-slate-400" />}
            />
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={fetchLogs}
            icon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />}
          >
            Sync
          </Button>
        </div>
      </div>

      {/* Audit Log Table */}
      <div className="bg-white rounded-xl border border-slate-200/90 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-600 uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4">Timestamp</th>
                <th className="py-3 px-4">Operator</th>
                <th className="py-3 px-4">Role</th>
                <th className="py-3 px-4">Action</th>
                <th className="py-3 px-4">Details</th>
                <th className="py-3 px-4">IP Origin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredLogs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-50/70 transition-colors">
                  <td className="py-3 px-4 whitespace-nowrap text-slate-500">
                    <div className="flex items-center gap-1.5">
                      <Clock className="w-3 h-3 text-slate-400" />
                      <span>{new Date(log.timestamp).toLocaleString()}</span>
                    </div>
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap font-bold text-slate-900">
                    {log.username}
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap">
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700 border border-slate-200">
                      {log.role}
                    </span>
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap font-semibold text-[#2563EB]">
                    {log.action}
                  </td>
                  <td className="py-3 px-4 text-slate-700 max-w-md truncate font-sans text-xs">
                    {log.details}
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap text-slate-500">
                    {log.ip_address || '127.0.0.1'}
                  </td>
                </tr>
              ))}

              {filteredLogs.length === 0 && !loading && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-400 font-mono">
                    No matching audit records found.
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
