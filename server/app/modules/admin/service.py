from __future__ import annotations

import json
import re
import secrets
import base64
import binascii
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import db
from app.core.identity_db import identity_db
from app.core.security import decrypt_credential, encrypt_credential, generate_secret, hash_secret
from app.modules.participant import service as participant_service
from app.modules.gps import service as gps_service
from app.modules.questionnaire import s0_import
from app.modules.light import service as light_service

SUBJECT_RE = re.compile(r"^\d{3}$")
PACK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,15}$")
CANDIDATE_EDIT_FIELDS = [
    "name", "phone", "wechat", "source", "sex", "age_group", "identity_type",
    "work_district", "home_district", "phone_os", "pickup_method", "availability", "notes",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def local_today() -> str:
    return datetime.now(ZoneInfo(settings.study_timezone)).date().isoformat()


def audit(operator: str, action: str, entity_type: str = "", entity_id: str = "", detail: dict | None = None):
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log(occurred_at_utc,operator_username,action,entity_type,entity_id,detail_json) VALUES(?,?,?,?,?,?)",
            (now_iso(), operator, action, entity_type, entity_id, json.dumps(detail or {}, ensure_ascii=False)),
        )


def _questionnaire_counts(conn, participant_ids: list[str]) -> dict[str, int]:
    if not participant_ids:
        return {}
    placeholders = ",".join("?" for _ in participant_ids)
    morning_date = participant_service.form_target_date("morning").isoformat()
    evening_date = participant_service.form_target_date("evening").isoformat()
    rows = conn.execute(
        f"""SELECT participant_id,COUNT(*) n FROM questionnaire_responses
            WHERE participant_id IN ({placeholders})
              AND ((form_key='morning' AND date_local=?) OR (form_key='evening' AND date_local=?))
            GROUP BY participant_id""",
        [*participant_ids, morning_date, evening_date],
    ).fetchall()
    return {r["participant_id"]: int(r["n"]) for r in rows}


def dashboard() -> dict[str, Any]:
    today = local_today()
    morning_date = participant_service.form_target_date("morning").isoformat()
    evening_date = participant_service.form_target_date("evening").isoformat()
    with identity_db() as conn:
        candidates = conn.execute("SELECT COUNT(*) n FROM candidates WHERE in_latest_snapshot=1 OR linked_participant_id IS NOT NULL").fetchone()["n"]
    with db() as conn:
        active = conn.execute("SELECT COUNT(*) n FROM study_subjects WHERE status='running'").fetchone()["n"]
        scheduled = conn.execute("SELECT COUNT(*) n FROM study_subjects WHERE status='scheduled'").fetchone()["n"]
        open_incidents = conn.execute("SELECT COUNT(*) n FROM incidents WHERE status!='closed'").fetchone()["n"]
        available_packs = conn.execute("SELECT COUNT(*) n FROM device_packs WHERE status='available'").fetchone()["n"]
        gps_subjects = conn.execute("SELECT COUNT(DISTINCT participant_id) n FROM gps_locations").fetchone()["n"]
        questionnaire_today = conn.execute(
            """SELECT COUNT(*) n FROM questionnaire_responses
               WHERE (form_key='morning' AND date_local=?) OR (form_key='evening' AND date_local=?)""",
            (morning_date, evening_date),
        ).fetchone()["n"]
        recent_incidents = [dict(r) for r in conn.execute("SELECT * FROM incidents WHERE status!='closed' ORDER BY updated_at_utc DESC LIMIT 12")]
        running = [dict(r) for r in conn.execute("SELECT * FROM study_subjects WHERE status='running' ORDER BY start_date, participant_id LIMIT 30")]
        counts = _questionnaire_counts(conn, [r["participant_id"] for r in running])
        for s in running:
            s["gps_last_received_at_utc"] = conn.execute("SELECT MAX(received_at_utc) x FROM gps_locations WHERE participant_id=?", (s["participant_id"],)).fetchone()["x"]
            s["questionnaire_today_completed"] = counts.get(s["participant_id"], 0)
            s["portal_enabled"] = bool(s.get("portal_token_id"))
            for secret_field in ("portal_token_id", "portal_token_salt", "portal_token_hash", "portal_token_ciphertext", "portal_token_created_at_utc"):
                s.pop(secret_field, None)
    return {
        "metrics": {
            "candidates": candidates,
            "scheduled": scheduled,
            "running": active,
            "open_incidents": open_incidents,
            "available_packs": available_packs,
            "gps_subjects": gps_subjects,
            "questionnaire_today": questionnaire_today,
        },
        "running": running,
        "incidents": recent_incidents,
    }


