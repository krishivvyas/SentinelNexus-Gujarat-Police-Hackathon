"""REST API for the command centre."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, EVIDENCE_DIR
from ..db import get_db
from ..models import Camera, LocationAccuracy, Role, Sighting, User
from ..services import alerts as alert_service
from ..services import ingest as ingest_service
from ..services import registry, reports, search, security
from ..services import watchlist as wl

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- schemas

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    full_name: str


class CameraOut(BaseModel):
    camera_id: str
    name: str
    department: str
    district: str
    location_name: str
    latitude: float | None
    longitude: float | None
    location_accuracy: str
    location_source: str
    protocol: str
    codec: str
    width: int | None
    height: int | None
    fps: float | None
    status: str
    plate_score: float
    triage_note: str
    ai_enabled: bool
    time_cluster: int | None
    overlay_ts: datetime | None
    hls_url: str | None

    @classmethod
    def of(cls, c: Camera) -> "CameraOut":
        return cls(
            camera_id=c.camera_id, name=c.name, department=c.department,
            district=c.district, location_name=c.location_name,
            latitude=c.latitude, longitude=c.longitude,
            location_accuracy=c.location_accuracy.value if c.location_accuracy else "UNKNOWN",
            location_source=c.location_source, protocol=c.protocol, codec=c.codec,
            width=c.width, height=c.height, fps=c.fps,
            status=c.status.value if c.status else "UNKNOWN",
            plate_score=c.plate_score, triage_note=c.triage_note,
            ai_enabled=bool(c.ai_enabled), time_cluster=c.time_cluster,
            overlay_ts=c.overlay_ts, hls_url=c.hls_url,
        )


class CameraLocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: str = "VERIFIED"
    note: str = ""


class WatchlistIn(BaseModel):
    plate: str
    category: str = "OTHER"
    severity: str = "MEDIUM"
    vehicle_type: str | None = None
    vehicle_color: str | None = None
    owner_name: str | None = None
    notes: str = ""


# ------------------------------------------------------------------------ auth

@router.post("/auth/login", response_model=LoginResponse)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    user = security.authenticate(db, form.username, form.password)
    if user is None:
        security.record_audit(db, username=form.username, action="LOGIN_FAILED",
                              request=request)
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    security.record_audit(db, username=user.username, action="LOGIN", request=request)
    return LoginResponse(
        access_token=security.create_access_token(user.username, user.role.value),
        username=user.username, role=user.role.value, full_name=user.full_name,
    )


@router.get("/auth/me")
def me(user: User = Depends(security.get_current_user)):
    return {"username": user.username, "role": user.role.value,
            "full_name": user.full_name}


# --------------------------------------------------------------------- cameras

@router.get("/cameras", response_model=list[CameraOut])
def list_cameras(department: str | None = None, status: str | None = None,
                 district: str | None = None, ai_enabled: bool | None = None,
                 q: str | None = None, db: Session = Depends(get_db),
                 _: User = Depends(security.require_any)):
    cams = registry.list_cameras(db, department=department, status=status,
                                 district=district, ai_enabled=ai_enabled, search=q)
    return [CameraOut.of(c) for c in cams]


@router.get("/cameras/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: str, db: Session = Depends(get_db),
               _: User = Depends(security.require_any)):
    camera = registry.get_camera(db, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return CameraOut.of(camera)


@router.put("/cameras/{camera_id}/location", response_model=CameraOut)
def set_camera_location(camera_id: str, body: CameraLocationIn, request: Request,
                        db: Session = Depends(get_db),
                        user: User = Depends(security.require_operator)):
    """Place a camera by hand. Every change is written to the metadata audit."""
    camera = registry.get_camera(db, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    try:
        accuracy = LocationAccuracy(body.accuracy)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid accuracy") from None

    registry.upsert_camera(db, {
        "camera_id": camera_id,
        "latitude": body.latitude,
        "longitude": body.longitude,
        "location_accuracy": accuracy,
        "location_source": body.note or f"set manually by {user.username}",
    }, actor=user.username)
    db.commit()
    security.record_audit(db, username=user.username, action="CAMERA_LOCATION_SET",
                          entity="camera", entity_id=camera_id,
                          detail=f"{body.latitude},{body.longitude}", request=request)
    return CameraOut.of(registry.get_camera(db, camera_id))


@router.get("/cameras/{camera_id}/thumbnail")
def camera_thumbnail(camera_id: str, db: Session = Depends(get_db)):
    camera = registry.get_camera(db, camera_id)
    if camera is None or not camera.thumbnail_path:
        raise HTTPException(status_code=404, detail="No thumbnail")
    path = DATA_DIR / camera.thumbnail_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="No thumbnail")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/cameras/{camera_id}/live")
def camera_live(camera_id: str, detect: bool = True, token: str | None = None,
                db: Session = Depends(get_db)):
    """Live MJPEG preview with detection boxes drawn on.

    Authenticated by query parameter rather than header: this endpoint is
    consumed by an ``<img src=...>`` tag, which cannot send an Authorization
    header. The token is the same JWT used everywhere else.
    """
    from jose import JWTError, jwt

    from ..config import settings as cfg
    from ..services import live as live_service

    if not token:
        raise HTTPException(status_code=401, detail="token query parameter required")
    try:
        jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid token") from None

    camera = registry.get_camera(db, camera_id)
    if camera is None or not camera.stream_url:
        raise HTTPException(status_code=404, detail="Camera not found")

    return StreamingResponse(
        live_service.stream_camera(
            camera.stream_url,
            camera_id=camera.camera_id,
            detect=detect,
            fallback_url=camera.hls_url,
        ),
        media_type=live_service.media_type(),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/cameras/geo/features")
def camera_geojson(db: Session = Depends(get_db),
                   _: User = Depends(security.require_any)):
    """GeoJSON for the map. Cameras without a trustworthy position are omitted."""
    features = []
    for c in registry.list_cameras(db):
        if c.latitude is None or c.longitude is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c.longitude, c.latitude]},
            "properties": {
                "camera_id": c.camera_id,
                "name": c.name,
                "location": c.location_name,
                "status": c.status.value if c.status else "UNKNOWN",
                "accuracy": c.location_accuracy.value if c.location_accuracy else "UNKNOWN",
                "plate_score": c.plate_score,
                "ai_enabled": bool(c.ai_enabled),
                "time_cluster": c.time_cluster,
            },
        })
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------- ingest

@router.post("/ingest/start")
def start_ingest(camera_ids: list[str] | None = None, ai: bool = False,
                 user: User = Depends(security.require_operator)):
    if ai or not camera_ids:
        started = ingest_service.manager.start_ai_cameras()
    else:
        started = [c for c in camera_ids if ingest_service.manager.start_camera(c)]
    return {"started": started}


@router.post("/ingest/stop")
def stop_ingest(camera_ids: list[str] | None = None,
                user: User = Depends(security.require_operator)):
    if not camera_ids:
        ingest_service.manager.stop_all()
        return {"stopped": "all"}
    return {"stopped": [c for c in camera_ids
                        if ingest_service.manager.stop_camera(c)]}


@router.get("/ingest/status")
def ingest_status(_: User = Depends(security.require_any)):
    return {"cameras": ingest_service.manager.status()}


# -------------------------------------------------------------------- sightings

@router.get("/sightings/recent")
def recent(limit: int = Query(50, le=500), with_plate_only: bool = False,
           db: Session = Depends(get_db), _: User = Depends(security.require_any)):
    return [p.to_dict() for p in
            search.recent_sightings(db, limit=limit, with_plate_only=with_plate_only)]


@router.get("/search")
def vehicle_search(q: str | None = None, plate: str | None = None,
                   vehicle_type: str | None = None, vehicle_color: str | None = None,
                   camera_id: str | None = None, direction: str | None = None,
                   db: Session = Depends(get_db),
                   _: User = Depends(security.require_any)):
    """Investigate a vehicle by plate, by description, or by free text."""
    if plate:
        return search.search_by_plate(db, plate).to_dict()
    if vehicle_type or vehicle_color or camera_id or direction:
        return search.search_by_attributes(
            db, vehicle_type=vehicle_type, vehicle_color=vehicle_color,
            camera_id=camera_id, direction=direction).to_dict()
    if q:
        return search.search_free_text(db, q).to_dict()
    raise HTTPException(status_code=400, detail="Provide q, plate, or attributes")


@router.get("/evidence/{filename}")
def evidence(filename: str):
    path = (EVIDENCE_DIR / filename).resolve()
    # Prevent path traversal out of the evidence directory.
    if not str(path).startswith(str(EVIDENCE_DIR.resolve())) or not path.exists():
        raise HTTPException(status_code=404, detail="Evidence not found")
    return FileResponse(path, media_type="image/jpeg")


# -------------------------------------------------------------------- watchlist

@router.get("/watchlist")
def get_watchlist(active_only: bool = False, db: Session = Depends(get_db),
                  _: User = Depends(security.require_any)):
    return [{
        "id": e.id, "plate": e.plate, "category": e.category,
        "severity": e.severity.value, "active": e.active,
        "vehicle_type": e.vehicle_type, "vehicle_color": e.vehicle_color,
        "owner_name": e.owner_name, "notes": e.notes,
        "created_at": e.created_at,
    } for e in wl.list_entries(db, active_only=active_only)]


@router.post("/watchlist")
def add_watchlist(body: WatchlistIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(security.require_operator)):
    entry = wl.add_entry(db, plate=body.plate, category=body.category,
                         severity=body.severity, vehicle_type=body.vehicle_type,
                         vehicle_color=body.vehicle_color, owner_name=body.owner_name,
                         notes=body.notes, added_by=user.username)
    security.record_audit(db, username=user.username, action="WATCHLIST_ADD",
                          entity="watchlist", entity_id=entry.plate, request=request)
    return {"id": entry.id, "plate": entry.plate, "severity": entry.severity.value}


@router.delete("/watchlist/{entry_id}")
def delete_watchlist(entry_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(security.require_operator)):
    if not wl.remove_entry(db, entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    security.record_audit(db, username=user.username, action="WATCHLIST_REMOVE",
                          entity="watchlist", entity_id=str(entry_id), request=request)
    return {"deleted": entry_id}


# ----------------------------------------------------------------------- alerts

@router.get("/alerts")
def get_alerts(status: str | None = None, severity: str | None = None,
               limit: int = Query(100, le=500), db: Session = Depends(get_db),
               _: User = Depends(security.require_any)):
    return [{
        "id": a.id, "plate": a.plate, "camera_id": a.camera_id,
        "category": a.category, "severity": a.severity.value,
        "status": a.status.value, "message": a.message,
        "match_confidence": a.match_confidence, "created_at": a.created_at,
        "acknowledged_by": a.acknowledged_by,
        "evidence": a.sighting.evidence_path if a.sighting else None,
        "latitude": a.sighting.latitude if a.sighting else None,
        "longitude": a.sighting.longitude if a.sighting else None,
        "location": a.sighting.location_name if a.sighting else "",
        "event_ts": a.sighting.event_ts if a.sighting else None,
    } for a in alert_service.list_alerts(db, status=status, severity=severity,
                                         limit=limit)]


@router.post("/alerts/{alert_id}/acknowledge")
def ack_alert(alert_id: int, db: Session = Depends(get_db),
              user: User = Depends(security.require_operator)):
    alert = alert_service.acknowledge(db, alert_id, user.username)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"id": alert.id, "status": alert.status.value}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db),
                  user: User = Depends(security.require_operator)):
    alert = alert_service.resolve(db, alert_id, user.username)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"id": alert.id, "status": alert.status.value}


@router.websocket("/ws/alerts")
async def alert_stream(websocket: WebSocket):
    """Live alert feed. Auth is by token query parameter (WebSocket has no headers)."""
    await websocket.accept()
    queue = alert_service.broadcaster.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "connected"}))
        while True:
            message = await queue.get()
            await websocket.send_text(message)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        alert_service.broadcaster.unsubscribe(queue)


# ---------------------------------------------------------------- stats/reports

@router.get("/stats")
def stats(db: Session = Depends(get_db), _: User = Depends(security.require_any)):
    cameras = registry.registry_stats(db)
    return {
        "cameras": cameras,
        "alerts": alert_service.alert_stats(db),
        "detections": reports.summary(db),
        "ingest": ingest_service.manager.status(),
    }


@router.get("/stats/timeline")
def timeline(db: Session = Depends(get_db), _: User = Depends(security.require_any)):
    """Sightings per camera, for the dashboard."""
    rows = db.execute(
        select(Sighting.camera_id, func.count())
        .group_by(Sighting.camera_id).order_by(func.count().desc())
    ).all()
    types = db.execute(
        select(Sighting.vehicle_type, func.count())
        .group_by(Sighting.vehicle_type).order_by(func.count().desc())
    ).all()
    return {
        "by_camera": [{"camera_id": c, "count": n} for c, n in rows],
        "by_type": [{"type": t or "unknown", "count": n} for t, n in types],
    }


@router.get("/reports/detections.csv")
def detections_csv(plate: str | None = None, camera_id: str | None = None,
                   with_plate_only: bool = False, db: Session = Depends(get_db),
                   _: User = Depends(security.require_any)):
    body = reports.detection_csv(db, plate=plate, camera_id=camera_id,
                                 with_plate_only=with_plate_only)
    return Response(content=body, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=sentinel_detections.csv"})


@router.get("/reports/trace.pdf")
def trace_pdf(plate: str, db: Session = Depends(get_db),
              _: User = Depends(security.require_any)):
    trace = search.search_by_plate(db, plate)
    body = reports.trace_pdf(trace, title=f"Vehicle Movement Report — {trace.query}")
    return Response(content=body, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=trace_{trace.query}.pdf"})


@router.get("/audit")
def audit(limit: int = Query(200, le=1000), db: Session = Depends(get_db),
          _: User = Depends(security.require_admin)):
    return [{"ts": a.ts, "username": a.username, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id, "detail": a.detail}
            for a in security.list_audit(db, limit=limit)]
