import importlib
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def test_participant_portal_questionnaire_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PROJECT_NAME", "LEHUE")
        monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setenv("DATA_DIR", td)
        monkeypatch.setenv("DB_PATH", str(Path(td) / "main.sqlite3"))
        monkeypatch.setenv("IDENTITY_DB_PATH", str(Path(td) / "identity.sqlite3"))
        monkeypatch.setenv("RAW_ARCHIVE_DIR", str(Path(td) / "raw"))
        monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")

        import app.core.config as config; importlib.reload(config)
        import app.core.db as dbmod; importlib.reload(dbmod)
        import app.core.identity_db as idb; importlib.reload(idb)
        import app.core.security as sec; importlib.reload(sec)
        import app.core.web_security as ws; importlib.reload(ws)
        import app.modules.gps.service as gps; importlib.reload(gps)
        import app.modules.participant.service as portal; importlib.reload(portal)
        import app.modules.participant.router as portal_router; importlib.reload(portal_router)
        import app.modules.admin.service as svc; importlib.reload(svc)
        import app.modules.admin.router as admin_router; importlib.reload(admin_router)
        import app.modules.gps.router as gps_router; importlib.reload(gps_router)
        import app.main as main; importlib.reload(main)
        dbmod.init_db(); idb.init_identity_db()

        # Minimal subject/device setup for portal integration.
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with dbmod.db() as conn:
            conn.execute(
                "INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?)",
                ("001", "running", today, today, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )

        token = portal.generate_portal_token("001")
        assert "." in token

        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url="http://127.0.0.1:8085") as client:
            assert client.get(f"/p/{token}").status_code == 200
            state = client.get(f"/api/v1/portal/{token}")
            assert state.status_code == 200
            data = state.json()
            assert data["study_day"] == 1
            assert data["status"] == "running"
            assert data["gps"]["status"] == "never"
            assert [x["key"] for x in data["forms"]] == ["morning", "evening"]
            assert all(not x["completed"] for x in data["forms"])
            # Participant ID is intentionally not exposed in participant-facing state.
            assert "participant_id" not in data

            morning = client.post(
                f"/api/v1/portal/{token}/questionnaires/morning",
                json={"answers": {"sleep_duration_hours": 7.5, "sleep_quality": 4, "sleepiness": 3}},
            )
            assert morning.status_code == 200
            duplicate = client.post(
                f"/api/v1/portal/{token}/questionnaires/morning",
                json={"answers": {"sleep_duration_hours": 7.5, "sleep_quality": 4, "sleepiness": 3}},
            )
            assert duplicate.status_code == 400

            state2 = client.get(f"/api/v1/portal/{token}").json()
            morning_state = next(x for x in state2["forms"] if x["key"] == "morning")
            assert morning_state["completed"] is True

            subjects = svc.list_subjects()
            assert subjects[0]["portal_enabled"] is True
            assert subjects[0]["questionnaire_today_completed"] == 1

            source = {x["key"]: x for x in svc.data_sources()}
            assert source["questionnaire"]["status"] == "connected"
            assert source["questionnaire"]["records"] == 1

            assert client.get("/api/v1/portal/bad-token").status_code == 404
