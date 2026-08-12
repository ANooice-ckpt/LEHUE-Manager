from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.db import connect, init_db
from app.core.identity_db import connect_identity, init_identity_db


STATE_BUNDLE_FORMAT = 1
DB_NAMES = ("lehue.sqlite3", "lehue_identity.sqlite3")
KEY_PATH = "metadata/credential_source_key"
REQUIRED_TABLES = {
    "lehue.sqlite3": {"participants", "study_subjects", "lighting_files", "raw_events"},
    "lehue_identity.sqlite3": {"admin_users", "candidates", "web_sessions"},
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid state bundle app version: {value}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_database(source, destination: Path, *, clear_sessions: bool = False) -> None:
    with closing(source()) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        if clear_sessions:
            dst.execute("DELETE FROM web_sessions")
        dst.commit()


def _lighting_references(database: Path) -> list[dict]:
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute(
            """SELECT upload_uid,storage_backend,object_key,sha256,file_size_bytes
               FROM lighting_files ORDER BY id"""
        ).fetchall()
    return [
        {
            "upload_uid": row[0],
            "storage_backend": row[1],
            "bucket": settings.oss_bucket if row[1] == "oss" else "",
            "object_key": row[2],
            "sha256": row[3],
            "size": row[4],
            "portable": row[1] == "oss",
        }
        for row in rows
    ]


def _counts(main_database: Path, identity_database: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    with closing(sqlite3.connect(main_database)) as conn:
        for table in (
            "study_subjects", "device_packs", "incidents", "gps_locations",
            "questionnaire_responses", "lighting_files", "raw_events",
        ):
            result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    with closing(sqlite3.connect(identity_database)) as conn:
        for table in ("admin_users", "candidates", "contact_logs", "s0_imports"):
            result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return result


def create_state_bundle(destination: Path) -> Path:
    """Create the single portable representation of a LEHUE runtime state."""
    source_key = settings.credential_encryption_key.strip()
    try:
        Fernet(source_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("The current CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key") from exc

    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        destination = destination / f"LEHUE_{settings.runtime_env.upper()}_state_bundle_{_utc_stamp()}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lehue-state-bundle-") as temporary_text:
        temporary = Path(temporary_text)
        main_copy = temporary / DB_NAMES[0]
        identity_copy = temporary / DB_NAMES[1]
        _copy_database(connect, main_copy)
        _copy_database(connect_identity, identity_copy, clear_sessions=True)
        lighting = _lighting_references(main_copy)
        metadata = temporary / "metadata"
        metadata.mkdir()
        (metadata / "credential_source_key").write_text(source_key, encoding="ascii")
        manifest = {
            "state_bundle_format": STATE_BUNDLE_FORMAT,
            "project": settings.project_name,
            "runtime_environment": settings.runtime_env,
            "app_version": settings.app_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "study_timezone": settings.study_timezone,
            "contents": [*DB_NAMES, "gps_raw/", KEY_PATH],
            "database_sha256": {name: _sha256(temporary / name) for name in DB_NAMES},
            "counts": _counts(main_copy, identity_copy),
            "credential_metadata": {
                "cipher": "fernet",
                "source_key_path": KEY_PATH,
                "encrypted_fields": ["participants.secret_ciphertext", "study_subjects.portal_token_ciphertext"],
            },
            "lighting_canonical_objects": lighting,
            "contains_local_lighting_references": any(not item["portable"] for item in lighting),
            "excludes": [
                "web_sessions", "Lighting raw bytes", "server .env", "DOMAIN",
                "RAM Role and OSS endpoint configuration", "ADMIN_TOKEN",
            ],
            "sensitive": True,
            "note": "Contains credentials and participant data. Store as a secret.",
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        gps_files = sorted(settings.raw_archive_dir.rglob("*.jsonl")) if settings.raw_archive_dir.exists() else []
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in DB_NAMES:
                archive.write(temporary / name, name)
            archive.write(manifest_path, "manifest.json")
            archive.write(metadata / "credential_source_key", KEY_PATH)
            for path in gps_files:
                archive.write(path, (Path("gps_raw") / path.relative_to(settings.raw_archive_dir)).as_posix())
    return destination


def create_temporary_state_bundle() -> tuple[str, str]:
    temporary = Path(tempfile.mkdtemp(prefix="lehue_state_bundle_"))
    return str(create_state_bundle(temporary)), str(temporary)


def _safe_archive_names(archive: zipfile.ZipFile) -> set[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("State bundle ZIP contains duplicate paths")
    for name in names:
        path = PurePosixPath(name)
        if not name or name.startswith(("/", "\\")) or "\\" in name or ".." in path.parts:
            raise ValueError("State bundle ZIP contains an unsafe path")
    return set(names)


def _validate_database(path: Path, name: str) -> None:
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError(f"State bundle database failed integrity check: {name}")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"State bundle contains an invalid SQLite database: {name}") from exc
    missing = REQUIRED_TABLES[name] - tables
    if missing:
        raise ValueError(f"State bundle database {name} is missing required tables: {', '.join(sorted(missing))}")


def _validate_target_compatibility(manifest: dict, main_database: Path) -> None:
    if manifest.get("project") != settings.project_name:
        raise ValueError("State bundle belongs to a different project")
    source_env = str(manifest.get("runtime_environment") or "")
    if source_env != settings.runtime_env:
        raise ValueError(
            f"State bundle environment {source_env or 'unknown'} cannot replace {settings.runtime_env}; "
            "TEST and PROD state are isolated"
        )
    if manifest.get("state_bundle_format") != STATE_BUNDLE_FORMAT:
        raise ValueError("Unsupported state bundle format")
    if _version_tuple(manifest.get("app_version")) > _version_tuple(settings.app_version):
        raise ValueError("State bundle was created by a newer LEHUE version; upgrade the target first")

    with closing(sqlite3.connect(main_database)) as conn:
        rows = conn.execute(
            "SELECT upload_uid,storage_backend,object_key,sha256,file_size_bytes FROM lighting_files ORDER BY id"
        ).fetchall()
    manifest_lighting = manifest.get("lighting_canonical_objects")
    if not isinstance(manifest_lighting, list):
        raise ValueError("State bundle is missing the Lighting canonical-object manifest")
    database_references = [
        (row[0], row[1], row[2], row[3], row[4]) for row in rows
    ]
    manifest_references = [
        (
            item.get("upload_uid"), item.get("storage_backend"), item.get("object_key"),
            item.get("sha256"), item.get("size"),
        )
        for item in manifest_lighting
        if isinstance(item, dict)
    ]
    if manifest_references != database_references:
        raise ValueError("State bundle Lighting manifest does not match its database")
    if settings.light_storage_backend == "oss" and any(row[1] != "oss" for row in rows):
        raise ValueError(
            "State bundle contains local-only Lighting raw references and cannot be applied to an OSS environment"
        )
    if settings.light_storage_backend == "oss":
        source_buckets = {
            str(item.get("bucket") or "")
            for item in manifest_lighting
            if item.get("storage_backend") == "oss"
        }
        if source_buckets and source_buckets != {settings.oss_bucket}:
            raise ValueError(
                "State bundle Lighting objects reference a different OSS bucket; copy the canonical objects first"
            )


def _extract_and_validate(source: Path, destination: Path) -> tuple[dict, str]:
    try:
        with zipfile.ZipFile(source) as archive:
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ValueError(f"State bundle ZIP is corrupt at {corrupt}")
            names = _safe_archive_names(archive)
            required = {*DB_NAMES, "manifest.json", KEY_PATH}
            if not required <= names:
                raise ValueError("State bundle is missing databases, manifest, or credential metadata")
            try:
                manifest = json.loads(archive.read("manifest.json"))
                source_key = archive.read(KEY_PATH).decode("ascii").strip()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("State bundle manifest or credential metadata is invalid") from exc
            for name in DB_NAMES:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
            gps_root = destination / "gps_raw"
            gps_root.mkdir(parents=True, exist_ok=True)
            for name in sorted(names):
                if name.startswith("gps_raw/") and not name.endswith("/"):
                    target = destination / Path(*PurePosixPath(name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as reader, target.open("wb") as writer:
                        shutil.copyfileobj(reader, writer)
    except zipfile.BadZipFile as exc:
        raise ValueError("State bundle is not a valid ZIP archive") from exc

    try:
        Fernet(source_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("State bundle credential source key is invalid") from exc
    for name in DB_NAMES:
        expected = str(manifest.get("database_sha256", {}).get(name) or "")
        if not expected or _sha256(destination / name) != expected:
            raise ValueError(f"State bundle database checksum does not match manifest: {name}")
        _validate_database(destination / name, name)
    _validate_target_compatibility(manifest, destination / DB_NAMES[0])
    return manifest, source_key


def inspect_state_bundle(source: Path) -> dict:
    source = Path(source)
    with tempfile.TemporaryDirectory(prefix="lehue-state-inspect-") as temporary_text:
        manifest, _ = _extract_and_validate(source, Path(temporary_text))
    return manifest


def _reencrypt_credentials(database: Path, source_key: str) -> None:
    source_cipher = Fernet(source_key.encode("ascii"))
    target_cipher = Fernet(settings.credential_encryption_key.strip().encode("ascii"))
    with closing(sqlite3.connect(database)) as conn:
        try:
            for table, key_column, cipher_column in (
                ("participants", "participant_id", "secret_ciphertext"),
                ("study_subjects", "participant_id", "portal_token_ciphertext"),
            ):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if cipher_column not in columns:
                    continue
                rows = conn.execute(
                    f"SELECT {key_column},{cipher_column} FROM {table} WHERE {cipher_column}<>''"
                ).fetchall()
                replacements = []
                for row_key, ciphertext in rows:
                    plaintext = source_cipher.decrypt(ciphertext.encode("ascii"))
                    replacements.append((target_cipher.encrypt(plaintext).decode("ascii"), row_key))
                conn.executemany(
                    f"UPDATE {table} SET {cipher_column}=? WHERE {key_column}=?", replacements
                )
            conn.commit()
        except (InvalidToken, UnicodeEncodeError) as exc:
            conn.rollback()
            raise ValueError("State bundle contains credential data that its source key cannot decrypt") from exc


def restore_state_bundle(source: Path, backup_dir: Path) -> tuple[Path, dict]:
    """Validate, create a rollback bundle, re-key credentials, then replace runtime state."""
    source, backup_dir = Path(source), Path(backup_dir)
    settings.data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lehue-state-restore-", dir=settings.data_dir.parent) as temporary_text:
        temporary = Path(temporary_text)
        manifest, source_key = _extract_and_validate(source, temporary)
        rollback = create_state_bundle(backup_dir / f"before_restore_{_utc_stamp()}.zip")
        _reencrypt_credentials(temporary / DB_NAMES[0], source_key)

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        for name, target in zip(DB_NAMES, (settings.db_path, settings.identity_db_path)):
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{target}{suffix}").unlink(missing_ok=True)
            os.replace(temporary / name, target)

        staged_gps = temporary / "gps_raw"
        if settings.raw_archive_dir.exists():
            shutil.rmtree(settings.raw_archive_dir)
        shutil.copytree(staged_gps, settings.raw_archive_dir)
        init_db()
        init_identity_db()
    return rollback, manifest
