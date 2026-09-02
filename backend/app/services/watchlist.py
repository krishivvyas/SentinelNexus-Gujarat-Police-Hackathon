"""Watchlist storage and matching.

Plates are stored normalised so that a match never depends on how the entry was
typed. Matching is exact on the normalised form first, then falls back to a
tolerant comparison that survives one or two OCR character confusions -- which is
the realistic failure mode on this footage.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Severity, WatchlistEntry
from ..pipeline.ocr import normalise

# Character pairs OCR genuinely confuses. Used only to decide whether a near-miss
# should still alert -- never to rewrite what was actually read.
_CONFUSABLE = [
    {"0", "O", "D", "Q"}, {"1", "I", "L"}, {"5", "S"}, {"8", "B"},
    {"2", "Z"}, {"6", "G"}, {"4", "A"}, {"7", "T"},
]


@dataclass
class WatchlistMatch:
    entry: WatchlistEntry
    confidence: float
    exact: bool
    note: str = ""


def _confusable(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in group and b in group for group in _CONFUSABLE)


def fuzzy_score(candidate: str, target: str) -> float:
    """1.0 for identical; lower as confusable substitutions accumulate.

    Returns 0.0 when the strings differ in length or contain a difference that
    OCR would not plausibly produce, so unrelated plates never score.
    """
    if len(candidate) != len(target):
        return 0.0
    mismatches = 0
    for a, b in zip(candidate, target):
        if a == b:
            continue
        if _confusable(a, b):
            mismatches += 1
        else:
            return 0.0
    if mismatches == 0:
        return 1.0
    if mismatches > 2:
        return 0.0
    return 1.0 - 0.18 * mismatches


def add_entry(db: Session, *, plate: str, category: str = "OTHER",
              severity: str = "MEDIUM", vehicle_type: str | None = None,
              vehicle_color: str | None = None, owner_name: str | None = None,
              notes: str = "", added_by: str = "system") -> WatchlistEntry:
    normalised, _ = normalise(plate)
    existing = db.scalar(select(WatchlistEntry).where(WatchlistEntry.plate == normalised))
    if existing:
        existing.category = category
        existing.severity = Severity(severity)
        existing.notes = notes or existing.notes
        existing.active = True
        db.commit()
        return existing

    entry = WatchlistEntry(
        plate=normalised,
        plate_raw=plate,
        category=category,
        severity=Severity(severity),
        vehicle_type=vehicle_type,
        vehicle_color=vehicle_color,
        owner_name=owner_name,
        notes=notes,
        added_by=added_by,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_entries(db: Session, *, active_only: bool = False,
                 category: str | None = None) -> list[WatchlistEntry]:
    stmt = select(WatchlistEntry)
    if active_only:
        stmt = stmt.where(WatchlistEntry.active.is_(True))
    if category:
        stmt = stmt.where(WatchlistEntry.category == category)
    return list(db.scalars(stmt.order_by(WatchlistEntry.created_at.desc())))


def remove_entry(db: Session, entry_id: int) -> bool:
    entry = db.get(WatchlistEntry, entry_id)
    if entry is None:
        return False
    db.delete(entry)
    db.commit()
    return True


def match_plate(db: Session, plate: str, *, allow_fuzzy: bool = True,
                min_score: float = 0.6) -> WatchlistMatch | None:
    """Check one detected plate against the active watchlist."""
    if not plate:
        return None
    normalised, _ = normalise(plate)

    exact = db.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.plate == normalised,
            WatchlistEntry.active.is_(True),
        )
    )
    if exact is not None:
        return WatchlistMatch(entry=exact, confidence=1.0, exact=True,
                              note="exact plate match")

    if not allow_fuzzy:
        return None

    best: WatchlistMatch | None = None
    for entry in db.scalars(
        select(WatchlistEntry).where(WatchlistEntry.active.is_(True))
    ):
        score = fuzzy_score(normalised, entry.plate)
        if score >= min_score and (best is None or score > best.confidence):
            best = WatchlistMatch(
                entry=entry, confidence=round(score, 3), exact=False,
                note=f"tolerant match: read {normalised}, watchlist {entry.plate}",
            )
    return best


def seed_demo_watchlist(db: Session, plates: list[dict] | None = None) -> int:
    """Populate a representative watchlist.

    The challenge permits a representative list for demonstration. These are
    illustrative records, not real stolen-vehicle data.
    """
    default = [
        {"plate": "GJ01AB1234", "category": "STOLEN_VEHICLE", "severity": "HIGH",
         "notes": "Demonstration record"},
        {"plate": "GJ05CD5678", "category": "WANTED", "severity": "CRITICAL",
         "notes": "Demonstration record"},
        {"plate": "GJ18EF9012", "category": "SUSPECT_VEHICLE", "severity": "MEDIUM",
         "notes": "Demonstration record"},
        {"plate": "MH12GH3456", "category": "BOLO", "severity": "LOW",
         "notes": "Demonstration record"},
    ]
    added = 0
    for record in (plates or default):
        add_entry(db, added_by="seed", **record)
        added += 1
    return added
