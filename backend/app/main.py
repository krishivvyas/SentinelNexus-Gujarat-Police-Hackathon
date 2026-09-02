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


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")