def list_candidates():
    with identity_db() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT candidate_uid,linked_participant_id,name,phone,wechat,source,sex,age_group,
                      beijing_based,identity_type,education,health_rating,work_schedule,chronotype,activity_mode,
                      fixed_position_ratio,screen_time_ratio,indoor_daylight,artificial_light_reliance,
                      outdoor_time,exposure_mechanism,work_district,home_district,commute_mode,
                      commute_duration,phone_os,willingness,pickup_method,
                      availability,notes,source_seq,in_latest_snapshot,created_at_utc,updated_at_utc
               FROM candidates
               ORDER BY in_latest_snapshot DESC, linked_participant_id IS NOT NULL DESC, updated_at_utc DESC"""
        )]


def import_s0_file(data: dict[str, Any], operator: str) -> dict:
    filename = str(data.get("filename") or "").strip()
    encoded = str(data.get("content_b64") or "")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("S0 文件内容不是有效的 base64") from exc
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("S0 文件超过 20 MB，请确认选择了正确的问卷星累计表")
    result = s0_import.import_s0(filename, raw, operator)
    audit(operator, "s0.import", "s0_import", result["import_uid"], {key: result[key] for key in ("total", "imported", "filtered", "duplicate")})
    return result


def add_candidate(data: dict[str, Any], operator: str):
    uid = data.get("candidate_uid") or f"cand_{secrets.token_hex(6)}"
    now = now_iso()
    values = [str(data.get(field) or "").strip() for field in CANDIDATE_EDIT_FIELDS]
    with identity_db() as conn:
        conn.execute(
            f"INSERT INTO candidates(candidate_uid,{','.join(CANDIDATE_EDIT_FIELDS)},created_at_utc,updated_at_utc) VALUES(?{',?'*len(CANDIDATE_EDIT_FIELDS)},?,?)",
            [uid, *values, now, now],
        )
    audit(operator, "candidate.create", "candidate", uid)
    return uid


def update_candidate(candidate_uid: str, data: dict[str, Any], operator: str):
    now = now_iso()
    with identity_db() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
        if not row:
            raise ValueError("candidate not found")
        values = [str(data.get(field, row[field]) or "").strip() for field in CANDIDATE_EDIT_FIELDS]
        assignments = ",".join(f"{field}=?" for field in CANDIDATE_EDIT_FIELDS)
        conn.execute(
            f"UPDATE candidates SET {assignments},updated_at_utc=? WHERE candidate_uid=?",
            [*values, now, candidate_uid],
        )
    audit(operator, "candidate.update", "candidate", candidate_uid)


def list_subjects():
    today = local_today()
    today_date = date.fromisoformat(today)
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM study_subjects ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, participant_id")]
        counts = _questionnaire_counts(conn, [r["participant_id"] for r in rows])
        gps_last = {
            r["participant_id"]: r
            for r in conn.execute(
                """SELECT g.participant_id,g.recorded_at_utc,g.received_at_utc
                   FROM gps_locations g
                   JOIN (SELECT participant_id,MAX(received_at_utc) received_at_utc
                         FROM gps_locations GROUP BY participant_id) latest
                     ON latest.participant_id=g.participant_id AND latest.received_at_utc=g.received_at_utc"""
            )
        }
        for row in rows:
            last = gps_last.get(row["participant_id"])
            gps = gps_service.online_state(
                last["received_at_utc"] if last else None,
                last["recorded_at_utc"] if last else None,
            )
            row["gps_status"] = gps["status"]
            row["gps_last_received_at_utc"] = gps["last_received_at_utc"]
            row["gps_delivery_delay_seconds"] = gps["delivery_delay_seconds"]
            row["study_day"] = None
            if row["status"] == "running" and row["start_date"]:
                try:
                    study_day = (today_date - date.fromisoformat(row["start_date"])).days + 1
                    row["study_day"] = study_day if study_day >= 1 else None
                except ValueError:
                    pass
            row["questionnaire_today_completed"] = counts.get(row["participant_id"], 0)
            row["lighting_today"] = light_service.portal_light_state(row["participant_id"], today)
            row["portal_enabled"] = bool(row.get("portal_token_id"))
            for secret_field in ("portal_token_id", "portal_token_salt", "portal_token_hash", "portal_token_ciphertext", "portal_token_created_at_utc"):
                row.pop(secret_field, None)
    with identity_db() as conn:
        names = {r["linked_participant_id"]: {"name":r["name"],"phone":r["phone"],"wechat":r["wechat"]} for r in conn.execute("SELECT linked_participant_id,name,phone,wechat FROM candidates WHERE linked_participant_id IS NOT NULL")}
    for row in rows:
        row.update(names.get(row["participant_id"], {}))
    return rows


def gps_track(participant_id: str, hours: int) -> dict[str, Any]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone():
            raise LookupError("participant not found")
    return gps_service.track_diagnostic(participant_id, hours)


def update_subject(participant_id: str, data: dict[str, Any], operator: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone()
        if not row:
            raise ValueError("participant not found")
        actual_period = row["status"] != "scheduled" or bool(row["start_date"])
        current_start = row["start_date"] if actual_period else row["expected_start"]
        current_end = row["end_date"] if actual_period else row["expected_end"]
        planned_start = str(data.get("planned_start", current_start) or "").strip()
        planned_end = str(data.get("planned_end", current_end) or "").strip()
        for label, value in (("开始", planned_start), ("结束", planned_end)):
            if not value:
                continue
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{label}日期必须使用 YYYY-MM-DD") from exc
        if planned_start and planned_end and planned_end < planned_start:
            raise ValueError("结束日期不能早于开始日期")
        start_field = "start_date" if actual_period else "expected_start"
        end_field = "end_date" if actual_period else "expected_end"
        now = now_iso()
        conn.execute(
            f"UPDATE study_subjects SET batch_id=?,assigned_ra=?,notes=?,{start_field}=?,{end_field}=?,updated_at_utc=? WHERE participant_id=?",
            (
                str(data.get("batch_id", row["batch_id"]) or "").strip(),
                str(data.get("assigned_ra", row["assigned_ra"]) or "").strip(),
                str(data.get("notes", row["notes"]) or "").strip(),
                planned_start,
                planned_end,
                now,
                participant_id,
            ),
        )
        if actual_period and row["pack_id"]:
            conn.execute(
                "UPDATE device_packs SET issued_date=?,expected_return_date=?,updated_at_utc=? WHERE pack_id=? AND current_participant_id=?",
                (planned_start, planned_end, now, row["pack_id"], participant_id),
            )
    audit(operator, "subject.update", "participant", participant_id)


def promote_candidate(candidate_uid: str, data: dict[str, Any], operator: str):
    sid = str(data.get("participant_id") or "").strip()
    if not SUBJECT_RE.fullmatch(sid):
        raise ValueError("participant_id must be three digits, e.g. 006")
    now = now_iso()
    with identity_db() as conn:
        cand = conn.execute("SELECT * FROM candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
        if not cand:
            raise ValueError("candidate not found")
    with db() as conn:
        if conn.execute("SELECT 1 FROM study_subjects WHERE participant_id=?", (sid,)).fetchone():
            raise ValueError("participant_id already exists")
        conn.execute(
            """INSERT INTO study_subjects(participant_id,candidate_uid,status,batch_id,expected_start,expected_end,pack_id,assigned_ra,notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (sid,candidate_uid,"scheduled",str(data.get("batch_id") or ""),str(data.get("expected_start") or ""),str(data.get("expected_end") or ""),str(data.get("pack_id") or ""),str(data.get("assigned_ra") or ""),str(data.get("notes") or ""),now,now),
        )
    with identity_db() as conn:
        conn.execute("UPDATE candidates SET linked_participant_id=?,updated_at_utc=? WHERE candidate_uid=?", (sid,now,candidate_uid))
    audit(operator, "candidate.promote", "participant", sid, {"candidate_uid": candidate_uid})
    return sid


