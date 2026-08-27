from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.db import db
from app.core.web_security import require_operator_write
from . import service

router = APIRouter()


def skip_preparation_test(participant_id: str, operator: str) -> dict[str, object]:
    """Manually promote an in-preparation scheduled subject to Ready without fabricating test data."""
    now = service.now_iso()
    with db() as conn:
        subject = conn.execute(
            "SELECT status,preparation_started_at_utc FROM study_subjects WHERE participant_id=?",
            (participant_id,),
        ).fetchone()
        if not subject:
            raise ValueError("participant not found")
        if subject["status"] != "scheduled" or not subject["preparation_started_at_utc"]:
            raise ValueError("only a participant currently in preparation testing can skip the test")
        updated = conn.execute(
            """UPDATE study_subjects
               SET status='ready',ready_at_utc=?,updated_at_utc=?
               WHERE participant_id=? AND status='scheduled' AND preparation_started_at_utc<>''""",
            (now, now, participant_id),
        )
        if updated.rowcount != 1:
            raise ValueError("participant preparation state changed; refresh and try again")

    service.audit(
        operator,
        "participant.prepare.skip",
        "participant",
        participant_id,
        {"readiness_override": True, "test_data_fabricated": False},
    )
    return {
        "ok": True,
        "participant_id": participant_id,
        "status": "ready",
        "ready_at_utc": now,
        "readiness_override": True,
    }


@router.post("/api/v1/web/subjects/{participant_id}/skip-preparation-test")
def skip_preparation_test_route(participant_id: str, operator=Depends(require_operator_write)):
    try:
        return skip_preparation_test(participant_id, operator.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
