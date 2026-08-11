import sqlite3
import shutil
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.core.config as config
import app.core.db as dbmod
import app.core.test_seed as test_seed


def _sqlite_file(path: Path, marker: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES(?)", (marker,))
        conn.commit()


def test_test_seed_installs_once_and_never_overwrites(monkeypatch, tmp_path):
    seed_dir = tmp_path / "seed"
    data_dir = tmp_path / "runtime" / "test"
    seed_dir.mkdir()
    _sqlite_file(seed_dir / "lehue.sqlite3", "main-seed")
    _sqlite_file(seed_dir / "lehue_identity.sqlite3", "identity-seed")
    (seed_dir / "raw" / "gps").mkdir(parents=True)
    (seed_dir / "raw" / "gps" / "event.jsonl").write_text("{}\n", encoding="utf-8")

    settings = SimpleNamespace(
        runtime_env="test",
        load_test_seed=True,
        data_dir=data_dir,
        db_path=data_dir / "lehue.sqlite3",
        identity_db_path=data_dir / "lehue_identity.sqlite3",
    )
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(test_seed, "TEST_SEED_DIR", seed_dir)

    assert test_seed.install_test_seed_if_empty() is True
    assert (data_dir / "raw" / "gps" / "event.jsonl").read_text(encoding="utf-8") == "{}\n"
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("UPDATE marker SET value='runtime-change'")
        conn.commit()

    assert test_seed.install_test_seed_if_empty() is False
    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "runtime-change"


def test_test_seed_replaces_preexisting_empty_databases(monkeypatch, tmp_path):
    seed_dir = tmp_path / "seed"
    data_dir = tmp_path / "runtime" / "test"
    seed_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    _sqlite_file(seed_dir / "lehue.sqlite3", "main-seed")
    _sqlite_file(seed_dir / "lehue_identity.sqlite3", "identity-seed")
    for name in ("lehue.sqlite3", "lehue_identity.sqlite3"):
        with closing(sqlite3.connect(data_dir / name)) as conn:
            conn.execute("CREATE TABLE empty_table(value TEXT)")
            conn.commit()

    settings = SimpleNamespace(
        runtime_env="test",
        load_test_seed=True,
        data_dir=data_dir,
        db_path=data_dir / "lehue.sqlite3",
        identity_db_path=data_dir / "lehue_identity.sqlite3",
    )
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(test_seed, "TEST_SEED_DIR", seed_dir)

    assert test_seed.install_test_seed_if_empty() is True
    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "main-seed"


def test_prod_never_reads_test_seed(monkeypatch, tmp_path):
    data_dir = tmp_path / "prod"
    settings = SimpleNamespace(
        runtime_env="prod",
        load_test_seed=True,
        data_dir=data_dir,
        db_path=data_dir / "lehue.sqlite3",
        identity_db_path=data_dir / "lehue_identity.sqlite3",
    )
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(test_seed, "TEST_SEED_DIR", tmp_path / "missing-seed")

    assert test_seed.install_test_seed_if_empty() is False
    assert not data_dir.exists()


def test_prod_requires_oss_backend(monkeypatch):
    monkeypatch.setattr(config, "_RUNTIME_ENV", "prod")
    monkeypatch.delenv("LIGHT_STORAGE_BACKEND", raising=False)
    assert config._light_storage_backend() == "oss"
    monkeypatch.setenv("LIGHT_STORAGE_BACKEND", "local")
    with pytest.raises(RuntimeError, match="PROD Lighting raw storage must use OSS"):
        config._light_storage_backend()
    monkeypatch.setenv("OSS_CREDENTIAL_MODE", "access_key")
    with pytest.raises(RuntimeError, match="PROD OSS access must use an ECS RAM role"):
        config._oss_credential_mode()


def test_oss_settings_support_role_and_explicit_test_credentials():
    with pytest.raises(RuntimeError, match="OSS_BUCKET"):
        config.Settings(light_storage_backend="oss")
    role_settings = config.Settings(
        light_storage_backend="oss",
        oss_bucket="lehue-test",
        oss_region="cn-hongkong",
        oss_credential_mode="ecs_ram_role",
    )
    assert role_settings.oss_access_key_id == ""
    settings = config.Settings(
        light_storage_backend="oss",
        oss_bucket="lehue-test",
        oss_region="cn-hongkong",
        oss_credential_mode="access_key",
        oss_access_key_id="test-key-id",
        oss_access_key_secret="test-key-secret",
    )
    assert settings.oss_bucket == "lehue-test"


def test_existing_lighting_table_gets_storage_columns(monkeypatch, tmp_path):
    data_dir = tmp_path / "test"
    data_dir.mkdir()
    db_path = data_dir / "lehue.sqlite3"
    shutil.copyfile(Path(__file__).parents[1] / "test_seed" / "lehue.sqlite3", db_path)
    settings = SimpleNamespace(
        data_dir=data_dir,
        db_path=db_path,
        raw_archive_dir=data_dir / "raw" / "gps",
        raw_light_dir=data_dir / "raw" / "lighting",
        light_storage_backend="local",
    )
    monkeypatch.setattr(dbmod, "settings", settings)

    dbmod.init_db()

    with closing(sqlite3.connect(db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lighting_files)")}
        assert {"storage_backend", "object_key", "upload_status"} <= columns
        assert conn.execute(
            "SELECT COUNT(*) FROM lighting_files WHERE object_key<>stored_path"
        ).fetchone()[0] == 0