def create_or_rotate_gps_credential(participant_id: str, operator: str) -> str:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone():
            raise ValueError("participant not found")
        secret = generate_secret()
        salt, digest = hash_secret(secret)
        ciphertext = encrypt_credential(secret)
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,secret_ciphertext,is_active,created_at_utc) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(participant_id) DO UPDATE SET secret_salt=excluded.secret_salt,secret_hash=excluded.secret_hash,secret_ciphertext=excluded.secret_ciphertext,is_active=1",
            (participant_id, salt, digest, ciphertext, 1, now_iso()),
        )
    audit(operator, "gps.credential.rotate", "participant", participant_id)
    return secret


def create_portal_link(participant_id: str, operator: str) -> str:
    token = participant_service.generate_portal_token(participant_id)
    audit(operator, "participant.portal.rotate", "participant", participant_id)
    return f"/p/{token}"


def reveal_credentials(participant_id: str, operator: str) -> dict:
    with db() as conn:
        subject = conn.execute(
            "SELECT portal_token_id,portal_token_ciphertext FROM study_subjects WHERE participant_id=?",
            (participant_id,),
        ).fetchone()
        if not subject:
            raise ValueError("participant not found")
        gps = conn.execute(
            "SELECT secret_ciphertext FROM participants WHERE participant_id=?",
            (participant_id,),
        ).fetchone()
    gps_password = decrypt_credential(gps["secret_ciphertext"]) if gps and gps["secret_ciphertext"] else ""
    portal_token = decrypt_credential(subject["portal_token_ciphertext"]) if subject["portal_token_ciphertext"] else ""
    audit(operator, "participant.credentials.view", "participant", participant_id)
    return {
        "participant_id": participant_id,
        "gps_exists": gps is not None,
        "gps_password": gps_password,
        "portal_exists": bool(subject["portal_token_id"]),
        "portal_path": f"/p/{portal_token}" if portal_token else "",
    }


