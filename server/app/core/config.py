from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.version import APP_VERSION


def _load_repo_env() -> None:
    """Load repository-root .env without adding a third-party dependency.

    Existing process environment variables always win. This keeps Windows local
    development simple while remaining compatible with Docker env_file.
    """
    repo_root = Path(__file__).resolve().parents[3]
    env_path = repo_root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_repo_env()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


_DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))


@dataclass(frozen=True)
class Settings:
    project_name: str = os.getenv("PROJECT_NAME", "LEHUE")
    domain: str = os.getenv("DOMAIN", "localhost")
    app_version: str = APP_VERSION
    study_timezone: str = os.getenv("STUDY_TIMEZONE", "Asia/Shanghai")
    admin_token: str = os.getenv("ADMIN_TOKEN", "CHANGE_ME_TO_A_LONG_RANDOM_ADMIN_TOKEN")
    data_dir: Path = _DATA_DIR
    db_path: Path = Path(os.getenv("DB_PATH", str(_DATA_DIR / "lehue.sqlite3")))
    identity_db_path: Path = Path(os.getenv("IDENTITY_DB_PATH", str(_DATA_DIR / "lehue_identity.sqlite3")))
    raw_archive_dir: Path = Path(os.getenv("RAW_ARCHIVE_DIR", str(_DATA_DIR / "raw" / "gps")))
    raw_light_dir: Path = Path(os.getenv("RAW_LIGHT_DIR", str(_DATA_DIR / "raw" / "lighting")))
    light_upload_max_bytes: int = _int("LIGHT_UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
    enable_docs: bool = _bool("ENABLE_DOCS", True)
    qc_gap_warning_seconds: int = _int("QC_GAP_WARNING_SECONDS", 300)
    qc_delay_warning_seconds: int = _int("QC_DELAY_WARNING_SECONDS", 120)
    qc_poor_accuracy_m: int = _int("QC_POOR_ACCURACY_M", 50)


settings = Settings()
