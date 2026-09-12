"""GIS context layers -- the geography a traced route is read against.

A camera pin on a blank grey canvas tells an operator almost nothing. The
questions actually asked of a trace are geographic: which district is this,
which highway does the route follow, where is the nearest police station, which
toll plaza would the vehicle pass on the way out of the state. Those need
boundaries, roads and points of interest under the pins.

Where the data comes from
-------------------------
OpenStreetMap, through the Overpass API, fetched on demand and cached to disk.
Not baked into the repo, and not invented.

That choice is deliberate and it is the honest one. It would be easy to hardcode
"126 police stations, 232 toll plazas, 741 railway stations" and draw
convincing-looking dots. Every count this module reports is the number of
features actually returned by a query that anyone can re-run, and when the fetch
fails the layer reports itself as unavailable rather than falling back to
plausible fiction. An operator who cannot tell a real toll plaza from a
decorative one cannot use the map to plan an interception.

Caching
-------
Overpass is a shared free service and rate-limits aggressively, so every layer
is cached to ``data/gis/<layer>.geojson`` and served from there. Refresh is an
explicit operator action, never automatic on page load.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import DATA_DIR

log = logging.getLogger("sentinel.gis")

GIS_DIR = DATA_DIR / "gis"
GIS_DIR.mkdir(parents=True, exist_ok=True)

#: Public Overpass instances, tried in order. The main one rejects bursts, so a
#: mirror is worth having rather than failing the whole refresh.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

#: Gujarat. ``ISO3166-2`` is the stable way to name a state-level admin area in
#: OSM -- matching on the name would also catch villages called "Gujarat".
AREA = 'area["ISO3166-2"="IN-GJ"]->.gj;'

#: Overpass is slow for large areas; these are generous but bounded.
QUERY_TIMEOUT_S = 180
HTTP_TIMEOUT_S = 200.0


@dataclass(frozen=True)
class LayerSpec:
    """One fetchable map layer."""

    key: str
    label: str
    kind: str                      # "point" | "line" | "area"
    query: str
    #: What the layer is for, shown in the UI. An operator toggling a layer
    #: should know why it is worth the clutter.
    rationale: str
    colour: str = "#7c8ba1"
    #: Overpass can return tens of thousands of ways for road layers. Geometry
    #: is simplified and capped so the browser stays responsive.
    max_features: int = 20000


LAYERS: dict[str, LayerSpec] = {
    "districts": LayerSpec(
        key="districts", label="District boundaries", kind="area",
        colour="#4d5f78",
        rationale="Jurisdiction. A sighting means something different either "
                  "side of a district line, because a different unit owns it.",
        query=f"{AREA}\n"
              'relation["boundary"="administrative"]["admin_level"="5"](area.gj);\n'
              "out geom;",
        max_features=60,
    ),
    "highways": LayerSpec(
        key="highways", label="National highways", kind="line",
        colour="#c98b3a",
        rationale="A traced vehicle follows roads, not straight lines. A route "
                  "that obviously did not happen undermines every number shown "
                  "beside it.",
        query=f"{AREA}\n"
              'way["highway"="motorway"](area.gj);\n'
              "out geom;",
    ),
    "trunk": LayerSpec(
        key="trunk", label="Major roads", kind="line",
        colour="#8a7a4e",
        rationale="The rest of the road network a vehicle can plausibly use "
                  "between two cameras.",
        query=f"{AREA}\n"
              'way["highway"="trunk"](area.gj);\n'
              "out geom;",
    ),
    # Scoped to a bounding box rather than the state, and deliberately so.
    # Every camera on this grid is in Ahmedabad, and a statewide primary/
    # secondary query returns well over 100,000 ways -- most of them hundreds of
    # kilometres from anything this platform watches. This is the street grid an
    # operator actually navigates by, over the area the cameras cover.
    "city": LayerSpec(
        key="city", label="Ahmedabad street grid", kind="line",
        colour="#54637a",
        rationale="The urban road network the cameras sit on. Without it a "
                  "junction pin has nothing to be a junction of.",
        query='way["highway"~"^(primary|secondary|tertiary)$"]'
              "(22.88,72.40,23.18,72.75);\n"
              "out geom;",
        max_features=12000,
    ),
    "police": LayerSpec(
        key="police", label="Police stations", kind="point",
        colour="#4d8ff5",
        rationale="Who responds, and how far away they are from the last "
                  "confirmed sighting.",
        query=f"{AREA}\n"
              'nwr["amenity"="police"](area.gj);\n'
              "out center;",
    ),
    "toll": LayerSpec(
        key="toll", label="Toll plazas", kind="point",
        colour="#f0a63c",
        rationale="A vehicle leaving the state passes one. That makes toll "
                  "plazas the natural interception points on a traced route.",
        query=f"{AREA}\n"
              'nwr["barrier"="toll_booth"](area.gj);\n'
              "out center;",
    ),
    "railway": LayerSpec(
        key="railway", label="Railway stations", kind="point",
        colour="#9b7fd4",
        rationale="Where a suspect can abandon a vehicle and continue without "
                  "one.",
        query=f"{AREA}\n"
              'nwr["railway"="station"](area.gj);\n'
              "out center;",
    ),
}


@dataclass
class LayerState:
    """What is known about one layer right now, for the UI to render honestly."""

    key: str
    label: str
    kind: str
    colour: str
    rationale: str
    cached: bool = False
    feature_count: int = 0
    fetched_at: str | None = None
    status: str = "empty"          # empty | ready | fetching | error
    error: str = ""
    source: str = "OpenStreetMap via Overpass API"

    def to_dict(self) -> dict:
        return {**self.__dict__}


# In-flight refreshes, so two operators clicking refresh do not fire two
# Overpass queries for the same layer.
_inflight: dict[str, float] = {}
_lock = threading.Lock()


def _path(key: str) -> Path:
    return GIS_DIR / f"{key}.geojson"


def _read(key: str) -> dict | None:
    path = _path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("gis layer %s unreadable: %s", key, exc)
        return None


# ------------------------------------------------------------------ conversion

def _element_to_feature(element: dict, spec: LayerSpec) -> dict | None:
    """Convert one Overpass element into a GeoJSON feature.

    Overpass speaks its own JSON, not GeoJSON. Nodes carry lat/lon; ways carry a
    ``geometry`` array when queried with ``out geom``; ways and relations queried
    with ``out center`` carry a ``center``. Anything that fits none of those has
    no drawable position and is dropped rather than guessed at.
    """
    tags = element.get("tags") or {}
    name = tags.get("name") or tags.get("official_name") or ""
    properties = {"name": name, "layer": spec.key,
                  "osm_id": element.get("id"), "osm_type": element.get("type")}

    if element.get("type") == "node" and "lat" in element:
        geometry = {"type": "Point",
                    "coordinates": [element["lon"], element["lat"]]}
    elif "center" in element:
        centre = element["center"]
        geometry = {"type": "Point", "coordinates": [centre["lon"], centre["lat"]]}
    elif element.get("geometry"):
        coordinates = [[p["lon"], p["lat"]] for p in element["geometry"]
                       if "lon" in p and "lat" in p]
        if len(coordinates) < 2:
            return None
        geometry = {"type": "LineString", "coordinates": coordinates}
    elif element.get("members"):
        # A boundary relation: keep each closed outer way as its own line. Not a
        # true polygon assembly -- for drawing a district outline the difference
        # is invisible, and stitching rings wrongly would draw a boundary that
        # does not exist.
        lines = []
        for member in element["members"]:
            if member.get("role") not in (None, "", "outer"):
                continue
            coordinates = [[p["lon"], p["lat"]] for p in member.get("geometry", [])
                           if "lon" in p and "lat" in p]
            if len(coordinates) >= 2:
                lines.append(coordinates)
        if not lines:
            return None
        geometry = {"type": "MultiLineString", "coordinates": lines}
    else:
        return None

    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _to_geojson(payload: dict, spec: LayerSpec) -> dict:
    features = []
    for element in payload.get("elements", []):
        feature = _element_to_feature(element, spec)
        if feature is not None:
            features.append(feature)
        if len(features) >= spec.max_features:
            log.info("gis layer %s capped at %d features", spec.key, spec.max_features)
            break
    return {
        "type": "FeatureCollection",
        "features": features,
        "sentinel": {
            "layer": spec.key,
            "label": spec.label,
            "kind": spec.kind,
            "colour": spec.colour,
            "rationale": spec.rationale,
            "source": "OpenStreetMap contributors, via the Overpass API",
            "licence": "ODbL 1.0",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "feature_count": len(features),
        },
    }


# --------------------------------------------------------------------- fetching

def fetch(key: str) -> LayerState:
    """Fetch one layer from Overpass and cache it. Blocking; call off-thread."""
    spec = LAYERS.get(key)
    if spec is None:
        raise KeyError(key)

    query = f"[out:json][timeout:{QUERY_TIMEOUT_S}];\n{spec.query}"
    last_error = ""

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            log.info("gis: fetching %s from %s", key, endpoint)
            response = httpx.post(endpoint, data={"data": query},
                                  timeout=HTTP_TIMEOUT_S,
                                  headers={"User-Agent": "SentinelNexus/1.0"})
            response.raise_for_status()
            geojson = _to_geojson(response.json(), spec)
            count = geojson["sentinel"]["feature_count"]

            # An empty result for "every police station in Gujarat" is not the
            # truth, it is a failed area lookup -- and mirrors return exactly
            # that, with a 200, when they are unhappy. Caching it would replace
            # good data with a confident, wrong "0 features". Treat it as a soft
            # failure and try the next endpoint instead.
            if count == 0:
                last_error = "endpoint returned zero features (likely a failed area lookup)"
                log.warning("gis: %s empty from %s, trying next endpoint", key, endpoint)
                continue

            _path(key).write_text(json.dumps(geojson), encoding="utf-8")
            log.info("gis: %s cached, %d features", key, count)
            return state(key)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning("gis: %s failed via %s (%s)", key, endpoint, last_error)

    result = state(key)
    result.status = "error"
    result.error = last_error
    # A previously cached copy is still the best answer available, so say so
    # rather than blanking the layer because a refresh failed.
    if result.cached:
        result.error += " -- showing the previously cached copy"
    return result


def refresh_async(key: str) -> LayerState:
    """Kick off a fetch in the background and report the layer as fetching.

    An Overpass query for a road layer takes tens of seconds. Holding an HTTP
    request open that long would time out in the browser, so the fetch is
    detached and the UI polls the layer's state.
    """
    if key not in LAYERS:
        raise KeyError(key)

    with _lock:
        started = _inflight.get(key)
        # Ten minutes: long enough that a slow Overpass query is not restarted
        # under itself, short enough that a crashed thread does not wedge the
        # layer permanently.
        if started and time.time() - started < 600:
            current = state(key)
            current.status = "fetching"
            return current
        _inflight[key] = time.time()

    def _run() -> None:
        try:
            fetch(key)
        finally:
            with _lock:
                _inflight.pop(key, None)

    threading.Thread(target=_run, daemon=True, name=f"gis-{key}").start()
    current = state(key)
    current.status = "fetching"
    return current


# ----------------------------------------------------------------------- access

def state(key: str) -> LayerState:
    """What is currently known about one layer."""
    spec = LAYERS[key]
    result = LayerState(key=spec.key, label=spec.label, kind=spec.kind,
                        colour=spec.colour, rationale=spec.rationale)

    with _lock:
        if key in _inflight:
            result.status = "fetching"

    cached = _read(key)
    if cached is None:
        return result

    meta = cached.get("sentinel", {})
    result.cached = True
    result.feature_count = meta.get("feature_count", len(cached.get("features", [])))
    result.fetched_at = meta.get("fetched_at")
    if result.status != "fetching":
        result.status = "ready"
    return result


def catalogue() -> list[dict]:
    """Every layer and its current state -- what the layer panel renders from."""
    return [state(key).to_dict() for key in LAYERS]


def geojson(key: str) -> dict | None:
    """The cached GeoJSON for one layer, or None if it has never been fetched."""
    if key not in LAYERS:
        raise KeyError(key)
    return _read(key)
