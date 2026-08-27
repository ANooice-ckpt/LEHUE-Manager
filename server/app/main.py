from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from app.core.config import settings
from app.core.db import db, init_db
from app.core.identity_db import init_identity_db
from app.core.test_seed import install_test_seed_if_empty
from app.modules.gps.router import router as gps_router
from app.modules.admin.router import router as admin_router
from app.modules.admin.skip_preparation import router as skip_preparation_router
from app.modules.participant.router import router as participant_router
from app.modules.light import service as light_service


WEB_ROOT = Path(__file__).resolve().parent / "web"
ADMIN_APP_JS = WEB_ROOT / "app.js"
ADMIN_TRACCAR_JS = WEB_ROOT / "admin_traccar.js"
ADMIN_SKIP_PREPARATION_JS = WEB_ROOT / "admin_skip_preparation.js"


async def _scheduled_daily_qc() -> None:
    while True:
        await asyncio.sleep(settings.daily_qc_interval_seconds)
        try:
            await asyncio.to_thread(light_service.run_daily_qc, "system:scheduler")
        except Exception:
            # A failed scheduled pass must not stop the API or future passes.
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_test_seed_if_empty()
    init_db()
    init_identity_db()
    qc_task = None
    if settings.daily_qc_interval_seconds > 0:
        qc_task = asyncio.create_task(_scheduled_daily_qc())
    try:
        yield
    finally:
        if qc_task:
            qc_task.cancel()
            try:
                await qc_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title=f"{settings.project_name} Server [{settings.runtime_env.upper()}]",
    version=settings.app_version,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/admin/app.js")
def admin_js_bundle():
    """Serve the existing admin client plus small additive admin controls."""
    content = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ADMIN_APP_JS, ADMIN_TRACCAR_JS, ADMIN_SKIP_PREPARATION_JS)
    )
    return Response(content=content, media_type="application/javascript", headers={"Cache-Control": "no-store"})


# Keep this route ahead of the admin router's original /admin/app.js route so the
# additive admin controls are included without changing the existing large client.
app.include_router(gps_router)
app.include_router(admin_router)
app.include_router(skip_preparation_router)
app.include_router(participant_router)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if (
        request.url.path.startswith("/admin")
        or request.url.path.startswith("/api/v1/web")
        or request.url.path.startswith("/p/")
        or request.url.path.startswith("/api/v1/portal/")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def root():
    return FileResponse(WEB_ROOT / "public.html", media_type="text/html")


@app.get("/public.css")
def public_css():
    return FileResponse(WEB_ROOT / "public.css", media_type="text/css")


@app.get("/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={"status": "error"})
