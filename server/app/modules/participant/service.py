from __future__ import annotations

import json
import secrets
import sqlite3
import base64
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import db, refresh_subject_ready
from app.core.security import decrypt_credential, encrypt_credential, hash_secret, verify_secret
from app.core.identity_db import identity_db
from app.core.owntracks import build_config
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


def _public_origin() -> str:
    domain = settings.domain.strip().rstrip("/")
    return f"https://{domain}" if domain not in {"", "localhost", "127.0.0.1"} else "http://127.0.0.1:8085"


def _owntracks_config(subject) -> dict:
    with db() as conn:
        credential = conn.execute(
            "SELECT secret_ciphertext FROM participants WHERE participant_id=? AND is_active=1",
            (subject["participant_id"],),
        ).fetchone()
    preparation_mode = subject["status"] in {"scheduled", "ready"} and bool(subject["preparation_started_at_utc"])
    if not credential or not credential["secret_ciphertext"] or (subject["status"] != "running" and not preparation_mode):
        return {"available": False}
    from app.core.security import decrypt_credential
    password = decrypt_credential(credential["secret_ciphertext"])
    endpoint = f"{_public_origin()}/api/v1/gps/owntracks"
    config = {
        "_type": "configuration", "mode": 3, "auth": True, "url": endpoint,
        "username": subject["participant_id"], "password": password,
        "deviceId": subject["participant_id"], "tid": subject["participant_id"][-2:],
        "locatorInterval": 10, "locatorDisplacement": 10, "adapt": 0, "downgrade": 0,
    }
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    return {
        "available": True,
        "uri": "owntracks:///config?inline=" + base64.b64encode(config_json.encode()).decode(),
        "config_json": config_json,
    }


def owntracks_download(token: str, platform: str) -> tuple[str, dict]:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    with db() as conn:
        credential = conn.execute(
            "SELECT secret_ciphertext FROM participants WHERE participant_id=? AND is_active=1",
            (subject["participant_id"],),
        ).fetchone()
    if not credential or not credential["secret_ciphertext"]:
        raise ValueError("GPS 配置当前不可用，请联系研究工作人员")
    password = decrypt_credential(credential["secret_ciphertext"])
    return subject["participant_id"], build_config(subject["participant_id"], password, platform)


def _recommended_phone_os(participant_id: str) -> str:
    try:
        with identity_db() as conn:
            row = conn.execute(
                "SELECT phone_os FROM candidates WHERE linked_participant_id=? ORDER BY updated_at_utc DESC LIMIT 1",
                (participant_id,),
            ).fetchone()
    except sqlite3.Error:
        row = None
    value = str(row["phone_os"] or "").lower() if row else ""
    return "ios" if "ios" in value or "iphone" in value else "android" if "android" in value else ""


