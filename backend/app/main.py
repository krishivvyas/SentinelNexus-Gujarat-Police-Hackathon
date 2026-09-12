"""Sentinel Nexus application entry point.

Serves the REST API, the live alert WebSocket, and the command-centre UI.

The UI is a static page served from this same process -- no Node toolchain, no
bundler, no node_modules. That keeps the whole platform to one Python process on
a low-end machine, which is the deployment constraint that matters here.

Run:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import SessionLocal, init_db
from .services import alerts as alert_service
from .services import ingest as ingest_service
from .services import security
from .services import watchlist as wl

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sentinel")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # The alert broadcaster publishes from ingest worker threads, so it needs a
    # handle on the running loop to hand messages back safely.
    alert_service.broadcaster.bind_loop(asyncio.get_running_loop())

    with SessionLocal() as db:
        created = security.seed_default_users(db)
        if created:
            log.info("created default users: %s", ", ".join(created))
        if not wl.list_entries(db):
            count = wl.seed_demo_watchlist(db)
            log.info("seeded representative watchlist (%d entries)", count)

    log.info("Sentinel Nexus ready | profile=%s | streams=%d | sample=%dms",
             settings.profile, settings.max_concurrent_streams,
             settings.sample_interval_ms)
    try:
        yield
    finally:
        ingest_service.manager.stop_all()
        log.info("ingest stopped, shutting down")


app = FastAPI(
    title="Sentinel Nexus",
    description="Unified CCTV interoperability and intelligence platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to the operator network in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "profile": settings.profile}


class RevalidatingStatics(StaticFiles):
    """Static files that must be revalidated before they are reused.

    Starlette's StaticFiles sends ``ETag`` and ``Last-Modified`` but **no**
    ``Cache-Control``. A browser given a validator and no freshness directive
    falls back to *heuristic* caching -- typically a tenth of the file's age --
    and during that window it does not revalidate at all. It just serves what it
    has.

    For a normal site that is a bandwidth optimisation. Here it is a
    correctness bug, and a nasty one: this UI is a set of ES modules that have
    to agree with each other. A browser holding a stale ``basemap.js`` beside a
    fresh ``theme.js`` produces an interface that is half-updated and blames
    nobody -- the symptom is a light command centre with a dark map, with no
    error anywhere to say why. That exact failure is what this class exists to
    stop; it cost a debugging session to find once.

    ``no-cache`` does not mean "do not store". It means "store it, but ask
    before reusing it", so the conditional request still answers 304 with an
    empty body. On an isolated operator network that is effectively free, and it
    is the right side of the trade: this platform is specified to run from one
    process on one machine, where a round trip costs nothing and a stale module
    costs an afternoon.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _page(name: str) -> FileResponse:
    """An application shell page, under the same revalidation rule as the
    modules it loads -- a cached index.html pinning an old module graph is the
    same bug one level up."""
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


if STATIC_DIR.exists():
    app.mount("/static", RevalidatingStatics(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return _page("index.html")

    @app.get("/wall")
    def wall():
        """The video wall, as its own page rather than a panel.

        The command centre is for investigating one thing and the wall is for
        watching the estate; those want opposite layouts, and sharing one screen
        meant the wall got 210 px of it. Separate pages also mean an operator can
        put the wall on a second display and keep the map on the first.
        """
        return _page("wall.html")
