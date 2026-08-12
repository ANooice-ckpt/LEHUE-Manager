import importlib
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


def _stack(monkeypatch, root: Path, runtime_env: str = "test"):
    source_key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEHUE_ENV", runtime_env)
    monkeypatch.setenv("DATA_ROOT", str(root))
    monkeypatch.setenv("LOAD_TEST_SEED", "false")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", source_key)
    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.core.state_bundle as bundles; importlib.reload(bundles)
    dbmod.init_db(); idb.init_identity_db()
    return config, dbmod, idb, bundles, source_key


def test_state_bundle_round_trip_rekeys_credentials_and_includes_gps(monkeypatch, tmp_path):
    config, dbmod, idb, bundles, source_key = _stack(monkeypatch, tmp_path)
    source_cipher = Fernet(source_key.encode())
    with dbmod.db() as conn:
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,secret_ciphertext,created_at_utc) VALUES(?,?,?,?,?)",
            ("001", "salt", "hash", source_cipher.encrypt(b"gps-secret").decode(), "now"),
        )
        conn.execute(
            """INSERT INTO study_subjects(participant_id,portal_token_ciphertext,created_at_utc,updated_at_utc)
               VALUES(?,?,?,?)""",
            ("001", source_cipher.encrypt(b"portal-secret").decode(), "now", "now"),
        )
        conn.execute(
            """INSERT INTO lighting_files(upload_uid,participant_id,date_local,original_filename,stored_path,
               storage_backend,object_key,file_size_bytes,sha256,uploaded_at_utc,parser_version,quality)
               VALUES('u1','001','2026-08-12','x.csv','raw/lighting/x','oss','raw/lighting/x',123,'abc','now','v1','valid')"""
        )
    with idb.identity_db() as conn:
        conn.execute("INSERT INTO candidates(candidate_uid,name,created_at_utc,updated_at_utc) VALUES('c1','before','now','now')")
    raw = config.settings.raw_archive_dir / "2026-08-12.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"x":1}\n', encoding="utf-8")

    archive = bundles.create_state_bundle(tmp_path / "portable.zip")
    with zipfile.ZipFile(archive) as bundle_zip:
        manifest = json.loads(bundle_zip.read("manifest.json"))
        assert "gps_raw/2026-08-12.jsonl" in bundle_zip.namelist()
        assert "metadata/credential_source_key" in bundle_zip.namelist()
        assert manifest["state_bundle_format"] == 1
        assert manifest["lighting_canonical_objects"][0]["object_key"] == "raw/lighting/x"
        assert not any(name.startswith("lighting_raw/") for name in bundle_zip.namelist())

    target_key = Fernet.generate_key().decode()
    bundles.settings = replace(bundles.settings, credential_encryption_key=target_key)
    with idb.identity_db() as conn:
        conn.execute("UPDATE candidates SET name='changed' WHERE candidate_uid='c1'")
    raw.write_text("changed\n", encoding="utf-8")
    rollback, manifest = bundles.restore_state_bundle(archive, tmp_path / "rollbacks")
    assert rollback.exists() and manifest["runtime_environment"] == "test"
    with sqlite3.connect(config.settings.identity_db_path) as conn:
        assert conn.execute("SELECT name FROM candidates WHERE candidate_uid='c1'").fetchone()[0] == "before"
    with sqlite3.connect(config.settings.db_path) as conn:
        gps_ciphertext = conn.execute("SELECT secret_ciphertext FROM participants WHERE participant_id='001'").fetchone()[0]
        portal_ciphertext = conn.execute("SELECT portal_token_ciphertext FROM study_subjects WHERE participant_id='001'").fetchone()[0]
    target_cipher = Fernet(target_key.encode())
    assert target_cipher.decrypt(gps_ciphertext.encode()) == b"gps-secret"
    assert target_cipher.decrypt(portal_ciphertext.encode()) == b"portal-secret"
    assert raw.read_text(encoding="utf-8") == '{"x":1}\n'


def test_state_bundle_rejects_cross_environment(monkeypatch, tmp_path):
    _, _, _, bundles, _ = _stack(monkeypatch, tmp_path)
    archive = bundles.create_state_bundle(tmp_path / "test.zip")
    bundles.settings = replace(bundles.settings, runtime_env="prod")
    with pytest.raises(ValueError, match="TEST and PROD state are isolated"):
        bundles.inspect_state_bundle(archive)


def test_state_bundle_rejects_local_lighting_when_target_uses_oss(monkeypatch, tmp_path):
    _, dbmod, _, bundles, _ = _stack(monkeypatch, tmp_path)
    with dbmod.db() as conn:
        conn.execute("INSERT INTO study_subjects(participant_id,created_at_utc,updated_at_utc) VALUES('001','now','now')")
        conn.execute(
            """INSERT INTO lighting_files(upload_uid,participant_id,date_local,original_filename,stored_path,
               storage_backend,object_key,file_size_bytes,sha256,uploaded_at_utc,parser_version,quality)
               VALUES('u1','001','2026-08-12','x.csv','raw/lighting/x','local','raw/lighting/x',123,'abc','now','v1','valid')"""
        )
    archive = bundles.create_state_bundle(tmp_path / "local.zip")
    with zipfile.ZipFile(archive) as bundle_zip:
        assert json.loads(bundle_zip.read("manifest.json"))["contains_local_lighting_references"] is True
    bundles.settings = replace(
        bundles.settings, light_storage_backend="oss", oss_bucket="target-private",
        oss_endpoint="https://oss-cn-shanghai.aliyuncs.com", oss_credential_mode="ecs_ram_role",
    )
    with pytest.raises(ValueError, match="local-only Lighting"):
        bundles.restore_state_bundle(archive, tmp_path / "rollbacks")


def test_state_bundle_detects_database_tampering(monkeypatch, tmp_path):
    _, _, _, bundles, _ = _stack(monkeypatch, tmp_path)
    archive = bundles.create_state_bundle(tmp_path / "good.zip")
    replacement = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(replacement, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "lehue.sqlite3":
                data += b"tampered"
            target.writestr(item, data)
    with pytest.raises(ValueError, match="checksum"):
        bundles.inspect_state_bundle(replacement)
