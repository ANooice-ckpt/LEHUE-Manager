from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pathlib import Path

from app.core.web_security import authenticate_admin, delete_session, new_session, require_operator, require_operator_write
from app.core.config import settings
from . import service

router = APIRouter()
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
INDEX = WEB_ROOT / "index.html"


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    return FileResponse(INDEX)


@router.post("/api/v1/web/login")
async def login(request: Request, response: Response):
    data = await request.json()
    row = authenticate_admin(str(data.get("username") or ""), str(data.get("password") or ""))
    if not row:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    sid, csrf = new_session(row["username"])
    response.set_cookie("lehue_session", sid, httponly=True, secure=(settings.domain not in {"localhost", "127.0.0.1"}), samesite="strict", max_age=43200, path="/")
    return {"ok": True, "csrf_token": csrf, "user": {"username":row["username"],"display_name":row["display_name"],"role":row["role"]}}


@router.post("/api/v1/web/logout")
def logout(response: Response, operator=Depends(require_operator), lehue_session: str | None = Cookie(default=None)):
    delete_session(lehue_session)
    response.delete_cookie("lehue_session", path="/")
    return {"ok": True}


@router.get("/api/v1/web/me")
def me(operator=Depends(require_operator)):
    return {"username":operator.username,"display_name":operator.display_name,"role":operator.role,"csrf_token":operator.csrf_token}


@router.get("/api/v1/web/dashboard")
def dashboard(operator=Depends(require_operator)):
    return service.dashboard()


@router.get("/api/v1/web/candidates")
def candidates(operator=Depends(require_operator)):
    return service.list_candidates()


@router.post("/api/v1/web/candidates")
async def create_candidate(request: Request, operator=Depends(require_operator_write)):
    return {"candidate_uid": service.add_candidate(await request.json(), operator.username)}


@router.post("/api/v1/web/candidates/{candidate_uid}/promote")
async def promote(candidate_uid: str, request: Request, operator=Depends(require_operator_write)):
    try:
        return {"participant_id": service.promote_candidate(candidate_uid, await request.json(), operator.username)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/v1/web/subjects")
def subjects(operator=Depends(require_operator)):
    return service.list_subjects()


@router.post("/api/v1/web/subjects/{participant_id}/gps-credential")
def gps_credential(participant_id: str, operator=Depends(require_operator_write)):
    try:
        return {"participant_id":participant_id,"password":service.ensure_gps_credential(participant_id, operator.username)}
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
