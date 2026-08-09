from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Settings:
    project_name: str = os.getenv("PROJECT_NAME", "LightTrace")
    app_version: str = os.getenv("APP_VERSION", "0.2.0")
    admin_token: str = os.getenv("ADMIN_TOKEN", "CHANGE_ME_TO_A_LONG_RANDOM_ADMIN_TOKEN")
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    db_path: Path = Path(os.getenv("DB_PATH", "./data/lighttrace.sqlite3"))
    raw_archive_dir: Path = Path(os.getenv("RAW_ARCHIVE_DIR", "./data/raw/gps"))
    enable_docs: bool = _bool("ENABLE_DOCS", True)
    qc_gap_warning_seconds: int = _int("QC_GAP_WARNING_SECONDS", 300)
    qc_delay_warning_seconds: int = _int("QC_DELAY_WARNING_SECONDS", 120)
    qc_poor_accuracy_m: int = _int("QC_POOR_ACCURACY_M", 50)


settings = Settings()
