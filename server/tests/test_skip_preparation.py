import importlib
import tempfile

import pytest
from cryptography.fernet import Fernet


def _reload_stack(monkeypatch, data_root: str):
    monkeypatch.setenv("LEHUE_ENV", "test")
    monkeypatch.setenv("DATA_ROOT", data_root)
    monkeypatch.setenv("LIGHT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOAD_TEST_SEED", "0")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("PROJECT_NAME", "LEHUE")
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as identity_dbmod; importlib.reload(identity_dbmod)
    import app.modules.admin.service as admin_service; importlib.reload(admin_service)
    import app.modules.admin.skip_preparation as skip_preparation; importlib.reload(skip_preparation)
    return dbmod, identity_dbmod, admin_service, skip_preparation


def test_skip_preparation_marks_ready_without_fabricating_test_data(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        dbmod, _, _, skip_preparation = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        with dbmod.db() as conn:
            conn.execute(
                """INSERT INTO study_subjects(
                       participant_id,status,preparation_started_at_utc,created_at_utc,updated_at_utc
                   ) VALUES('090','scheduled','2026-08-27T12:00:00Z','2026-08-27T12:00:00Z','2026-08-27T12:00:00Z')"""
            )

        result = skip_preparation.skip_preparation_test("090", "tester")
        assert result["status"] == "ready"
        assert result["readiness_override"] is True

        with dbmod.db() as conn:
            subject = conn.execute(
                "SELECT status,ready_at_utc FROM study_subjects WHERE participant_id='090'"
            ).fetchone()
            gps_count = conn.execute(
                "SELECT COUNT(*) n FROM gps_locations WHERE participant_id='090'"
            ).fetchone()["n"]
            light_count = conn.execute(
                "SELECT COUNT(*) n FROM lighting_files WHERE participant_id='090'"
            ).fetchone()["n"]
            audit = conn.execute(
                "SELECT action,detail_json FROM audit_log WHERE entity_id='090' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert subject["status"] == "ready"
        assert subject["ready_at_utc"]
        assert gps_count == 0
        assert light_count == 0
        assert audit["action"] == "participant.prepare.skip"
        assert '"test_data_fabricated": false' in audit["detail_json"]


def test_skip_preparation_rejects_subject_not_in_preparation(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        dbmod, _, _, skip_preparation = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        with dbmod.db() as conn:
            conn.execute(
                """INSERT INTO study_subjects(
                       participant_id,status,created_at_utc,updated_at_utc
                   ) VALUES('091','scheduled','2026-08-27T12:00:00Z','2026-08-27T12:00:00Z')"""
            )
        with pytest.raises(ValueError, match="currently in preparation testing"):
            skip_preparation.skip_preparation_test("091", "tester")


def test_completed_participant_id_cannot_be_reused(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        dbmod, identity_dbmod, admin_service, _ = _reload_stack(monkeypatch, td)
        dbmod.init_db()
        identity_dbmod.init_identity_db()
        candidate_uid = admin_service.add_candidate({"name": "Simulation candidate"}, "tester")
        with dbmod.db() as conn:
            conn.execute(
                """INSERT INTO study_subjects(
                       participant_id,status,created_at_utc,updated_at_utc
                   ) VALUES('092','completed','2026-08-01T00:00:00Z','2026-08-20T00:00:00Z')"""
            )

        with pytest.raises(ValueError, match="participant_id already exists"):
            admin_service.promote_candidate(
                candidate_uid,
                {"participant_id": "092", "expected_start": "2026-09-01", "expected_end": "2026-09-14"},
                "tester",
            )