def onboarding_card(participant_id: str, operator: str, origin: str) -> dict:
    credentials = reveal_credentials(participant_id, operator)
    with db() as conn:
        subject = conn.execute("SELECT * FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone()
        if not subject:
            raise ValueError("participant not found")
        device = conn.execute("SELECT * FROM device_packs WHERE pack_id=?", (subject["pack_id"],)).fetchone()
    configured_domain = settings.domain.strip().rstrip("/")
    if configured_domain and configured_domain not in {"localhost", "127.0.0.1"}:
        origin = f"https://{configured_domain}"
    endpoint = f"{origin.rstrip('/')}/api/v1/gps/owntracks"
    portal_url = f"{origin.rstrip('/')}{credentials['portal_path']}" if credentials["portal_path"] else ""
    config = {
        "_type": "configuration", "mode": 3, "auth": True,
        "url": endpoint, "username": participant_id, "password": credentials["gps_password"],
        "deviceId": participant_id, "tid": participant_id[-2:],
        "locatorInterval": 10, "locatorDisplacement": 10, "adapt": 0, "downgrade": 0,
    }
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    device_text = " / ".join(filter(None, [subject["pack_id"], device["light_serial"] if device else "", device["ax3_serial"] if device else ""])) or "未登记"
    return {
        **credentials, "status": subject["status"], "start_date": subject["start_date"], "end_date": subject["end_date"],
        "pack_id": subject["pack_id"], "light_serial": device["light_serial"] if device else "", "ax3_serial": device["ax3_serial"] if device else "",
        "gps_url": endpoint, "portal_url": portal_url, "owntracks_config": config, "owntracks_config_json": config_json,
        "owntracks_uri": "owntracks:///config?inline=" + base64.b64encode(config_json.encode()).decode(),
        "contact_text": (
            f"LEHUE 入组信息\n被试：{participant_id}\n实验日期：{subject['start_date']} 至 {subject['end_date']}\n设备：{device_text}\n"
            f"被试入口：{portal_url}\n\nOwnTracks（HTTP / Move 模式）\n地址：{endpoint}\n用户名：{participant_id}\n"
            f"密码：{credentials['gps_password']}\n目标间隔：10 秒。请允许始终定位和后台运行，完成配置后发送一次定位。"
        ),
    }


def list_devices():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM device_packs ORDER BY pack_id")]


def upsert_device(data: dict[str, Any], operator: str):
    pack = str(data.get("pack_id") or "").strip().upper()
    if not PACK_RE.fullmatch(pack):
        raise ValueError("pack_id should look like D01 or PACK-01")
    now = now_iso()
    with db() as conn:
        existing = conn.execute("SELECT status,current_participant_id FROM device_packs WHERE pack_id=?", (pack,)).fetchone()
        requested_status = str(data.get("status") or "available")
        if existing and existing["current_participant_id"] and requested_status != existing["status"]:
            raise ValueError("occupied device status is managed by starting or completing the study")
        status = existing["status"] if existing and existing["current_participant_id"] else requested_status
        conn.execute(
            """INSERT INTO device_packs(pack_id,status,current_participant_id,light_serial,ax3_serial,notes,updated_at_utc)
               VALUES(?,?,'',?,?,?,?) ON CONFLICT(pack_id) DO UPDATE SET status=excluded.status,light_serial=excluded.light_serial,ax3_serial=excluded.ax3_serial,notes=excluded.notes,updated_at_utc=excluded.updated_at_utc""",
            (pack,status,str(data.get("light_serial") or ""),str(data.get("ax3_serial") or ""),str(data.get("notes") or ""),now),
        )
    audit(operator, "device.upsert", "device_pack", pack)
    return pack


def start_subject(participant_id: str, data: dict[str, Any], operator: str, origin: str):
    pack = str(data.get("pack_id") or "").strip().upper()
    start = str(data.get("start_date") or "").strip()
    end = str(data.get("end_date") or "").strip()
    if not pack or not start or not end:
        raise ValueError("pack_id, start_date and end_date are required")
    try:
        start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("start_date and end_date must use YYYY-MM-DD") from exc
    if end_day < start_day:
        raise ValueError("end_date cannot be before start_date")
    now = now_iso()
    with db() as conn:
        sub = conn.execute("SELECT * FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone()
        if not sub: raise ValueError("participant not found")
        if sub["status"] != "scheduled":
            raise ValueError("only a scheduled participant can be started")
        dev = conn.execute("SELECT * FROM device_packs WHERE pack_id=?", (pack,)).fetchone()
        if not dev: raise ValueError("device pack not found")
        if dev["status"] != "available" or dev["current_participant_id"]:
            raise ValueError("device pack is not available")
        holder = conn.execute("SELECT participant_id FROM study_subjects WHERE status='running' AND pack_id=? AND participant_id<>?", (pack,participant_id)).fetchone()
        if holder: raise ValueError(f"device pack is already used by {holder['participant_id']}")
        gps = conn.execute("SELECT secret_ciphertext FROM participants WHERE participant_id=?", (participant_id,)).fetchone()
        if not gps or not gps["secret_ciphertext"]:
            gps_secret = generate_secret()
            gps_salt, gps_hash = hash_secret(gps_secret)
            conn.execute(
                "INSERT INTO participants(participant_id,secret_salt,secret_hash,secret_ciphertext,is_active,created_at_utc) VALUES(?,?,?,?,1,?) "
                "ON CONFLICT(participant_id) DO UPDATE SET secret_salt=excluded.secret_salt,secret_hash=excluded.secret_hash,secret_ciphertext=excluded.secret_ciphertext,is_active=1",
                (participant_id, gps_salt, gps_hash, encrypt_credential(gps_secret), now),
            )
        if not sub["portal_token_ciphertext"]:
            selector, portal_secret = secrets.token_hex(8), secrets.token_urlsafe(24)
            portal_salt, portal_hash = hash_secret(portal_secret)
            conn.execute(
                "UPDATE study_subjects SET portal_token_id=?,portal_token_salt=?,portal_token_hash=?,portal_token_ciphertext=?,portal_token_created_at_utc=? WHERE participant_id=?",
                (selector, portal_salt, portal_hash, encrypt_credential(f"{selector}.{portal_secret}"), now, participant_id),
            )
        conn.execute("UPDATE study_subjects SET status='running',pack_id=?,start_date=?,end_date=?,updated_at_utc=? WHERE participant_id=?", (pack,start,end,now,participant_id))
        conn.execute("UPDATE device_packs SET status='running',current_participant_id=?,issued_date=?,expected_return_date=?,updated_at_utc=? WHERE pack_id=?", (participant_id,start,end,now,pack))
    audit(operator, "participant.start", "participant", participant_id, {"pack_id":pack,"start_date":start,"end_date":end})
    return onboarding_card(participant_id, operator, origin)


def complete_subject(participant_id: str, operator: str) -> dict[str, Any]:
    now = now_iso()
    returned = local_today()
    with db() as conn:
        subject = conn.execute("SELECT * FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone()
        if not subject:
            raise ValueError("participant not found")
        if subject["status"] != "running":
            raise ValueError("only a running participant can be completed")
        conn.execute(
            "UPDATE study_subjects SET status='completed',final_end=?,updated_at_utc=? WHERE participant_id=?",
            (returned, now, participant_id),
        )
        if subject["pack_id"]:
            conn.execute(
                """UPDATE device_packs SET status='available',current_participant_id='',returned_date=?,updated_at_utc=?
                   WHERE pack_id=? AND current_participant_id=?""",
                (returned, now, subject["pack_id"], participant_id),
            )
        conn.execute("UPDATE participants SET is_active=0 WHERE participant_id=?", (participant_id,))
    audit(operator, "participant.complete", "participant", participant_id, {"pack_id": subject["pack_id"], "returned_date": returned})
    return {"ok": True, "participant_id": participant_id, "status": "completed", "returned_date": returned}


def list_incidents():
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT i.*,s.portal_token_ciphertext FROM incidents i
               LEFT JOIN study_subjects s ON s.participant_id=i.participant_id
               ORDER BY i.status='closed',i.updated_at_utc DESC"""
        )]
    domain = settings.domain.strip().rstrip("/")
    origin = f"https://{domain}" if domain not in {"", "localhost", "127.0.0.1"} else "http://127.0.0.1:8085"
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        ciphertext = row.pop("portal_token_ciphertext", "")
        portal_url = f"{origin}/p/{decrypt_credential(ciphertext)}" if ciphertext else ""
        participant_id, date_local = row["participant_id"], row["date_local"]
        # Records without both canonical grouping fields remain separate rather
        # than collapsing unrelated manual notes into one anonymous group.
        key = (participant_id, date_local, "") if participant_id and date_local else (participant_id, date_local, row["incident_uid"])
        group = groups.setdefault(key, {
            "participant_id": participant_id,
            "date_local": date_local,
            "portal_url": portal_url,
            "issues": [],
            "updated_at_utc": row["updated_at_utc"],
        })
        group["issues"].append({
            name: row[name]
            for name in ("incident_uid", "source", "incident_type", "summary", "notes", "status", "severity")
        })
        summary_prefix = f"{participant_id} 暴露日 {date_local}："
        if group["issues"][-1]["summary"].startswith(summary_prefix):
            group["issues"][-1]["summary"] = group["issues"][-1]["summary"][len(summary_prefix):]
        group["updated_at_utc"] = max(group["updated_at_utc"], row["updated_at_utc"])

    result = []
    for group in groups.values():
        open_issues = [item for item in group["issues"] if item["status"] != "closed"]
        contact_issues = open_issues or group["issues"]
        group["status"] = "open" if open_issues else "closed"
        group["open_count"] = len(open_issues)
        group["issue_count"] = len(group["issues"])
        lines = [str(item["summary"] or item["incident_type"] or item["source"] or "采集情况待确认") for item in contact_issues]
        numbered = "\n".join(f"{index}. {summary}" for index, summary in enumerate(lines, 1))
        group["contact_text"] = (
            f"LEHUE 采集提醒｜被试 {group['participant_id']}\n"
            f"{group['date_local']} 实验日有 {len(lines)} 项需要确认：\n{numbered}\n"
            "请打开被试入口补充或检查；如已处理可忽略。"
            + (f"\n入口：{group['portal_url']}" if group["portal_url"] else "")
        )
        result.append(group)
    result.sort(key=lambda group: group["updated_at_utc"], reverse=True)
    result.sort(key=lambda group: group["status"] == "closed")
    return result


def add_incident(data: dict[str, Any], operator: str):
    uid = str(data.get("incident_uid") or f"inc_{secrets.token_hex(6)}")
    now = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO incidents(incident_uid,participant_id,date_local,source,incident_type,severity,status,assigned_ra,summary,notes,created_at_utc,updated_at_utc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,str(data.get("participant_id") or ""),str(data.get("date_local") or ""),str(data.get("source") or "manual"),str(data.get("incident_type") or "other"),str(data.get("severity") or "normal"),"open",str(data.get("assigned_ra") or ""),str(data.get("summary") or ""),str(data.get("notes") or ""),now,now),
        )
    audit(operator, "incident.create", "incident", uid)
    return uid


def update_incident_status(uid: str, status: str, operator: str):
    if status not in {"open", "closed"}:
        raise ValueError("invalid incident status")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM incidents WHERE incident_uid=?", (uid,)).fetchone():
            raise ValueError("incident not found")
        conn.execute("UPDATE incidents SET status=?,updated_at_utc=? WHERE incident_uid=?", (status,now_iso(),uid))
    audit(operator, "incident.status", "incident", uid, {"status":status})


def list_lighting_uploads(participant_id: str = "", date_local: str = ""):
    return light_service.list_uploads(participant_id, date_local)


def upload_lighting_path(participant_id: str, date_local: str, filename: str, path: Path, operator: str):
    result = light_service.store_upload_path(participant_id, date_local, filename, path, f"admin:{operator}")
    audit(operator, "lighting.upload", "lighting_file", result["upload_uid"], {"participant_id": participant_id, "date_local": date_local, "quality": result["quality"]})
    return result


def rerun_lighting_qc(upload_uid: str, operator: str):
    result = light_service.rerun_qc(upload_uid)
    audit(operator, "lighting.qc.rerun", "lighting_file", upload_uid, {"quality": result["quality"]})
    return result


def daily_qc(run: bool, operator: str = ""):
    result = light_service.run_daily_qc(operator) if run else None
    rows = result["rows"] if result else light_service.daily_qc_rows()
    with db() as conn:
        encrypted_tokens = {
            row["participant_id"]: row["portal_token_ciphertext"]
            for row in conn.execute("SELECT participant_id,portal_token_ciphertext FROM study_subjects WHERE portal_token_ciphertext<>''")
        }
    domain = settings.domain.strip().rstrip("/")
    origin = f"https://{domain}" if domain not in {"", "localhost", "127.0.0.1"} else "http://127.0.0.1:8085"
    for row in rows:
        ciphertext = encrypted_tokens.get(row["participant_id"])
        row["portal_url"] = f"{origin}/p/{decrypt_credential(ciphertext)}" if ciphertext else ""
    summary = result["summary"] if result else {
        "total": len(rows),
        "ok": sum(row["status"] == "ok" for row in rows),
        "missing": sum(row["status"] == "missing" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
    }
    if run:
        audit(operator, "acquisition_qc.run", "daily_qc", "", summary)
    return {"rows": rows, "summary": summary, **({"operator": result["operator"]} if result else {})}
