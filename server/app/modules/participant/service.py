from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import db
from app.core.security import encrypt_credential, hash_secret, verify_secret
from app.modules.gps import service as gps_service
from app.modules.light import service as light_service
from app.modules.questionnaire import FORM_VERSION, get_form, list_forms, validate_answers


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.study_timezone)).date()


def generate_portal_token(participant_id: str) -> str:
    selector = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    salt, digest = hash_secret(secret)
    now = now_iso()
    with db() as conn:
        if not conn.execute("SELECT 1 FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone():
            raise ValueError("participant not found")
        conn.execute(
            "UPDATE study_subjects SET portal_token_id=?,portal_token_salt=?,portal_token_hash=?,portal_token_ciphertext=?,portal_token_created_at_utc=?,updated_at_utc=? WHERE participant_id=?",
            (selector, salt, digest, encrypt_credential(f"{selector}.{secret}"), now, now, participant_id),
        )
    return f"{selector}.{secret}"


def _resolve_subject(token: str):
    try:
        selector, secret = token.strip().split(".", 1)
    except ValueError:
        return None
    if not selector or not secret or len(selector) > 40 or len(secret) > 100:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM study_subjects WHERE portal_token_id=? AND portal_token_hash<>''",
            (selector,),
        ).fetchone()
    if not row or not verify_secret(secret, row["portal_token_salt"], row["portal_token_hash"]):
        return None
    return row


def _clock_for_date(subject, target: date) -> tuple[str, int, int]:
    start_raw = subject["start_date"] or subject["expected_start"]
    end_raw = subject["end_date"] or subject["expected_end"]
    if not start_raw:
        return target.isoformat(), 0, 14
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        return target.isoformat(), 0, 14
    try:
        end = date.fromisoformat(end_raw) if end_raw else start
    except ValueError:
        end = start
    total = max(1, (end - start).days + 1)
    day = (target - start).days + 1
    return target.isoformat(), day, total


def _study_clock(subject) -> tuple[str, int, int]:
    return _clock_for_date(subject, local_today())


def form_target_date(form_key: str, local_now: datetime | None = None) -> date:
    current = local_now or datetime.now(ZoneInfo(settings.study_timezone))
    target = current.date()
    if form_key == "evening" and current.hour < settings.questionnaire_evening_cutoff_hour:
        target -= timedelta(days=1)
    return target


def _form_clock(subject, form_key: str, local_now: datetime | None = None) -> tuple[str, int, int]:
    return _clock_for_date(subject, form_target_date(form_key, local_now))


def _gps_state(participant_id: str) -> dict:
    with db() as conn:
        last = conn.execute(
            "SELECT MAX(received_at_utc) AS x FROM gps_locations WHERE participant_id=?",
            (participant_id,),
        ).fetchone()["x"]
    return gps_service.online_state(last)


def portal_state(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    date_local, study_day, total_days = _study_clock(subject)
    clocks = {definition["key"]: _form_clock(subject, definition["key"]) for definition in list_forms()}
    with db() as conn:
        completed = {
            (r["form_key"], r["date_local"]): r["submitted_at_utc"]
            for r in conn.execute(
                "SELECT form_key,date_local,submitted_at_utc FROM questionnaire_responses WHERE participant_id=?",
                (subject["participant_id"],),
            )
        }
    forms = []
    for definition in list_forms():
        key = definition["key"]
        form_date, form_day, _ = clocks[key]
        forms.append({
            **definition,
            "version": FORM_VERSION,
            "date_local": form_date,
            "study_day": form_day,
            "completed": (key, form_date) in completed,
            "submitted_at_utc": completed.get((key, form_date)),
        })
    return {
        "study_title": "光迹计划（北京）",
        "portal_title": "LEHUE Study",
        "status": subject["status"],
        "date_local": date_local,
        "study_day": study_day,
        "total_days": total_days,
        "gps": _gps_state(subject["participant_id"]),
        "lighting": light_service.portal_light_state(subject["participant_id"], date_local),
        "forms": forms,
        "notice": "这是 LEHUE 被试专属工作入口。链接本身用于识别身份，请勿转发给他人。",
    }


def _lighting_participant(token: str, date_local: str) -> str:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    if subject["status"] != "running":
        raise ValueError("实验尚未处于运行状态，当前不能上传 Lighting")
    try:
        exposure_day = date.fromisoformat(date_local)
    except ValueError as exc:
        raise ValueError("Lighting 暴露日期必须使用 YYYY-MM-DD") from exc
    today = local_today()
    if exposure_day > today:
        raise ValueError("不能上传未来日期的 Lighting 文件")
    start_raw = subject["start_date"] or subject["expected_start"]
    if start_raw:
        try:
            start_day = date.fromisoformat(start_raw)
        except ValueError:
            start_day = None
        if start_day and exposure_day < start_day:
            raise ValueError("所选日期早于实验开始日期")
    return subject["participant_id"]


def submit_lighting_path(token: str, date_local: str, filename: str, path: Path) -> dict:
    participant_id = _lighting_participant(token, date_local)
    return light_service.store_upload_path(participant_id, date_local, filename, path, "participant_portal")


def submit_lighting(token: str, date_local: str, filename: str, raw: bytes) -> dict:
    participant_id = _lighting_participant(token, date_local)
    return light_service.store_upload(participant_id, date_local, filename, raw, "participant_portal")


def submit_questionnaire(token: str, form_key: str, answers: dict) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    if subject["status"] != "running":
        raise ValueError("实验尚未处于运行状态，当前不能提交每日问卷")
    form = get_form(form_key)
    if not form:
        raise LookupError("questionnaire not found")
    date_local, study_day, _ = _form_clock(subject, form_key)
    if study_day < 1:
        raise ValueError("实验尚未开始")
    normalized = validate_answers(form, answers or {})
    submitted = now_iso()
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,form_version,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?,?)",
                (
                    subject["participant_id"],
                    date_local,
                    study_day,
                    form_key,
                    FORM_VERSION,
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    submitted,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("今日该问卷已经提交，无需重复填写") from exc
    return {"ok": True, "form_key": form_key, "date_local": date_local, "study_day": study_day, "submitted_at_utc": submitted}
