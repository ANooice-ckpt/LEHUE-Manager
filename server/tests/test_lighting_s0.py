import importlib
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _reload(monkeypatch, td: str):
    monkeypatch.setenv("DATA_DIR", td)
    monkeypatch.setenv("DB_PATH", str(Path(td) / "main.sqlite3"))
    monkeypatch.setenv("IDENTITY_DB_PATH", str(Path(td) / "identity.sqlite3"))
    monkeypatch.setenv("RAW_ARCHIVE_DIR", str(Path(td) / "raw" / "gps"))
    monkeypatch.setenv("RAW_LIGHT_DIR", str(Path(td) / "raw" / "lighting"))
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.modules.light.service as light; importlib.reload(light)
    import app.modules.questionnaire.s0_import as s0; importlib.reload(s0)
    import app.modules.participant.service as portal; importlib.reload(portal)
    import app.modules.participant.router as portal_router; importlib.reload(portal_router)
    import app.modules.admin.service as admin_service; importlib.reload(admin_service)
    import app.modules.admin.router as admin_router; importlib.reload(admin_router)
    import app.modules.gps.router as gps_router; importlib.reload(gps_router)
    import app.main as main; importlib.reload(main)
    dbmod.init_db(); idb.init_identity_db()
    return dbmod, idb, light, s0, portal, main


def _valid_light_csv() -> bytes:
    rows = ["Photopic Lux,Melanopic,Is Saturate"]
    rows.extend(f"{100 + i % 7},{80 + i % 5},No" for i in range(6480))
    return ("\n".join(rows) + "\n").encode()


def test_lighting_upload_and_daily_qc(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        dbmod, _, light, _, portal, main = _reload(monkeypatch, td)
        zone = ZoneInfo("Asia/Shanghai")
        today = datetime.now(zone).date()
        yesterday = today - timedelta(days=1)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with dbmod.db() as conn:
            conn.execute(
                "INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", "running", yesterday.isoformat(), (today + timedelta(days=12)).isoformat(), now, now),
            )
            conn.execute("INSERT INTO participants VALUES(?,?,?,?,?)", ("001", "salt", "hash", 1, now))
            conn.execute(
                "INSERT INTO raw_events(participant_id,event_uid,message_type,recorded_at_utc,received_at_utc,raw_json,archive_ok) VALUES(?,?,?,?,?,?,1)",
                ("001", "event-1", "location", f"{yesterday.isoformat()}T04:00:00Z", now, "{}"),
            )
            raw_id = conn.execute("SELECT id FROM raw_events WHERE event_uid='event-1'").fetchone()["id"]
            conn.execute(
                "INSERT INTO gps_locations(raw_event_id,participant_id,recorded_at_utc,received_at_utc,lat,lon) VALUES(?,?,?,?,?,?)",
                (raw_id, "001", f"{yesterday.isoformat()}T04:00:00Z", now, 39.9, 116.4),
            )
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", yesterday.isoformat(), 1, "evening", "{}", now),
            )
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", today.isoformat(), 2, "morning", "{}", now),
            )

        raw = _valid_light_csv()
        parsed = light.parse_light_bytes("001_20260810_LIGHT.csv", raw)
        assert parsed["records_total"] == 6480
        assert parsed["records_valid"] == 6480
        assert parsed["valid_pct"] == 90.0
        assert parsed["quality"] == "valid"
        light.store_upload("001", yesterday.isoformat(), f"001_{yesterday.strftime('%Y%m%d')}_LIGHT.csv", raw, "test")

        token = portal.generate_portal_token("001")
        from fastapi.testclient import TestClient
        with TestClient(main.app) as client:
            filename = f"001_{today.strftime('%Y%m%d')}_LIGHT.csv"
            url = f"/api/v1/portal/{token}/lighting?date_local={today.isoformat()}&filename={filename}"
            uploaded = client.post(url, content=raw, headers={"Content-Type": "application/octet-stream"})
            assert uploaded.status_code == 200
            assert uploaded.json()["quality"] == "valid"
            assert client.post(url, content=raw).json()["duplicate"] is True
            mismatch = client.post(
                f"/api/v1/portal/{token}/lighting?date_local={today.isoformat()}&filename=002_{today.strftime('%Y%m%d')}_LIGHT.csv",
                content=raw,
            )
            assert mismatch.status_code == 400
            assert client.get(f"/api/v1/portal/{token}").json()["lighting"]["status"] == "done"

        qc = light.run_daily_qc("tester")
        yesterday_row = next(row for row in qc["rows"] if row["date_local"] == yesterday.isoformat())
        today_row = next(row for row in qc["rows"] if row["date_local"] == today.isoformat())
        assert yesterday_row["status"] == "ok"
        assert today_row["status"] == "pending"
        with dbmod.db() as conn:
            assert conn.execute("SELECT valid_days FROM study_subjects WHERE participant_id='001'").fetchone()["valid_days"] == 1


def test_s0_snapshot_import_preserves_candidate_identity(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, idb, _, s0, _, _ = _reload(monkeypatch, td)
        header = "序号,您的年龄,您的性别,16、您是否愿意参与本研究并接受补贴？,19、您的手机号（仅实验负责人可见）：,您的手机系统是：,姓名或称呼\n"
        first = (header + "1,20-29,女,愿意,138 0000 0001,iOS,候选甲\n2,30-39,男,不愿意,13800000002,Android,候选乙\n").encode("utf-8-sig")
        result = s0.import_s0("S0.csv", first, "pi")
        assert result == {"import_uid": result["import_uid"], "total": 2, "imported": 1, "filtered": 1, "duplicate": False}
        assert s0.import_s0("S0.csv", first, "pi")["duplicate"] is True
        with idb.identity_db() as conn:
            original = dict(conn.execute("SELECT * FROM candidates").fetchone())
            conn.execute("UPDATE candidates SET linked_participant_id='001',name='人工校正姓名',notes='已联系' WHERE candidate_uid=?", (original["candidate_uid"],))

        second = (header + "1,20-29,女,愿意,13800000001,iOS,问卷新姓名\n3,20-29,男,愿意,13800000003,Android,候选丙\n").encode("utf-8-sig")
        result2 = s0.import_s0("S0_new.csv", second, "ra01")
        assert result2["imported"] == 2
        with idb.identity_db() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM candidates ORDER BY source_seq")]
            assert len(rows) == 2
            assert rows[0]["candidate_uid"] == original["candidate_uid"]
            assert rows[0]["linked_participant_id"] == "001"
            assert rows[0]["name"] == "人工校正姓名"
            assert rows[0]["notes"] == "已联系"
            assert conn.execute("SELECT COUNT(*) n FROM s0_imports").fetchone()["n"] == 2

