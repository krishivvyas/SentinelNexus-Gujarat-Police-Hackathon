"""RTSP connector -- the working path against the Sentinel government feed.

Verified 2026-09-02: rtsp://103.250.160.189:8554/stream/camNN answers unauthenticated
for cam01..cam30. Mixed H.264/H.265, mixed 1920x1080/1280x720, mixed 20/25/30 fps,
and stream-open latency measured between 6 s and 91 s -- so discovery uses a cheap
RTSP DESCRIBE rather than opening a decoder session for every camera.
"""
from __future__ import annotations

import os
import socket
import time
from typing import Any, Iterator

# FFmpeg options must be set before cv2 opens any capture.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;10000000|probesize;500000|analyzeduration;1000000|max_delay;500000",
)

import cv2  # noqa: E402

from ..config import settings  # noqa: E402
from .base import BaseConnector, CameraDescriptor, ProbeResult  # noqa: E402

_CODEC_MAP = {"H264": "H.264", "H265": "H.265", "HEVC": "H.265", "MP4V-ES": "MPEG-4"}


class RTSPConnector(BaseConnector):
    protocol = "RTSP"

    def __init__(self, host: str | None = None, port: int | None = None,
                 path_template: str | None = None, count: int | None = None) -> None:
        self.host = host or settings.sentinel_rtsp_host
        self.port = port or settings.sentinel_rtsp_port
        self.path_template = path_template or settings.sentinel_rtsp_path
        self.count = count or settings.sentinel_camera_count

    # ------------------------------------------------------------------ discovery

    def _url(self, n: int) -> str:
        return f"rtsp://{self.host}:{self.port}{self.path_template.format(n=n)}"

    def _describe(self, url: str, timeout: float = 5.0) -> tuple[bool, str]:
        """Raw RTSP DESCRIBE. Cheap -- no decoder session, no multi-second open."""
        sock = socket.socket()
        sock.settimeout(timeout)
        try:
            sock.connect((self.host, self.port))
            req = (
                f"DESCRIBE {url} RTSP/1.0\r\n"
                "CSeq: 1\r\n"
                "Accept: application/sdp\r\n"
                "User-Agent: SentinelNexus/1.0\r\n\r\n"
            )
            sock.sendall(req.encode())
            data = sock.recv(8192).decode("utf-8", "ignore")
        except Exception:
            return False, ""
        finally:
            sock.close()

        if "200 OK" not in data.split("\r\n", 1)[0]:
            return False, ""

        codec = ""
        for line in data.split("\r\n"):
            if line.lower().startswith("a=rtpmap"):
                # e.g. "a=rtpmap:96 H265/90000"
                part = line.split(" ", 1)[-1].split("/")[0].strip().upper()
                codec = _CODEC_MAP.get(part, part)
                break
        return True, codec

    def discover(self) -> list[CameraDescriptor]:
        found: list[CameraDescriptor] = []
        for n in range(1, self.count + 1):
            url = self._url(n)
            ok, codec = self._describe(url)
            if not ok:
                continue
            cam_id = f"CAM-{n:02d}"
            found.append(
                CameraDescriptor(
                    camera_id=cam_id,
                    stream_url=url,
                    protocol="RTSP",
                    name=cam_id,
                    department="SENTINEL-GOV",
                    codec=codec,
                    hls_url=f"{settings.sentinel_hls_base}/cam{n:02d}/index.m3u8",
                    status="ONLINE",
                    extra={"source_index": n},
                )
            )
        return found

    # --------------------------------------------------------------------- probe

    def probe(self, descriptor: CameraDescriptor, *, grab_frames: int = 0) -> ProbeResult:
        reachable, codec = self._describe(descriptor.stream_url)
        if not reachable:
            return ProbeResult(descriptor.camera_id, False, note="DESCRIBE failed",
                               status="OFFLINE")

        result = ProbeResult(descriptor.camera_id, True, codec=codec or descriptor.codec,
                             status="ONLINE")
        if grab_frames <= 0:
            return result

        started = time.time()
        cap = cv2.VideoCapture(descriptor.stream_url, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                result.status = "OFFLINE"
                result.note = "decoder failed to open"
                result.open_latency_s = round(time.time() - started, 1)
                return result

            result.open_latency_s = round(time.time() - started, 1)
            last = None
            for _ in range(grab_frames):
                ok, frame = cap.read()
                if ok and frame is not None:
                    last = frame
                    result.frames_read += 1

            if last is None:
                # Opened but produced nothing usable -- cam16 behaved this way.
                result.status = "DEGRADED"
                result.note = f"opened in {result.open_latency_s}s but yielded no frame"
                return result

            result.sample_frame = last
            result.height, result.width = last.shape[:2]
            fps = cap.get(cv2.CAP_PROP_FPS)
            result.fps = round(fps, 1) if fps and fps > 0 else None
        finally:
            cap.release()

        return result

    # -------------------------------------------------------------------- frames

    def frames(self, descriptor: CameraDescriptor) -> Iterator[tuple[Any, float]]:
        """Yield ``(frame, pts_ms)``.

        Timing uses CAP_PROP_POS_MSEC (the decoder presentation timestamp), never
        ``frame_number / reported_fps`` -- reported fps is unreliable across this
        fleet and frame intervals are variable.
        """
        cap = cv2.VideoCapture(descriptor.stream_url, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    return          # caller owns reconnect/backoff policy
                yield frame, cap.get(cv2.CAP_PROP_POS_MSEC)
        finally:
            cap.release()
