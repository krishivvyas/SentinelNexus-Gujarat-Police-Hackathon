'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Shield,
  MapPin,
  LayoutGrid,
  ScanLine,
  Route,
  Bell,
  Camera,
  Activity,
  FileText,
  Search,
  Radio,
  User,
  LogOut,
} from 'lucide-react';
import { Kbd } from '@/components/ui/Kbd';
import { socket } from '@/lib/socket';

const NAV_ITEMS = [
  { href: '/', label: 'Map Grid', icon: MapPin },
  { href: '/wall_page', label: 'Video Wall', icon: LayoutGrid },
  { href: '/events_page', label: 'ANPR Stream', icon: ScanLine },
  { href: '/trace_page', label: 'Vehicle Trace', icon: Route },
  { href: '/watchlist_page', label: 'Watchlist', icon: Bell },
  { href: '/cameras_page', label: 'Registry', icon: Camera },
  { href: '/health_page', label: 'Health', icon: Activity },
  { href: '/audit_page', label: 'Audit', icon: FileText },
];

interface HeaderProps {
  onOpenCommandPalette: () => void;
}

export function Header({ onOpenCommandPalette }: HeaderProps) {
  const pathname = usePathname();
  const [isConnected, setIsConnected] = useState(false);
  const [username, setUsername] = useState('OPERATOR-01');
  const [role, setRole] = useState('OPERATOR');

  useEffect(() => {
    socket.connect();
    const unsub = socket.onStatusChange(setIsConnected);
    return () => {
      unsub();
    };
  }, []);

  return (
    <header className="sticky top-0 z-40 w-full bg-white/95 backdrop-blur-md border-b border-slate-200/90 shadow-xs">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14 gap-4">
          {/* Brand Logo & Name */}
          <Link href="/" className="flex items-center gap-2.5 flex-none group">
            <div className="w-8 h-8 rounded-lg bg-[#2563EB] flex items-center justify-center text-white shadow-tactile-btn group-hover:brightness-110 transition-all">
              <Shield className="w-4 h-4" />
            </div>
            <div className="flex flex-col">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-slate-900 text-sm tracking-tight">
                  SENTINEL NEXUS
                </span>
                <span className="px-1.5 py-0.2 rounded text-[10px] font-mono font-semibold bg-blue-50 text-blue-800 border border-blue-200">
                  GUJ-POLICE
                </span>
              </div>
              <span className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">
                Unified Surveillance Grid
              </span>
            </div>
          </Link>

          {/* Center Navigation Tabs */}
          <nav className="hidden md:flex items-center gap-1 p-1 rounded-xl bg-slate-100/80 border border-slate-200/80 shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.04)]">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all select-none ${
                    isActive
                      ? 'bg-white text-slate-900 shadow-sm border border-slate-200/80'
                      : 'text-slate-600 hover:text-slate-900 hover:bg-white/50 border border-transparent'
                  }`}
                >
                  <Icon className={`w-3.5 h-3.5 ${isActive ? 'text-[#2563EB]' : 'text-slate-400'}`} />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>

          {/* Right Action Utilities */}
          <div className="flex items-center gap-2.5 flex-none">
            {/* Quick Command Palette Button */}
            <button
              onClick={onOpenCommandPalette}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200/90 text-slate-600 text-xs shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.03)] hover:bg-slate-100 transition-all cursor-pointer"
            >
              <Search className="w-3.5 h-3.5 text-slate-400" />
              <span className="hidden sm:inline">Search / Jump</span>
              <Kbd>⌘K</Kbd>
            </button>

            {/* Live WebSocket Status Badge */}
            <div
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-medium border ${
                isConnected
                  ? 'bg-emerald-50 text-emerald-800 border-emerald-200/90'
                  : 'bg-amber-50 text-amber-800 border-amber-200/90'
              }`}
              title={isConnected ? 'Connected to live grid WebSocket' : 'Connecting to grid WebSocket...'}
            >
              <span className="relative flex h-2 w-2">
                {isConnected && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                )}
                <span
                  className={`relative inline-flex rounded-full h-2 w-2 ${
                    isConnected ? 'bg-emerald-500' : 'bg-amber-500'
                  }`}
                />
              </span>
              <span className="hidden sm:inline">
                {isConnected ? 'LIVE GRID' : 'CONNECTING'}
              </span>
            </div>

            {/* Operator Profile Badge */}
            <div className="flex items-center gap-2 pl-1 border-l border-slate-200">
              <div className="w-7 h-7 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-700 font-mono text-xs font-bold">
                <User className="w-3.5 h-3.5 text-slate-600" />
              </div>
            </div>
          </div>
        </div>

        {/* Mobile Navigation Row */}
        <div className="md:hidden flex items-center gap-1 py-2 overflow-x-auto border-t border-slate-100 no-scrollbar">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold whitespace-nowrap flex-none ${
                  isActive
                    ? 'bg-[#2563EB] text-white shadow-tactile-btn'
                    : 'text-slate-600 bg-slate-100'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      </div>
    </header>
  );
}
