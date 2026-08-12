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
    if form_key == "morning" or current.hour < settings.questionnaire_evening_cutoff_hour:
        return current.date() - timedelta(days=1)
    return current.date()


def allowed_exposure_days(form_key: str, local_now: datetime | None = None) -> tuple[date, date]:
    target = form_target_date(form_key, local_now)
    return target, target - timedelta(days=1)


def form_assignment(subject, form_key: str, exposure_day: date, calendar_day: date | None = None) -> dict:
    calendar_day = calendar_day or local_today()
    _, study_day, total_days = _clock_for_date(subject, exposure_day)
    return {
        "calendar_date_local": calendar_day.isoformat(),
        "experiment_date_local": exposure_day.isoformat(),
        "date_local": exposure_day.isoformat(),
        "study_day": study_day,
        "total_days": total_days,
        "calendar_to_experiment_days": (exposure_day - calendar_day).days,
    }


def _display_date(value: date) -> str:
    return f"{value.month}月{value.day}日"


def form_time_scope(form_key: str, target: date) -> dict[str, str]:
    if form_key == "morning":
        following = target + timedelta(days=1)
        return {
            "label": "昨晚睡眠",
            "range": f"{_display_date(target)}晚 → {_display_date(following)}早",
            "explanation": (
                f"这次睡眠统一归入{_display_date(target)}实验日。即使实际到{_display_date(following)}凌晨才入睡，"
                "入睡和醒来请填写实际日历时钟时间。"
            ),
            "qc_exposure_date": target.isoformat(),
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


def _study_bounds(subject) -> tuple[date | None, date | None]:
    try:
        start = date.fromisoformat(subject["start_date"] or subject["expected_start"])
    except (TypeError, ValueError):
        return None, None
    try:
        end = date.fromisoformat(subject["final_end"] or subject["end_date"] or subject["expected_end"])
    except (TypeError, ValueError):
        end = start
    return start, max(start, end)


def _portal_progress(conn, subject, targets: dict[str, date]) -> dict:
    start, end = _study_bounds(subject)
    if not start or not end:
        return {"completed": 0, "expected": 0, "percent": 0, "days": []}
    last_day = min(end, max(targets.values()))
    if last_day < start:
        return {"completed": 0, "expected": 0, "percent": 0, "days": []}
    questionnaires = {
        (row["date_local"], row["form_key"])
        for row in conn.execute(
            "SELECT date_local,form_key FROM questionnaire_responses WHERE participant_id=?",
            (subject["participant_id"],),
        )
    }
    lighting = {
        row["date_local"]
        for row in conn.execute(
            "SELECT DISTINCT date_local FROM lighting_files WHERE participant_id=? AND upload_status='qc' AND quality='valid'",
            (subject["participant_id"],),
        )
    }
    completed_total = expected_total = 0
    days = []
    current = start
    while current <= last_day:
        states = {
            "morning": "done" if (current.isoformat(), "morning") in questionnaires else "pending",
            "evening": "done" if (current.isoformat(), "evening") in questionnaires else "pending",
            "lighting": "done" if current.isoformat() in lighting else "pending",
        }
        due = {
            "morning": current <= targets["morning"],
            "evening": current <= targets["evening"],
            "lighting": current <= targets["evening"],
        }
        for key in states:
            if not due[key] and states[key] != "done":
                states[key] = "not_due"
        expected = sum(due.values())
        completed = sum(due[key] and states[key] == "done" for key in states)
        completed_total += completed
        expected_total += expected
        days.append({
            "date_local": current.isoformat(),
            "study_day": (current - start).days + 1,
            "completed": completed,
            "expected": expected,
            "morning": states["morning"],
            "evening": states["evening"],
            "lighting": states["lighting"],
        })
        current += timedelta(days=1)
    return {
        "completed": completed_total,
        "expected": expected_total,
        "percent": round(completed_total / expected_total * 100) if expected_total else 0,
        "days": days,
    }


def _cohort_progress(conn, participant_id: str, today: date) -> dict:
    running_others = completed_others = 0
    for row in conn.execute(
        "SELECT participant_id,status,expected_start,expected_end,start_date,end_date,final_end FROM study_subjects WHERE participant_id<>?",
        (participant_id,),
    ):
        start, end = _study_bounds(row)
        explicitly_completed = row["status"] in {"completed", "finished", "closed"}
        if row["status"] == "running" and start and end and start <= today <= end:
            running_others += 1
        elif explicitly_completed or (row["status"] == "running" and end and end < today):
            completed_others += 1
    local_start = datetime(today.year, today.month, today.day, tzinfo=ZoneInfo(settings.study_timezone))
    utc_start = local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    active_today = conn.execute(
        """SELECT COUNT(DISTINCT participant_id) n FROM (
               SELECT participant_id FROM questionnaire_responses
                WHERE calendar_date_local=? AND participant_id<>?
               UNION
               SELECT participant_id FROM lighting_files
                WHERE uploaded_at_utc>=? AND uploaded_at_utc<? AND participant_id<>?
           )""",
        (today.isoformat(), participant_id, utc_start, utc_end, participant_id),
    ).fetchone()["n"]
    return {
        "running_others": running_others,
        "completed_others": completed_others,
        "active_today": int(active_today),
    }


def _report_state(subject, today: date) -> dict:
    _, end = _study_bounds(subject)
    ended = subject["status"] in {"completed", "finished", "closed"} or bool(subject["start_date"] and end and end < today)
    return {
        "available": False,
        "status": "preparing" if ended else "locked",
        "unlocks_after": end.isoformat() if end else "",
    }


def portal_state(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    local_now = datetime.now(ZoneInfo(settings.study_timezone))
    definitions = list_forms()
    targets = {definition["key"]: form_target_date(definition["key"], local_now) for definition in definitions}
    active_assignment = form_assignment(subject, "evening", targets["evening"], local_now.date())
    with db() as conn:
        completed = {
            (r["form_key"], r["date_local"]): r["submitted_at_utc"]
            for r in conn.execute(
                "SELECT form_key,date_local,submitted_at_utc FROM questionnaire_responses WHERE participant_id=?",
                (subject["participant_id"],),
            )
        }
        progress = _portal_progress(conn, subject, targets)
        cohort = _cohort_progress(conn, subject["participant_id"], local_now.date())
    forms = []
    for is_makeup in (False, True):
        for definition in definitions:
            key = definition["key"]
            exposure_day = targets[key] - timedelta(days=1) if is_makeup else targets[key]
            assignment = form_assignment(subject, key, exposure_day, local_now.date())
            if not 1 <= assignment["study_day"] <= assignment["total_days"]:
                continue
            submitted_at = completed.get((key, assignment["date_local"]))
            if is_makeup and submitted_at:
                continue
            forms.append({
                **definition,
                "title": f"补填上一实验日 · {definition['title']}" if is_makeup else definition["title"],
                "version": FORM_VERSION,
                "task_id": f"{key}:{assignment['date_local']}",
                "is_makeup": is_makeup,
                **assignment,
                "time_scope": form_time_scope(key, exposure_day),
                "completed": bool(submitted_at),
                "submitted_at_utc": submitted_at,
            })
    exposure_date = targets["evening"]
    lighting_tasks = []
    for is_makeup, target in ((False, exposure_date), (True, exposure_date - timedelta(days=1))):
        assignment = form_assignment(subject, "evening", target, local_now.date())
        if not 1 <= assignment["study_day"] <= assignment["total_days"]:
            continue
        item = light_service.portal_light_state(subject["participant_id"], target.isoformat())
        if is_makeup and item["status"] != "missing":
            continue
        item.update({
            "task_id": f"lighting:{target.isoformat()}",
            "is_makeup": is_makeup,
            "title": "补传上一实验日 Lighting" if is_makeup else "Lighting 光照记录",
            "date_local": target.isoformat(),
            "study_day": assignment["study_day"],
            "direct_upload": settings.light_storage_backend == "oss",
            "time_scope": form_time_scope("evening", target),
        })
        lighting_tasks.append(item)
    lighting = lighting_tasks[0] if lighting_tasks else {
        "status": "missing", "uploaded": False, "quality": None, "valid_pct": None,
        "date_local": exposure_date.isoformat(), "direct_upload": settings.light_storage_backend == "oss",
        "time_scope": form_time_scope("evening", exposure_date),
    }
    return {
        "study_title": "光迹计划（北京）",
        "portal_title": "LEHUE Study",
        "participant_id": subject["participant_id"],
        "study_timezone": settings.study_timezone,
        "status": subject["status"],
        "date_local": local_now.date().isoformat(),
        "calendar_date_local": local_now.date().isoformat(),
        "experiment_date_local": active_assignment["experiment_date_local"],
        "study_day": active_assignment["study_day"],
        "total_days": active_assignment["total_days"],
        "study": {
            "start_date": subject["start_date"] or subject["expected_start"],
            "end_date": subject["end_date"] or subject["expected_end"],
            "pack_id": subject["pack_id"],
        },
        "progress": progress,
        "cohort": cohort,
        "report": _report_state(subject, local_now.date()),
        "gps": _gps_state(subject["participant_id"]),
        "lighting": lighting,
        "lighting_tasks": lighting_tasks,
        "forms": forms,
        "notice": "这是 LEHUE 被试专属工作入口。链接本身用于识别身份，请勿转发给他人。",
        "help": [
            {"title": "OwnTracks 没有回传", "text": "打开 OwnTracks，确认使用 Move 模式；允许始终定位、精确位置和后台运行，然后点一次上传/定位按钮。"},
            {"title": "问卷跨过午夜", "text": "晨间问卷记录昨晚睡眠；睡前问卷和 Lighting 仍归入入睡前开始的实验日。Portal 已自动标注归属日期。"},
            {"title": "Lighting 选错文件", "text": "直接重新上传正确的 CSV、XLSX 或 TXT。已收到的 raw 会保留，系统按实验日显示质量更好的结果。"},
        ],
    }


def participant_report(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    report = _report_state(subject, local_today())
    if report["status"] == "locked":
        raise PermissionError("实验结束后才可查看个人睡眠与光照报告")
    return {
        **report,
        "participant_id": subject["participant_id"],
        "message": "实验报告接口已就绪；报告生成完成后将在这里开放查看。",
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
    if exposure_day not in allowed_exposure_days("evening"):
        raise ValueError("Lighting 只允许上传当前目标实验日或上一实验日")
    assignment = form_assignment(subject, "evening", exposure_day)
    if not 1 <= assignment["study_day"] <= assignment["total_days"]:
        raise ValueError("所选实验日不在本次实验范围内")
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


def submit_questionnaire(token: str, form_key: str, answers: dict, date_local: str = "") -> dict:
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
        exposure_day = date.fromisoformat(date_local) if date_local else form_target_date(form_key, local_now)
    except ValueError as exc:
        raise ValueError("实验日必须使用 YYYY-MM-DD") from exc
    if exposure_day not in allowed_exposure_days(form_key, local_now):
        raise ValueError("问卷只允许填写当前目标实验日或上一实验日")
    assignment = form_assignment(subject, form_key, exposure_day, local_now.date())
    if not 1 <= assignment["study_day"] <= assignment["total_days"]:
        raise ValueError("所选实验日不在本次实验范围内")
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
