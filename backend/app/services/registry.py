"""Camera registry -- the single source of truth for what cameras exist and where.

Cameras arrive here from connectors (RTSP discovery, catalogue API, bulk import,
manual entry). Stream URLs are never hardcoded anywhere above this layer.
Every metadata change is recorded in camera_metadata_history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.base import CameraDescriptor
from ..models import Camera, CameraMetadataHistory, CameraStatus, LocationAccuracy

_TRACKED_FIELDS = (
    "name", "department", "district", "location_name", "latitude", "longitude",
    "vendor", "protocol", "codec", "width", "height", "fps", "stream_url",
    "hls_url", "status", "plate_score", "triage_note", "ai_enabled",
    "location_accuracy", "location_source", "overlay_ts", "time_cluster",
)


def get_camera(db: Session, camera_id: str) -> Camera | None:
    return db.scalar(select(Camera).where(Camera.camera_id == camera_id))


def list_cameras(
    db: Session,
    *,
    department: str | None = None,
    district: str | None = None,
    status: str | None = None,
    protocol: str | None = None,
    ai_enabled: bool | None = None,
    search: str | None = None,
) -> list[Camera]:
    stmt = select(Camera)
    if department:
        stmt = stmt.where(Camera.department == department)
    if district:
        stmt = stmt.where(Camera.district == district)
    if status:
        stmt = stmt.where(Camera.status == CameraStatus(status))
    if protocol:
        stmt = stmt.where(Camera.protocol == protocol)
    if ai_enabled is not None:
        stmt = stmt.where(Camera.ai_enabled == ai_enabled)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            Camera.camera_id.like(like)
            | Camera.name.like(like)
            | Camera.location_name.like(like)
        )
    return list(db.scalars(stmt.order_by(Camera.camera_id)))


def _record_change(db: Session, camera: Camera, field: str,
                   old: Any, new: Any, actor: str) -> None:
    db.add(CameraMetadataHistory(
        camera_fk=camera.id,
        field=field,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        changed_by=actor,
    ))


def upsert_camera(db: Session, values: dict[str, Any], *, actor: str = "system") -> Camera:
    """Insert or update one camera, auditing every field that actually changed."""
    camera_id = values["camera_id"]
    camera = get_camera(db, camera_id)

    if camera is None:
        camera = Camera(camera_id=camera_id)
        db.add(camera)
        db.flush()
        _record_change(db, camera, "camera_id", None, camera_id, actor)

    for field in _TRACKED_FIELDS:
        if field not in values:
            continue
        new = values[field]
        if new is None:
            continue
        if field == "status" and isinstance(new, str):
            new = CameraStatus(new)
        if field == "location_accuracy" and isinstance(new, str):
            new = LocationAccuracy(new)
        old = getattr(camera, field)
        if old != new:
            _record_change(db, camera, field, old, new, actor)
            setattr(camera, field, new)

    for field in ("last_seen", "open_latency_s", "health_note", "thumbnail_path"):
        if values.get(field) is not None:
            setattr(camera, field, values[field])

    db.flush()
    return camera


def upsert_from_descriptor(db: Session, desc: CameraDescriptor, *,
                           actor: str = "connector") -> Camera:
    return upsert_camera(db, desc.to_dict(), actor=actor)


def ingest_survey(db: Session, rows: Iterable[dict], *,
                  ai_top_n: int = 8, actor: str = "survey") -> dict[str, int]:
    """Load survey output into the registry and flag the best cameras for AI.

    Only cameras that are ONLINE and actually scored are eligible for AI, so a
    dark or corrupt feed never consumes a worker slot.
    """
    rows = list(rows)
    eligible = sorted(
        [r for r in rows if r.get("status") == "ONLINE" and r.get("plate_score", 0) > 0],
        key=lambda r: r["plate_score"],
        reverse=True,
    )
    ai_ids = {r["camera_id"] for r in eligible[:ai_top_n]}

    now = datetime.now(timezone.utc)
    counts = {"created": 0, "updated": 0, "ai_enabled": 0}

    for row in rows:
        existed = get_camera(db, row["camera_id"]) is not None
        values = {
            "camera_id": row["camera_id"],
            "name": row.get("name") or row["camera_id"],
            "department": row.get("department", "SENTINEL-GOV"),
            "district": row.get("district", ""),
            "location_name": row.get("location_name", ""),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "location_accuracy": row.get("location_accuracy", "UNKNOWN"),
            "location_source": row.get("location_source", ""),
            "overlay_ts": row.get("overlay_ts"),
            "time_cluster": row.get("time_cluster"),
            "protocol": row.get("protocol", "RTSP"),
            "codec": row.get("codec", ""),
            "width": row.get("width"),
            "height": row.get("height"),
            "fps": row.get("fps"),
            "stream_url": row.get("stream_url", ""),
            "hls_url": row.get("hls_url"),
            "status": row.get("status", "UNKNOWN"),
            "plate_score": row.get("plate_score", 0.0),
            "triage_note": row.get("triage_note", ""),
            "ai_enabled": row["camera_id"] in ai_ids,
            "open_latency_s": row.get("open_latency_s"),
            "thumbnail_path": row.get("thumbnail_path"),
            "health_note": row.get("triage_note", ""),
            "last_seen": now if row.get("status") == "ONLINE" else None,
        }
        upsert_camera(db, values, actor=actor)
        counts["updated" if existed else "created"] += 1

    counts["ai_enabled"] = len(ai_ids)
    db.commit()
    return counts


def registry_stats(db: Session) -> dict[str, int]:
    cams = list(db.scalars(select(Camera)))
    return {
        "total": len(cams),
        "online": sum(c.status == CameraStatus.ONLINE for c in cams),
        "offline": sum(c.status == CameraStatus.OFFLINE for c in cams),
        "degraded": sum(c.status == CameraStatus.DEGRADED for c in cams),
        "ai_enabled": sum(bool(c.ai_enabled) for c in cams),
        "geolocated": sum(c.latitude is not None for c in cams),
        "geo_approximate": sum(
            c.location_accuracy == LocationAccuracy.APPROXIMATE for c in cams),
        "geo_unknown": sum(c.location_accuracy == LocationAccuracy.UNKNOWN for c in cams),
    }
