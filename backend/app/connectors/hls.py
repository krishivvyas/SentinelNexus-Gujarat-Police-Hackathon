"""HLS connector -- the fallback transport when RTSP/8554 is blocked.

The Sentinel HLS endpoint sits behind ``/auth/login``, so a session cookie or
bearer token is required. RTSP stays the primary path where port 8554 is
reachable; this exists so a client on a restricted network still works.
"""
from __future__ import annotations

import os
from typing import Any, Iterator

import cv2

from .base import BaseConnector, CameraDescriptor, ProbeResult


class HLSConnector(BaseConnector):
    protocol = "HLS"

    def __init__(self, cookie: str | None = None, auth_token: str | None = None) -> None:
        self.cookie = cookie
        self.auth_token = auth_token

    def _apply_headers(self) -> None:
        """FFmpeg reads HLS auth headers from the capture options env var."""
        headers = []
        if self.cookie:
            headers.append(f"Cookie: {self.cookie}")
        if self.auth_token:
            headers.append(f"Authorization: Bearer {self.auth_token}")
        if headers:
            joined = "\\r\\n".join(headers) + "\\r\\n"
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"headers;{joined}|loglevel;error"

    def discover(self) -> list[CameraDescriptor]:
        # HLS has no enumeration of its own; the catalogue supplies the list.
        return []

    def probe(self, descriptor: CameraDescriptor, *, grab_frames: int = 0) -> ProbeResult:
        import httpx

        url = descriptor.hls_url or descriptor.stream_url
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            resp = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=False)
        except Exception as exc:
            return ProbeResult(descriptor.camera_id, False, note=str(exc), status="OFFLINE")

        if resp.status_code in (301, 302, 303, 307, 308):
            return ProbeResult(descriptor.camera_id, False,
                               note="redirected to login -- credentials required",
                               status="OFFLINE")
        if resp.status_code != 200:
            return ProbeResult(descriptor.camera_id, False,
                               note=f"HTTP {resp.status_code}", status="OFFLINE")

        result = ProbeResult(descriptor.camera_id, True, status="ONLINE")
        if grab_frames <= 0:
            return result

        self._apply_headers()
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                result.status = "OFFLINE"
                result.note = "decoder failed to open HLS"
                return result
            last = None
            for _ in range(grab_frames):
                ok, frame = cap.read()
                if ok and frame is not None:
                    last = frame
                    result.frames_read += 1
            if last is not None:
                result.sample_frame = last
                result.height, result.width = last.shape[:2]
        finally:
            cap.release()
        return result

    def frames(self, descriptor: CameraDescriptor) -> Iterator[tuple[Any, float]]:
        self._apply_headers()
        url = descriptor.hls_url or descriptor.stream_url
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    return
                yield frame, cap.get(cv2.CAP_PROP_POS_MSEC)
        finally:
            cap.release()
