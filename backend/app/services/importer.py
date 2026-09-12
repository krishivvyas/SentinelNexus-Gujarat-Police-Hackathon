"""Bulk camera onboarding from whatever spreadsheet a department already keeps.

The federation problem is not technical, it is clerical. Twenty-six departments
run their own cameras and every one of them keeps the list in a spreadsheet with
its own column names: ``Camera ID`` here, ``cam_no`` there, ``Device Code``
somewhere else. Latitude arrives as ``lat``, ``Latitude``, ``Y_COORD`` and
``GPS Lat``. Asking each department to re-key its estate into our schema is how
an interoperability project stalls for a year.

So the importer reads whatever headers it is given and maps them itself, then
shows every decision for confirmation before anything is written. Two rules
govern the design:

  * **Nothing is saved without review.** ``preview`` is a pure function -- it
    parses, maps, validates and returns; it never touches the database. Commit
    is a second, explicit call.
  * **A guess is labelled as a guess.** Each mapping carries how it was made:
    an exact header match, a known synonym, or a fuzzy match with its score. An
    operator scanning the preview should be able to see instantly which columns
    the importer was sure about and which one it is asking about.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..models import CameraStatus, LocationAccuracy

log = logging.getLogger("sentinel.importer")


@dataclass(frozen=True)
class Field_:
    """One column of the camera schema an import can populate."""

    name: str
    label: str
    required: bool = False
    #: Header spellings seen in the wild. Matched after normalisation, so
    #: "Camera ID", "camera_id" and "CAMERA-ID" all reduce to "cameraid".
    synonyms: tuple[str, ...] = ()
    kind: str = "text"            # text | float | int | bool | enum


SCHEMA: tuple[Field_, ...] = (
    Field_("camera_id", "Camera ID", required=True, kind="text",
           synonyms=("id", "cam", "camno", "camnumber", "cameracode",
                     "devicecode", "deviceid", "unitid", "assetid", "camid")),
    Field_("name", "Name", synonyms=("cameraname", "title", "label",
                                     "description", "site", "sitename")),
    Field_("department", "Owning department",
           synonyms=("dept", "owner", "agency", "owningdepartment",
                     "organisation", "organization", "office")),
    Field_("district", "District",
           synonyms=("dist", "zone", "region", "city", "taluka", "circle")),
    Field_("location_name", "Location",
           synonyms=("location", "place", "junction", "address", "landmark",
                     "area", "locality", "installedat")),
    Field_("latitude", "Latitude", kind="float",
           synonyms=("lat", "ycoord", "y", "gpslat", "latitudedeg", "coordy")),
    Field_("longitude", "Longitude", kind="float",
           synonyms=("lon", "lng", "long", "xcoord", "x", "gpslon",
                     "longitudedeg", "coordx")),
    Field_("stream_url", "Stream URL",
           synonyms=("rtsp", "rtspurl", "url", "streamurl", "uri", "feed",
                     "feedurl", "streamlink")),
    Field_("hls_url", "HLS URL", synonyms=("hls", "hlsurl", "m3u8", "playlist")),
    Field_("protocol", "Protocol", synonyms=("transport", "streamtype")),
    Field_("vendor", "Vendor", synonyms=("make", "manufacturer", "brand",
                                         "model", "oem")),
    Field_("codec", "Codec", synonyms=("encoding", "videocodec", "compression")),
    Field_("width", "Width", kind="int", synonyms=("resx", "hres", "pixelwidth")),
    Field_("height", "Height", kind="int", synonyms=("resy", "vres", "pixelheight")),
    Field_("fps", "Frame rate", kind="float", synonyms=("framerate", "framespersecond")),
    Field_("status", "Status", kind="enum",
           synonyms=("state", "health", "operational", "working", "live")),
    Field_("ai_enabled", "ANPR capable", kind="bool",
           synonyms=("anpr", "anprcapable", "lpr", "platereading", "aienabled",
                     "ai", "analytics")),
)

BY_NAME = {f.name: f for f in SCHEMA}

#: Below this a fuzzy match is not offered at all. Set from the failure mode
#: that matters: "Latitude" vs "Longitude" score 0.82 against each other, so
#: anything lower than that admits a swap that would put every camera in the
#: wrong hemisphere.
FUZZY_FLOOR = 0.86

TRUE_WORDS = {"1", "y", "yes", "true", "t", "on", "enabled", "capable", "anpr"}
FALSE_WORDS = {"0", "n", "no", "false", "f", "off", "disabled", "none", ""}


def _normalise(header: str) -> str:
    """Reduce a header to comparable form: lowercase, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


@dataclass
class Mapping:
    """One source column bound (or not) to a schema field."""

    source: str
    field: str | None
    method: str                    # exact | synonym | fuzzy | unmapped
    score: float = 1.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"source": self.source, "field": self.field, "method": self.method,
                "score": round(self.score, 2), "note": self.note,
                "label": BY_NAME[self.field].label if self.field else None}


