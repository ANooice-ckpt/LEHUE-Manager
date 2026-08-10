from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import db
from app.core.security import hash_secret, verify_secret

FORM_VERSION = "test_v1"
FORMS = {
    "morning": {
        "key": "morning",
        "title": "晨间睡眠记录",
        "description": "起床后填写 · 当前为系统联调测试版",
        "questions": [
            {"key": "sleep_duration_hours", "type": "number", "label": "昨晚大约睡了多少小时？", "min": 0, "max": 16, "step": 0.1, "required": True, "unit": "小时"},
            {"key": "sleep_quality", "type": "scale", "label": "昨晚整体睡眠质量如何？", "min": 1, "max": 5, "required": True, "low": "很差", "high": "很好"},
            {"key": "sleepiness", "type": "scale", "label": "此刻困倦程度", "min": 0, "max": 10, "required": True, "low": "完全不困", "high": "非常困"},
        ],
    },
    "evening": {
        "key": "evening",
        "title": "睡前状态记录",
        "description": "睡前填写 · 当前为系统联调测试版",
        "questions": [
            {"key": "mood", "type": "scale", "label": "此刻整体情绪", "min": 0, "max": 10, "required": True, "low": "很差", "high": "很好"},
            {"key": "stress", "type": "scale", "label": "今天整体压力程度", "min": 0, "max": 10, "required": True, "low": "没有压力", "high": "压力很大"},
            {"key": "fatigue", "type": "scale", "label": "此刻疲劳程度", "min": 0, "max": 10, "required": True, "low": "完全不累", "high": "非常疲劳"},
            {"key": "notes", "type": "text", "label": "今天是否有需要主试知道的特殊情况？", "required": False, "placeholder": "可留空"},
        ],
    },
}


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
            "UPDATE study_subjects SET portal_token_id=?,portal_token_salt=?,portal_token_hash=?,portal_token_created_at_utc=?,updated_at_utc=? WHERE participant_id=?",
            (selector, salt, digest, now, now, participant_id),
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


def _study_clock(subject) -> tuple[str, int, int]:
    today = local_today()
    start_raw = subject["start_date"] or subject["expected_start"]
    end_raw = subject["end_date"] or subject["expected_end"]
    if not start_raw:
        return today.isoformat(), 0, 14
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        return today.isoformat(), 0, 14
    try:
        end = date.fromisoformat(end_raw) if end_raw else start
    except ValueError:
        end = start
    total = max(1, (end - start).days + 1)
    day = (today - start).days + 1
    return today.isoformat(), day, total


def _gps_state(participant_id: str) -> dict:
    with db() as conn:
        last = conn.execute(
            "SELECT MAX(received_at_utc) AS x FROM gps_locations WHERE participant_id=?",
            (participant_id,),
        ).fetchone()["x"]
    if not last:
        return {"status": "never", "last_received_at_utc": None, "seconds_since_last": None}
    try:
        parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except ValueError:
        age = None
    if age is None:
        status = "unknown"
    elif age <= 600:
        status = "live"
    elif age <= 3600:
        status = "stale"
    else:
        status = "offline"
    return {"status": status, "last_received_at_utc": last, "seconds_since_last": age}


def portal_state(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    date_local, study_day, total_days = _study_clock(subject)
    with db() as conn:
        completed = {
            r["form_key"]: r["submitted_at_utc"]
            for r in conn.execute(
                "SELECT form_key,submitted_at_utc FROM questionnaire_responses WHERE participant_id=? AND date_local=?",
                (subject["participant_id"], date_local),
            )
        }
    forms = []
    for key, definition in FORMS.items():
        forms.append({
            **definition,
            "version": FORM_VERSION,
            "completed": key in completed,
            "submitted_at_utc": completed.get(key),
        })
    return {
        "study_title": "光迹计划（北京）",
        "portal_title": "LEHUE Study",
        "status": subject["status"],
        "date_local": date_local,
        "study_day": study_day,
        "total_days": total_days,
        "gps": _gps_state(subject["participant_id"]),
        "forms": forms,
        "notice": "这是 LEHUE 被试专属工作入口。链接本身用于识别身份，请勿转发给他人。",
    }


def _validate_answers(form: dict, answers: dict) -> dict:
    normalized: dict = {}
    allowed = {q["key"]: q for q in form["questions"]}
    for key, q in allowed.items():
        value = answers.get(key)
        if q.get("required") and (value is None or value == ""):
            raise ValueError(f"请完成：{q['label']}")
        if value is None or value == "":
            normalized[key] = ""
            continue
        if q["type"] in {"scale", "number"}:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"无效答案：{q['label']}")
            if number < float(q.get("min", number)) or number > float(q.get("max", number)):
                raise ValueError(f"答案超出范围：{q['label']}")
            if q["type"] == "scale" and number.is_integer():
                normalized[key] = int(number)
            else:
                normalized[key] = number
        else:
            text = str(value).strip()
            if len(text) > 2000:
                raise ValueError(f"文本过长：{q['label']}")
            normalized[key] = text
    return normalized


def submit_questionnaire(token: str, form_key: str, answers: dict) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    if subject["status"] != "running":
        raise ValueError("实验尚未处于运行状态，当前不能提交每日问卷")
    form = FORMS.get(form_key)
    if not form:
        raise LookupError("questionnaire not found")
    date_local, study_day, _ = _study_clock(subject)
    if study_day < 1:
        raise ValueError("实验尚未开始")
    normalized = _validate_answers(form, answers or {})
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
    return {"ok": True, "form_key": form_key, "submitted_at_utc": submitted}
