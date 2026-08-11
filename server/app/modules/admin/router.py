from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from app.core import web_security
from app.core.config import settings
from app.modules.light import service as light_service
from app.core.web_security import (
    authenticate_admin,
    delete_session,
    new_session,
    require_operator,
    require_operator_write,
    require_pi,
    require_pi_write,
)
from . import service
from .backup import create_system_backup

router = APIRouter()
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
INDEX = WEB_ROOT / "index.html"
LEAFLET_ROOT = WEB_ROOT / "vendor" / "leaflet-1.9.4"


def _is_local_request(request: Request) -> bool:
    return (request.url.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def _set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        "lehue_session",
        sid,
        httponly=True,
        secure=(settings.domain not in {"localhost", "127.0.0.1"}),
        samesite="strict",
        max_age=43200,
        path="/",
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    return FileResponse(INDEX)


@router.get("/admin/style.css")
def admin_css():
    return FileResponse(WEB_ROOT / "style.css", media_type="text/css")


@router.get("/admin/app.js")
def admin_js():
    return FileResponse(WEB_ROOT / "app.js", media_type="application/javascript")


@router.get("/admin/vendor/leaflet.css")
def leaflet_css():
    return FileResponse(LEAFLET_ROOT / "leaflet.css", media_type="text/css")


@router.get("/admin/vendor/leaflet.js")
def leaflet_js():
    return FileResponse(LEAFLET_ROOT / "leaflet.js", media_type="application/javascript")


@router.get("/api/v1/web/setup-status")
def setup_status(request: Request):
    return web_security.setup_status(local_request=_is_local_request(request))


@router.post("/api/v1/web/setup")
async def first_setup(request: Request, response: Response):
    data = await request.json()
    try:
        row = web_security.bootstrap_first_pi(
            str(data.get("username") or ""),
            str(data.get("password") or ""),
            str(data.get("display_name") or ""),
            str(data.get("setup_token") or ""),
            local_request=_is_local_request(request),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    sid, csrf = new_session(row["username"])
    _set_session_cookie(response, sid)
    service.audit(row["username"], "system.bootstrap", "admin_user", row["username"])
    return {
        "ok": True,
        "csrf_token": csrf,
        "user": {"username": row["username"], "display_name": row["display_name"], "role": row["role"]},
    }


@router.post("/api/v1/web/login")
async def login(request: Request, response: Response):
    data = await request.json()
    row = authenticate_admin(str(data.get("username") or ""), str(data.get("password") or ""))
    if not row:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    sid, csrf = new_session(row["username"])
    _set_session_cookie(response, sid)
    return {"ok": True, "csrf_token": csrf, "user": {"username":row["username"],"display_name":row["display_name"],"role":row["role"]}}


@router.post("/api/v1/web/logout")
def logout(response: Response, operator=Depends(require_operator), lehue_session: str | None = Cookie(default=None)):
    delete_session(lehue_session)
    response.delete_cookie("lehue_session", path="/")
    return {"ok": True}


@router.get("/api/v1/web/me")
def me(operator=Depends(require_operator)):
    return {"username":operator.username,"display_name":operator.display_name,"role":operator.role,"csrf_token":operator.csrf_token}


@router.get("/api/v1/web/users")
def users(operator=Depends(require_pi)):
    return web_security.list_admin_users()


@router.post("/api/v1/web/users")
async def create_user(request: Request, operator=Depends(require_pi_write)):
    data = await request.json()
    password = str(data.get("password") or "") or secrets.token_urlsafe(18)
    try:
        web_security.create_admin_user(
            str(data.get("username") or ""),
            password,
            str(data.get("role") or "ra"),
            str(data.get("display_name") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    username = str(data.get("username") or "").strip().lower()
    service.audit(operator.username, "admin_user.create", "admin_user", username, {"role": str(data.get("role") or "ra")})
    return {"ok": True, "username": username, "password": password}


@router.post("/api/v1/web/users/{username}/active")
async def user_active(username: str, request: Request, operator=Depends(require_pi_write)):
    data = await request.json()
    try:
        web_security.set_admin_active(username, bool(data.get("is_active")), operator.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    service.audit(operator.username, "admin_user.active", "admin_user", username, {"is_active": bool(data.get("is_active"))})
    return {"ok": True}


@router.post("/api/v1/web/users/{username}/reset-password")
async def reset_password(username: str, request: Request, operator=Depends(require_pi_write)):
    data = await request.json()
    password = str(data.get("password") or "") or secrets.token_urlsafe(18)
    try:
        web_security.reset_admin_password(username, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    service.audit(operator.username, "admin_user.password_reset", "admin_user", username)
    return {"ok": True, "username": username.strip().lower(), "password": password, "session_invalidated": True}


@router.get("/api/v1/web/backup")
def download_backup(operator=Depends(require_pi)):
    zip_path, temp_dir = create_system_backup()
    service.audit(operator.username, "system.backup.download")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=Path(zip_path).name,
        background=BackgroundTask(shutil.rmtree, temp_dir, True),
    )


@router.get("/api/v1/web/dashboard")
def dashboard(operator=Depends(require_operator)):
    return service.dashboard()


@router.get("/api/v1/web/candidates")
def candidates(operator=Depends(require_operator)):
    return service.list_candidates()


@router.post("/api/v1/web/candidates")
async def create_candidate(request: Request, operator=Depends(require_operator_write)):
    return {"candidate_uid": service.add_candidate(await request.json(), operator.username)}


@router.post("/api/v1/web/candidates/import-s0")
async def import_s0(request: Request, operator=Depends(require_operator_write)):
    try:
        return service.import_s0_file(await request.json(), operator.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/web/candidates/{candidate_uid}/promote")
async def promote(candidate_uid: str, request: Request, operator=Depends(require_operator_write)):
    try:
        return {"participant_id": service.promote_candidate(candidate_uid, await request.json(), operator.username)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/subjects")
def subjects(operator=Depends(require_operator)):
    return service.list_subjects()


@router.get("/api/v1/web/subjects/{participant_id}/gps-track")
def subject_gps_track(participant_id: str, hours: int = 12, operator=Depends(require_operator)):
    try:
        return service.gps_track(participant_id, hours)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/web/subjects/{participant_id}/gps-credential")
def gps_credential(participant_id: str, operator=Depends(require_operator_write)):
    try:
        return {"participant_id":participant_id,"password":service.create_or_rotate_gps_credential(participant_id, operator.username)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/web/subjects/{participant_id}/portal")
def participant_portal(participant_id: str, operator=Depends(require_operator_write)):
    try:
        return {"participant_id": participant_id, "path": service.create_portal_link(participant_id, operator.username)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/web/subjects/{participant_id}/start")
async def start_subject(participant_id: str, request: Request, operator=Depends(require_operator_write)):
    try:
        service.start_subject(participant_id, await request.json(), operator.username)
        return {"ok":True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/devices")
def devices(operator=Depends(require_operator)):
    return service.list_devices()


@router.post("/api/v1/web/devices")
async def device_upsert(request: Request, operator=Depends(require_operator_write)):
    try:
        return {"pack_id":service.upsert_device(await request.json(), operator.username)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/incidents")
def incidents(operator=Depends(require_operator)):
    return service.list_incidents()


@router.get("/api/v1/web/lighting")
def lighting_uploads(participant_id: str = "", date_local: str = "", operator=Depends(require_operator)):
    return service.list_lighting_uploads(participant_id, date_local)


@router.post("/api/v1/web/lighting/upload")
async def lighting_upload(participant_id: str, date_local: str, filename: str, request: Request, operator=Depends(require_operator_write)):
    path = None
    try:
        path = await light_service.request_to_temp(request, filename)
        return service.upload_lighting_path(participant_id, date_local, filename, path, operator.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@router.get("/api/v1/web/subjects/{participant_id}/credentials")
def participant_credentials(participant_id: str, operator=Depends(require_operator)):
    try:
        return service.reveal_credentials(participant_id, operator.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/daily-qc")
def daily_qc(operator=Depends(require_operator)):
    return service.daily_qc(False)


@router.post("/api/v1/web/daily-qc/run")
def daily_qc_run(operator=Depends(require_operator_write)):
    return service.daily_qc(True, operator.username)


@router.post("/api/v1/web/incidents")
async def incident_add(request: Request, operator=Depends(require_operator_write)):
    return {"incident_uid":service.add_incident(await request.json(), operator.username)}


@router.post("/api/v1/web/incidents/{incident_uid}/status")
async def incident_status(incident_uid: str, request: Request, operator=Depends(require_operator_write)):
    try:
        service.update_incident_status(incident_uid, str((await request.json()).get("status") or ""), operator.username)
        return {"ok":True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/architecture")
def architecture(operator=Depends(require_operator)):
    return service.architecture()


@router.get("/api/v1/web/data-sources")
def data_sources(operator=Depends(require_operator)):
    return service.data_sources()
