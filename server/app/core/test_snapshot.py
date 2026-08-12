from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.db import connect
from app.core.identity_db import connect_identity

SNAPSHOT_FORMAT = 1
DB_NAMES = ("lehue.sqlite3", "lehue_identity.sqlite3")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise ValueError(f"Invalid snapshot app version: {value}") from exc


def _copy_database(source, destination: Path, *, clear_sessions: bool = False) -> None:
    with closing(source()) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        if clear_sessions:
            dst.execute("DELETE FROM web_sessions")
        dst.commit()


def create_test_snapshot(destination: Path) -> Path:
    if settings.runtime_env != "test":
        raise RuntimeError("TEST snapshots can only be exported from LEHUE_ENV=test")
    key = settings.credential_encryption_key.strip()
    if not key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required")
    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        destination = destination / f"LEHUE_TEST_snapshot_{_utc_stamp()}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lehue-test-snapshot-") as temporary_text:
        temporary = Path(temporary_text)
        _copy_database(connect, temporary / DB_NAMES[0])
        _copy_database(connect_identity, temporary / DB_NAMES[1], clear_sessions=True)
        with closing(sqlite3.connect(temporary / DB_NAMES[0])) as conn:
            lighting = [
                {"upload_uid": row[0], "storage_backend": row[1], "object_key": row[2], "sha256": row[3], "file_size_bytes": row[4]}
                for row in conn.execute("SELECT upload_uid,storage_backend,object_key,sha256,file_size_bytes FROM lighting_files ORDER BY id")
            ]
        manifest = {
            "snapshot_format": SNAPSHOT_FORMAT,
            "project": settings.project_name,
            "runtime_environment": "test",
            "app_version": settings.app_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "study_timezone": settings.study_timezone,
            "lighting_canonical_objects": lighting,
            "contains": [*DB_NAMES, "gps_raw/", "metadata/credential_encryption_key"],
            "excludes": ["web_sessions", "Lighting raw bytes", "ADMIN_TOKEN"],
            "sensitive": True,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata = temporary / "metadata"
        metadata.mkdir()
        (metadata / "credential_encryption_key").write_text(key, encoding="ascii")
        gps_files = sorted(settings.raw_archive_dir.rglob("*.jsonl")) if settings.raw_archive_dir.exists() else []
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in DB_NAMES:
                archive.write(temporary / name, name)
            archive.write(temporary / "manifest.json", "manifest.json")
            archive.write(metadata / "credential_encryption_key", "metadata/credential_encryption_key")
            for path in gps_files:
                archive.write(path, (Path("gps_raw") / path.relative_to(settings.raw_archive_dir)).as_posix())
    return destination


def inspect_test_snapshot(source: Path) -> tuple[dict, str]:
    source = Path(source)
    with zipfile.ZipFile(source) as archive:
        if archive.testzip() is not None:
            raise ValueError("Snapshot ZIP is corrupt")
        names = set(archive.namelist())
        required = {*DB_NAMES, "manifest.json", "metadata/credential_encryption_key"}
        if not required <= names:
            raise ValueError("Snapshot is missing required TEST data")
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError("Snapshot contains an unsafe path")
        manifest = json.loads(archive.read("manifest.json"))
        key = archive.read("metadata/credential_encryption_key").decode("ascii").strip()
    if manifest.get("project") != settings.project_name or manifest.get("runtime_environment") != "test":
        raise ValueError("Only a LEHUE TEST snapshot can be restored")
    if manifest.get("snapshot_format") != SNAPSHOT_FORMAT:
        raise ValueError("Unsupported snapshot format")
    if _version_tuple(str(manifest.get("app_version", ""))) > _version_tuple(settings.app_version):
        raise ValueError("Snapshot was created by a newer LEHUE version; upgrade the target first")
    from cryptography.fernet import Fernet
    try:
        Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Snapshot credential key is invalid") from exc
    return manifest, key


def restore_test_snapshot(source: Path, backup_dir: Path) -> tuple[Path, str]:
    if settings.runtime_env != "test":
        raise RuntimeError("Snapshot restore is forbidden unless LEHUE_ENV=test")
    source, backup_dir = Path(source), Path(backup_dir)
    _, key = inspect_test_snapshot(source)
    rollback = create_test_snapshot(backup_dir / f"before_restore_{_utc_stamp()}.zip")
    settings.data_dir.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the data volume so os.replace remains atomic on Windows as well
    # as Linux (the system temp directory may be on another drive).
    with tempfile.TemporaryDirectory(prefix="lehue-test-restore-", dir=settings.data_dir.parent) as temporary_text:
        temporary = Path(temporary_text)
        with zipfile.ZipFile(source) as archive:
            for name in DB_NAMES:
                archive.extract(name, temporary)
            gps_names = [name for name in archive.namelist() if name.startswith("gps_raw/") and not name.endswith("/")]
            for name in gps_names:
                archive.extract(name, temporary)
        for name in DB_NAMES:
            with closing(sqlite3.connect(temporary / name)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"Snapshot database failed integrity check: {name}")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        for name, target in zip(DB_NAMES, (settings.db_path, settings.identity_db_path)):
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{target}{suffix}").unlink(missing_ok=True)
            os.replace(temporary / name, target)
        if settings.raw_archive_dir.exists():
            shutil.rmtree(settings.raw_archive_dir)
        extracted_gps = temporary / "gps_raw"
        if extracted_gps.exists():
            shutil.copytree(extracted_gps, settings.raw_archive_dir)
        else:
            settings.raw_archive_dir.mkdir(parents=True, exist_ok=True)
    return rollback, key
