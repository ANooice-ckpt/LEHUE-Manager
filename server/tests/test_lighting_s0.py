import importlib
import hashlib
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _reload(monkeypatch, td: str):
    monkeypatch.setenv("DATA_DIR", td)
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("QUESTIONNAIRE_EVENING_CUTOFF_HOUR", "0")
    monkeypatch.setenv("QC_DAY_CLOSE_HOUR", "0")
    monkeypatch.setenv("GPS_DAILY_MIN_POINTS", "1")
    monkeypatch.setenv("GPS_DAILY_EDGE_COVERAGE_HOURS", "24")
    monkeypatch.setenv("GPS_DAILY_MAX_GAP_SECONDS", "86400")

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.modules.gps.service as gps; importlib.reload(gps)
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


def _timed_light_csv(day: str, count: int = 2) -> bytes:
    rows = ["Modify Time,Photopic Lux,Melanopic,Is Saturate"]
    rows.extend(f"{day} 23:59:{i:02d},100,80,No" for i in range(count))
    return ("\n".join(rows) + "\n").encode()


def test_content_date_qc_warns_only_for_clear_wrong_day(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, _, light, _, _, _ = _reload(monkeypatch, td)
        warning = light.CONTENT_DATE_WARNING

        wrong = light.parse_light_bytes("anything.csv", _timed_light_csv("2026-08-01"), "2026-08-10")
        assert wrong["parse_error"] == warning
        assert wrong["quality"] == "insufficient"

        cross_midnight = (
            "Modify Time,Photopic Lux,Melanopic,Is Saturate\n"
            "2026-08-10 23:59:59,100,80,No\n"
            "2026-08-11 00:01:00,100,80,No\n"
        ).encode()
        assert light.parse_light_bytes("anything.csv", cross_midnight, "2026-08-10")["parse_error"] == ""

        exif_time = _timed_light_csv("2026:08:01")
        assert light.parse_light_bytes("anything.csv", exif_time, "2026-08-10")["parse_error"] == warning

        uncertain = _timed_light_csv("2026-08-01") + b"not-a-time,100,80,No\n"
        assert light.parse_light_bytes("anything.csv", uncertain, "2026-08-10")["parse_error"] == ""

        missing_time = _timed_light_csv("2026-08-01") + b",100,80,No\n"
        assert light.parse_light_bytes("anything.csv", missing_time, "2026-08-10")["parse_error"] == ""


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
            conn.execute("INSERT INTO participants(participant_id,secret_salt,secret_hash,is_active,created_at_utc) VALUES(?,?,?,?,?)", ("001", "salt", "hash", 1, now))
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
                ("001", yesterday.isoformat(), 1, "morning", "{}", now),
            )

        raw = _valid_light_csv()
        parsed = light.parse_light_bytes("001_20260810_LIGHT.csv", raw)
        assert parsed["records_total"] == 6480
        assert parsed["records_valid"] == 6480
        assert parsed["valid_pct"] == 90.0
        assert parsed["quality"] == "valid"
        import app.modules.light.storage as light_storage
        download_calls = []
        original_download = light_storage.LocalLightStorage.download_to_temp

        def counted_download(self, object_key):
            download_calls.append(object_key)
            return original_download(self, object_key)

        monkeypatch.setattr(light_storage.LocalLightStorage, "download_to_temp", counted_download)
        first = light.store_upload("001", yesterday.isoformat(), f"001_{yesterday.strftime('%Y%m%d')}_LIGHT.csv", raw, "test")
        assert download_calls == []
        assert light.rerun_qc(first["upload_uid"])["quality"] == "valid"
        assert len(download_calls) == 1
        with dbmod.db() as conn:
            stored = dict(conn.execute("SELECT * FROM lighting_files WHERE date_local=?", (yesterday.isoformat(),)).fetchone())
        assert stored["storage_backend"] == "local"
        assert stored["object_key"].startswith(f"raw/lighting/001/{yesterday.isoformat()}/light_")
        assert (light.settings.data_dir / Path(*stored["object_key"].split("/"))).is_file()

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
                content=raw + b"\n",
            )
            assert mismatch.status_code == 200
            assert mismatch.json()["original_filename"].startswith("002_")
            unreadable = client.post(
                f"/api/v1/portal/{token}/lighting?date_local={today.isoformat()}&filename=arbitrary-name.txt",
                content=b"raw was received even though this is not parseable lighting data",
            )
            assert unreadable.status_code == 200
            assert unreadable.json()["quality"] == "unreadable"
            assert client.get(f"/api/v1/portal/{token}").json()["lighting"]["status"] == "done"

        with dbmod.db() as conn:
            conn.execute("DELETE FROM questionnaire_responses WHERE participant_id='001' AND form_key='morning'")
        missing_qc = light.run_daily_qc("tester")
        missing_yesterday = next(row for row in missing_qc["rows"] if row["date_local"] == yesterday.isoformat())
        assert missing_yesterday["status"] == "missing"
        assert any(issue["type"] == "missing_morning" for issue in missing_yesterday["issues"])
        with dbmod.db() as conn:
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", yesterday.isoformat(), 1, "morning", "{}", now),
            )
        qc = light.run_daily_qc("tester")
        yesterday_row = next(row for row in qc["rows"] if row["date_local"] == yesterday.isoformat())
        today_row = next(row for row in qc["rows"] if row["date_local"] == today.isoformat())
        assert yesterday_row["status"] == "ok"
        assert yesterday_row["morning_date"] == yesterday.isoformat()
        assert today_row["status"] == "pending"
        with dbmod.db() as conn:
            assert conn.execute("SELECT valid_days FROM study_subjects WHERE participant_id='001'").fetchone()["valid_days"] == 1
            assert conn.execute(
                "SELECT status FROM incidents WHERE incident_uid=?",
                (f"acq_001_{yesterday.isoformat()}_missing_morning",),
            ).fetchone()["status"] == "closed"

        wrong_day = light.store_upload(
            "001", yesterday.isoformat(), "untrusted-provenance-name.csv",
            _timed_light_csv((yesterday - timedelta(days=8)).isoformat(), 6480), "test",
        )
        assert wrong_day["parse_error"] == light.CONTENT_DATE_WARNING
        wrong_day_qc = light.run_daily_qc("tester")
        wrong_day_row = next(row for row in wrong_day_qc["rows"] if row["date_local"] == yesterday.isoformat())
        assert any(issue["type"] == "wrong_day_light" for issue in wrong_day_row["issues"])
        with dbmod.db() as conn:
            incident = conn.execute(
                "SELECT status FROM incidents WHERE incident_uid=?",
                (f"acq_001_{yesterday.isoformat()}_wrong_day_light",),
            ).fetchone()
            assert incident["status"] == "open"


