from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from app.core.db import db
from app.core.identity_db import identity_db
from app.core.security import generate_secret, hash_secret

SUBJECT_RE = re.compile(r"^\d{3}$")
PACK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,15}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def audit(operator: str, action: str, entity_type: str = "", entity_id: str = "", detail: dict | None = None):
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log(occurred_at_utc,operator_username,action,entity_type,entity_id,detail_json) VALUES(?,?,?,?,?,?)",
            (now_iso(), operator, action, entity_type, entity_id, json.dumps(detail or {}, ensure_ascii=False)),
        )


def dashboard() -> dict[str, Any]:
    with identity_db() as conn:
        candidates = conn.execute("SELECT COUNT(*) n FROM candidates").fetchone()["n"]
    with db() as conn:
        active = conn.execute("SELECT COUNT(*) n FROM study_subjects WHERE status='running'").fetchone()["n"]
        scheduled = conn.execute("SELECT COUNT(*) n FROM study_subjects WHERE status='scheduled'").fetchone()["n"]
        open_incidents = conn.execute("SELECT COUNT(*) n FROM incidents WHERE status!='closed'").fetchone()["n"]
        available_packs = conn.execute("SELECT COUNT(*) n FROM device_packs WHERE status='available'").fetchone()["n"]
        gps_subjects = conn.execute("SELECT COUNT(DISTINCT participant_id) n FROM gps_locations").fetchone()["n"]
        recent_incidents = [dict(r) for r in conn.execute("SELECT * FROM incidents WHERE status!='closed' ORDER BY updated_at_utc DESC LIMIT 12")]
        running = [dict(r) for r in conn.execute("SELECT * FROM study_subjects WHERE status='running' ORDER BY start_date, participant_id LIMIT 30")]
    for s in running:
        with db() as conn:
            last = conn.execute("SELECT MAX(received_at_utc) x FROM gps_locations WHERE participant_id=?", (s["participant_id"],)).fetchone()["x"]
        s["gps_last_received_at_utc"] = last
    return {
        "metrics": {
            "candidates": candidates,
            "scheduled": scheduled,
            "running": active,
            "open_incidents": open_incidents,
            "available_packs": available_packs,
            "gps_subjects": gps_subjects,
        },
        "running": running,
        "incidents": recent_incidents,
    }


def list_candidates():
    with identity_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM candidates ORDER BY updated_at_utc DESC")]


def add_candidate(data: dict[str, Any], operator: str):
    uid = data.get("candidate_uid") or f"cand_{secrets.token_hex(6)}"
    now = now_iso()
    fields = ["name","phone","wechat","source","sex","age_group","identity_type","light_type","work_district","home_district","phone_os","pickup_method","availability","notes"]
    values = [str(data.get(f) or "").strip() for f in fields]
    with identity_db() as conn:
        conn.execute(
            f"INSERT INTO candidates(candidate_uid,{','.join(fields)},created_at_utc,updated_at_utc) VALUES(?{',?'*len(fields)},?,?)",
            [uid, *values, now, now],
        )
    audit(operator, "candidate.create", "candidate", uid)
    return uid


def list_subjects():
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM study_subjects ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, participant_id")]
    with identity_db() as conn:
        names = {r["linked_participant_id"]: {"name":r["name"],"phone":r["phone"],"wechat":r["wechat"]} for r in conn.execute("SELECT linked_participant_id,name,phone,wechat FROM candidates WHERE linked_participant_id IS NOT NULL")}
    for row in rows:
        row.update(names.get(row["participant_id"], {}))
        with db() as conn:
            last = conn.execute("SELECT MAX(received_at_utc) x FROM gps_locations WHERE participant_id=?", (row["participant_id"],)).fetchone()["x"]
        row["gps_last_received_at_utc"] = last
    return rows


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


def ensure_gps_credential(participant_id: str, operator: str) -> str:
    with db() as conn:
        row = conn.execute("SELECT 1 FROM participants WHERE participant_id=?", (participant_id,)).fetchone()
        if row:
            raise ValueError("GPS credential already exists; password cannot be recovered. Rotate explicitly later if needed.")
        secret = generate_secret()
        salt, digest = hash_secret(secret)
        conn.execute("INSERT INTO participants(participant_id,secret_salt,secret_hash,is_active,created_at_utc) VALUES(?,?,?,?,?)", (participant_id,salt,digest,1,now_iso()))
    audit(operator, "gps.credential.create", "participant", participant_id)
    return secret


def list_devices():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM device_packs ORDER BY pack_id")]


