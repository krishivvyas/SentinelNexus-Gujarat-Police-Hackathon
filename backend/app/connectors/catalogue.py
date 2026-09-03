"""Catalogue connector -- the camera list comes from cameras.json, never from code.

The catalogue is the contract. Camera identity, stream URLs, codecs and
resolutions are read from a JSON document, so onboarding a camera means editing
the catalogue rather than touching the application.

The document can come from:
  * a local file (``backend/data/cameras.json``), or
  * an authenticated HTTP endpoint (the Sentinel portal serves ``/cameras.json``
    behind ``/auth/login``; supply a session cookie or bearer token).

Both produce the same CameraDescriptor objects, so nothing downstream changes
when the real catalogue replaces the locally-built one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .base import BaseConnector, CameraDescriptor, ProbeResult

CATALOGUE_VERSION = 1


def _index_from_id(camera_id: str) -> int | None:
    """Numeric index from a catalogue id ("cam04" -> 4, "CAM-12" -> 12)."""
    digits = "".join(c for c in str(camera_id) if c.isdigit())
    return int(digits) if digits else None


class CatalogueConnector(BaseConnector):
    """Reads a camera catalogue and delegates streaming to a transport connector."""

    protocol = "CATALOGUE"

    def __init__(
        self,
        source: str | Path,
        *,
        auth_token: str | None = None,
        cookie: str | None = None,
        transports: dict[str, BaseConnector] | None = None,
    ) -> None:
        self.source = source
        self.auth_token = auth_token
        self.cookie = cookie
        #: protocol name -> connector that can actually pull frames
        self.transports = transports or {}

    # ----------------------------------------------------------------- loading

    def _load_document(self) -> dict[str, Any] | list[Any]:
        source = str(self.source)
        if source.startswith(("http://", "https://")):
            import httpx

            headers: dict[str, str] = {"User-Agent": "SentinelNexus/1.0"}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
            if self.cookie:
                headers["Cookie"] = self.cookie
            resp = httpx.get(source, headers=headers, timeout=20.0,
                             follow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                raise PermissionError(
                    f"catalogue at {source} redirected to "
                    f"{resp.headers.get('location', '?')} -- credentials required"
                )
            resp.raise_for_status()
            return resp.json()

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"catalogue not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # --------------------------------------------------------------- discovery

    def discover(self) -> list[CameraDescriptor]:
        doc = self._load_document()
        entries = doc if isinstance(doc, list) else doc.get("cameras", [])

        cameras: list[CameraDescriptor] = []
        for entry in entries:
            rtsp = entry.get("rtsp") or entry.get("stream_url") or ""
            hls = entry.get("hls") or entry.get("hls_url")
            if not rtsp and not hls:
                # The portal catalogue carries only id and name, so the stream
                # URLs come from the configured templates. The camera set still
                # comes from the catalogue -- only the URL shape is templated.
                index = _index_from_id(entry.get("camera_id") or entry.get("id", ""))
                if index is not None:
                    from ..config import settings

                    rtsp = settings.rtsp_url(index)
                    hls = f"{settings.sentinel_hls_base}/cam{index:02d}/index.m3u8"

            # Prefer RTSP; fall back to HLS when that is all the catalogue offers.
            protocol = "RTSP" if rtsp else ("HLS" if hls else "UNKNOWN")

            cameras.append(CameraDescriptor(
                camera_id=entry.get("camera_id") or entry.get("id", ""),
                stream_url=rtsp or hls or "",
                hls_url=hls,
                protocol=protocol,
                name=entry.get("name", ""),
                department=entry.get("department", "SENTINEL-GOV"),
                district=entry.get("district", ""),
                location_name=entry.get("location_name", entry.get("location", "")),
                latitude=entry.get("lat", entry.get("latitude")),
                longitude=entry.get("lon", entry.get("longitude")),
                vendor=entry.get("vendor", ""),
                codec=entry.get("codec", ""),
                width=entry.get("width"),
                height=entry.get("height"),
                fps=entry.get("fps"),
                status=entry.get("status", "UNKNOWN"),
                extra={k: v for k, v in entry.items() if k.startswith("_")
                       or k in ("plate_score", "time_cluster", "overlay_ts",
                                "location_accuracy", "location_source",
                                "triage_note", "source_index")},
            ))
        return cameras

    # -------------------------------------------------- delegated transport ops

    def _transport(self, descriptor: CameraDescriptor) -> BaseConnector:
        transport = self.transports.get(descriptor.protocol.upper())
        if transport is None:
            raise LookupError(
                f"no transport registered for {descriptor.protocol!r} "
                f"(camera {descriptor.camera_id})"
            )
        return transport

    def probe(self, descriptor: CameraDescriptor, *, grab_frames: int = 0) -> ProbeResult:
        return self._transport(descriptor).probe(descriptor, grab_frames=grab_frames)

    def frames(self, descriptor: CameraDescriptor) -> Iterator[tuple[Any, float]]:
        return self._transport(descriptor).frames(descriptor)

    def supports(self, descriptor: CameraDescriptor) -> bool:
        return descriptor.protocol.upper() in self.transports


def write_catalogue(path: Path, cameras: list[dict[str, Any]], *,
                    source: str = "rtsp-discovery") -> Path:
    """Write a catalogue document that the connector can read back."""
    doc = {
        "version": CATALOGUE_VERSION,
        "source": source,
        "cameras": cameras,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path
