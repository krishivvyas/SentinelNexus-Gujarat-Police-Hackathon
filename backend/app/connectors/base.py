"""Connector contract for the federation layer.

Every CCTV source -- RTSP, HLS, ONVIF, a vendor VMS API, or a future protocol --
is wrapped by a connector that converts it into the SAME internal representation.
Nothing above this layer knows what protocol a camera speaks.

    Different CCTV/VMS  ->  Connector  ->  CameraDescriptor  ->  Sentinel Nexus

This is what lets departments keep their existing infrastructure: Sentinel Nexus
adds an interoperability layer instead of replacing the systems underneath it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator


@dataclass
class CameraDescriptor:
    """The common internal representation every connector must produce."""

    camera_id: str
    stream_url: str
    protocol: str = "RTSP"
    name: str = ""
    department: str = "UNASSIGNED"
    district: str = ""
    location_name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    vendor: str = ""
    codec: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    hls_url: str | None = None
    status: str = "UNKNOWN"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeResult:
    """Outcome of interrogating a single camera."""

    camera_id: str
    reachable: bool
    codec: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    open_latency_s: float | None = None
    frames_read: int = 0
    note: str = ""
    sample_frame: Any = None          # numpy array, or None
    status: str = "UNKNOWN"


class BaseConnector(abc.ABC):
    """Abstract adapter. Subclass per protocol/vendor."""

    #: short identifier used in the registry, e.g. "RTSP", "HLS", "ONVIF"
    protocol: str = "UNKNOWN"

    @abc.abstractmethod
    def discover(self) -> list[CameraDescriptor]:
        """Enumerate cameras this connector can reach."""

    @abc.abstractmethod
    def probe(self, descriptor: CameraDescriptor, *, grab_frames: int = 0) -> ProbeResult:
        """Interrogate one camera for codec/resolution/health without committing to a session."""

    @abc.abstractmethod
    def frames(self, descriptor: CameraDescriptor) -> Iterator[tuple[Any, float]]:
        """Yield ``(frame, pts_ms)`` pairs. Must release resources on exit."""

    def supports(self, descriptor: CameraDescriptor) -> bool:
        return descriptor.protocol.upper() == self.protocol.upper()


class ConnectorRegistry:
    """Routes a camera to the connector that can speak its protocol."""

    def __init__(self) -> None:
        self._connectors: list[BaseConnector] = []

    def register(self, connector: BaseConnector) -> None:
        self._connectors.append(connector)

    def for_camera(self, descriptor: CameraDescriptor) -> BaseConnector:
        for c in self._connectors:
            if c.supports(descriptor):
                return c
        raise LookupError(f"No connector registered for protocol {descriptor.protocol!r}")

    def all(self) -> list[BaseConnector]:
        return list(self._connectors)
