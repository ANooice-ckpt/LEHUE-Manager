from __future__ import annotations

from app.core.db import db
from app.core.security import decrypt_credential
from app.core.traccar import public_config
from . import service


def config_for_portal(token: str) -> dict[str, object]:
    subject = service._resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")

    preparation_mode = (
        subject["status"] in {"scheduled", "ready"}
        and bool(subject["preparation_started_at_utc"])
    )
    if subject["status"] != "running" and not preparation_mode:
        return {"available": False, "platform": "android"}

    with db() as conn:
        credential = conn.execute(
            "SELECT secret_ciphertext FROM participants WHERE participant_id=? AND is_active=1",
            (subject["participant_id"],),
        ).fetchone()
    if not credential or not credential["secret_ciphertext"]:
        return {"available": False, "platform": "android"}

    secret = decrypt_credential(credential["secret_ciphertext"])
    return public_config(subject["participant_id"], secret)