def upsert_device(data: dict[str, Any], operator: str):
    pack = str(data.get("pack_id") or "").strip().upper()
    if not PACK_RE.fullmatch(pack):
        raise ValueError("pack_id should look like D01 or PACK-01")
    now = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO device_packs(pack_id,status,current_participant_id,light_serial,ax3_serial,notes,updated_at_utc)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(pack_id) DO UPDATE SET status=excluded.status,light_serial=excluded.light_serial,ax3_serial=excluded.ax3_serial,notes=excluded.notes,updated_at_utc=excluded.updated_at_utc""",
            (pack,str(data.get("status") or "available"),str(data.get("current_participant_id") or ""),str(data.get("light_serial") or ""),str(data.get("ax3_serial") or ""),str(data.get("notes") or ""),now),
        )
    audit(operator, "device.upsert", "device_pack", pack)
    return pack


def start_subject(participant_id: str, data: dict[str, Any], operator: str):
    pack = str(data.get("pack_id") or "").strip().upper()
    start = str(data.get("start_date") or "").strip()
    end = str(data.get("end_date") or "").strip()
    if not pack or not start or not end:
        raise ValueError("pack_id, start_date and end_date are required")
    now = now_iso()
    with db() as conn:
        sub = conn.execute("SELECT * FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone()
        if not sub: raise ValueError("participant not found")
        dev = conn.execute("SELECT * FROM device_packs WHERE pack_id=?", (pack,)).fetchone()
        if not dev: raise ValueError("device pack not found")
        if dev["status"] not in {"available","assigned"} and dev["current_participant_id"] != participant_id:
            raise ValueError("device pack is not available")
        holder = conn.execute("SELECT participant_id FROM study_subjects WHERE status='running' AND pack_id=? AND participant_id<>?", (pack,participant_id)).fetchone()
        if holder: raise ValueError(f"device pack is already used by {holder['participant_id']}")
        conn.execute("UPDATE study_subjects SET status='running',pack_id=?,start_date=?,end_date=?,updated_at_utc=? WHERE participant_id=?", (pack,start,end,now,participant_id))
        conn.execute("UPDATE device_packs SET status='running',current_participant_id=?,issued_date=?,expected_return_date=?,updated_at_utc=? WHERE pack_id=?", (participant_id,start,end,now,pack))
    audit(operator, "participant.start", "participant", participant_id, {"pack_id":pack,"start_date":start,"end_date":end})


def list_incidents():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM incidents ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'handling' THEN 1 ELSE 2 END, updated_at_utc DESC")]


def add_incident(data: dict[str, Any], operator: str):
    uid = str(data.get("incident_uid") or f"inc_{secrets.token_hex(6)}")
    now = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO incidents(incident_uid,participant_id,date_local,source,incident_type,severity,status,assigned_ra,summary,notes,created_at_utc,updated_at_utc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,str(data.get("participant_id") or ""),str(data.get("date_local") or ""),str(data.get("source") or "manual"),str(data.get("incident_type") or "other"),str(data.get("severity") or "normal"),str(data.get("status") or "open"),str(data.get("assigned_ra") or ""),str(data.get("summary") or ""),str(data.get("notes") or ""),now,now),
        )
    audit(operator, "incident.create", "incident", uid)
    return uid


def update_incident_status(uid: str, status: str, operator: str):
    if status not in {"open","handling","resolved","closed"}:
        raise ValueError("invalid incident status")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM incidents WHERE incident_uid=?", (uid,)).fetchone():
            raise ValueError("incident not found")
        conn.execute("UPDATE incidents SET status=?,updated_at_utc=? WHERE incident_uid=?", (status,now_iso(),uid))
    audit(operator, "incident.status", "incident", uid, {"status":status})


def architecture() -> dict[str, Any]:
    return {
        "layers": [
            {"id":"participants","label":"Participants / RA / PI","kind":"people"},
            {"id":"web","label":"LEHUE Web Admin","kind":"interface"},
            {"id":"api","label":"FastAPI backend","kind":"service"},
            {"id":"gps","label":"GPS / OwnTracks","kind":"source"},
            {"id":"light","label":"Lighting upload / OSS (reserved)","kind":"source"},
            {"id":"questionnaire","label":"Wenjuanxing manual import (reserved)","kind":"source"},
            {"id":"ax3","label":"AX3 return + local import (reserved)","kind":"source"},
            {"id":"identity","label":"Identity DB","kind":"storage"},
            {"id":"study","label":"Operations + GPS DB","kind":"storage"},
            {"id":"local","label":"Local scientific workstation","kind":"external"},
        ],
        "principle":"Cloud handles study operations and acquisition state; scientific analysis stays local."
    }


def data_sources() -> list[dict[str, Any]]:
    with db() as conn:
        gps_count = conn.execute("SELECT COUNT(*) n FROM gps_locations").fetchone()["n"]
        gps_last = conn.execute("SELECT MAX(received_at_utc) x FROM gps_locations").fetchone()["x"]
    return [
        {"key":"gps","name":"GPS","status":"connected","acquisition":"OwnTracks HTTP/HTTPS realtime","storage":"lehue.sqlite3 + raw JSONL","automation":"Realtime ingest + acquisition QC","last_event":gps_last,"records":gps_count},
        {"key":"light","name":"Lighting","status":"reserved","acquisition":"Participant manual upload","storage":"Future OSS raw objects + server metadata","automation":"Parser/QC adapter reserved","last_event":None,"records":None},
        {"key":"questionnaire","name":"Questionnaire","status":"manual","acquisition":"Wenjuanxing download → import","storage":"Import adapter reserved","automation":"Manual download remains; parsing will be automated","last_event":None,"records":None},
        {"key":"ax3","name":"AX3","status":"offline","acquisition":"Device return → batch download","storage":"Local/raw archive; cloud status only","automation":"Post-return ingest reserved","last_event":None,"records":None},
        {"key":"identity","name":"Identity & contact","status":"connected","acquisition":"PI/RA Web Admin","storage":"lehue_identity.sqlite3","automation":"Separated from GPS/research records","last_event":None,"records":None},
    ]
