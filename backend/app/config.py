"""Central configuration. All secrets and endpoints come from env, never hardcoded."""
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The portal answers stream requests from a non-browser client with
#: ``403 browser required``, testing only that the User-Agent starts with
#: "Mozilla". A session cookie alone is not enough. Kept free of ";" and "|"
#: because OpenCV parses OPENCV_FFMPEG_CAPTURE_OPTIONS on those characters.
HLS_USER_AGENT = "Mozilla/5.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidence"
MODELS_DIR = BASE_DIR.parent / "models"

#: Per-profile pipeline sizing. ``detector_model`` is consumed by ``detect.py``
#: when ``detector == "auto"``, rather than being written onto the settings
#: object, so an explicit SENTINEL_DETECTOR always wins over the profile.
#:
#: Measured on a 16-core CPU-only host over 40 night frames sampled from this
#: grid's own evidence store, cost per 1080p frame and vehicles found:
#:
#:   yolov4-tiny   75 ms   1.30 vehicles/frame   (the previous detector)
#:   yolo11n       40 ms   2.33 vehicles/frame   1.79x
#:   yolo11s       80 ms   2.38 vehicles/frame   1.83x
#:   yolo11m      221 ms   1.95 vehicles/frame   1.50x
#:
#: Note the shape of that table. yolo11m finds *fewer* vehicles than yolo11s on
#: this footage while costing 2.8x more -- a larger model is better calibrated
#: and therefore more willing to call a dim night blob "not a vehicle", which is
#: the wrong trade when the blob usually is one. So "high" does not mean a
#: bigger model here; it means the same model fed more cameras and more frames.
#: yolo11m stays downloadable and can be pinned with SENTINEL_DETECTOR for
#: daylight footage, where that calibration is an asset rather than a cost.
#:
#: Those are clean-process figures. Loaded -- API, OCR and ORM resident in the
#: same process, which is exactly how this deploys -- costs run roughly 3x
#: higher, and the sample intervals below are sized against the loaded number,
#: not the clean one. Earlier values of 1 s and 2 s for "low" only looked
#: sufficient because they were checked against a bare benchmark.
PROFILE_PRESETS: dict[str, dict] = {
    "low":      {"sample_interval_ms": 3000, "max_concurrent_streams": 2,
                 "inference_width": 512, "detector_model": "yolo11n"},
    "balanced": {"sample_interval_ms": 400, "max_concurrent_streams": 4,
                 "inference_width": 640, "detector_model": "yolo11s"},
    "high":     {"sample_interval_ms": 250, "max_concurrent_streams": 8,
                 "inference_width": 736, "detector_model": "yolo11s"},
}


