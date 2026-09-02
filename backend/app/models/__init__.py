"""ORM models for Sentinel Nexus."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CameraStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"   # opens but corrupt/no usable frames (e.g. cam12 H.265 smear)
    UNKNOWN = "UNKNOWN"


class LocationAccuracy(str, enum.Enum):
    """How much a camera's coordinates can be trusted.

    Surfaced in the UI and in every exported report. An APPROXIMATE pin is a
    landmark-level estimate derived from the camera's overlay label, not a
    surveyed position, and must never be read as one.
    """
    VERIFIED = "VERIFIED"          # from the operator's own catalogue, or set by hand
    GEOCODED = "GEOCODED"          # resolved by geocoder and validated inside the city bbox
    APPROXIMATE = "APPROXIMATE"    # landmark-level estimate from the site name
    UNKNOWN = "UNKNOWN"            # no trustworthy position; not drawn on the map


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    ANALYST = "ANALYST"


class AlertStatus(str, enum.Enum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class Severity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Camera(Base):
    """A camera in the federated registry -- the single source of truth.

    Populated by connectors (RTSP probe, catalogue API), never hardcoded stream URLs.
    """
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")

    # Ownership / jurisdiction
    department: Mapped[str] = mapped_column(String(128), default="UNASSIGNED", index=True)
    district: Mapped[str] = mapped_column(String(128), default="", index=True)
    location_name: Mapped[str] = mapped_column(String(255), default="")

    # Geography. Plain lat/lon in SQLite; becomes a PostGIS POINT in production.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy: Mapped[LocationAccuracy] = mapped_column(
        Enum(LocationAccuracy), default=LocationAccuracy.UNKNOWN, index=True
    )
    location_source: Mapped[str] = mapped_column(String(255), default="")

    # Recording window this camera's replay covers, read from the burned-in overlay
    # clock. Cameras only correlate with others whose windows overlap.
    overlay_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_cluster: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Technical profile, discovered by the connector rather than assumed
    vendor: Mapped[str] = mapped_column(String(128), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="RTSP", index=True)
    codec: Mapped[str] = mapped_column(String(32), default="")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    stream_url: Mapped[str] = mapped_column(Text, default="")
    hls_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Health / observability
    status: Mapped[CameraStatus] = mapped_column(
        Enum(CameraStatus), default=CameraStatus.UNKNOWN, index=True
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_latency_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_note: Mapped[str] = mapped_column(Text, default="")

    # ANPR triage: how usable this camera is for plate reading (0-100).
    # Drives which cameras the CPU-bound AI workers prioritise.
    plate_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    triage_note: Mapped[str] = mapped_column(Text, default="")

    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sightings: Mapped[list[Sighting]] = relationship(back_populates="camera")
    history: Mapped[list[CameraMetadataHistory]] = relationship(back_populates="camera")


class CameraMetadataHistory(Base):
    """Append-only audit of camera metadata changes (registry requirement)."""
    __tablename__ = "camera_metadata_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_fk: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(128), default="system")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    camera: Mapped[Camera] = relationship(back_populates="history")


class Sighting(Base):
    """One vehicle observed on one camera at one instant.

    A sighting is recorded even when the plate is unreadable -- attributes alone
    still support cross-camera correlation. See implementation.md section 3.
    """
    __tablename__ = "sightings"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_fk: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)

    # Plate, when it could be read at all
    plate: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    plate_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_frames_voted: Mapped[int] = mapped_column(Integer, default=0)

    # Attributes -- always populated, the fallback correlation key
    vehicle_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    vehicle_color: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detection_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "x,y,w,h"

    # Time. event_ts comes from the burned-in overlay clock (authoritative);
    # pts_ms is the decoder presentation timestamp; ingested_at is server wall-clock.
    event_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_ts_source: Mapped[str] = mapped_column(String(16), default="overlay")
    pts_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    # Denormalised for fast map/report queries without a join
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_name: Mapped[str] = mapped_column(String(255), default="")

    evidence_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    plate_crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    camera: Mapped[Camera] = relationship(back_populates="sightings")
    alerts: Mapped[list[Alert]] = relationship(back_populates="sighting")


Index("ix_sightings_plate_time", Sighting.plate, Sighting.event_ts)
Index("ix_sightings_attr_time", Sighting.vehicle_type, Sighting.vehicle_color, Sighting.event_ts)


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)          # normalised
    plate_raw: Mapped[str] = mapped_column(String(64), default="")      # as entered
    category: Mapped[str] = mapped_column(String(64), default="OTHER", index=True)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.MEDIUM)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    vehicle_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(128), default="system")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (UniqueConstraint("plate", name="uq_watchlist_plate"),)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    sighting_fk: Mapped[int] = mapped_column(ForeignKey("sightings.id", ondelete="CASCADE"), index=True)
    watchlist_fk: Mapped[int | None] = mapped_column(
        ForeignKey("watchlist.id", ondelete="SET NULL"), nullable=True, index=True
    )

    plate: Mapped[str | None] = mapped_column(String(32), index=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.MEDIUM, index=True)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus), default=AlertStatus.NEW, index=True
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sighting: Mapped[Sighting] = relationship(back_populates="alerts")
    watchlist_entry: Mapped[WatchlistEntry | None] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.ANALYST)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), default="system", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
