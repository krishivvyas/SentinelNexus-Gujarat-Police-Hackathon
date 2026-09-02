"""Vehicle investigation: cross-camera search and movement reconstruction.

Two ways to trace a vehicle:

  * **By plate** -- the mandatory foundation. Exact on the normalised plate, with
    a tolerant fallback for single-character OCR confusions.
  * **By attributes** -- type, colour and direction inside a time window. This is
    what carries the government feed, where plates are frequently unreadable.

Sightings are ordered by the burned-in overlay clock, never by ingest time: the
feeds are replayed recordings, so ingest order says nothing about what happened
when.

One hard constraint is enforced here. The cameras are independent recordings
spanning several dates, so a "route" may only be built from cameras whose
footage actually overlaps in time. Joining sightings across recording days would
invent a journey that never happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Camera, Sighting
from ..pipeline.ocr import normalise
from .watchlist import fuzzy_score


@dataclass
class RoutePoint:
    sighting_id: int
    camera_id: str
    location_name: str
    latitude: float | None
    longitude: float | None
    event_ts: datetime | None
    plate: str | None
    plate_confidence: float | None
    vehicle_type: str | None
    vehicle_color: str | None
    direction: str | None
    evidence_path: str | None
    time_cluster: int | None = None

    def to_dict(self) -> dict:
        return {
            "sighting_id": self.sighting_id,
            "camera_id": self.camera_id,
            "location": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timestamp": self.event_ts.isoformat() if self.event_ts else None,
            "plate": self.plate,
            "plate_confidence": self.plate_confidence,
            "vehicle_type": self.vehicle_type,
            "vehicle_color": self.vehicle_color,
            "direction": self.direction,
            "evidence": self.evidence_path,
            "time_cluster": self.time_cluster,
        }


@dataclass
class VehicleTrace:
    query: str
    match_type: str                       # "plate" | "attributes"
    points: list[RoutePoint] = field(default_factory=list)
    segments: list[list[RoutePoint]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def cameras(self) -> list[str]:
        seen: list[str] = []
        for p in self.points:
            if p.camera_id not in seen:
                seen.append(p.camera_id)
        return seen

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "match_type": self.match_type,
            "sighting_count": len(self.points),
            "camera_count": len(self.cameras),
            "cameras": self.cameras,
            "first_seen": self.points[0].event_ts.isoformat()
            if self.points and self.points[0].event_ts else None,
            "last_seen": self.points[-1].event_ts.isoformat()
            if self.points and self.points[-1].event_ts else None,
            "points": [p.to_dict() for p in self.points],
            "route_segments": [[p.to_dict() for p in seg] for seg in self.segments],
            "notes": self.notes,
        }


def _camera_clusters(db: Session) -> dict[str, int | None]:
    return {c.camera_id: c.time_cluster for c in db.scalars(select(Camera))}


def _to_point(s: Sighting, clusters: dict[str, int | None]) -> RoutePoint:
    return RoutePoint(
        sighting_id=s.id,
        camera_id=s.camera_id,
        location_name=s.location_name or s.camera_id,
        latitude=s.latitude,
        longitude=s.longitude,
        event_ts=s.event_ts,
        plate=s.plate,
        plate_confidence=s.plate_confidence,
        vehicle_type=s.vehicle_type,
        vehicle_color=s.vehicle_color,
        direction=s.direction,
        evidence_path=s.evidence_path,
        time_cluster=clusters.get(s.camera_id),
    )


def _build_segments(points: list[RoutePoint], *, max_gap: timedelta
                    ) -> list[list[RoutePoint]]:
    """Split a sighting list into journeys that could physically be one trip.

    A new segment starts whenever the recording cluster changes or the time gap
    is too large for the sightings to belong to the same journey.
    """
    segments: list[list[RoutePoint]] = []
    current: list[RoutePoint] = []

    for point in points:
        if not current:
            current = [point]
            continue
        previous = current[-1]
        same_cluster = previous.time_cluster == point.time_cluster
        gap_ok = (
            previous.event_ts is not None
            and point.event_ts is not None
            and (point.event_ts - previous.event_ts) <= max_gap
        )
        if same_cluster and gap_ok:
            current.append(point)
        else:
            segments.append(current)
            current = [point]
    if current:
        segments.append(current)
    return segments


def search_by_plate(db: Session, plate: str, *, allow_fuzzy: bool = True,
                    max_gap_minutes: int = 90) -> VehicleTrace:
    """Find every sighting of a registration number across the network."""
    normalised, _ = normalise(plate)
    trace = VehicleTrace(query=normalised, match_type="plate")
    clusters = _camera_clusters(db)

    rows = list(db.scalars(
        select(Sighting).where(Sighting.plate.isnot(None))
        .order_by(Sighting.event_ts.asc().nulls_last(), Sighting.id.asc())
    ))

    exact = [s for s in rows if s.plate == normalised]
    matched = exact
    if not exact and allow_fuzzy:
        near = [(s, fuzzy_score(s.plate or "", normalised)) for s in rows]
        near = [(s, sc) for s, sc in near if sc >= 0.6]
        if near:
            near.sort(key=lambda t: t[1], reverse=True)
            matched = [s for s, _ in near]
            trace.notes.append(
                f"No exact match for {normalised}. Showing {len(matched)} tolerant "
                "match(es) allowing for OCR character confusion."
            )

    if not matched:
        trace.notes.append(f"No sightings recorded for {normalised}.")
        return trace

    trace.points = [_to_point(s, clusters) for s in matched]
    trace.segments = _build_segments(trace.points,
                                     max_gap=timedelta(minutes=max_gap_minutes))
    _annotate_clusters(trace)
    return trace


def search_by_attributes(db: Session, *, vehicle_type: str | None = None,
                         vehicle_color: str | None = None,
                         camera_id: str | None = None,
                         since: datetime | None = None,
                         until: datetime | None = None,
                         direction: str | None = None,
                         limit: int = 500,
                         max_gap_minutes: int = 30) -> VehicleTrace:
    """Trace a vehicle by description when the plate could not be read."""
    stmt = select(Sighting)
    if vehicle_type:
        stmt = stmt.where(Sighting.vehicle_type == vehicle_type)
    if vehicle_color:
        stmt = stmt.where(Sighting.vehicle_color == vehicle_color)
    if camera_id:
        stmt = stmt.where(Sighting.camera_id == camera_id)
    if direction:
        stmt = stmt.where(Sighting.direction == direction)
    if since:
        stmt = stmt.where(Sighting.event_ts >= since)
    if until:
        stmt = stmt.where(Sighting.event_ts <= until)

    rows = list(db.scalars(
        stmt.order_by(Sighting.event_ts.asc().nulls_last(), Sighting.id.asc()).limit(limit)
    ))

    descriptor = " ".join(x for x in (vehicle_color, vehicle_type) if x) or "any vehicle"
    trace = VehicleTrace(query=descriptor, match_type="attributes")
    if not rows:
        trace.notes.append("No sightings match that description in the given window.")
        return trace

    clusters = _camera_clusters(db)
    trace.points = [_to_point(s, clusters) for s in rows]
    trace.segments = _build_segments(trace.points,
                                     max_gap=timedelta(minutes=max_gap_minutes))
    trace.notes.append(
        "Attribute match: these sightings share a description, which is weaker "
        "evidence than a plate match and may include more than one vehicle."
    )
    _annotate_clusters(trace)
    return trace


def _annotate_clusters(trace: VehicleTrace) -> None:
    """Warn when sightings span recordings that cannot be one journey."""
    found = {p.time_cluster for p in trace.points}
    if len(found) > 1:
        trace.notes.append(
            f"Sightings span {len(found)} separate recording windows, so they "
            "cannot all be one journey. The route is split into segments."
        )


def recent_sightings(db: Session, *, limit: int = 50,
                     with_plate_only: bool = False) -> list[RoutePoint]:
    stmt = select(Sighting)
    if with_plate_only:
        stmt = stmt.where(Sighting.plate.isnot(None))
    rows = list(db.scalars(stmt.order_by(Sighting.id.desc()).limit(limit)))
    clusters = _camera_clusters(db)
    return [_to_point(s, clusters) for s in rows]


def search_free_text(db: Session, query: str, *, limit: int = 200) -> VehicleTrace:
    """Search a plate if it looks like one, otherwise treat it as a description."""
    cleaned = query.strip()
    normalised, valid = normalise(cleaned)
    if valid or any(ch.isdigit() for ch in cleaned):
        return search_by_plate(db, cleaned)

    words = cleaned.lower().split()
    colours = {"white", "black", "grey", "gray", "red", "blue", "green",
               "yellow", "orange", "purple", "cyan"}
    types = {"car", "truck", "bus", "motorcycle", "bicycle"}
    colour = next((w for w in words if w in colours), None)
    vtype = next((w for w in words if w in types), None)
    return search_by_attributes(db, vehicle_type=vtype,
                                vehicle_color="grey" if colour == "gray" else colour,
                                limit=limit)
