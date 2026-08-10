from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.db import db, init_db
from app.core.identity_db import init_identity_db
from app.modules.gps.router import router as gps_router
from app.modules.admin.router import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_identity_db()
    yield


app = FastAPI(
    title=f"{settings.project_name} Server",
    version=settings.app_version,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(gps_router)
app.include_router(admin_router)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/admin") or request.url.path.startswith("/api/v1/web"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def root():
    return {
        "project": settings.project_name,
        "service": "lehue-manager-backend",
        "version": settings.app_version,
        "implemented_modules": ["gps", "web_admin"],
        "study_timezone": settings.study_timezone,
        "reserved_modules": ["light", "questionnaire", "qc"],
    }


@app.get("/health")
def health():
    try:
        with db() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM gps_locations").fetchone()["n"]
            raw_count = conn.execute("SELECT COUNT(*) AS n FROM raw_events").fetchone()["n"]
            archive_failures = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_events WHERE archive_ok=0"
            ).fetchone()["n"]
            last = conn.execute(
                "SELECT received_at_utc FROM raw_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "status": "ok" if archive_failures == 0 else "degraded",
            "project": settings.project_name,
            "version": settings.app_version,
            "database": "ok",
            "gps_location_count": count,
            "raw_event_count": raw_count,
            "raw_archive_failures": archive_failures,
            "last_event_received_at_utc": last["received_at_utc"] if last else None,
        }
    except sqlite3.Error as exc:
        return {
            "status": "error",
            "project": settings.project_name,
            "version": settings.app_version,
            "database": "error",
            "detail": str(exc),
        }
