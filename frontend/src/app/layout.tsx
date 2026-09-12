'use client';

import React, { useState, useEffect } from 'react';
import './globals.css';
import { BlueprintBackground } from '@/components/layout/BlueprintBackground';
import { Header } from '@/components/layout/Header';
import { CommandPalette } from '@/components/CommandPalette';
import { AlertToast } from '@/components/alerts/AlertToast';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [commandOpen, setCommandOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setCommandOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <html lang="en">
      <head>
        <title>Sentinel Nexus — Gujarat Police Surveillance Grid</title>
        <meta
          name="description"
          content="Unified CCTV Interoperability, ANPR Intelligence and Command Centre for Gujarat Police."
        />
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
          crossOrigin=""
        />
      </head>
      <body className="min-h-screen bg-[#FAFAFA] text-slate-900 flex flex-col relative antialiased selection:bg-[#2563EB]/15 selection:text-[#2563EB]">
        <BlueprintBackground />
        <Header onOpenCommandPalette={() => setCommandOpen(true)} />
        <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          {children}
        </main>
        <CommandPalette
          isOpen={commandOpen}
          onClose={() => setCommandOpen(false)}
        />
        <AlertToast />
      </body>
    </html>
  );
}