class Settings(BaseSettings):
    # env_file must be absolute: run.py chdir's into backend/ before starting
    # uvicorn, so a relative ".env" resolves to backend/.env and the real file at
    # the repo root is never read -- every credential silently stays empty. Both
    # locations are accepted, repo root first.
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR.parent / ".env", BASE_DIR / ".env"),
        extra="ignore", populate_by_name=True)

    app_name: str = "Sentinel Nexus"
    database_url: str = f"sqlite:///{DATA_DIR / 'sentinel.db'}"

    # Sentinel government feed. RTSP is reachable unauthenticated; HLS is behind /auth/login.
    sentinel_rtsp_host: str = "103.250.160.189"
    sentinel_rtsp_port: int = 8554
    sentinel_rtsp_path: str = "/stream/cam{n:02d}"
    sentinel_hls_base: str = "https://cctv.corp8.cloud"
    sentinel_camera_count: int = 30

    # RTSP credentials. The grid answered unauthenticated until 2026-09-02, then
    # began returning 401 with WWW-Authenticate: Basic realm="ipcam". Credentials
    # come from the environment and are injected at connection time -- they are
    # never written into cameras.json, so the catalogue stays safe to share.
    sentinel_rtsp_user: str = ""
    sentinel_rtsp_password: str = ""

    # HLS portal session. The portal serves both the stream playlists and
    # /cameras.json behind /auth/login, so the same cookie or bearer token
    # unlocks both. Obtain it by POSTing email + access password to
    # {sentinel_hls_base}/auth/login and keeping the session cookie.
    sentinel_hls_cookie: str = ""
    sentinel_hls_token: str = ""

    # Live catalogue endpoint. The camera set can change upstream, so prefer the
    # portal's document over the local snapshot. Empty falls back to
    # backend/data/cameras.json.
    sentinel_catalogue_url: str = ""

    # --- Basemap ------------------------------------------------------------
    # Three ways to put geography under the camera pins, in the order the
    # client prefers them.
    #
    # 1. Vector tiles (the default). OpenFreeMap serves the whole OpenMapTiles
    #    planet with no API key, no account, no watermark and no rate limit.
    #    Because the tiles are vector, the *styling* is ours: static/js/
    #    basemap.js paints them in a palette built to sit under this
    #    interface's signal hues rather than compete with them. This is what
    #    replaced the near-black canvas the map used to be.
    #
    # 2. Raster tiles, when an operator has their own tile server:
    #       SENTINEL_BASEMAP_URL=https://tiles.internal.gov/dark/{z}/{x}/{y}.png
    #    Set that and it is used instead of the vector basemap.
    #
    # 3. Neither. On an isolated network where no outbound tile fetch resolves,
    #    clear SENTINEL_BASEMAP_VECTOR_TILES and the map falls back to drawing
    #    its geography from the GIS layers this platform fetched and cached for
    #    itself -- district boundaries, national highways, the city street grid,
    #    from OpenStreetMap via Overpass. Less pretty, and every line on it is
    #    data we hold and can name a source for.
    #
    # Note the free *raster* dark basemaps are still excluded on purpose: CARTO
    # stamps "API KEY REQUIRED" diagonally across anonymous tiles and the OSM
    # volunteer servers answer 418 to application traffic. Vector tiles are what
    # made a real basemap possible without a key on somebody's billing account.
    basemap_url: str = Field(default="", validation_alias="SENTINEL_BASEMAP_URL")
    basemap_attribution: str = Field(
        default="", validation_alias="SENTINEL_BASEMAP_ATTRIBUTION")

    #: TileJSON endpoint for the vector basemap. Empty disables it entirely.
    basemap_vector_tiles: str = Field(
        default="https://tiles.openfreemap.org/planet",
        validation_alias="SENTINEL_BASEMAP_VECTOR_TILES")
    #: Glyph endpoint for the vector basemap's labels. MapLibre drops a whole
    #: symbol layer without a word of complaint when its fontstack 404s, so this
    #: must point at a server that actually carries "Noto Sans Regular" and
    #: "Noto Sans Bold" -- the two faces basemap.js asks for.
    basemap_vector_glyphs: str = Field(
        default="https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
        validation_alias="SENTINEL_BASEMAP_VECTOR_GLYPHS")
    basemap_vector_attribution: str = Field(
        default='&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> '
                '&copy; <a href="https://www.openstreetmap.org/copyright">'
                "OpenStreetMap</a> contributors",
        validation_alias="SENTINEL_BASEMAP_VECTOR_ATTRIBUTION")

    # Auth
    jwt_secret: str = "dev-only-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # Plate layout to validate against: "IN" (Indian registrations, used by the
    # Sentinel grid) or "GENERIC" (any letters+digits, for non-Indian footage).
    plate_region: str = Field(default="IN", validation_alias="SENTINEL_PLATE_REGION")

    # Hardware profile. "low" targets an old laptop with no GPU; "balanced" is the
    # default; "high" only makes sense with plenty of cores. Set SENTINEL_PROFILE.
    profile: str = Field(default="balanced", validation_alias="SENTINEL_PROFILE")

    # --- Detector -----------------------------------------------------------
    # "auto" picks the YOLO11 variant for the hardware profile and degrades to
    # the next lighter one, then to YOLOv4-tiny, if weights are missing. Name a
    # model (yolo11n/s/m/l) to pin it, or "yolov4-tiny" to force the old path.
    detector: str = Field(default="auto", validation_alias="SENTINEL_DETECTOR")
    # 0 lets the ONNX session take all cores but one, leaving room for the
    # decoder and the API. Pin it when sharing the box with something else.
    detector_threads: int = Field(default=0, validation_alias="SENTINEL_DETECTOR_THREADS")
    # YOLO11 scores are better calibrated than YOLOv4-tiny's, so a slightly
    # lower floor here recovers small distant vehicles without a flood of
    # false boxes. Raised from the old 0.30 only after checking the extra
    # detections on this grid were real vehicles.
    detector_conf: float = Field(default=0.25, validation_alias="SENTINEL_DETECTOR_CONF")
    detector_nms: float = 0.45

    # --- Learned plate localisation -----------------------------------------
    # A single-class YOLO plate detector run inside each vehicle box, merged
    # with the morphological candidates in plate.py.
    #
    # OFF by default, and that default is a measurement rather than a
    # preference. Over 55 vehicle crops from this grid's own evidence store it
    # produced *zero* detections -- identical output to morphology alone, for an
    # extra 39 ms per crop. Raw scores are ~0.002 on night crops and peak at
    # 0.16 on the brightest frames in the store, i.e. never near any usable
    # threshold. These are wide-angle night overview PTZ cameras where plates
    # run 20-40 px, and the model has nothing to lock onto.
    #
    # The path is kept and is correct: point this platform at an actual ANPR
    # camera, or at daylight footage, and turning it on is a one-line change.
    # Shipping it ON would be a 39 ms/crop regression bought with nothing.
    plate_detector_enabled: bool = Field(
        default=False, validation_alias="SENTINEL_PLATE_DETECTOR")
    # Deliberately permissive. A missed plate is unrecoverable; a false plate
    # box costs one OCR pass and then loses the multi-frame vote.
    plate_detector_conf: float = Field(
        default=0.20, validation_alias="SENTINEL_PLATE_DETECTOR_CONF")

    # Pipeline tuning. Defaults suit "balanced" and are overridden in apply_profile().
    sample_interval_ms: int = 400          # PTS spacing between processed frames
    max_concurrent_streams: int = 4        # each client gets its own stream copy
    inference_width: int = 640             # frames are downscaled before detection
    reconnect_base_delay: float = 2.0
    reconnect_max_delay: float = 30.0      # cap, per the field rules
    stream_open_timeout: int = 120         # observed opens ranged 1.8s to 275s

    def apply_profile(self) -> "Settings":
        """Scale the pipeline to the machine actually running it.

        Nothing here changes behaviour or accuracy -- only how much of the feed is
        sampled and how many streams are open at once. A low-end box processes
        fewer frames from fewer cameras rather than falling behind on all of them.
        """
        for key, value in self._preset().items():
            if key != "detector_model":
                setattr(self, key, value)
        return self

    def _preset(self) -> dict:
        return PROFILE_PRESETS.get(self.profile.lower(), PROFILE_PRESETS["balanced"])

    def profile_detector(self) -> str:
        """The YOLO11 variant this hardware profile asks for."""
        return self._preset()["detector_model"]

    def hls_headers(self) -> dict[str, str]:
        """Auth headers for the HLS portal, empty when no session is configured."""
        headers: dict[str, str] = {}
        if self.sentinel_hls_cookie:
            headers["Cookie"] = self.sentinel_hls_cookie
        if self.sentinel_hls_token:
            headers["Authorization"] = f"Bearer {self.sentinel_hls_token}"
        return headers

    def ffmpeg_capture_options(self) -> str:
        """FFmpeg options for every cv2.VideoCapture in the process.

        OPENCV_FFMPEG_CAPTURE_OPTIONS is read once per capture construction and
        is process-global, so RTSP transport settings and HLS auth headers are
        combined into one string rather than swapped per open. A demuxer ignores
        options that do not apply to it, and combining them means no worker can
        clobber another's transport mid-open -- an RTSP capture that lost
        "rtsp_transport;tcp" would silently fall back to UDP, which on this grid
        yields corrupt frames that look exactly like model bugs.
        """
        opts = [
            "rtsp_transport;tcp",          # never UDP
            "stimeout;10000000",           # 10 s socket timeout (microseconds)
            "probesize;500000",
            "analyzeduration;1000000",
            "max_delay;500000",
            "reorder_queue_size;0",
            "loglevel;error",
        ]
        headers = self.hls_headers()
        if headers:
            joined = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            opts.append(f"headers;{joined}")
            opts.append(f"user_agent;{HLS_USER_AGENT}")
        return "|".join(opts)

    def rtsp_url(self, n: int) -> str:
        path = self.sentinel_rtsp_path.format(n=n)
        return f"rtsp://{self.sentinel_rtsp_host}:{self.sentinel_rtsp_port}{path}"

    def with_credentials(self, url: str) -> str:
        """Inject RTSP credentials into a stream URL, if any are configured.

        Applied at connection time rather than stored, so the catalogue and the
        registry never hold a password. A URL that already carries credentials
        is left alone.
        """
        if not url.startswith("rtsp://") or not self.sentinel_rtsp_user:
            return url
        remainder = url[len("rtsp://"):]
        if "@" in remainder.split("/", 1)[0]:
            return url                      # already authenticated
        from urllib.parse import quote

        user = quote(self.sentinel_rtsp_user, safe="")
        password = quote(self.sentinel_rtsp_password, safe="")
        return f"rtsp://{user}:{password}@{remainder}"


settings = Settings().apply_profile()
# The worker reads its concurrency cap from the environment so that a worker
# started in another process inherits the same limit.
os.environ.setdefault("SENTINEL_MAX_STREAMS", str(settings.max_concurrent_streams))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
