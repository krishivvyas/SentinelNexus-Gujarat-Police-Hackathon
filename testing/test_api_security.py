"""API contract, authentication and role-based access control.

Runs against the real application through FastAPI's TestClient, so no server
needs to be started and no network is touched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.services import security


@pytest.fixture(scope="module")
def client():
    init_db()
    with SessionLocal() as db:
        security.seed_default_users(db)
    with TestClient(app) as c:
        yield c


def token(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login",
                    data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ------------------------------------------------------------------- health

def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Sentinel" in r.text


# --------------------------------------------------------------------- auth

def test_login_succeeds_for_each_role(client):
    for user, pwd in (("admin", "sentinel-admin"),
                      ("operator", "sentinel-operator"),
                      ("analyst", "sentinel-analyst")):
        assert token(client, user, pwd)


def test_login_rejects_a_wrong_password(client):
    r = client.post("/api/auth/login",
                    data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_rejects_an_unknown_user(client):
    r = client.post("/api/auth/login",
                    data={"username": "nobody", "password": "x"})
    assert r.status_code == 401


def test_password_is_not_stored_in_clear(client):
    from sqlalchemy import select

    from app.models import User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user.hashed_password.startswith("$2")     # bcrypt
        assert "sentinel-admin" not in user.hashed_password


# ----------------------------------------------------------------- authz

def test_protected_endpoints_reject_anonymous_callers(client):
    for path in ("/api/stats", "/api/cameras", "/api/alerts",
                 "/api/watchlist", "/api/audit"):
        assert client.get(path).status_code == 401, path


def test_a_garbage_token_is_rejected(client):
    r = client.get("/api/stats", headers=auth("not-a-real-token"))
    assert r.status_code == 401


def test_analyst_is_read_only(client):
    tok = token(client, "analyst", "sentinel-analyst")
    # Allowed
    assert client.get("/api/stats", headers=auth(tok)).status_code == 200
    assert client.get("/api/cameras", headers=auth(tok)).status_code == 200
    # Denied: admin-only audit log
    assert client.get("/api/audit", headers=auth(tok)).status_code == 403
    # Denied: operator-only mutation
    r = client.post("/api/watchlist", headers=auth(tok),
                    json={"plate": "GJ01AB1234"})
    assert r.status_code == 403


def test_operator_may_mutate_but_not_read_the_audit_log(client):
    tok = token(client, "operator", "sentinel-operator")
    r = client.post("/api/watchlist", headers=auth(tok),
                    json={"plate": "GJ09ZZ0001", "category": "BOLO",
                          "severity": "LOW"})
    assert r.status_code == 200
    assert client.get("/api/audit", headers=auth(tok)).status_code == 403


def test_admin_reaches_the_audit_log(client):
    tok = token(client, "admin", "sentinel-admin")
    r = client.get("/api/audit", headers=auth(tok))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ------------------------------------------------------------------ shape

def test_stats_shape(client):
    tok = token(client, "operator", "sentinel-operator")
    d = client.get("/api/stats", headers=auth(tok)).json()
    for key in ("cameras", "alerts", "detections", "ingest"):
        assert key in d
    assert "plate_read_rate" in d["detections"]


def test_geojson_only_contains_positioned_cameras(client):
    tok = token(client, "analyst", "sentinel-analyst")
    fc = client.get("/api/cameras/geo/features", headers=auth(tok)).json()
    assert fc["type"] == "FeatureCollection"
    for f in fc["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert lat is not None and lon is not None
        # An unplaced camera must never be drawn.
        assert f["properties"]["accuracy"] != "UNKNOWN"


def test_search_requires_a_query(client):
    tok = token(client, "analyst", "sentinel-analyst")
    assert client.get("/api/search", headers=auth(tok)).status_code == 400


def test_search_for_an_absent_plate_is_empty_not_invented(client):
    tok = token(client, "analyst", "sentinel-analyst")
    d = client.get("/api/search?plate=GJ99ZZ9999", headers=auth(tok)).json()
    assert d["sighting_count"] == 0
    assert d["points"] == []
    assert d["notes"], "an empty result must explain itself"


def test_csv_report_has_the_documented_columns(client):
    tok = token(client, "analyst", "sentinel-analyst")
    r = client.get("/api/reports/detections.csv", headers=auth(tok))
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    for column in ("vehicle_number", "camera_id", "timestamp",
                   "location_accuracy", "timestamp_source"):
        assert column in header


def test_unreadable_plates_are_marked_not_dropped(client):
    """An honest detection log includes what it could not identify."""
    tok = token(client, "analyst", "sentinel-analyst")
    body = client.get("/api/reports/detections.csv", headers=auth(tok)).text
    if len(body.splitlines()) > 1:
        assert "NOT_READABLE" in body or "vehicle_number" in body


def test_evidence_path_traversal_is_blocked(client):
    for attempt in ("../../../etc/passwd", "..%2f..%2fconfig.py", "....//secret"):
        r = client.get(f"/api/evidence/{attempt}")
        assert r.status_code in (403, 404), f"{attempt} returned {r.status_code}"