def map_headers(headers: list[str]) -> list[Mapping]:
    """Bind source columns to schema fields, best-first and one-to-one.

    Candidates are scored for every (column, field) pair and then assigned
    greedily by score. Greedy assignment is what stops the classic failure:
    matching "Longitude" to ``latitude`` because it happened to be considered
    first, when a better home for it existed further down the row.
    """
    candidates: list[tuple[float, str, str, str]] = []   # score, header, field, method

    for header in headers:
        normalised = _normalise(header)
        if not normalised:
            continue
        for schema_field in SCHEMA:
            if normalised == schema_field.name.replace("_", ""):
                candidates.append((1.0, header, schema_field.name, "exact"))
            elif normalised in schema_field.synonyms:
                candidates.append((0.95, header, schema_field.name, "synonym"))
            else:
                score = SequenceMatcher(None, normalised,
                                        schema_field.name.replace("_", "")).ratio()
                best_synonym = max(
                    (SequenceMatcher(None, normalised, s).ratio()
                     for s in schema_field.synonyms), default=0.0)
                score = max(score, best_synonym)
                if score >= FUZZY_FLOOR:
                    candidates.append((score, header, schema_field.name, "fuzzy"))

    candidates.sort(key=lambda c: -c[0])

    taken_fields: set[str] = set()
    taken_headers: set[str] = set()
    resolved: dict[str, Mapping] = {}

    for score, header, field_name, method in candidates:
        if header in taken_headers or field_name in taken_fields:
            continue
        taken_headers.add(header)
        taken_fields.add(field_name)
        note = ""
        if method == "fuzzy":
            note = f"matched by similarity to '{BY_NAME[field_name].label}'; confirm"
        elif method == "synonym":
            note = "recognised alternative spelling"
        resolved[header] = Mapping(source=header, field=field_name,
                                   method=method, score=score, note=note)

    return [resolved.get(h, Mapping(source=h, field=None, method="unmapped",
                                    score=0.0, note="not imported"))
            for h in headers]


# ------------------------------------------------------------------- conversion

def _coerce(field_name: str, raw: str) -> tuple[object | None, str | None]:
    """Convert one cell, returning ``(value, error)``. Blank is not an error."""
    value = (raw or "").strip()
    if not value:
        return None, None

    kind = BY_NAME[field_name].kind
    try:
        if kind == "float":
            number = float(value.replace(",", ""))
            if field_name == "latitude" and not -90 <= number <= 90:
                return None, f"latitude {number} is outside -90..90"
            if field_name == "longitude" and not -180 <= number <= 180:
                return None, f"longitude {number} is outside -180..180"
            return number, None
        if kind == "int":
            return int(float(value)), None
        if kind == "bool":
            lowered = value.lower()
            if lowered in TRUE_WORDS:
                return True, None
            if lowered in FALSE_WORDS:
                return False, None
            return None, f"cannot read '{value}' as yes/no"
        if kind == "enum" and field_name == "status":
            upper = value.upper()
            if upper in CameraStatus.__members__:
                return upper, None
            # A department's own vocabulary, mapped to ours rather than rejected.
            if upper in {"UP", "ACTIVE", "WORKING", "LIVE", "OK", "FUNCTIONAL"}:
                return "ONLINE", None
            if upper in {"DOWN", "DEAD", "INACTIVE", "FAULTY", "BROKEN"}:
                return "OFFLINE", None
            # Status is frequently a yes/no column -- "Live", "Operational",
            # "Working" -- rather than a state name. Reading "yes" as UNKNOWN
            # would discard a fact the department actually gave us.
            if upper.lower() in TRUE_WORDS:
                return "ONLINE", None
            if upper.lower() in FALSE_WORDS:
                return "OFFLINE", None
            return "UNKNOWN", f"unrecognised status '{value}', recorded as UNKNOWN"
        return value, None
    except ValueError:
        return None, f"cannot read '{value}' as {kind}"


@dataclass
class RowPreview:
    number: int
    values: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"number": self.number, "values": self.values,
                "errors": self.errors, "warnings": self.warnings, "ok": self.ok}


def _read_csv(text: str) -> tuple[list[str], list[dict]]:
    """Parse CSV, sniffing the delimiter.

    Government exports are as often semicolon- or tab-separated as comma, and a
    file parsed with the wrong delimiter yields one enormous column with a
    perfectly sensible-looking name -- which then fuzzy-matches to something.
    """
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    return headers, list(reader)