def test_oss_direct_upload_can_resume_and_finish_qc(monkeypatch):
    monkeypatch.setenv("LIGHT_STORAGE_BACKEND", "oss")
    monkeypatch.setenv("OSS_BUCKET", "test-lighting")
    monkeypatch.setenv("OSS_REGION", "cn-hongkong")
    monkeypatch.setenv("OSS_CREDENTIAL_MODE", "access_key")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    with tempfile.TemporaryDirectory() as td:
        dbmod, _, light, _, _, _ = _reload(monkeypatch, td)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        with dbmod.db() as conn:
            conn.execute(
                "INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", "running", today.isoformat(), today.isoformat(), now, now),
            )

        raw = _valid_light_csv()
        digest = hashlib.sha256(raw).hexdigest()

        class FakeOSS:
            backend = "oss"

            def __init__(self):
                self.data = None
                self.key = ""
                self.digest = ""

            def exists(self, object_key):
                return self.data is not None and object_key == self.key

            def head(self, object_key):
                from app.modules.light.storage import LightObjectHead
                return LightObjectHead(len(self.data), self.digest)

            def presign_put(self, object_key, sha256, expires_seconds):
                self.key, self.digest = object_key, sha256
                return {"url": "https://upload.example.test/signed", "headers": {"x-oss-meta-sha256": sha256}}

            def download_to_temp(self, object_key):
                handle = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
                try:
                    handle.write(self.data)
                    return Path(handle.name)
                finally:
                    handle.close()

        storage = FakeOSS()
        monkeypatch.setattr(light, "get_light_storage", lambda: storage)
        filename = f"001_{today.strftime('%Y%m%d')}_LIGHT.csv"
        first = light.prepare_direct_upload("001", today.isoformat(), filename, len(raw), digest)
        assert first["status"] == "pending"
        resumed = light.prepare_direct_upload("001", today.isoformat(), filename, len(raw), digest)
        assert resumed["upload_uid"] == first["upload_uid"]
        storage.data = raw
        uploaded = light.prepare_direct_upload("001", today.isoformat(), filename, len(raw), digest)
        assert uploaded["status"] == "uploaded"
        finished = light.complete_direct_upload("001", first["upload_uid"])
        assert finished["quality"] == "valid"
        assert finished["upload_status"] == "qc"
        with dbmod.db() as conn:
            assert conn.execute(
                "SELECT upload_status FROM lighting_files WHERE upload_uid=?", (first["upload_uid"],)
            ).fetchone()["upload_status"] == "qc"


