from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db
from app.core.identity_db import identity_db, init_identity_db
from app.modules.light.service import ALLOWED_EXTENSIONS, infer_date, infer_subject_id, store_upload


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sid(v):
    x=str(v or "").strip().upper()
    if x.startswith("P"): x=x[1:]
    return x.zfill(3) if x.isdigit() else x


def main():
    ap=argparse.ArgumentParser(description="Import the operational portion of ANOLighting V5 state.json")
    ap.add_argument("state_json")
    ap.add_argument("--light-dir", help="Optional V5 Lighting raw-data directory to copy and parse")
    args=ap.parse_args()
    state=json.loads(Path(args.state_json).read_text(encoding="utf-8"))
    init_db(); init_identity_db(); now=now_iso()
    counts={"candidates":0,"subjects":0,"devices":0,"incidents":0,"lighting":0,"lighting_skipped":0}

    candidate_raw = state.get("forms", {}).get("candidate_raw", []) or []

    with identity_db() as conn:
        for c in state.get("candidates",[]):
            uid=str(c.get("candidate_uid") or f"legacy_{counts['candidates']+1}")
            linked=sid(c.get("linked_subject_id")) or None
            row_index = c.get("_row_index")
            raw_row = candidate_raw[row_index] if isinstance(row_index, int) and 0 <= row_index < len(candidate_raw) else {}
            conn.execute("""INSERT INTO candidates(candidate_uid,linked_participant_id,name,phone,wechat,source,sex,age_group,identity_type,light_type,work_district,home_district,phone_os,pickup_method,availability,notes,source_seq,in_latest_snapshot,s0_raw_json,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(candidate_uid) DO UPDATE SET linked_participant_id=excluded.linked_participant_id,name=excluded.name,phone=excluded.phone,wechat=excluded.wechat,source=excluded.source,sex=excluded.sex,age_group=excluded.age_group,identity_type=excluded.identity_type,light_type=excluded.light_type,work_district=excluded.work_district,home_district=excluded.home_district,phone_os=excluded.phone_os,pickup_method=excluded.pickup_method,availability=excluded.availability,notes=excluded.notes,source_seq=excluded.source_seq,in_latest_snapshot=excluded.in_latest_snapshot,s0_raw_json=excluded.s0_raw_json,updated_at_utc=excluded.updated_at_utc""",
            (uid,linked,c.get("name",''),c.get("phone",''),c.get("wechat",''),c.get("source",''),c.get("sex",''),c.get("age_group",''),c.get("identity_type",''),c.get("light_type",''),c.get("work_district",''),c.get("home_district",''),c.get("phone_os",''),c.get("pickup_method",''),c.get("availability",''),c.get("notes",''),c.get("source_seq",''),int(c.get("in_latest_snapshot",True)),json.dumps(raw_row,ensure_ascii=False,separators=(",",":")),now,now))
            counts["candidates"]+=1

    with db() as conn:
        for s in state.get("subjects",[]):
            p=sid(s.get("subject_id"))
            if not p: continue
            conn.execute("""INSERT INTO study_subjects(participant_id,candidate_uid,status,batch_id,expected_start,expected_end,start_date,end_date,final_end,pack_id,assigned_ra,s1_status,latest_data_status,valid_days,completion_type,compensation,notes,awaiting_final_morning,close_notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(participant_id) DO UPDATE SET candidate_uid=excluded.candidate_uid,status=excluded.status,batch_id=excluded.batch_id,expected_start=excluded.expected_start,expected_end=excluded.expected_end,start_date=excluded.start_date,end_date=excluded.end_date,final_end=excluded.final_end,pack_id=excluded.pack_id,assigned_ra=excluded.assigned_ra,s1_status=excluded.s1_status,latest_data_status=excluded.latest_data_status,valid_days=excluded.valid_days,completion_type=excluded.completion_type,compensation=excluded.compensation,notes=excluded.notes,awaiting_final_morning=excluded.awaiting_final_morning,close_notes=excluded.close_notes,updated_at_utc=excluded.updated_at_utc""",
            (p,s.get("candidate_uid",''),s.get("status",'scheduled'),s.get("batch_id",''),s.get("expected_start",''),s.get("expected_end",''),s.get("start_date",''),s.get("end_date",''),s.get("final_end",''),s.get("pack_id",''),s.get("assigned_ra",''),s.get("s1_status",''),s.get("latest_data_status",''),int(s.get("valid_days") or 0),s.get("completion_type",''),s.get("compensation",''),s.get("notes",''),int(bool(s.get("awaiting_final_morning"))),s.get("close_notes",''),now,now))
            counts["subjects"]+=1
        for d in state.get("devices",[]):
            pack=str(d.get("pack_id") or '').strip().upper()
            if not pack: continue
            conn.execute("""INSERT INTO device_packs(pack_id,status,current_participant_id,light_serial,ax3_serial,issued_date,expected_return_date,returned_date,notes,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(pack_id) DO UPDATE SET status=excluded.status,current_participant_id=excluded.current_participant_id,light_serial=excluded.light_serial,ax3_serial=excluded.ax3_serial,issued_date=excluded.issued_date,expected_return_date=excluded.expected_return_date,returned_date=excluded.returned_date,notes=excluded.notes,updated_at_utc=excluded.updated_at_utc""",
            (pack,d.get("status",'available'),sid(d.get("current_subject_id")),d.get("light_serial",''),d.get("ax3_serial",''),d.get("issued_date",''),d.get("expected_return_date",''),d.get("returned_date",''),d.get("notes",''),now))
            counts["devices"]+=1
        for i in state.get("issues",[]):
            uid=str(i.get("issue_id") or f"legacy_inc_{counts['incidents']+1}")
            status=i.get("status",'open')
            if status == 'resolved': status='resolved'
            conn.execute("""INSERT INTO incidents(incident_uid,participant_id,date_local,source,incident_type,severity,status,assigned_ra,summary,notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(incident_uid) DO UPDATE SET participant_id=excluded.participant_id,date_local=excluded.date_local,source=excluded.source,incident_type=excluded.incident_type,severity=excluded.severity,status=excluded.status,assigned_ra=excluded.assigned_ra,summary=excluded.summary,notes=excluded.notes,updated_at_utc=excluded.updated_at_utc""",
            (uid,sid(i.get("subject_id")),i.get("date",''),'legacy',i.get("issue_type",''),i.get("severity",'normal'),status,i.get("assigned_ra",''),i.get("message") or i.get("item_label",''),i.get("notes",''),now,now))
            counts["incidents"]+=1
    if args.light_dir:
        known_subjects = {sid(subject.get("subject_id")) for subject in state.get("subjects", [])}
        light_root = Path(args.light_dir)
        if not light_root.is_dir():
            raise SystemExit(f"Lighting directory not found: {light_root}")
        for path in sorted(light_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            participant_id = infer_subject_id(path.name)
            date_local = infer_date(path.name)
            if not participant_id or not date_local or participant_id not in known_subjects:
                counts["lighting_skipped"] += 1
                continue
            store_upload(participant_id,date_local,path.name,path.read_bytes(),"legacy_migration")
            counts["lighting"] += 1
    print(json.dumps(counts,ensure_ascii=False,indent=2))
    print("Not migrated by design: old daily-questionnaire CSV cache, old GPS manifests/parser state, derived daily QC rows, scan_index.")

if __name__ == '__main__': main()
