from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.version import APP_VERSION


RUNTIME_ENV_VAR = "LEHUE_ENV"
VALID_RUNTIME_ENVS = {"test", "prod"}


def _load_repo_env() -> None:
    """Load repository-root .env without adding a third-party dependency.

    Existing process environment variables always win. LEHUE_ENV is intentionally
    never loaded from .env: every server start must explicitly choose test or prod.
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
        if key == RUNTIME_ENV_VAR:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_repo_env()


def _runtime_env() -> str:
    value = os.getenv(RUNTIME_ENV_VAR, "").strip().lower()
    if value not in VALID_RUNTIME_ENVS:
        raise RuntimeError(
            "LEHUE_ENV must be selected explicitly for every server start: "
            "set LEHUE_ENV=test or LEHUE_ENV=prod. It is intentionally not read from .env."
        )
    return value


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


_RUNTIME_ENV = _runtime_env()
# Backward-compatible interpretation: an existing DATA_DIR setting becomes the
# common root, while LEHUE always appends /test or /prod itself.
_DATA_ROOT = Path(os.getenv("DATA_ROOT", os.getenv("DATA_DIR", "./data")))
_DATA_DIR = _DATA_ROOT / _RUNTIME_ENV


def _light_storage_backend() -> str:
    value = os.getenv("LIGHT_STORAGE_BACKEND", "oss" if _RUNTIME_ENV == "prod" else "local").strip().lower()
    if value not in {"local", "oss"}:
        raise RuntimeError("LIGHT_STORAGE_BACKEND must be local or oss")
    if _RUNTIME_ENV == "prod" and value != "oss":
        raise RuntimeError("PROD Lighting raw storage must use OSS")
    return value


@dataclass(frozen=True)
class Settings:
    project_name: str = os.getenv("PROJECT_NAME", "LEHUE")
    domain: str = os.getenv("DOMAIN", "localhost")
    app_version: str = APP_VERSION
    runtime_env: str = _RUNTIME_ENV
    study_timezone: str = os.getenv("STUDY_TIMEZONE", "Asia/Shanghai")
    admin_token: str = os.getenv("ADMIN_TOKEN", "CHANGE_ME_TO_A_LONG_RANDOM_ADMIN_TOKEN")
    credential_encryption_key: str = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    data_root: Path = _DATA_ROOT
    data_dir: Path = _DATA_DIR
    db_path: Path = _DATA_DIR / "lehue.sqlite3"
    identity_db_path: Path = _DATA_DIR / "lehue_identity.sqlite3"
    raw_archive_dir: Path = _DATA_DIR / "raw" / "gps"
    raw_light_dir: Path = _DATA_DIR / "raw" / "lighting"
    light_storage_backend: str = _light_storage_backend()
    oss_bucket: str = os.getenv("OSS_BUCKET", "").strip()
    oss_region: str = os.getenv("OSS_REGION", "").strip()
    oss_endpoint: str = os.getenv("OSS_ENDPOINT", "").strip()
    oss_access_key_id: str = os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    oss_access_key_secret: str = os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    load_test_seed: bool = _bool("LOAD_TEST_SEED", True)
    light_upload_max_bytes: int = _int("LIGHT_UPLOAD_MAX_BYTES", 64 * 1024 * 1024)
    enable_docs: bool = _bool("ENABLE_DOCS", True)
    qc_gap_warning_seconds: int = _int("QC_GAP_WARNING_SECONDS", 300)
    qc_delay_warning_seconds: int = _int("QC_DELAY_WARNING_SECONDS", 120)
    qc_poor_accuracy_m: int = _int("QC_POOR_ACCURACY_M", 50)

    def __post_init__(self) -> None:
        if self.light_storage_backend != "oss":
            return
        if not self.oss_bucket or not (self.oss_region or self.oss_endpoint):
            raise RuntimeError("OSS Lighting storage requires OSS_BUCKET and OSS_REGION or OSS_ENDPOINT")
        if not self.oss_access_key_id or not self.oss_access_key_secret:
            raise RuntimeError("OSS Lighting storage requires OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET")


settings = Settings()
