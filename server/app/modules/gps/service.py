from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from statistics import median
from typing import Any

from app.core.config import settings
from app.core.db import db, refresh_subject_ready
from app.core.security import verify_secret

_archive_lock = threading.Lock()
_auth_cache_lock = threading.Lock()
_auth_cache: dict[tuple[str, str, str], float] = {}
TARGET_SAMPLE_SECONDS = 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def from_unix(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_uid(payload: dict[str, Any]) -> str:
    event_id = payload.get("_id")
    if event_id:
        return f"owntracks:{event_id}"
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def authenticate_participant(participant_id: str, secret: str) -> bool:
    with db() as conn:
        row = conn.execute(
            """SELECT p.secret_salt,p.secret_hash,p.is_active
               FROM participants p JOIN study_subjects s ON s.participant_id=p.participant_id
               WHERE p.participant_id=?
                 AND (s.status='running' OR (s.status IN ('scheduled','ready') AND s.preparation_started_at_utc<>''))""",
            (participant_id,),
        ).fetchone()
    if not row or not row["is_active"]:
        return False
    now = time.monotonic()
    secret_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    cache_key = (participant_id, row["secret_hash"], secret_fingerprint)
    with _auth_cache_lock:
        if _auth_cache.get(cache_key, 0) > now:
            return True
    valid = verify_secret(secret, row["secret_salt"], row["secret_hash"])
    if valid and settings.gps_auth_cache_seconds > 0:
        with _auth_cache_lock:
            if len(_auth_cache) >= 2048:
                _auth_cache.clear()
            _auth_cache[cache_key] = now + settings.gps_auth_cache_seconds
    return valid


def _append_raw_archive(record: dict[str, Any], received: datetime) -> bool:
    study_day = received.astimezone(ZoneInfo(settings.study_timezone)).strftime("%Y-%m-%d")
    path = settings.raw_archive_dir / f"{study_day}.jsonl"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _archive_lock:
            with open(path, "a", encoding="utf-8", buffering=1) as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return True
    except OSError:
        return False


def ingest(
    participant_id: str,
    payload: dict[str, Any],
    headers_user: str | None,
    headers_device: str | None,
    received_override: datetime | None = None,
) -> dict[str, Any]:
    received = received_override.astimezone(timezone.utc) if received_override else utc_now()
    msg_type = str(payload.get("_type") or "unknown")
    rec_dt = from_unix(payload.get("tst"))
    created_dt = from_unix(payload.get("created_at"))
    uid = event_uid(payload)
    raw = canonical_json(payload)

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
                    participant_id, uid, payload.get("_id"), msg_type,
                    iso_utc(rec_dt), iso_utc(created_dt), iso_utc(received),
                    headers_user, headers_device, raw,
                ),
            )
            raw_id = cur.lastrowid

            if msg_type == "location":
                lat, lon = payload.get("lat"), payload.get("lon")
                if lat is None or lon is None or rec_dt is None:
                    raise ValueError("Location payload requires lat, lon, and valid tst.")
                conn.execute(
                    """
                    INSERT INTO gps_locations(
                        raw_event_id,participant_id,recorded_at_utc,created_at_utc,received_at_utc,
                        lat,lon,accuracy_m,altitude_m,vertical_accuracy_m,velocity_kmh,course_deg,
                        battery_pct,battery_status,connection,monitoring_mode,trigger,source,owntracks_tid
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        raw_id, participant_id, iso_utc(rec_dt), iso_utc(created_dt), iso_utc(received),
                        lat, lon, payload.get("acc"), payload.get("alt"), payload.get("vac"),
                        payload.get("vel"), payload.get("cog"), payload.get("batt"), payload.get("bs"),
                        payload.get("conn"), payload.get("m"), payload.get("t"), payload.get("source"),
                        payload.get("tid"),
                    ),
                )
                refresh_subject_ready(conn, participant_id, iso_utc(received))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: raw_events.participant_id, raw_events.event_uid" in str(exc):
            return {"stored": False, "duplicate": True, "event_uid": uid}
        raise

    archive_record = {
        "participant_id": participant_id,
        "server_received_at_utc": iso_utc(received),
        "headers_user": headers_user,
        "headers_device": headers_device,
        "payload": payload,
    }
    archived = _append_raw_archive(archive_record, received)
    if archived:
        with db() as conn:
            conn.execute("UPDATE raw_events SET archive_ok=1 WHERE id=?", (raw_id,))

    return {
        "stored": True,
        "duplicate": False,
        "event_uid": uid,
        "message_type": msg_type,
        "archive_ok": archived,
    }


def _date_bounds(date_str: str | None) -> tuple[str | None, str | None]:
    """Convert a study-local YYYY-MM-DD into UTC boundaries for storage queries."""
    if not date_str:
        return None, None
    local_tz = ZoneInfo(settings.study_timezone)
    start_local = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return iso_utc(start_local), iso_utc(end_local)


def get_locations(participant_id: str, date_str: str | None = None):
    start, end = _date_bounds(date_str)
    sql = """
        SELECT g.*, r.headers_device AS owntracks_device_id
        FROM gps_locations AS g
        JOIN raw_events AS r ON r.id = g.raw_event_id
        WHERE g.participant_id=?
    """
    args: list[Any] = [participant_id]
    if start:
        sql += " AND g.recorded_at_utc>=? AND g.recorded_at_utc<?"
        args.extend([start, end])
    sql += " ORDER BY g.recorded_at_utc"
    with db() as conn:
        return conn.execute(sql, args).fetchall()

def participant_exists(participant_id: str) -> bool:
    with db() as conn:
        row = conn.execute("SELECT 1 FROM participants WHERE participant_id=?", (participant_id,)).fetchone()
    return row is not None


def online_state(
    last_received_at_utc: str | None,
    last_recorded_at_utc: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not last_received_at_utc:
        return {
            "status": "never", "last_received_at_utc": None, "last_recorded_at_utc": None,
            "seconds_since_last": None, "delivery_delay_seconds": None,
        }
    try:
        received = datetime.fromisoformat(last_received_at_utc.replace("Z", "+00:00"))
        age = max(0, int(((now or utc_now()) - received).total_seconds()))
    except ValueError:
        age = None
        received = None
    try:
        recorded = datetime.fromisoformat(last_recorded_at_utc.replace("Z", "+00:00")) if last_recorded_at_utc else None
    except ValueError:
        recorded = None
    delivery_delay = max(0, int((received - recorded).total_seconds())) if received and recorded else None
    if age is None:
        status = "unknown"
    elif age <= 600 and delivery_delay is not None and delivery_delay >= settings.gps_backfill_delay_seconds:
        status = "backfilling"
    elif age <= 600:
        status = "live"
    elif age <= 3600:
        status = "stale"
    else:
        status = "offline"
    return {
        "status": status,
        "last_received_at_utc": last_received_at_utc,
        "last_recorded_at_utc": last_recorded_at_utc,
        "seconds_since_last": age,
        "delivery_delay_seconds": delivery_delay,
    }


TRACK_SAMPLE_SECONDS = {1: TARGET_SAMPLE_SECONDS, 12: 30, 24: 50}


def daily_acquisition_summary(participant_id: str, start_utc: str, end_utc: str) -> dict[str, Any]:
    count = 0
    first: datetime | None = None
    last: datetime | None = None
    previous: datetime | None = None
    max_gap = 0.0
    with db() as conn:
        rows = conn.execute(
            """SELECT recorded_at_utc FROM gps_locations
               WHERE participant_id=? AND recorded_at_utc>=? AND recorded_at_utc<?
               ORDER BY recorded_at_utc""",
            (participant_id, start_utc, end_utc),
        )
        for row in rows:
            recorded = datetime.fromisoformat(row["recorded_at_utc"].replace("Z", "+00:00"))
            count += 1
            first = first or recorded
            if previous is not None:
                max_gap = max(max_gap, (recorded - previous).total_seconds())
            previous = last = recorded
    start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    first_delay = (first - start).total_seconds() if first else None
    last_shortfall = (end - last).total_seconds() if last else None
    edge_limit = settings.gps_daily_edge_coverage_hours * 3600
    issues = []
    if count == 0:
        issues.append("no_points")
    else:
        if count < settings.gps_daily_min_points:
            issues.append("low_point_count")
        if first_delay is not None and first_delay > edge_limit:
            issues.append("late_first_point")
        if last_shortfall is not None and last_shortfall > edge_limit:
            issues.append("early_last_point")
        if max_gap > settings.gps_daily_max_gap_seconds:
            issues.append("long_gap")
    return {
        "complete": not issues,
        "point_count": count,
        "first_recorded_at_utc": iso_utc(first),
        "last_recorded_at_utc": iso_utc(last),
        "first_delay_seconds": round(first_delay, 1) if first_delay is not None else None,
        "last_shortfall_seconds": round(last_shortfall, 1) if last_shortfall is not None else None,
        "max_gap_seconds": round(max_gap, 1),
        "issues": issues,
    }


def track_diagnostic(participant_id: str, hours: int, now: datetime | None = None) -> dict[str, Any]:
    if hours not in TRACK_SAMPLE_SECONDS:
        raise ValueError("hours must be 1, 12 or 24")
    end = (now or utc_now()).astimezone(timezone.utc)
    start = end - timedelta(hours=hours)
    sample_seconds = TRACK_SAMPLE_SECONDS[hours]
    points: list[dict[str, Any]] = []
    total = 0
    poor = 0
    accuracy_count = 0
    max_gap = 0.0
    previous_time: datetime | None = None
    last_display_time: datetime | None = None
    last_row: dict[str, Any] | None = None
    pending_break = False

    with db() as conn:
        rows = conn.execute(
            """SELECT recorded_at_utc,lat,lon,accuracy_m
               FROM gps_locations
               WHERE participant_id=? AND recorded_at_utc>=? AND recorded_at_utc<=?
               ORDER BY recorded_at_utc""",
            (participant_id, iso_utc(start), iso_utc(end)),
        )
        for row in rows:
            recorded = datetime.fromisoformat(row["recorded_at_utc"].replace("Z", "+00:00"))
            total += 1
            if row["accuracy_m"] is not None:
                accuracy_count += 1
                poor += int(float(row["accuracy_m"]) > settings.qc_poor_accuracy_m)
            if previous_time is not None:
                gap = max(0.0, (recorded - previous_time).total_seconds())
                max_gap = max(max_gap, gap)
                if gap > settings.qc_gap_warning_seconds:
                    pending_break = True
            point = {
                "recorded_at_utc": row["recorded_at_utc"],
                "lat": row["lat"],
                "lon": row["lon"],
                "break_before": pending_break,
            }
            if last_display_time is None or (recorded - last_display_time).total_seconds() >= sample_seconds:
                points.append(point)
                last_display_time = recorded
                pending_break = False
            previous_time = recorded
            last_row = point

    if last_row is not None and (not points or points[-1]["recorded_at_utc"] != last_row["recorded_at_utc"]):
        last_row["break_before"] = pending_break
        points.append(last_row)

    return {
        "participant_id": participant_id,
        "window_hours": hours,
        "latest_recorded_at_utc": last_row["recorded_at_utc"] if last_row else None,
        "total_point_count": total,
        "max_gap_seconds": round(max_gap, 1),
        "poor_accuracy_percentage": round(poor / accuracy_count * 100, 1) if accuracy_count else None,
        "points": points,
        "gap_warning_seconds": settings.qc_gap_warning_seconds,
        "map": {
            "tile_url": settings.gps_tile_url,
            "attribution": settings.gps_tile_attribution,
        },
    }


def qc_summary(participant_id: str, date_str: str | None = None) -> dict[str, Any]:
    rows = get_locations(participant_id, date_str)
    if not rows:
        return {
            "participant_id": participant_id,
            "date_local": date_str,
            "study_timezone": settings.study_timezone,
            "status": "NO_DATA",
            "location_count": 0,
        }

    rec_times = [datetime.fromisoformat(r["recorded_at_utc"].replace("Z", "+00:00")) for r in rows]
    recv_times = [datetime.fromisoformat(r["received_at_utc"].replace("Z", "+00:00")) for r in rows]
    gaps = [(b-a).total_seconds() for a,b in zip(rec_times, rec_times[1:])]
    delays = [(recv-rec).total_seconds() for rec,recv in zip(rec_times, recv_times)]
    accs = sorted(float(r["accuracy_m"]) for r in rows if r["accuracy_m"] is not None)

    def p90(values: list[float]) -> float | None:
        if not values:
            return None
        idx = max(0, min(len(values)-1, int(round(0.9*(len(values)-1)))))
        return values[idx]

    max_gap = max(gaps) if gaps else 0.0
    poor_acc = sum(1 for a in accs if a > settings.qc_poor_accuracy_m)
    delayed = sum(1 for d in delays if d > settings.qc_delay_warning_seconds)

    warnings = []
    if max_gap > settings.qc_gap_warning_seconds:
        warnings.append(f"max_gap>{settings.qc_gap_warning_seconds}s")
    if poor_acc:
        warnings.append(f"accuracy>{settings.qc_poor_accuracy_m}m:{poor_acc}")

    return {
        "participant_id": participant_id,
        "date_local": date_str,
            "study_timezone": settings.study_timezone,
        "status": "WARNING" if warnings else "OK",
        "warnings": warnings,
        "location_count": len(rows),
        "first_recorded_at_utc": rows[0]["recorded_at_utc"],
        "last_recorded_at_utc": rows[-1]["recorded_at_utc"],
        "last_received_at_utc": iso_utc(max(recv_times)),
        "median_gap_seconds": round(median(gaps), 2) if gaps else None,
        "max_gap_seconds": round(max_gap, 2),
        "median_accuracy_m": round(median(accs), 2) if accs else None,
        "p90_accuracy_m": round(p90(accs), 2) if accs else None,
        "poor_accuracy_count": poor_acc,
        "delayed_delivery_count": delayed,
        "max_delivery_delay_seconds": round(max(delays), 2) if delays else None,
        "last_battery_pct": rows[-1]["battery_pct"],
        "last_device_tid": rows[-1]["owntracks_tid"],
        "last_device_id": rows[-1]["owntracks_device_id"],
    }


def export_csv(participant_id: str, date_str: str | None = None) -> str:
    rows = get_locations(participant_id, date_str)
    output = io.StringIO(newline="")
    fields = [
        "participant_id","recorded_at_utc","created_at_utc","received_at_utc",
        "lat","lon","accuracy_m","altitude_m","vertical_accuracy_m",
        "velocity_kmh","course_deg","battery_pct","battery_status","connection",
        "monitoring_mode","trigger","source","owntracks_tid","owntracks_device_id",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r[k] for k in fields})
    return output.getvalue()
