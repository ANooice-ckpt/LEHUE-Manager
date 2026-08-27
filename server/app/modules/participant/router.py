from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from app.core.owntracks import config_filename

from app.modules.light import service as light_service
from . import service, traccar_config

router = APIRouter()
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
PORTAL_HTML = WEB_ROOT / "participant.html"
PORTAL_GPS_CLIENTS_JS = WEB_ROOT / "participant_gps_clients.js"
PORTAL_PAGE = PORTAL_HTML.read_text(encoding="utf-8").replace(
    "</body>",
    '<script src="/participant-gps-clients.js"></script>\n</body>',
)


@router.get("/p/{portal_token}", response_class=HTMLResponse)
def participant_page(portal_token: str):
    return HTMLResponse(PORTAL_PAGE)


@router.get("/participant-gps-clients.js")
def participant_gps_clients_js():
    return FileResponse(
        PORTAL_GPS_CLIENTS_JS,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/v1/portal/{portal_token}")
def participant_state(portal_token: str):
    try:
        return service.portal_state(portal_token)
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")


@router.get("/api/v1/portal/{portal_token}/traccar/config")
def participant_traccar_config(portal_token: str):
    try:
        return traccar_config.config_for_portal(portal_token)
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")


@router.post("/api/v1/portal/{portal_token}/questionnaires/{form_key}")
async def questionnaire_submit(portal_token: str, form_key: str, request: Request):
    data = await request.json()
    try:
        return service.submit_questionnaire(
            portal_token,
            form_key,
            data.get("answers") or {},
            str(data.get("date_local") or ""),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/portal/{portal_token}/lighting")
async def lighting_submit(portal_token: str, date_local: str, filename: str, request: Request):
    path = None
    try:
        path = await light_service.request_to_temp(request, filename)
        return service.submit_lighting_path(portal_token, date_local, filename, path)
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@router.get("/api/v1/portal/{portal_token}/owntracks/{platform}")
def participant_owntracks_config(portal_token: str, platform: str):
    try:
        participant_id, config = service.owntracks_download(portal_token, platform)
        return Response(
            content=json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{config_filename(participant_id, platform)}"'},
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/portal/{portal_token}/lighting/direct")
async def lighting_direct_prepare(portal_token: str, request: Request):
    data = await request.json()
    try:
        return service.prepare_lighting_direct(
            portal_token,
            str(data.get("date_local") or ""),
            str(data.get("filename") or ""),
            int(data.get("size_bytes") or 0),
            str(data.get("sha256") or ""),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/v1/portal/{portal_token}/lighting/direct/{upload_uid}/complete")
def lighting_direct_complete(portal_token: str, upload_uid: str):
    try:
        return service.complete_lighting_direct(portal_token, upload_uid)
    except LookupError:
        raise HTTPException(status_code=404, detail="Invalid or expired participant link")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
