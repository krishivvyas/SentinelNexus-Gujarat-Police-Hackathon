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
        presets = {
            # Sized from the loaded case, not a clean benchmark process.
            # Detection costs ~233 ms per 1080p frame at 2 threads in a bare
            # process, but ~775 ms once the full application (API, OCR, ORM) is
            # resident in the same process -- which is exactly how it is deployed.
            # That yields ~1.6 detections/s, so a 3 s sample across 2 cameras
            # (0.67/s required) leaves a genuine 2.4x margin. Earlier values of
            # 1 s and 2 s only looked sufficient against the clean-process number.
            "low":      {"sample_interval_ms": 3000, "max_concurrent_streams": 2,
                         "inference_width": 512},
            "balanced": {"sample_interval_ms": 400, "max_concurrent_streams": 4,
                         "inference_width": 640},
            "high":     {"sample_interval_ms": 250, "max_concurrent_streams": 8,
                         "inference_width": 736},
        }
        for key, value in presets.get(self.profile.lower(), presets["balanced"]).items():
            setattr(self, key, value)
        return self

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
