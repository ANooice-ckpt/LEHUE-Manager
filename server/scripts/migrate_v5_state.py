from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db
from app.core.identity_db import identity_db, init_identity_db


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sid(v):
    x=str(v or "").strip().upper()
    if x.startswith("P"): x=x[1:]
    return x.zfill(3) if x.isdigit() else x


def main():
    ap=argparse.ArgumentParser(description="Import the operational portion of ANOLighting V5 state.json")
    ap.add_argument("state_json")
    args=ap.parse_args()
    state=json.loads(Path(args.state_json).read_text(encoding="utf-8"))
    init_db(); init_identity_db(); now=now_iso()
    counts={"candidates":0,"subjects":0,"devices":0,"incidents":0}

    with identity_db() as conn:
        for c in state.get("candidates",[]):
            uid=str(c.get("candidate_uid") or f"legacy_{counts['candidates']+1}")
            linked=sid(c.get("linked_subject_id")) or None
            conn.execute("""INSERT OR IGNORE INTO candidates(candidate_uid,linked_participant_id,name,phone,wechat,source,sex,age_group,identity_type,light_type,work_district,home_district,phone_os,pickup_method,availability,notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,linked,c.get("name",''),c.get("phone",''),c.get("wechat",''),c.get("source",''),c.get("sex",''),c.get("age_group",''),c.get("identity_type",''),c.get("light_type",''),c.get("work_district",''),c.get("home_district",''),c.get("phone_os",''),c.get("pickup_method",''),c.get("availability",''),c.get("notes",''),now,now))
            counts["candidates"]+=1

    with db() as conn:
        for s in state.get("subjects",[]):
            p=sid(s.get("subject_id"))
            if not p: continue
            conn.execute("""INSERT OR IGNORE INTO study_subjects(participant_id,candidate_uid,status,batch_id,expected_start,expected_end,start_date,end_date,final_end,pack_id,assigned_ra,s1_status,latest_data_status,valid_days,completion_type,compensation,notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p,s.get("candidate_uid",''),s.get("status",'scheduled'),s.get("batch_id",''),s.get("expected_start",''),s.get("expected_end",''),s.get("start_date",''),s.get("end_date",''),s.get("final_end",''),s.get("pack_id",''),s.get("assigned_ra",''),s.get("s1_status",''),s.get("latest_data_status",''),int(s.get("valid_days") or 0),s.get("completion_type",''),s.get("compensation",''),s.get("notes",''),now,now))
            counts["subjects"]+=1
        for d in state.get("devices",[]):
            pack=str(d.get("pack_id") or '').strip().upper()
            if not pack: continue
            conn.execute("""INSERT OR IGNORE INTO device_packs(pack_id,status,current_participant_id,light_serial,ax3_serial,issued_date,expected_return_date,returned_date,notes,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (pack,d.get("status",'available'),sid(d.get("current_subject_id")),d.get("light_serial",''),d.get("ax3_serial",''),d.get("issued_date",''),d.get("expected_return_date",''),d.get("returned_date",''),d.get("notes",''),now))
            counts["devices"]+=1
        for i in state.get("issues",[]):
            uid=str(i.get("issue_id") or f"legacy_inc_{counts['incidents']+1}")
            status=i.get("status",'open')
            if status == 'resolved': status='resolved'
            conn.execute("""INSERT OR IGNORE INTO incidents(incident_uid,participant_id,date_local,source,incident_type,severity,status,assigned_ra,summary,notes,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,sid(i.get("subject_id")),i.get("date",''),'legacy',i.get("issue_type",''),i.get("severity",'normal'),status,i.get("assigned_ra",''),i.get("message") or i.get("item_label",''),i.get("notes",''),now,now))
            counts["incidents"]+=1
    print(json.dumps(counts,ensure_ascii=False,indent=2))
    print("Not migrated by design: questionnaire parse cache, light/gps manifests, daily scientific/QC records, scan_index.")

if __name__ == '__main__': main()
