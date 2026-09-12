'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Shield, Lock, User, KeyRound, ArrowRight } from 'lucide-react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('operator');
  const [password, setPassword] = useState('operator123');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setLoading(true);
      setError('');
      await api.login(username, password);
      router.push('/');
    } catch (e: any) {
      setError('Invalid operator credentials. Access denied.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Login Bento Card */}
        <div className="bg-white rounded-2xl border border-slate-200/90 p-8 shadow-modal-pop">
          {/* Header & Logo */}
          <div className="flex flex-col items-center text-center mb-8">
            <div className="w-12 h-12 rounded-xl bg-[#2563EB] flex items-center justify-center text-white shadow-tactile-btn mb-4">
              <Shield className="w-6 h-6" />
            </div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              SENTINEL NEXUS
            </h1>
            <span className="text-xs font-mono font-semibold text-blue-700 bg-blue-50 px-2.5 py-0.5 rounded-full border border-blue-200 mt-1">
              GUJARAT POLICE SURVEILLANCE GRID
            </span>
            <p className="text-xs text-slate-500 mt-2 font-mono">
              Authorized Command Centre Access Gate
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleLogin} className="space-y-4">
            {error && (
              <div className="p-3 rounded-lg bg-rose-50 border border-rose-200 text-xs font-mono font-semibold text-rose-700">
                {error}
              </div>
            )}

            <Input
              label="Operator ID / Username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              icon={<User className="w-4 h-4" />}
              required
            />

            <Input
              label="Security Key / Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              icon={<KeyRound className="w-4 h-4" />}
              required
            />

            <Button
              variant="primary"
              size="lg"
              type="submit"
              className="w-full mt-2"
              disabled={loading}
              icon={<ArrowRight className="w-4 h-4" />}
            >
              {loading ? 'Authenticating...' : 'Authenticate & Enter'}
            </Button>
          </form>

          {/* Quick Demo Credentials */}
          <div className="mt-8 pt-6 border-t border-slate-100">
            <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block text-center mb-2">
              Default Grid Credentials
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setUsername('operator');
                  setPassword('operator123');
                }}
                className="flex-1 p-2 rounded-lg bg-slate-50 border border-slate-200 text-[11px] font-mono text-slate-700 hover:bg-slate-100 transition-colors text-center cursor-pointer"
              >
                Operator / operator123
              </button>
              <button
                type="button"
                onClick={() => {
                  setUsername('admin');
                  setPassword('admin123');
                }}
                className="flex-1 p-2 rounded-lg bg-slate-50 border border-slate-200 text-[11px] font-mono text-slate-700 hover:bg-slate-100 transition-colors text-center cursor-pointer"
              >
                Admin / admin123
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
