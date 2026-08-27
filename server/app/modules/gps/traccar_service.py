from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.core.db import db, refresh_subject_ready
from . import service as gps_service

KNOTS_TO_KMH = 1.852


def _number(payload: dict[str, Any], key: str, *, required: bool = False) -> float | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise ValueError(f"Traccar field '{key}' is required.")
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Traccar field '{key}' must be numeric.") from exc


def _recorded_at(payload: dict[str, Any]) -> datetime:
    raw = _number(payload, "timestamp", required=True)
    assert raw is not None
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise ValueError("Traccar field 'timestamp' must be a valid Unix timestamp in seconds.") from exc


def _sanitized_payload(participant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    clean["id"] = participant_id
    clean["_credential_redacted"] = True
    return clean


def _event_uid(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(gps_service.canonical_json(payload).encode("utf-8")).hexdigest()
    return f"traccar:{digest}"


def ingest(
    participant_id: str,
    payload: dict[str, Any],
    received_override: datetime | None = None,
) -> dict[str, Any]:
    received = received_override.astimezone(timezone.utc) if received_override else gps_service.utc_now()
    recorded = _recorded_at(payload)
    lat = _number(payload, "lat", required=True)
    lon = _number(payload, "lon", required=True)
    assert lat is not None and lon is not None
    if not -90 <= lat <= 90:
        raise ValueError("Traccar latitude is outside [-90, 90].")
    if not -180 <= lon <= 180:
        raise ValueError("Traccar longitude is outside [-180, 180].")

    accuracy = _number(payload, "accuracy")
    altitude = _number(payload, "altitude")
    speed_knots = _number(payload, "speed")
    bearing = _number(payload, "bearing")
    battery = _number(payload, "batt")
    if accuracy is not None and accuracy < 0:
        raise ValueError("Traccar accuracy cannot be negative.")
    if speed_knots is not None and speed_knots < 0:
        raise ValueError("Traccar speed cannot be negative.")
    if battery is not None and not 0 <= battery <= 100:
        raise ValueError("Traccar battery percentage is outside [0, 100].")

    sanitized = _sanitized_payload(participant_id, payload)
    uid = _event_uid(sanitized)
    raw = gps_service.canonical_json(sanitized)
    recorded_iso = gps_service.iso_utc(recorded)
    received_iso = gps_service.iso_utc(received)

    try:
        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO raw_events(
                    participant_id,event_uid,owntracks_event_id,message_type,
                    recorded_at_utc,created_at_utc,received_at_utc,
                    headers_user,headers_device,raw_json,archive_ok
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    participant_id, uid, None, "location",
                    recorded_iso, None, received_iso,
                    None, "traccar", raw,
                ),
            )
            raw_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO gps_locations(
                    raw_event_id,participant_id,recorded_at_utc,created_at_utc,received_at_utc,
                    lat,lon,accuracy_m,altitude_m,vertical_accuracy_m,velocity_kmh,course_deg,
                    battery_pct,battery_status,connection,monitoring_mode,trigger,source,owntracks_tid
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    raw_id, participant_id, recorded_iso, None, received_iso,
                    lat, lon, accuracy, altitude, None,
                    speed_knots * KNOTS_TO_KMH if speed_knots is not None else None,
                    bearing, int(round(battery)) if battery is not None else None,
                    None, None, None, payload.get("alarm") or None, "traccar", None,
                ),
            )
            refresh_subject_ready(conn, participant_id, received_iso)
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: raw_events.participant_id, raw_events.event_uid" in str(exc):
            return {"stored": False, "duplicate": True, "event_uid": uid}
        raise

    archive_record = {
        "participant_id": participant_id,
        "server_received_at_utc": received_iso,
        "headers_user": None,
        "headers_device": "traccar",
        "payload": sanitized,
    }
    archived = gps_service._append_raw_archive(archive_record, received)
    if archived:
        with db() as conn:
            conn.execute("UPDATE raw_events SET archive_ok=1 WHERE id=?", (raw_id,))

    return {
        "stored": True,
        "duplicate": False,
        "event_uid": uid,
        "message_type": "location",
        "archive_ok": archived,
    }