def preview(text: str, *, limit: int = 200) -> dict:
    """Parse and validate an upload without writing anything.

    Returns the header mapping, per-row values and problems, and a summary. The
    caller renders this for confirmation and then posts the same content back to
    ``commit``.
    """
    headers, raw_rows = _read_csv(text)
    if not headers:
        return {"error": "no header row found", "mappings": [], "rows": [],
                "summary": {}}

    mappings = map_headers(headers)
    bound = {m.source: m.field for m in mappings if m.field}
    mapped_fields = set(bound.values())

    missing_required = [f.label for f in SCHEMA
                        if f.required and f.name not in mapped_fields]

    rows: list[RowPreview] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_rows[:limit], start=1):
        values: dict = {}
        row = RowPreview(number=index, values=values)

        for source, field_name in bound.items():
            value, problem = _coerce(field_name, raw.get(source) or "")
            if problem:
                # A bad optional cell is a warning; a bad required one is fatal
                # for that row only, never for the file.
                (row.errors if BY_NAME[field_name].required
                 else row.warnings).append(f"{source}: {problem}")
            if value is not None:
                values[field_name] = value

        camera_id = values.get("camera_id")
        if not camera_id:
            row.errors.append("camera_id is empty")
        elif camera_id in seen_ids:
            row.errors.append(f"duplicate camera_id '{camera_id}' within this file")
        elif camera_id:
            seen_ids.add(str(camera_id))

        has_lat = "latitude" in values
        has_lon = "longitude" in values
        if has_lat != has_lon:
            row.warnings.append(
                "only one of latitude/longitude given; the camera will be "
                "registered without a position rather than half-placed")
            values.pop("latitude", None)
            values.pop("longitude", None)

        rows.append(row)

    good = sum(r.ok for r in rows)
    return {
        "mappings": [m.to_dict() for m in mappings],
        "rows": [r.to_dict() for r in rows],
        "summary": {
            "headers": len(headers),
            "mapped": len(bound),
            "unmapped": len(headers) - len(bound),
            "fuzzy": sum(m.method == "fuzzy" for m in mappings),
            "total_rows": len(raw_rows),
            "previewed": len(rows),
            "importable": good,
            "rejected": len(rows) - good,
            "missing_required": missing_required,
            "truncated": len(raw_rows) > limit,
        },
        "schema": [{"name": f.name, "label": f.label, "required": f.required,
                    "kind": f.kind} for f in SCHEMA],
    }


def commit(db: Session, text: str, *, actor: str,
           overrides: dict[str, str] | None = None) -> dict:
    """Write an already-reviewed import into the registry.

    ``overrides`` re-binds source columns the operator corrected in the preview,
    as ``{source_header: field_name}``. Rows that failed validation are skipped
    and counted; a bad row never blocks a good one.

    Every camera goes through ``registry.upsert_camera``, so each field change
    lands in ``camera_metadata_history`` with this actor against it -- an
    imported camera is as auditable as a hand-edited one.
    """
    from . import registry

    headers, raw_rows = _read_csv(text)
    mappings = map_headers(headers)
    bound = {m.source: m.field for m in mappings if m.field}

    for source, field_name in (overrides or {}).items():
        if field_name in BY_NAME:
            # An override wins, and it also releases whatever column previously
            # held that field, so two columns cannot both write one field.
            bound = {s: f for s, f in bound.items() if f != field_name}
            bound[source] = field_name
        else:
            bound.pop(source, None)

    created = updated = skipped = 0
    problems: list[str] = []

    for index, raw in enumerate(raw_rows, start=1):
        values: dict = {}
        fatal = False
        for source, field_name in bound.items():
            value, problem = _coerce(field_name, raw.get(source) or "")
            if problem and BY_NAME[field_name].required:
                fatal = True
            if value is not None:
                values[field_name] = value

        camera_id = values.get("camera_id")
        if fatal or not camera_id:
            skipped += 1
            if len(problems) < 25:
                problems.append(f"row {index}: no usable camera_id")
            continue

        if ("latitude" in values) != ("longitude" in values):
            values.pop("latitude", None)
            values.pop("longitude", None)

        if "latitude" in values:
            # Imported coordinates come from the department's own records, which
            # is the strongest provenance available here -- but they are still
            # somebody's spreadsheet, so they are marked as such rather than
            # promoted to VERIFIED.
            values.setdefault("location_accuracy", LocationAccuracy.GEOCODED)
            values.setdefault("location_source", f"bulk import by {actor}")

        values.setdefault("name", str(camera_id))
        values.setdefault("protocol",
                          "HLS" if str(values.get("stream_url", "")).endswith(".m3u8")
                          else "RTSP")

        existed = registry.get_camera(db, str(camera_id)) is not None
        registry.upsert_camera(db, values, actor=actor)
        created, updated = (created, updated + 1) if existed else (created + 1, updated)

    db.commit()
    log.info("import by %s: %d created, %d updated, %d skipped",
             actor, created, updated, skipped)
    return {"created": created, "updated": updated, "skipped": skipped,
            "problems": problems}


def template_csv() -> str:
    """A blank import file with our own column names, for departments who want one."""
    headers = [f.name for f in SCHEMA]
    example = {
        "camera_id": "GJ-AHM-001", "name": "Paldi Circle North",
        "department": "TRAFFIC", "district": "Ahmedabad",
        "location_name": "Paldi Circle", "latitude": "23.0107",
        "longitude": "72.5619",
        "stream_url": "rtsp://10.0.0.5:554/stream1",
        "hls_url": "", "protocol": "RTSP", "vendor": "Hikvision",
        "codec": "H.264", "width": "1920", "height": "1080", "fps": "25",
        "status": "ONLINE", "ai_enabled": "yes",
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerow([example.get(h, "") for h in headers])
    return buffer.getvalue()
