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


def form_target_date(form_key: str, local_now: datetime | None = None) -> date:
    current = local_now or datetime.now(ZoneInfo(settings.study_timezone))
    target = current.date()
    if form_key == "evening" and current.hour < settings.questionnaire_evening_cutoff_hour:
        target -= timedelta(days=1)
    return target


def form_assignment(subject, form_key: str, local_now: datetime | None = None, calendar_day: date | None = None) -> dict:
    current = local_now or datetime.now(ZoneInfo(settings.study_timezone))
    calendar_day = calendar_day or current.date()
    if form_key == "morning":
        experiment_day = calendar_day - timedelta(days=1)
        response_day = calendar_day
    else:
        crosses_midnight = current.hour < settings.questionnaire_evening_cutoff_hour
        experiment_day = calendar_day - timedelta(days=1) if crosses_midnight else calendar_day
        response_day = experiment_day
    _, study_day, total_days = _clock_for_date(subject, experiment_day)
    return {
        "calendar_date_local": calendar_day.isoformat(),
        "experiment_date_local": experiment_day.isoformat(),
        "date_local": response_day.isoformat(),
        "study_day": study_day,
        "total_days": total_days,
        "calendar_to_experiment_days": (experiment_day - calendar_day).days,
    }


def _display_date(value: date) -> str:
    return f"{value.month}月{value.day}日"


def form_time_scope(form_key: str, target: date) -> dict[str, str]:
    if form_key == "morning":
        previous = target - timedelta(days=1)
        return {
            "label": "昨晚睡眠",
            "range": f"{_display_date(previous)}晚 → {_display_date(target)}早",
            "explanation": (
                f"“昨晚”是这次主要睡眠的实验归属名称。即使实际到{_display_date(target)}凌晨才入睡，"
                f"入睡日历日仍是{_display_date(target)}，但睡眠归入{_display_date(previous)}晚；"
                "入睡和醒来请填写实际日历时钟时间。"
            ),
            "qc_exposure_date": previous.isoformat(),
        }
    following = target + timedelta(days=1)
    return {
        "label": "今天白天",
        "range": f"{_display_date(target)}起床后 → 本次入睡前",
        "explanation": (
            f"指{_display_date(target)}这次起床后到本次入睡前的整个清醒时段。"
            f"如果实际到{_display_date(following)}凌晨才入睡，入睡日历日是{_display_date(following)}，"
            f"但这段清醒时间仍归入{_display_date(target)}实验日。"
        ),
        "qc_exposure_date": target.isoformat(),
    }


def _gps_state(participant_id: str) -> dict:
    with db() as conn:
        last = conn.execute(
            """SELECT recorded_at_utc,received_at_utc FROM gps_locations
               WHERE participant_id=? ORDER BY received_at_utc DESC LIMIT 1""",
            (participant_id,),
        ).fetchone()
    return gps_service.online_state(
        last["received_at_utc"] if last else None,
        last["recorded_at_utc"] if last else None,
    )


def portal_state(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    local_now = datetime.now(ZoneInfo(settings.study_timezone))
    definitions = list_forms()
    assignments = {definition["key"]: form_assignment(subject, definition["key"], local_now) for definition in definitions}
    active_assignment = assignments["evening"]
    with db() as conn:
        completed = {
            (r["form_key"], r["date_local"]): r["submitted_at_utc"]
            for r in conn.execute(
                "SELECT form_key,date_local,submitted_at_utc FROM questionnaire_responses WHERE participant_id=?",
                (subject["participant_id"],),
            )
        }
    forms = []
    for definition in definitions:
        key = definition["key"]
        assignment = assignments[key]
        target = date.fromisoformat(
            assignment["calendar_date_local"] if key == "morning" else assignment["experiment_date_local"]
        )
        forms.append({
            **definition,
            "version": FORM_VERSION,
            **assignment,
            "time_scope": form_time_scope(key, target),
            "completed": (key, assignment["date_local"]) in completed,
            "submitted_at_utc": completed.get((key, assignment["date_local"])),
        })
    exposure_date = form_target_date("evening", local_now)
    lighting = light_service.portal_light_state(subject["participant_id"], exposure_date.isoformat())
    lighting["date_local"] = exposure_date.isoformat()
    lighting["direct_upload"] = settings.light_storage_backend == "oss"
    lighting["time_scope"] = form_time_scope("evening", exposure_date)
    return {
        "study_title": "光迹计划（北京）",
        "portal_title": "LEHUE Study",
        "status": subject["status"],
        "date_local": local_now.date().isoformat(),
        "calendar_date_local": local_now.date().isoformat(),
        "experiment_date_local": active_assignment["experiment_date_local"],
        "study_day": active_assignment["study_day"],
        "total_days": active_assignment["total_days"],
        "gps": _gps_state(subject["participant_id"]),
        "lighting": lighting,
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


def prepare_lighting_direct(token: str, date_local: str, filename: str, size_bytes: int, sha256: str) -> dict:
    participant_id = _lighting_participant(token, date_local)
    return light_service.prepare_direct_upload(participant_id, date_local, filename, size_bytes, sha256)


def complete_lighting_direct(token: str, upload_uid: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    return light_service.complete_direct_upload(subject["participant_id"], upload_uid)


def submit_questionnaire(token: str, form_key: str, answers: dict, calendar_date_local: str = "") -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    if subject["status"] != "running":
        raise ValueError("实验尚未处于运行状态，当前不能提交每日问卷")
    form = get_form(form_key)
    if not form:
        raise LookupError("questionnaire not found")
    local_now = datetime.now(ZoneInfo(settings.study_timezone))
    try:
        calendar_day = date.fromisoformat(calendar_date_local) if calendar_date_local else local_now.date()
    except ValueError as exc:
        raise ValueError("日历日必须使用 YYYY-MM-DD") from exc
    assignment = form_assignment(subject, form_key, local_now, calendar_day)
    if not 1 <= assignment["study_day"] <= assignment["total_days"]:
        raise ValueError("所选日历日不在本次实验范围内")
    normalized = validate_answers(form, answers or {})
    submitted = now_iso()
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,calendar_date_local,study_day,form_key,form_version,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?,?,?)",
                (
                    subject["participant_id"],
                    assignment["date_local"],
                    assignment["calendar_date_local"],
                    assignment["study_day"],
                    form_key,
                    FORM_VERSION,
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    submitted,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("该实验日的这份问卷已经提交，无需重复填写") from exc
    return {"ok": True, "form_key": form_key, **assignment, "submitted_at_utc": submitted}
