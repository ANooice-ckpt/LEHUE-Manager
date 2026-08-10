from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.db import connect, db
from app.core.identity_db import connect_identity, identity_db


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_system_backup() -> tuple[str, str]:
    """Create a consistent downloadable snapshot of LEHUE's two SQLite DBs."""
    temp_dir = Path(tempfile.mkdtemp(prefix="lehue_backup_"))
    main_copy = temp_dir / "lehue.sqlite3"
    identity_copy = temp_dir / "lehue_identity.sqlite3"

    with connect() as src:
        dst = sqlite3.connect(main_copy)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()

    with connect_identity() as src:
        dst = sqlite3.connect(identity_copy)
        try:
            src.backup(dst)
            # Backups preserve accounts but intentionally invalidate browser sessions.
            dst.execute("DELETE FROM web_sessions")
            dst.commit()
        finally:
            dst.close()

    with db() as conn:
        counts = {
            "study_subjects": conn.execute("SELECT COUNT(*) n FROM study_subjects").fetchone()["n"],
            "device_packs": conn.execute("SELECT COUNT(*) n FROM device_packs").fetchone()["n"],
            "incidents": conn.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"],
            "gps_locations": conn.execute("SELECT COUNT(*) n FROM gps_locations").fetchone()["n"],
            "raw_events": conn.execute("SELECT COUNT(*) n FROM raw_events").fetchone()["n"],
        }
    with identity_db() as conn:
        counts.update({
            "admin_users": conn.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"],
            "candidates": conn.execute("SELECT COUNT(*) n FROM candidates").fetchone()["n"],
            "contact_logs": conn.execute("SELECT COUNT(*) n FROM contact_logs").fetchone()["n"],
        })

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "project": settings.project_name,
        "version": settings.app_version,
        "generated_at_utc": generated,
        "study_timezone": settings.study_timezone,
        "contents": ["lehue.sqlite3", "lehue_identity.sqlite3"],
        "excluded": ["web_sessions", "server/data/raw/gps JSONL mirror"],
        "counts": counts,
        "sha256": {
            "lehue.sqlite3": _sha256(main_copy),
            "lehue_identity.sqlite3": _sha256(identity_copy),
        },
        "note": "Sensitive backup: contains identity/contact data and GPS records. Store securely.",
    }
    manifest_path = temp_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    zip_path = temp_dir / f"LEHUE_system_backup_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(main_copy, arcname=main_copy.name)
        zf.write(identity_copy, arcname=identity_copy.name)
        zf.write(manifest_path, arcname=manifest_path.name)
    return str(zip_path), str(temp_dir)