def test_s0_snapshot_import_preserves_candidate_identity(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, idb, _, s0, _, _ = _reload(monkeypatch, td)
        header = "序号,您的年龄,您的性别,16、您是否愿意参与本研究并接受补贴？,19、您的手机号（仅实验负责人可见）：,您的手机系统是：,姓名或称呼\n"
        first = (header + "1,20-29,女,愿意,138 0000 0001,iOS,候选甲\n2,30-39,男,不愿意,13800000002,Android,候选乙\n").encode("utf-8-sig")
        result = s0.import_s0("S0.csv", first, "pi")
        assert result == {"import_uid": result["import_uid"], "total": 2, "imported": 2, "filtered": 0, "duplicate": False}
        assert s0.import_s0("S0.csv", first, "pi")["duplicate"] is True
        with idb.identity_db() as conn:
            original = dict(conn.execute("SELECT * FROM candidates WHERE source_seq='1'").fetchone())
            conn.execute("UPDATE candidates SET linked_participant_id='001',name='人工校正姓名',notes='已联系' WHERE candidate_uid=?", (original["candidate_uid"],))

        second = (header + "1,20-29,女,愿意,13800000001,iOS,问卷新姓名\n3,20-29,男,愿意,13800000003,Android,候选丙\n").encode("utf-8-sig")
        result2 = s0.import_s0("S0_new.csv", second, "ra01")
        assert result2["imported"] == 2
        with idb.identity_db() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM candidates ORDER BY source_seq")]
            assert len(rows) == 3
            assert rows[0]["candidate_uid"] == original["candidate_uid"]
            assert rows[0]["linked_participant_id"] == "001"
            assert rows[0]["name"] == "人工校正姓名"
            assert rows[0]["notes"] == "已联系"
            assert conn.execute("SELECT COUNT(*) n FROM s0_imports").fetchone()["n"] == 2


def test_current_recruitment_s0_maps_mechanism_and_operations(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, idb, _, s0, _, _ = _reload(monkeypatch, td)
        headers = [
            "序号", "您的年龄", "您的性别", "您目前是否主要在北京市工作、学习或生活？", "您的学历", "您目前最接近哪类身份？", "总体而言，您认为自己目前的健康状况如何？",
            "您目前的日常作息更接近哪种？", "在没有工作或学习安排限制时，您通常更倾向于：",
            "典型工作/学习日中，您的主要活动方式更接近哪种？",
            "典型工作/学习日中，您的工作/学习时间有多少比例在同一个固定工位、座位或学习位完成？",
            "典型工作/学习日中，您的屏幕工作/学习时间占总工作/学习时间的比例大约为：",
            "自然光是否充足", "是否依赖开灯照明",
            "典型日间活动中，您白天在户外、半户外或明显强自然光环境中的累计时间大约为？",
            "您的主要工作/学习地点大致位于北京市哪个区？", "您的主要居住地点大致位于北京市哪个区？",
            "典型工作或学习日中，您的主要通勤方式是：", "您典型工作/学习日的单程通勤时间约为：",
            "您的手机系统是：", "您是否愿意参与本研究并接受补贴？", "您方便参与实验的时间段是：",
            "您更方便的设备领取和归还方式是：", "您的手机号（仅实验负责人可见）：",
        ]
        values = [
            "1", "25-34岁", "女", "是", "硕士", "研究生或科研人员", "较好", "日间固定作息为主", "稍偏晚",
            "久坐阅读、写作、电脑工作或会议为主", "大于75%", "50%-75%", "较少", "较多",
            "30-60分钟", "海淀区", "朝阳区", "地铁", "30-60分钟", "iOS", "需要了解后再决定",
            "未来2个月内可参与", "均可", "13800000001",
        ]
        raw = (",".join(headers) + "\n" + ",".join(values) + "\n").encode("utf-8-sig")
        assert s0.import_s0("recruit0812.csv", raw, "pi")["imported"] == 1
        with idb.identity_db() as conn:
            row = dict(conn.execute("SELECT * FROM candidates").fetchone())
        assert row["education"] == "硕士"
        assert row["beijing_based"] == "是" and row["health_rating"] == "较好"
        assert row["fixed_position_ratio"] == "大于75%"
        assert row["indoor_daylight"] == "较少"
        assert row["outdoor_time"] == "30-60分钟"
        assert row["screen_time_ratio"] == "50%-75%"
        assert row["exposure_mechanism"] == "固定位置主导 × 日光受限"
        assert row["commute_mode"] == "地铁" and row["commute_duration"] == "30-60分钟"
        assert row["willingness"] == "需要了解后再决定"
        assert row["light_type"] == ""
        assert "自然光是否充足" in row["s0_raw_json"]

        assert s0._mechanism_category("大于75%", "很多") == "固定位置主导 × 日光可达"
        assert s0._mechanism_category("50%–75%", "较少") == "固定位置主导 × 日光受限"
        assert s0._mechanism_category("小于50%", "一般") == "非固定位置主导 × 日光可达"
        assert s0._mechanism_category("不固定", "很少") == "非固定位置主导 × 日光受限"
