import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import app.core.config as config
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
