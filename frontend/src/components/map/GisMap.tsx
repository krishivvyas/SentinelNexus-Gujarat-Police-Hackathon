'use client';

import React, { useEffect, useRef } from 'react';
import type { Camera } from '@/lib/api';

interface GisMapProps {
  cameras: Camera[];
  selectedCamera: Camera | null;
  onSelectCamera: (cam: Camera) => void;
  onPlaceLocation?: (lat: number, lng: number) => void;
  isPlacing?: boolean;
}

export default function GisMap({
  cameras,
  selectedCamera,
  onSelectCamera,
  onPlaceLocation,
  isPlacing = false,
}: GisMapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const markersRef = useRef<{ [id: string]: any }>({});

  useEffect(() => {
    if (typeof window === 'undefined' || !mapContainerRef.current) return;

    // Dynamically import leaflet on client
    import('leaflet').then((L) => {
      if (mapInstanceRef.current) return;

      // Default Gujarat / Ahmedabad coordinates
      const defaultCenter: [number, number] = [23.0225, 72.5714];

      const map = L.map(mapContainerRef.current!, {
        center: defaultCenter,
        zoom: 12,
        zoomControl: false,
      });

      // CartoDB Positron tiles for clean editorial map look
      L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
        maxZoom: 19,
      }).addTo(map);

      L.control.zoom({ position: 'bottomright' }).addTo(map);

      mapInstanceRef.current = map;

      map.on('click', (e: any) => {
        if (isPlacing && onPlaceLocation) {
          onPlaceLocation(e.latlng.lat, e.latlng.lng);
        }
      });
    });

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  // Update Markers
  useEffect(() => {
    if (!mapInstanceRef.current) return;

    import('leaflet').then((L) => {
      const map = mapInstanceRef.current;

      // Clear existing markers
      Object.values(markersRef.current).forEach((m: any) => m.remove());
      markersRef.current = {};

      const placedCameras = cameras.filter((c) => c.latitude && c.longitude);

      placedCameras.forEach((cam) => {
        const isSelected = selectedCamera?.camera_id === cam.camera_id;
        const isOnline = cam.status === 'ONLINE';
        const isDegraded = cam.status === 'DEGRADED';

        const color = isOnline ? '#16A34A' : isDegraded ? '#D97706' : '#DC2626';

        const customIcon = L.divIcon({
          className: 'custom-map-pin',
          html: `
            <div style="
              width: ${isSelected ? '28px' : '20px'};
              height: ${isSelected ? '28px' : '20px'};
              border-radius: 50%;
              background: ${color};
              border: 3px solid #ffffff;
              box-shadow: 0 2px 8px rgba(0,0,0,0.25);
              display: flex;
              align-items: center;
              justify-content: center;
              transition: all 0.2s;
              cursor: pointer;
            ">
              ${cam.ai_enabled ? '<span style="width:6px; height:6px; border-radius:50%; background:#ffffff;"></span>' : ''}
            </div>
          `,
          iconSize: [isSelected ? 28 : 20, isSelected ? 28 : 20],
          iconAnchor: [isSelected ? 14 : 10, isSelected ? 14 : 10],
        });

        const marker = L.marker([cam.latitude!, cam.longitude!], { icon: customIcon })
          .addTo(map)
          .on('click', () => onSelectCamera(cam));

        markersRef.current[cam.camera_id] = marker;
      });

      // Center map on selected camera if available
      if (selectedCamera?.latitude && selectedCamera?.longitude) {
        map.setView([selectedCamera.latitude, selectedCamera.longitude], 15, { animate: true });
      }
    });
  }, [cameras, selectedCamera, isPlacing]);

  return (
    <div className="relative w-full h-full rounded-xl overflow-hidden border border-slate-200 shadow-xs">
      <div ref={mapContainerRef} className="w-full h-full z-0" />
      {isPlacing && (
        <div className="absolute top-4 left-4 z-10 px-4 py-2 bg-blue-600 text-white rounded-lg font-mono text-xs font-bold shadow-tactile-btn animate-pulse">
          Click on the map to place camera position
        </div>
      )}
    </div>
  );
}
