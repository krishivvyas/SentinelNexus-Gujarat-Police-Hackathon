"""Alert engine: watchlist hits become operator alerts, with an audit trail.

Alerts are raised at the moment a sighting is written, deduplicated per
plate-and-camera over a short window so one vehicle lingering in view does not
flood the operator, and broadcast to connected dashboards over WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, AlertStatus, AuditLog, Severity, Sighting
from . import watchlist as wl

log = logging.getLogger("sentinel.alerts")

#: Suppress a repeat alert for the same plate on the same camera inside this window.
DEDUP_WINDOW = timedelta(minutes=2)


class AlertBroadcaster:
    """Fan-out to connected WebSocket clients.

    Kept deliberately small: a set of queues, one per client. Slow or dead
    clients are dropped rather than allowed to block the ingest thread.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, payload: dict) -> None:
        """Thread-safe publish -- ingest runs in worker threads, not the loop."""
        if self._loop is None or not self._subscribers:
            return
        message = json.dumps(payload, default=str)

        def _dispatch() -> None:
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    self._subscribers.discard(queue)

        try:
            self._loop.call_soon_threadsafe(_dispatch)
        except RuntimeError:
            pass


broadcaster = AlertBroadcaster()


def _recent_duplicate(db: Session, plate: str, camera_id: str) -> bool:
    cutoff = datetime.now(timezone.utc) - DEDUP_WINDOW
    existing = db.scalar(
        select(Alert).where(
            Alert.plate == plate,
            Alert.camera_id == camera_id,
            Alert.created_at >= cutoff,
        )
    )
    return existing is not None


def evaluate_sighting(db: Session, sighting: Sighting) -> Alert | None:
    """Check a new sighting against the watchlist and raise an alert if it hits."""
    if not sighting.plate:
        return None

    match = wl.match_plate(db, sighting.plate)
    if match is None:
        return None

    if _recent_duplicate(db, sighting.plate, sighting.camera_id):
        return None

    entry = match.entry
    message = (
        f"{entry.category.replace('_', ' ').title()} vehicle {sighting.plate} "
        f"detected on {sighting.camera_id}"
        f"{' at ' + sighting.location_name if sighting.location_name else ''}"
    )
    if not match.exact:
        message += f" ({match.note})"

    alert = Alert(
        sighting_fk=sighting.id,
        watchlist_fk=entry.id,
        plate=sighting.plate,
        camera_id=sighting.camera_id,
        category=entry.category,
        severity=entry.severity,
        status=AlertStatus.NEW,
        match_confidence=match.confidence,
        message=message,
    )
    db.add(alert)
    db.add(AuditLog(username="system", action="ALERT_RAISED", entity="alert",
                    entity_id=str(sighting.plate), detail=message))
    db.commit()
    db.refresh(alert)

    log.warning("ALERT %s | %s | %s", entry.severity.value, sighting.plate,
                sighting.camera_id)

    broadcaster.publish({
        "type": "alert",
        "id": alert.id,
        "plate": alert.plate,
        "camera_id": alert.camera_id,
        "location": sighting.location_name,
        "latitude": sighting.latitude,
        "longitude": sighting.longitude,
        "category": alert.category,
        "severity": alert.severity.value,
        "confidence": alert.match_confidence,
        "message": alert.message,
        "event_ts": sighting.event_ts,
        "evidence": sighting.evidence_path,
        "created_at": alert.created_at,
    })
    return alert


def list_alerts(db: Session, *, status: str | None = None,
                severity: str | None = None, limit: int = 100) -> list[Alert]:
    stmt = select(Alert)
    if status:
        stmt = stmt.where(Alert.status == AlertStatus(status))
    if severity:
        stmt = stmt.where(Alert.severity == Severity(severity))
    return list(db.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit)))


def acknowledge(db: Session, alert_id: int, username: str) -> Alert | None:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = username
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.add(AuditLog(username=username, action="ALERT_ACKNOWLEDGED", entity="alert",
                    entity_id=str(alert_id), detail=alert.message))
    db.commit()
    db.refresh(alert)
    return alert


def resolve(db: Session, alert_id: int, username: str) -> Alert | None:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    db.add(AuditLog(username=username, action="ALERT_RESOLVED", entity="alert",
                    entity_id=str(alert_id), detail=alert.message))
    db.commit()
    db.refresh(alert)
    return alert


def alert_stats(db: Session) -> dict[str, int]:
    alerts = list(db.scalars(select(Alert)))
    return {
        "total": len(alerts),
        "new": sum(a.status == AlertStatus.NEW for a in alerts),
        "acknowledged": sum(a.status == AlertStatus.ACKNOWLEDGED for a in alerts),
        "resolved": sum(a.status == AlertStatus.RESOLVED for a in alerts),
        "critical": sum(a.severity == Severity.CRITICAL for a in alerts),
        "high": sum(a.severity == Severity.HIGH for a in alerts),
    }
