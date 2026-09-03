"""Evidence reports.

The challenge asks for an output report of detected vehicles and plates with
corresponding timestamps. Two formats:

  * CSV  -- the machine-readable detection log
  * PDF  -- an operator-facing evidence report with the trace summary

Timestamps come from the burned-in overlay clock, and every row says so. Where a
plate could not be read the row is still included, with the plate column marked
and the vehicle described by its attributes -- an honest detection log is more
useful than one that silently drops what it could not identify.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Camera, Sighting
from .search import VehicleTrace

CSV_COLUMNS = [
    "sighting_id", "vehicle_number", "plate_confidence", "plate_frames_voted",
    "vehicle_type", "direction", "camera_id", "location",
    "latitude", "longitude", "location_accuracy", "timestamp",
    "timestamp_source", "detection_confidence", "evidence_image",
]


def _row(sighting: Sighting, accuracy: str = "") -> dict:
    return {
        "sighting_id": sighting.id,
        "vehicle_number": sighting.plate or "NOT_READABLE",
        "plate_confidence": f"{sighting.plate_confidence:.2f}"
        if sighting.plate_confidence else "",
        "plate_frames_voted": sighting.plate_frames_voted or "",
        "vehicle_type": sighting.vehicle_type or "",
        "direction": sighting.direction or "",
        "camera_id": sighting.camera_id,
        "location": sighting.location_name or "",
        "latitude": sighting.latitude if sighting.latitude is not None else "",
        "longitude": sighting.longitude if sighting.longitude is not None else "",
        "location_accuracy": accuracy,
        "timestamp": sighting.event_ts.strftime("%Y-%m-%d %H:%M:%S")
        if sighting.event_ts else "",
        "timestamp_source": sighting.event_ts_source or "",
        "detection_confidence": f"{sighting.detection_confidence:.2f}"
        if sighting.detection_confidence else "",
        "evidence_image": sighting.evidence_path or "",
    }


def _accuracy_map(db: Session) -> dict[str, str]:
    return {
        c.camera_id: c.location_accuracy.value if c.location_accuracy else ""
        for c in db.scalars(select(Camera))
    }


def detection_csv(db: Session, *, plate: str | None = None,
                  camera_id: str | None = None,
                  with_plate_only: bool = False,
                  limit: int = 5000) -> str:
    """The government-feed output report: detections with timestamps."""
    stmt = select(Sighting)
    if plate:
        stmt = stmt.where(Sighting.plate == plate)
    if camera_id:
        stmt = stmt.where(Sighting.camera_id == camera_id)
    if with_plate_only:
        stmt = stmt.where(Sighting.plate.isnot(None))
    stmt = stmt.order_by(Sighting.event_ts.asc().nulls_last(), Sighting.id.asc())

    accuracy = _accuracy_map(db)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for sighting in db.scalars(stmt.limit(limit)):
        writer.writerow(_row(sighting, accuracy.get(sighting.camera_id, "")))
    return buffer.getvalue()


def trace_csv(trace: VehicleTrace) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["vehicle", "camera", "location", "timestamp",
                     "latitude", "longitude", "confidence", "evidence"])
    for point in trace.points:
        writer.writerow([
            point.plate or "NOT_READABLE",
            point.camera_id,
            point.location_name,
            point.event_ts.strftime("%Y-%m-%d %H:%M:%S") if point.event_ts else "",
            point.latitude if point.latitude is not None else "",
            point.longitude if point.longitude is not None else "",
            f"{point.plate_confidence:.2f}" if point.plate_confidence else "",
            point.evidence_path or "",
        ])
    return buffer.getvalue()


def trace_pdf(trace: VehicleTrace, *, title: str = "Vehicle Movement Report") -> bytes:
    """Operator-facing evidence report."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=title)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"<b>Sentinel Nexus — {title}</b>", styles["Title"]))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(f"<b>Query:</b> {trace.query} "
                           f"({trace.match_type} match)", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Sightings:</b> {len(trace.points)} across "
        f"{len(trace.cameras)} camera(s)", styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    for note in trace.notes:
        story.append(Paragraph(f"<i>Note: {note}</i>", styles["Normal"]))
    if trace.notes:
        story.append(Spacer(1, 4 * mm))

    data = [["#", "Camera", "Location", "Timestamp", "Vehicle", "Conf."]]
    for i, p in enumerate(trace.points, 1):
        data.append([
            str(i),
            p.camera_id,
            (p.location_name or "")[:26],
            p.event_ts.strftime("%Y-%m-%d %H:%M:%S") if p.event_ts else "—",
            p.plate or (p.vehicle_type or "")
            or "unidentified",
            f"{p.plate_confidence:.2f}" if p.plate_confidence else "—",
        ])

    table = Table(data, repeatRows=1,
                  colWidths=[10 * mm, 22 * mm, 44 * mm, 36 * mm, 40 * mm, 14 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9ca3af")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f3f4f6")]),
    ]))
    story.append(table)

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "<font size=7>Timestamps are read from each camera's burned-in overlay "
        "clock, which reflects when the footage was recorded. Rows marked "
        "NOT_READABLE had no legible registration plate and are identified by "
        "vehicle attributes only. Camera coordinates marked APPROXIMATE are "
        "landmark-level estimates, not surveyed positions.</font>",
        styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()


def summary(db: Session) -> dict:
    """Headline counts for the dashboard overview."""
    from sqlalchemy import func

    total = db.scalar(select(func.count()).select_from(Sighting)) or 0
    with_plate = db.scalar(
        select(func.count()).select_from(Sighting).where(Sighting.plate.isnot(None))
    ) or 0
    unique_plates = db.scalar(
        select(func.count(func.distinct(Sighting.plate)))
        .where(Sighting.plate.isnot(None))
    ) or 0
    return {
        "sightings": total,
        "sightings_with_plate": with_plate,
        "unique_plates": unique_plates,
        "plate_read_rate": round(with_plate / total, 4) if total else 0.0,
    }