def portal_state(token: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    local_now = datetime.now(ZoneInfo(settings.study_timezone))
    if subject["status"] in {"scheduled", "ready"} and subject["preparation_started_at_utc"]:
        today = local_now.date().isoformat()
        with db() as conn:
            gps_ok = bool(conn.execute(
                "SELECT 1 FROM gps_locations WHERE participant_id=? AND received_at_utc>=? LIMIT 1",
                (subject["participant_id"], subject["preparation_started_at_utc"]),
            ).fetchone())
            lighting_row = conn.execute(
                """SELECT * FROM lighting_files WHERE participant_id=? AND is_test=1
                   AND upload_status='qc' AND uploaded_at_utc>=? ORDER BY uploaded_at_utc DESC LIMIT 1""",
                (subject["participant_id"], subject["preparation_started_at_utc"]),
            ).fetchone()
        light_state = light_service._public_upload(lighting_row) if lighting_row else {
            "status": "missing", "uploaded": False, "quality": None, "valid_pct": None,
        }
        light_state.update({
            "task_id": f"lighting-test:{today}", "title": "Lighting 测试上传", "date_local": today,
            "study_day": 0, "is_test": True, "direct_upload": settings.light_storage_backend == "oss",
            "time_scope": {"range": "准备测试", "explanation": "该文件仅用于验证 Lighting 实际上传链路，不计入正式 Study Day。"},
        })
        light_ok = bool(lighting_row and lighting_row["quality"] != "unreadable" and lighting_row["records_total"] > 0)
        with db() as conn:
            s1_response = conn.execute(
                "SELECT submitted_at_utc FROM questionnaire_responses WHERE participant_id=? AND form_key='s1' LIMIT 1",
                (subject["participant_id"],),
            ).fetchone()
        s1_form = get_form("s1")
        s1_task = {
            **s1_form, "version": FORM_VERSION, "task_id": "s1:onboarding", "is_makeup": False,
            "calendar_date_local": today, "experiment_date_local": today, "date_local": today,
            "study_day": 0, "total_days": 0, "time_scope": {"label": "正式开始前", "range": "入组阶段", "explanation": "完成后将自动计入 Ready。"},
            "completed": bool(s1_response), "submitted_at_utc": s1_response["submitted_at_utc"] if s1_response else None,
        }
        completed_tests = int(gps_ok) + int(light_ok) + int(bool(s1_response))
        return {
            "study_title": "光迹计划（北京）", "portal_title": "LEHUE Study",
            "participant_id": subject["participant_id"], "study_timezone": settings.study_timezone,
            "status": "running", "lifecycle_status": subject["status"], "mode": "preparation", "date_local": today,
            "calendar_date_local": today, "experiment_date_local": "", "study_day": 0, "total_days": 0,
            "study": {"start_date": subject["expected_start"], "end_date": subject["expected_end"], "pack_id": subject["pack_id"]},
            "read_only": False, "owntracks": {**_owntracks_config(subject), "recommended_platform": _recommended_phone_os(subject["participant_id"])},
            "progress": {"completed": completed_tests, "expected": 3, "percent": round(completed_tests / 3 * 100), "days": []},
            "readiness": {"s1_completed": bool(s1_response), "gps_test_received": gps_ok, "lighting_test_uploaded": light_ok, "ready": subject["status"] == "ready"},
            "gps": _gps_state(subject["participant_id"]), "lighting": light_state, "lighting_tasks": [light_state], "forms": [s1_task],
            "notice": "当前为测试/教学模式。完成 S1、一次 GPS 实际回传和一次可解析的 Lighting 测试后，系统自动标记 Ready。",
            "help": [],
        }
    definitions = list_forms()
    targets = {definition["key"]: form_target_date(definition["key"], local_now) for definition in definitions}
    closing_mode = bool(subject["status"] == "running" and subject["awaiting_final_morning"] and subject["final_end"])
    if closing_mode:
        targets["morning"] = date.fromisoformat(subject["final_end"])
        targets["evening"] = date.fromisoformat(subject["final_end"])
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
    forms = []
    for is_makeup in (False, True):
        for definition in definitions:
            key = definition["key"]
            if closing_mode and key == "evening":
                continue
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
        if is_makeup and item.get("quality") == "valid" and not (
            item.get("coverage_minutes") is not None and item["coverage_minutes"] < light_service.SHORT_COVERAGE_MINUTES
        ):
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
    evening_done = bool(completed.get(("evening", targets["evening"].isoformat())))
    lighting_uploaded = any(
        item["date_local"] == targets["evening"].isoformat() and item.get("uploaded")
        for item in lighting_tasks
    )
    morning_done = any(form_key == "morning" for form_key, _ in completed)
    if subject["awaiting_final_morning"] and subject["final_end"]:
        final_day = date.fromisoformat(subject["final_end"])
        s2_done = completed.get(("s2", subject["final_end"]))
        forms.append({
            **get_form("s2"), "version": FORM_VERSION, "task_id": f"s2:{subject['final_end']}",
            "is_makeup": False, "calendar_date_local": local_now.date().isoformat(),
            "experiment_date_local": subject["final_end"], "date_local": subject["final_end"],
            "study_day": (final_day - date.fromisoformat(subject["start_date"])).days + 1,
            "total_days": (final_day - date.fromisoformat(subject["start_date"])).days + 1,
            "time_scope": {"label": "实验结束", "range": "结束正式曝光后", "explanation": "用于记录实验期间变化和参与体验。"},
            "completed": bool(s2_done), "submitted_at_utc": s2_done,
        })
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
        "read_only": subject["status"] != "running",
        "owntracks": {**_owntracks_config(subject), "recommended_platform": _recommended_phone_os(subject["participant_id"])},
        "progress": progress,
        "gps": _gps_state(subject["participant_id"]),
        "lighting": lighting,
        "lighting_tasks": lighting_tasks,
        "forms": forms,
        "field_reminders": {
            "morning_restart": {
                "show": subject["status"] == "running",
                "completed": morning_done,
                "text": "拔下 Lighting 并开始今日记录",
            },
            "evening_charge": {
                "show": subject["status"] == "running" and evening_done and lighting_uploaded,
                "text": "今天的上传和睡前问卷已完成，请给 Lighting 接上充电。",
            },
            "wearing": "白天请按要求佩戴 Lighting；临时摘下后记得重新戴回。",
            "raw_file": "上传后请保留 Lighting 原始文件，确认研究结束前不要删除。",
        },
        "notice": "实验已完成；此入口保留用于查看既有记录。" if subject["status"] == "completed" else (
            "正式曝光已结束；请完成最终晨间问卷、S2 和仍显示的必要补传。" if subject["awaiting_final_morning"] else
            "这是 LEHUE 被试专属工作入口。链接本身用于识别身份，请勿转发给他人。"
        ),
        "help": [
            {"title": "OwnTracks 没有回传", "text": "打开 OwnTracks，确认使用 Move 模式；允许始终定位、精确位置和后台运行，然后点一次上传/定位按钮。"},
            {"title": "问卷跨过午夜", "text": "晨间问卷记录昨晚睡眠；睡前问卷和 Lighting 仍归入入睡前开始的实验日。Portal 已自动标注归属日期。"},
            {"title": "Lighting 选错文件", "text": "直接重新上传正确的 CSV、XLSX 或 TXT。已收到的 raw 会保留，系统按实验日显示质量更好的结果。"},
        ],
    }


