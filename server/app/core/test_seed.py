from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


TEST_SEED_DIR = Path(__file__).resolve().parents[2] / "test_seed"


def _database_has_rows(path: Path) -> bool:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return any(conn.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}" LIMIT 1)').fetchone()[0] for table in tables)


def _raw_has_files(data_dir: Path) -> bool:
    raw_dir = data_dir / "raw"
    return raw_dir.is_dir() and any(path.is_file() and path.name != ".gitkeep" for path in raw_dir.rglob("*"))


def _atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.seed-copy")
    shutil.copy2(source, temporary)
    temporary.replace(target)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)


def install_test_seed_if_empty() -> bool:
    """Install the immutable synthetic baseline into a new or empty TEST tree."""
    # Resolve settings at call time so pytest can reload environment-specific config.
    from app.core.config import settings

    if settings.runtime_env != "test" or not settings.load_test_seed:
        return False

    targets = (settings.db_path, settings.identity_db_path)
    existing = [path.exists() for path in targets]
    if all(existing):
        if any(_database_has_rows(path) for path in targets) or _raw_has_files(settings.data_dir):
            return False
    if any(existing) and not all(existing):
        raise RuntimeError(
            f"Incomplete TEST data directory: {settings.data_dir}. "
            "Both SQLite files must exist together; no seed data was copied."
        )

    sources = (TEST_SEED_DIR / "lehue.sqlite3", TEST_SEED_DIR / "lehue_identity.sqlite3")
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise RuntimeError(f"TEST seed is incomplete: missing {', '.join(missing)}")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    for source, target in zip(sources, targets, strict=True):
        _atomic_copy(source, target)

    seed_raw = TEST_SEED_DIR / "raw"
    if seed_raw.is_dir():
        shutil.copytree(seed_raw, settings.data_dir / "raw", dirs_exist_ok=True)
    return True
