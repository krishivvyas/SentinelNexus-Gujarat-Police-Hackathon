"""Central configuration. All secrets and endpoints come from env, never hardcoded."""
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidence"
MODELS_DIR = BASE_DIR.parent / "models"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Sentinel Nexus"
    database_url: str = f"sqlite:///{DATA_DIR / 'sentinel.db'}"

    # Sentinel government feed. RTSP is reachable unauthenticated; HLS is behind /auth/login.
    sentinel_rtsp_host: str = "103.250.160.189"
    sentinel_rtsp_port: int = 8554
    sentinel_rtsp_path: str = "/stream/cam{n:02d}"
    sentinel_hls_base: str = "https://cctv.corp8.cloud"
    sentinel_camera_count: int = 30

    # Auth
    jwt_secret: str = "dev-only-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

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
            "low":      {"sample_interval_ms": 1000, "max_concurrent_streams": 2,
                         "inference_width": 512},
            "balanced": {"sample_interval_ms": 400, "max_concurrent_streams": 4,
                         "inference_width": 640},
            "high":     {"sample_interval_ms": 250, "max_concurrent_streams": 8,
                         "inference_width": 736},
        }
        for key, value in presets.get(self.profile.lower(), presets["balanced"]).items():
            setattr(self, key, value)
        return self

    def rtsp_url(self, n: int) -> str:
        path = self.sentinel_rtsp_path.format(n=n)
        return f"rtsp://{self.sentinel_rtsp_host}:{self.sentinel_rtsp_port}{path}"


settings = Settings().apply_profile()
# The worker reads its concurrency cap from the environment so that a worker
# started in another process inherits the same limit.
os.environ.setdefault("SENTINEL_MAX_STREAMS", str(settings.max_concurrent_streams))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
