import importlib
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


def _stack(monkeypatch, root: Path, runtime_env: str = "test"):
    monkeypatch.setenv("LEHUE_ENV", runtime_env)
    monkeypatch.setenv("DATA_ROOT", str(root))
    monkeypatch.setenv("LOAD_TEST_SEED", "false")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.core.test_snapshot as snapshot; importlib.reload(snapshot)
    dbmod.init_db(); idb.init_identity_db()
    return config, dbmod, idb, snapshot


def test_snapshot_round_trip_includes_gps_and_lighting_manifest(monkeypatch, tmp_path):
    config, dbmod, idb, snapshot = _stack(monkeypatch, tmp_path)
    with dbmod.db() as conn:
        conn.execute("INSERT INTO study_subjects(participant_id,created_at_utc,updated_at_utc) VALUES('001','now','now')")
        conn.execute("INSERT INTO lighting_files(upload_uid,participant_id,date_local,original_filename,stored_path,storage_backend,object_key,file_size_bytes,sha256,uploaded_at_utc,parser_version,quality) VALUES('u1','001','2026-08-12','x.csv','raw/lighting/x','oss','raw/lighting/x',123,'abc','now','v1','valid')")
    with idb.identity_db() as conn:
        conn.execute("INSERT INTO candidates(candidate_uid,name,created_at_utc,updated_at_utc) VALUES('c1','before','now','now')")
    raw = config.settings.raw_archive_dir / "2026-08-12.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True); raw.write_text('{"x":1}\n', encoding="utf-8")
    archive = snapshot.create_test_snapshot(tmp_path / "portable.zip")
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert "gps_raw/2026-08-12.jsonl" in zf.namelist()
        assert manifest["runtime_environment"] == "test"
        assert manifest["lighting_canonical_objects"][0]["object_key"] == "raw/lighting/x"
        assert not any(name.startswith("lighting_raw/") for name in zf.namelist())
    with idb.identity_db() as conn:
        conn.execute("UPDATE candidates SET name='changed' WHERE candidate_uid='c1'")
    raw.write_text("changed\n", encoding="utf-8")
    Path(f"{config.settings.db_path}-wal").write_text("stale", encoding="utf-8")
    Path(f"{config.settings.identity_db_path}-shm").write_text("stale", encoding="utf-8")
    rollback, key = snapshot.restore_test_snapshot(archive, tmp_path / "rollbacks")
    assert rollback.exists() and key
    assert not Path(f"{config.settings.db_path}-wal").exists()
    assert not Path(f"{config.settings.identity_db_path}-shm").exists()
    with sqlite3.connect(config.settings.identity_db_path) as conn:
        assert conn.execute("SELECT name FROM candidates WHERE candidate_uid='c1'").fetchone()[0] == "before"
    assert raw.read_text(encoding="utf-8") == '{"x":1}\n'


def test_snapshot_restore_rejects_prod(monkeypatch, tmp_path):
    _, _, _, snapshot = _stack(monkeypatch, tmp_path / "test-root")
    monkeypatch.setattr(snapshot, "settings", replace(snapshot.settings, runtime_env="prod"))
    with pytest.raises(RuntimeError, match="forbidden"):
        snapshot.restore_test_snapshot(tmp_path / "anything.zip", tmp_path / "backups")


def test_snapshot_rejects_wrong_environment_manifest(monkeypatch, tmp_path):
    _, _, _, snapshot = _stack(monkeypatch, tmp_path)
    archive = snapshot.create_test_snapshot(tmp_path / "bad.zip")
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(replacement, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(data); manifest["runtime_environment"] = "prod"; data = json.dumps(manifest).encode()
            dst.writestr(item, data)
    with pytest.raises(ValueError, match="TEST snapshot"):
        snapshot.inspect_test_snapshot(replacement)
