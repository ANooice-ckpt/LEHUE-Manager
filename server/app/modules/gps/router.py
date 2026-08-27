from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.security import require_admin
from . import service, traccar_service

router = APIRouter()
security = HTTPBasic(auto_error=False)


@router.post("/api/v1/gps/owntracks")
async def owntracks_ingest(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
    x_limit_u: str | None = Header(default=None, alias="X-Limit-U"),
    x_limit_d: str | None = Header(default=None, alias="X-Limit-D"),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="OwnTracks Basic Auth required.")
    participant_id = credentials.username.strip()
    if not service.authenticate_participant(participant_id, credentials.password):
        raise HTTPException(status_code=403, detail="Invalid participant credential.")

    try:
        payload: Any = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="OwnTracks payload must be a JSON object.")

    # OwnTracks may send X-Limit-U as its configured username. It is diagnostic only;
    # participant identity is taken from authenticated Basic Auth credentials.
    try:
        service.ingest(participant_id, payload, x_limit_u, x_limit_d)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # OwnTracks HTTP endpoints accept an empty JSON command array.
    return []


@router.post("/api/v1/gps/traccar", response_class=PlainTextResponse)
async def traccar_ingest(request: Request):
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("application/x-www-form-urlencoded"):
        raise HTTPException(status_code=415, detail="Traccar form-urlencoded payload required.")

    body = await request.body()
    if len(body) > 16_384:
        raise HTTPException(status_code=413, detail="Traccar payload too large.")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Traccar form payload: {exc}")
    payload = {key: values[-1] if values else "" for key, values in parsed.items()}

    tracker_id = str(payload.get("id") or "").strip()
    try:
        participant_id, secret = tracker_id.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Traccar device credential.")
    participant_id = participant_id.strip()
    if not participant_id or not secret:
        raise HTTPException(status_code=401, detail="Invalid Traccar device credential.")
    if not service.authenticate_participant(participant_id, secret):
        raise HTTPException(status_code=403, detail="Invalid participant credential.")

    try:
        traccar_service.ingest(participant_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Traccar treats any 2xx response as a successful delivery acknowledgement.
    return PlainTextResponse("OK", status_code=200)


@router.get("/api/v1/admin/gps/status/{participant_id}", dependencies=[Depends(require_admin)])
def gps_status(participant_id: str, date: str | None = None):
    if not service.participant_exists(participant_id):
        raise HTTPException(status_code=404, detail="Unknown participant.")
    try:
        return service.qc_summary(participant_id, date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD in the configured study timezone.")


@router.get("/api/v1/admin/gps/export/{participant_id}.csv", dependencies=[Depends(require_admin)])
def gps_export(participant_id: str, date: str | None = None):
    if not service.participant_exists(participant_id):
        raise HTTPException(status_code=404, detail="Unknown participant.")
    try:
        text = service.export_csv(participant_id, date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD in the configured study timezone.")
    filename = f"{participant_id}_gps{('_'+date) if date else ''}.csv"
    return PlainTextResponse(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
