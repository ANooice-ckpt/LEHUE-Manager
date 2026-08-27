import importlib
import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _reload_stack(monkeypatch, data_dir: str):
    monkeypatch.setenv("PROJECT_NAME", "LEHUE")
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("DATA_DIR", data_dir)
    monkeypatch.setenv("DOMAIN", "study.lehue.cn")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.security as security; importlib.reload(security)
    import app.core.traccar as traccar_core; importlib.reload(traccar_core)
    import app.modules.gps.service as gps_service; importlib.reload(gps_service)
    import app.modules.gps.traccar_service as traccar_service; importlib.reload(traccar_service)
    import app.modules.gps.router as gps_router; importlib.reload(gps_router)
    import app.modules.participant.service as participant_service; importlib.reload(participant_service)
    import app.modules.participant.traccar_config as traccar_config; importlib.reload(traccar_config)
    import app.modules.participant.router as participant_router; importlib.reload(participant_router)
    import app.main as main; importlib.reload(main)
    return config, dbmod, security, traccar_core, participant_service, main


def _seed_participant(dbmod, security, participant_id="TEST01", secret="gps-secret"):
    salt, digest = security.hash_secret(secret)
    encrypted = security.encrypt_credential(secret)
    with dbmod.db() as conn:
        conn.execute(
            """INSERT INTO participants(
                   participant_id,secret_salt,secret_hash,secret_ciphertext,is_active,created_at_utc
               ) VALUES(?,?,?,?,1,'2026-01-01T00:00:00Z')""",
            (participant_id, salt, digest, encrypted),
        )
        conn.execute(
            """INSERT INTO study_subjects(
                   participant_id,status,start_date,end_date,created_at_utc,updated_at_utc
               ) VALUES(?,'running','2026-08-01','2026-09-30','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')""",
            (participant_id,),
        )


def test_traccar_ingest_is_shared_redacted_and_idempotent(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        config, dbmod, security, _, _, main = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        _seed_participant(dbmod, security)

        from fastapi.testclient import TestClient

        form = {
            "id": "TEST01.gps-secret",
            "lat": "39.999",
            "lon": "116.320",
            "timestamp": "1787850000",
            "accuracy": "8",
            "altitude": "50",
            "speed": "10",
            "bearing": "90",
            "batt": "80",
            "charge": "true",
        }
        with TestClient(main.app) as client:
            first = client.post("/api/v1/gps/traccar", data=form)
            second = client.post("/api/v1/gps/traccar", data=form)
            assert first.status_code == 200 and first.text == "OK"
            assert second.status_code == 200 and second.text == "OK"

        with dbmod.db() as conn:
            raw_rows = conn.execute("SELECT * FROM raw_events WHERE participant_id='TEST01'").fetchall()
            gps_rows = conn.execute("SELECT * FROM gps_locations WHERE participant_id='TEST01'").fetchall()
        assert len(raw_rows) == 1
        assert len(gps_rows) == 1
        raw = json.loads(raw_rows[0]["raw_json"])
        assert raw["id"] == "TEST01"
        assert raw["_credential_redacted"] is True
        assert "gps-secret" not in raw_rows[0]["raw_json"]
        assert gps_rows[0]["source"] == "traccar"
        assert abs(gps_rows[0]["velocity_kmh"] - 18.52) < 1e-9
        assert gps_rows[0]["battery_pct"] == 80

        archives = list(Path(config.settings.raw_archive_dir).glob("*.jsonl"))
        assert len(archives) == 1
        archive_text = archives[0].read_text(encoding="utf-8")
        assert "gps-secret" not in archive_text
        assert '"_credential_redacted":true' in archive_text


def test_traccar_auth_and_content_type(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, dbmod, security, _, _, main = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        _seed_participant(dbmod, security)
        from fastapi.testclient import TestClient

        base = {"lat": "39.9", "lon": "116.3", "timestamp": "1787850000"}
        with TestClient(main.app) as client:
            wrong = client.post("/api/v1/gps/traccar", data={**base, "id": "TEST01.wrong"})
            assert wrong.status_code == 403
            missing = client.post("/api/v1/gps/traccar", data=base)
            assert missing.status_code == 401
            json_body = client.post(
                "/api/v1/gps/traccar",
                json={**base, "id": "TEST01.gps-secret"},
            )
            assert json_body.status_code == 415


def test_traccar_portal_config_matches_client_parameters(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, dbmod, security, traccar_core, participant_service, main = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        _seed_participant(dbmod, security)
        token = participant_service.generate_portal_token("TEST01")

        from fastapi.testclient import TestClient
        with TestClient(main.app) as client:
            response = client.get(f"/api/v1/portal/{token}/traccar/config")
            assert response.status_code == 200
            config = response.json()
            assert config["available"] is True
            uri = urlparse(config["uri"])
            assert uri.scheme == "org.traccar.client"
            assert uri.netloc == "config"
            params = parse_qs(uri.query)
            assert params["url"] == ["https://study.lehue.cn/api/v1/gps/traccar"]
            assert params["id"] == ["TEST01.gps-secret"]
            assert params["accuracy"] == ["high"]
            assert params["distance"] == ["0"]
            assert params["interval"] == ["5"]
            assert params["heartbeat"] == ["0"]
            assert params["buffer"] == ["true"]
            assert params["wakelock"] == ["true"]
            assert params["stop_detection"] == ["false"]
            assert params["prefer_platform_providers"] == ["false"]
            assert config["uri"] == traccar_core.build_config_uri("TEST01", "gps-secret")

            page = client.get(f"/p/{token}")
            assert page.status_code == 200
            assert '<script src="/participant-gps-clients.js"></script>' in page.text
            addon = client.get("/participant-gps-clients.js")
            assert addon.status_code == 200
            assert "Traccar 一键配置" in addon.text

            admin_bundle = client.get("/admin/app.js")
            assert admin_bundle.status_code == 200
            assert "traccarAndroidLaunch" in admin_bundle.text
            assert "Android · Traccar 一键配置" in admin_bundle.text
            assert "accuracy: 'high'" in admin_bundle.text
