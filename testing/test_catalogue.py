"""Catalogue contract: the camera list is read from cameras.json.

Checklist item: "Camera list read from cameras.json; mixed H.264/H.265 and
resolutions handled."
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.connectors.base import BaseConnector, CameraDescriptor
from app.connectors.catalogue import CatalogueConnector, write_catalogue
from app.connectors.rtsp import RTSPConnector

CATALOGUE = Path(__file__).resolve().parent.parent / "backend" / "data" / "cameras.json"

pytestmark = pytest.mark.skipif(
    not CATALOGUE.exists(),
    reason="cameras.json not built; run scripts/seed_registry.py first",
)


@pytest.fixture(scope="module")
def cameras() -> list[CameraDescriptor]:
    return CatalogueConnector(CATALOGUE).discover()


def test_catalogue_is_the_source_of_the_camera_list(cameras):
    assert len(cameras) >= 1
    doc = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    assert doc["version"] >= 1
    assert len(doc["cameras"]) == len(cameras)


def test_no_stream_url_is_hardcoded_in_application_code():
    """Stream URLs live in the catalogue, not in the codebase.

    An f-string template assembled from settings -- ``rtsp://{host}:{port}{path}``
    -- is how a configured endpoint is built and is fine. What must not appear is
    a literal host baked into the source, because that is exactly what the
    catalogue exists to own.
    """
    import re

    from helpers import code_only

    app_dir = Path(__file__).resolve().parent.parent / "backend" / "app"
    # rtsp:// followed by anything that is not an interpolation placeholder.
    literal_host = re.compile(r"rtsp://(?!\{)[A-Za-z0-9]")
    offenders = []
    for path in app_dir.rglob("*.py"):
        # Docstrings and comments are documentation, not configuration.
        if literal_host.search(code_only(path)):
            offenders.append(path.name)
    assert not offenders, f"hardcoded RTSP host in: {offenders}"


def test_mixed_codecs_are_present_and_carried(cameras):
    codecs = {c.codec for c in cameras if c.codec}
    assert "H.264" in codecs and "H.265" in codecs, (
        f"the fleet is mixed-codec; catalogue holds {codecs}")


def test_mixed_resolutions_are_carried(cameras):
    resolutions = {(c.width, c.height) for c in cameras if c.width}
    assert len(resolutions) >= 3, f"expected a spread of resolutions, got {resolutions}"
    widths = [w for w, _ in resolutions]
    assert min(widths) < 1920 < max(widths) or min(widths) < max(widths)


def test_every_camera_has_a_transport(cameras):
    for c in cameras:
        assert c.stream_url, f"{c.camera_id} has no stream URL"
        assert c.protocol in ("RTSP", "HLS"), f"{c.camera_id} protocol {c.protocol}"


def test_hls_fallback_url_is_recorded(cameras):
    """Remote clients use HLS, so the catalogue must carry that URL too."""
    with_hls = [c for c in cameras if c.hls_url]
    assert with_hls, "no camera carries an HLS URL"


def test_absurd_frame_rates_survive_the_round_trip(cameras):
    """CAM-06 reports 90000 fps. The catalogue records it; nothing computes with it."""
    rates = {c.fps for c in cameras if c.fps}
    assert rates, "no frame rates recorded"
    # The point is that a nonsense value is stored rather than sanitised away,
    # so the operator can see the camera is misreporting.
    assert max(rates) > 100, (
        "expected at least one misreporting camera to be recorded as-is")


def test_catalogue_round_trips(tmp_path):
    path = tmp_path / "cameras.json"
    write_catalogue(path, [{
        "id": "cam99", "camera_id": "CAM-99", "name": "Test",
        "rtsp": "rtsp://host:8554/stream/cam99",
        "hls": "https://host/cam99/index.m3u8",
        "codec": "H.265", "width": 2560, "height": 1440, "fps": 12,
        "lat": 23.0, "lon": 72.5, "status": "ONLINE",
    }], source="unit-test")

    out = CatalogueConnector(path).discover()
    assert len(out) == 1
    cam = out[0]
    assert cam.camera_id == "CAM-99"
    assert cam.protocol == "RTSP"
    assert cam.codec == "H.265"
    assert (cam.width, cam.height) == (2560, 1440)
    assert cam.hls_url.endswith("index.m3u8")


def test_catalogue_falls_back_to_hls_when_no_rtsp(tmp_path):
    path = tmp_path / "cameras.json"
    write_catalogue(path, [{"camera_id": "CAM-HLS",
                            "hls": "https://host/cam/index.m3u8"}])
    cam = CatalogueConnector(path).discover()[0]
    assert cam.protocol == "HLS"
    assert cam.stream_url.endswith("index.m3u8")


def test_authenticated_catalogue_reports_a_login_redirect(monkeypatch):
    """The portal's cameras.json sits behind /auth/login; that must be explicit."""
    import httpx

    class FakeResponse:
        status_code = 302
        headers = {"location": "/auth/login"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())
    connector = CatalogueConnector("https://portal.example/cameras.json")
    with pytest.raises(PermissionError, match="credentials required"):
        connector.discover()


def test_connector_contract_is_uniform():
    """Every adapter emits the same representation, so nothing above cares."""
    for cls in (RTSPConnector, CatalogueConnector):
        assert issubclass(cls, BaseConnector)
    for name in ("discover", "probe", "frames"):
        assert callable(getattr(RTSPConnector, name))


def test_descriptor_is_serialisable():
    d = CameraDescriptor(camera_id="CAM-01", stream_url="rtsp://h/s", codec="H.264")
    as_dict = d.to_dict()
    assert as_dict["camera_id"] == "CAM-01"
    json.dumps(as_dict)          # must survive the API boundary
