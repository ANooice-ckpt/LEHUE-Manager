import base64
import importlib
import sqlite3
import tempfile


def test_end_to_end(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PROJECT_NAME", "LEHUE")
        monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setenv("DATA_DIR", td)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")

        import app.core.config as config
        importlib.reload(config)
        import app.core.db as dbmod
        importlib.reload(dbmod)
        import app.core.security as sec
        importlib.reload(sec)
        import app.modules.gps.service as service
        importlib.reload(service)
        import app.modules.gps.router as router
        importlib.reload(router)
        import app.modules.light.service as light
        importlib.reload(light)
        import app.modules.questionnaire.s0_import as s0
        importlib.reload(s0)
        import app.modules.participant.service as portal
        importlib.reload(portal)
        import app.main as main
        importlib.reload(main)

        dbmod.init_db()
        salt, digest = sec.hash_secret("pw")
        with dbmod.db() as conn:
            conn.execute(
                "INSERT INTO participants(participant_id,secret_salt,secret_hash,is_active,created_at_utc) VALUES(?,?,?,?,?)",
                ("TEST01", salt, digest, 1, "2026-01-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO study_subjects(participant_id,status,created_at_utc,updated_at_utc) VALUES('TEST01','running','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
            )

        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            auth = "Basic " + base64.b64encode(b"TEST01:pw").decode()
            payload = {
                "_type": "location",
                "_id": "abc123",
                "tst": 1786276022,
                "created_at": 1786276022,
                "lat": 30.4,
                "lon": 103.4,
                "acc": 6,
                "tid": "T1",
            }
            headers = {
                "Authorization": auth,
                "X-Limit-U": "TEST01",
                "X-Limit-D": "android-test-phone",
            }
            r = client.post("/api/v1/gps/owntracks", json=payload, headers=headers)
            assert r.status_code == 200
            r2 = client.post("/api/v1/gps/owntracks", json=payload, headers=headers)
            assert r2.status_code == 200

            root = client.get("/")
            assert root.status_code == 200
            assert "text/html" in root.headers["content-type"]
            assert "光迹计划" in root.text
            assert "Light Exposure Histories in Urban Environments" in root.text
            assert "王宇骁" in root.text
            assert 'href="/admin"' in root.text
            assert "X-LEHUE-Environment" not in root.headers
            assert client.get("/public.css").status_code == 200

            h = client.get("/health").json()
            assert h == {"status": "ok"}

            admin_headers = {"Authorization": "Bearer admin-test-token"}
            status = client.get(
                "/api/v1/admin/gps/status/TEST01", headers=admin_headers
            )
            assert status.status_code == 200
            assert status.json()["location_count"] == 1
            assert status.json()["last_device_id"] == "android-test-phone"

            # Calendar date is interpreted in study timezone, not UTC.
            local_day = client.get(
                "/api/v1/admin/gps/status/TEST01?date=2026-08-09",
                headers=admin_headers,
            )
            assert local_day.status_code == 200
            assert local_day.json()["study_timezone"] == "Asia/Shanghai"


def test_health_database_error_returns_503(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("DATA_DIR", td)
        import app.core.config as config; importlib.reload(config)
        import app.core.db as dbmod; importlib.reload(dbmod)
        import app.main as main; importlib.reload(main)
        from fastapi.testclient import TestClient

        class BrokenDb:
            def __enter__(self):
                raise sqlite3.OperationalError("database unavailable")
            def __exit__(self, *_):
                return False

        monkeypatch.setattr(main, "db", lambda: BrokenDb())
        with TestClient(main.app) as client:
            response = client.get("/health")
            assert response.status_code == 503
            assert response.json() == {"status": "error"}
