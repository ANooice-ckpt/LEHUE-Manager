import importlib
import tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo


def test_questionnaire_date_rollover(monkeypatch):
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("QUESTIONNAIRE_EVENING_CUTOFF_HOUR", "12")
    import app.core.config as config; importlib.reload(config)
    import app.modules.participant.service as portal; importlib.reload(portal)

    subject = {"start_date": "2026-08-14", "expected_start": "", "end_date": "2026-08-27", "expected_end": ""}
    early_morning = datetime(2026, 8, 15, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert portal.form_target_date("evening", early_morning) == date(2026, 8, 14)
    assert portal.form_target_date("morning", early_morning) == date(2026, 8, 14)
    evening = portal.form_assignment(subject, "evening", date(2026, 8, 14), date(2026, 8, 15))
    morning = portal.form_assignment(subject, "morning", date(2026, 8, 14), date(2026, 8, 15))
    assert evening["calendar_date_local"] == "2026-08-15"
    assert evening["experiment_date_local"] == "2026-08-14"
    assert evening["date_local"] == "2026-08-14"
    assert evening["study_day"] == 1
    assert morning["calendar_date_local"] == "2026-08-15"
    assert morning["experiment_date_local"] == "2026-08-14"
    assert morning["date_local"] == "2026-08-14"
    assert morning["study_day"] == 1
    afternoon = datetime(2026, 8, 16, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert portal.allowed_exposure_days("evening", afternoon) == (date(2026, 8, 16), date(2026, 8, 15))
    assert portal.allowed_exposure_days("morning", afternoon) == (date(2026, 8, 15), date(2026, 8, 14))
    assert portal.form_time_scope("morning", date(2026, 8, 15))["range"] == "8月15日晚 → 8月16日早"


def test_participant_portal_questionnaire_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PROJECT_NAME", "LEHUE")
        monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setenv("QUESTIONNAIRE_EVENING_CUTOFF_HOUR", "0")
        monkeypatch.setenv("DATA_DIR", td)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")

        import app.core.config as config; importlib.reload(config)
        import app.core.db as dbmod; importlib.reload(dbmod)
        import app.core.identity_db as idb; importlib.reload(idb)
        import app.core.security as sec; importlib.reload(sec)
        import app.core.web_security as ws; importlib.reload(ws)
        import app.modules.light.service as light; importlib.reload(light)
        import app.modules.questionnaire.s0_import as s0; importlib.reload(s0)
        import app.modules.gps.service as gps; importlib.reload(gps)
        import app.modules.participant.service as portal; importlib.reload(portal)
        import app.modules.participant.router as portal_router; importlib.reload(portal_router)
        import app.modules.admin.service as svc; importlib.reload(svc)
        import app.modules.admin.router as admin_router; importlib.reload(admin_router)
        import app.modules.gps.router as gps_router; importlib.reload(gps_router)
        import app.main as main; importlib.reload(main)
        dbmod.init_db(); idb.init_identity_db()

        # Minimal subject/device setup for portal integration.
        today_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        today = today_date.isoformat()
        yesterday = (today_date - date.resolution).isoformat()
        end = (today_date + date.resolution * 12).isoformat()
        with dbmod.db() as conn:
            # Existing deployments created before this field was declared do not have it.
            conn.execute("ALTER TABLE lighting_files DROP COLUMN calendar_date_local")
            conn.execute(
                "INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", "running", yesterday, end, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO questionnaire_responses(participant_id,date_local,calendar_date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES(?,?,?,?,?,?,?)",
                ("001", today, today, 1, "morning", "{}", "2026-01-01T00:00:00Z"),
            )
        dbmod.init_db()
        with dbmod.db() as conn:
            assert conn.execute("SELECT date_local FROM questionnaire_responses").fetchone()["date_local"] == yesterday
            conn.execute("DELETE FROM questionnaire_responses")

        token = portal.generate_portal_token("001")
        assert "." in token

        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url="http://127.0.0.1:8085") as client:
            assert client.get(f"/p/{token}").status_code == 200
            state = client.get(f"/api/v1/portal/{token}")
            assert state.status_code == 200
            data = state.json()
            assert data["study_day"] == 2
            assert data["status"] == "running"
            assert data["gps"]["status"] == "never"
            assert [x["key"] for x in data["forms"]] == ["morning", "evening", "evening"]
            assert [len(x["questions"]) for x in data["forms"]] == [10, 6, 6]
            assert all(x["version"] == "formal_v1" for x in data["forms"])
            assert all(not x["completed"] for x in data["forms"])
            morning_state = next(x for x in data["forms"] if x["key"] == "morning" and not x["is_makeup"])
            assert morning_state["calendar_date_local"] == today
            assert morning_state["experiment_date_local"] == yesterday
            assert morning_state["date_local"] == yesterday
            assert morning_state["study_day"] == 1
            assert next(x for x in data["forms"] if x["key"] == "evening" and x["is_makeup"])["date_local"] == yesterday
            assert [x["date_local"] for x in data["lighting_tasks"]] == [today, yesterday]
            assert data["participant_id"] == "001"
            assert data["study_timezone"] == "Asia/Shanghai"
            assert data["progress"]["expected"] == 5
            assert data["progress"]["completed"] == 0
            assert [x["study_day"] for x in data["progress"]["days"]] == [1, 2]
            assert data["cohort"] == {"running_others": 0, "completed_others": 0, "active_today": 0}
            assert data["report"]["status"] == "locked"
            assert client.get(f"/api/v1/portal/{token}/report").status_code == 403

            morning = client.post(
                f"/api/v1/portal/{token}/questionnaires/morning",
                json={"date_local": yesterday, "answers": {
                    "bedtime": "23:15", "wake_time": "07:10", "alertness": "alert",
                    "sleep_quality": 1, "sleep_recovery": 2, "sleep_continuity": 1,
                    "sleep_sufficiency": 2, "sleep_onset_ease": 1, "wake_ease": 2,
                    "sleep_influences": ["none"],
                }},
            )
            assert morning.status_code == 200
            assert morning.json()["experiment_date_local"] == yesterday
            assert morning.json()["date_local"] == yesterday
            duplicate = client.post(
                f"/api/v1/portal/{token}/questionnaires/morning",
                json={"answers": {
                    "bedtime": "23:15", "wake_time": "07:10", "alertness": "alert",
                    "sleep_quality": 1, "sleep_recovery": 2, "sleep_continuity": 1,
                    "sleep_sufficiency": 2, "sleep_onset_ease": 1, "wake_ease": 2,
                    "sleep_influences": ["none"],
                }},
            )
            assert duplicate.status_code == 400

            state2 = client.get(f"/api/v1/portal/{token}").json()
            morning_state = next(x for x in state2["forms"] if x["key"] == "morning" and not x["is_makeup"])
            assert morning_state["completed"] is True
            with dbmod.db() as conn:
                stored = conn.execute("SELECT date_local,calendar_date_local,study_day FROM questionnaire_responses WHERE form_key='morning'").fetchone()
            assert stored["date_local"] == yesterday
            assert stored["calendar_date_local"] == today and stored["study_day"] == 1

            evening_makeup = client.post(
                f"/api/v1/portal/{token}/questionnaires/evening",
                json={"date_local": yesterday, "answers": {
                    "alertness": "alert", "day_energy": 1, "day_mood": 1,
                    "day_activation": 0, "nap_duration": "none",
                    "device_status": {"gps": "normal", "lighting": "normal"},
                }},
            )
            assert evening_makeup.status_code == 200
            assert evening_makeup.json()["date_local"] == yesterday
            rejected = client.post(
                f"/api/v1/portal/{token}/questionnaires/evening",
                json={"date_local": (today_date - date.resolution * 2).isoformat(), "answers": {}},
            )
            assert rejected.status_code == 400

            light_name = f"001_{yesterday.replace('-', '')}_LIGHT.csv"
            light_upload = client.post(
                f"/api/v1/portal/{token}/lighting?date_local={yesterday}&filename={light_name}",
                content=b"Photopic Lux,Melanopic,Is Saturate\n100,80,No\n",
            )
            assert light_upload.status_code == 200
            with dbmod.db() as conn:
                assert conn.execute(
                    "SELECT COUNT(*) n FROM lighting_files WHERE participant_id='001' AND date_local=?", (yesterday,)
                ).fetchone()["n"] == 1
            state3 = client.get(f"/api/v1/portal/{token}").json()
            assert yesterday not in [x["date_local"] for x in state3["lighting_tasks"] if x["is_makeup"]]
            # The deliberately short test file is kept as incomplete Lighting, so only both questionnaires count.
            assert state3["progress"]["completed"] == 2

            subjects = svc.list_subjects()
            assert subjects[0]["portal_enabled"] is True
            assert subjects[0]["questionnaire_today_completed"] == 1

            source = {x["key"]: x for x in svc.data_sources()}
            assert source["questionnaire"]["status"] == "connected"
            assert source["questionnaire"]["records"] == 2

            with dbmod.db() as conn:
                conn.execute("UPDATE study_subjects SET end_date=? WHERE participant_id='001'", ((today_date - date.resolution).isoformat(),))
            report = client.get(f"/api/v1/portal/{token}/report")
            assert report.status_code == 200
            assert report.json()["status"] == "preparing"

            assert client.get("/api/v1/portal/bad-token").status_code == 404