def _lighting_participant(token: str, date_local: str) -> tuple[str, bool]:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    preparation_mode = subject["status"] in {"scheduled", "ready"} and bool(subject["preparation_started_at_utc"])
    if subject["status"] != "running" and not preparation_mode:
        raise ValueError("实验尚未处于运行状态，当前不能上传 Lighting")
    try:
        exposure_day = date.fromisoformat(date_local)
    except ValueError as exc:
        raise ValueError("Lighting 暴露日期必须使用 YYYY-MM-DD") from exc
    if preparation_mode:
        if exposure_day != local_today():
            raise ValueError("Lighting test upload date must be today")
        return subject["participant_id"], True
    closing_days = set()
    if subject["awaiting_final_morning"] and subject["final_end"]:
        final_day = date.fromisoformat(subject["final_end"])
        closing_days = {final_day, final_day - timedelta(days=1)}
    if exposure_day not in (closing_days or set(allowed_exposure_days("evening"))):
        raise ValueError("Lighting 只允许上传当前目标实验日或上一实验日")
    assignment = form_assignment(subject, "evening", exposure_day)
    if not 1 <= assignment["study_day"] <= assignment["total_days"]:
        raise ValueError("所选实验日不在本次实验范围内")
    return subject["participant_id"], False


def submit_lighting_path(token: str, date_local: str, filename: str, path: Path) -> dict:
    participant_id, is_test = _lighting_participant(token, date_local)
    return light_service.store_upload_path(participant_id, date_local, filename, path, "participant_portal", is_test=is_test)


def prepare_lighting_direct(token: str, date_local: str, filename: str, size_bytes: int, sha256: str) -> dict:
    participant_id, is_test = _lighting_participant(token, date_local)
    return light_service.prepare_direct_upload(participant_id, date_local, filename, size_bytes, sha256, is_test=is_test)


def complete_lighting_direct(token: str, upload_uid: str) -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    if subject["status"] != "running" and not (subject["status"] in {"scheduled", "ready"} and subject["preparation_started_at_utc"]):
        raise ValueError("the completed study portal is read-only")
    return light_service.complete_direct_upload(subject["participant_id"], upload_uid)


def submit_questionnaire(token: str, form_key: str, answers: dict, date_local: str = "") -> dict:
    subject = _resolve_subject(token)
    if not subject:
        raise LookupError("invalid participant link")
    preparation_mode = subject["status"] in {"scheduled", "ready"} and bool(subject["preparation_started_at_utc"])
    closing_mode = subject["status"] == "running" and bool(subject["awaiting_final_morning"])
    if subject["status"] != "running" and not preparation_mode:
        raise ValueError("当前不能提交问卷")
    form = get_form(form_key)
    if not form:
        raise LookupError("questionnaire not found")
    local_now = datetime.now(ZoneInfo(settings.study_timezone))
    if form_key == "s1":
        if not preparation_mode:
            raise ValueError("S1 is available only during preparation")
        exposure_day = local_now.date()
        assignment = {"date_local": exposure_day.isoformat(), "calendar_date_local": exposure_day.isoformat(), "experiment_date_local": exposure_day.isoformat(), "study_day": 0, "total_days": 0}
    elif form_key == "s2":
        if not closing_mode or not subject["final_end"]:
            raise ValueError("S2 is available only after formal exposure ends")
        exposure_day = date.fromisoformat(subject["final_end"])
        assignment = form_assignment(subject, form_key, exposure_day, local_now.date())
    else:
        if closing_mode and form_key != "morning":
            raise ValueError("only final morning and S2 remain after exposure ends")
        if closing_mode:
            exposure_day = date.fromisoformat(subject["final_end"])
        else:
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
            if form_key == "s1":
                conn.execute("UPDATE study_subjects SET s1_status='completed',updated_at_utc=? WHERE participant_id=?", (submitted, subject["participant_id"]))
                refresh_subject_ready(conn, subject["participant_id"], submitted)
    except sqlite3.IntegrityError as exc:
        raise ValueError("该实验日的这份问卷已经提交，无需重复填写") from exc
    return {"ok": True, "form_key": form_key, **assignment, "submitted_at_utc": submitted}
