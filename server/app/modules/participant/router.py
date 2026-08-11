from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.modules.light import service as light_service
from . import service

router = APIRouter()
PORTAL_HTML = Path(__file__).resolve().parents[2] / "web" / "participant.html"


@router.get("/p/{portal_token}", response_class=HTMLResponse)
def participant_page(portal_token: str):
    return FileResponse(PORTAL_HTML)


@router.get("/api/v1/portal/{portal_token}")
def participant_state(portal_token: str):
    try:
        return service.portal_state(portal_token)
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
            str(data.get("calendar_date_local") or ""),
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
