from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

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


def _environment_value(name: str, *, legacy_test: bool = False) -> str:
    test_value = os.getenv(f"{name}_TEST", "").strip()
    if not test_value and legacy_test:
        test_value = os.getenv(name, "").strip()
    prod_value = os.getenv(f"{name}_PROD", "").strip()
    if test_value and prod_value and test_value == prod_value:
        raise RuntimeError(f"{name}_TEST and {name}_PROD must be different")
    value = os.getenv(f"{name}_{_RUNTIME_ENV.upper()}", "").strip()
    if not value and legacy_test and _RUNTIME_ENV == "test":
        value = os.getenv(name, "").strip()
    return value


def _isolated_bucket(name: str) -> str:
    test = os.getenv(f"{name}_TEST", "").strip()
    prod = os.getenv(f"{name}_PROD", "").strip()
    if test and prod and test == prod:
        raise RuntimeError(f"{name}_TEST and {name}_PROD must be different")
    if name == "BACKUP_OSS_BUCKET" and (test or prod) and not (test and prod):
        raise RuntimeError("Declare both BACKUP_OSS_BUCKET_TEST and BACKUP_OSS_BUCKET_PROD when enabling OSS backups")
    return _environment_value(name)


def _light_storage_backend() -> str:
    value = os.getenv("LIGHT_STORAGE_BACKEND", "oss" if _RUNTIME_ENV == "prod" else "local").strip().lower()
    if value not in {"local", "oss"}:
        raise RuntimeError("LIGHT_STORAGE_BACKEND must be local or oss")
    if _RUNTIME_ENV == "prod" and value != "oss":
        raise RuntimeError("PROD Lighting raw storage must use OSS")
    return value


def _oss_credential_mode() -> str:
    value = os.getenv("OSS_CREDENTIAL_MODE", "ecs_ram_role" if _RUNTIME_ENV == "prod" else "access_key").strip().lower()
    if value not in {"ecs_ram_role", "access_key"}:
        raise RuntimeError("OSS_CREDENTIAL_MODE must be ecs_ram_role or access_key")
    if _RUNTIME_ENV == "prod" and value != "ecs_ram_role":
        raise RuntimeError("PROD OSS access must use an ECS RAM role")
    return value


@dataclass(frozen=True)
class Settings:
    project_name: str = os.getenv("PROJECT_NAME", "LEHUE")
    domain: str = os.getenv("DOMAIN", "localhost")
    app_version: str = APP_VERSION
    runtime_env: str = _RUNTIME_ENV
    study_timezone: str = os.getenv("STUDY_TIMEZONE", "Asia/Shanghai")
    # Compatibility secret for the legacy /api/v1/admin endpoints. New
    # deployments use Web Admin sessions; setup scripts persist this value so
    # operators never have to create or manage it themselves.
    admin_token: str = _environment_value("ADMIN_TOKEN", legacy_test=True) or secrets.token_urlsafe(48)
    credential_encryption_key: str = _environment_value("CREDENTIAL_ENCRYPTION_KEY", legacy_test=True)
    data_dir: Path = _DATA_DIR
    db_path: Path = _DATA_DIR / "lehue.sqlite3"
    identity_db_path: Path = _DATA_DIR / "lehue_identity.sqlite3"
    raw_archive_dir: Path = _DATA_DIR / "raw" / "gps"
    raw_light_dir: Path = _DATA_DIR / "raw" / "lighting"
    light_storage_backend: str = _light_storage_backend()
    oss_bucket: str = _isolated_bucket("OSS_BUCKET")
    oss_bucket_test: str = os.getenv("OSS_BUCKET_TEST", "").strip()
    oss_bucket_prod: str = os.getenv("OSS_BUCKET_PROD", "").strip()
    oss_region: str = os.getenv("OSS_REGION", "").strip()
    oss_endpoint: str = os.getenv("OSS_ENDPOINT", "").strip()
    oss_public_endpoint: str = os.getenv("OSS_PUBLIC_ENDPOINT", "").strip()
    oss_credential_mode: str = _oss_credential_mode()
    oss_role_name: str = _environment_value("OSS_ROLE_NAME", legacy_test=True)
    oss_access_key_id: str = _environment_value("OSS_ACCESS_KEY_ID", legacy_test=True)
    oss_access_key_secret: str = _environment_value("OSS_ACCESS_KEY_SECRET", legacy_test=True)
    oss_upload_url_seconds: int = _int("OSS_UPLOAD_URL_SECONDS", 900)
    backup_oss_bucket: str = _isolated_bucket("BACKUP_OSS_BUCKET")
    backup_oss_prefix: str = os.getenv("BACKUP_OSS_PREFIX", "lehue-backups").strip().strip("/")
    load_test_seed: bool = _bool("LOAD_TEST_SEED", True)
    light_upload_max_bytes: int = _int("LIGHT_UPLOAD_MAX_BYTES", 64 * 1024 * 1024)
    enable_docs: bool = _bool("ENABLE_DOCS", True)
    qc_gap_warning_seconds: int = _int("QC_GAP_WARNING_SECONDS", 300)
    qc_delay_warning_seconds: int = _int("QC_DELAY_WARNING_SECONDS", 120)
    qc_poor_accuracy_m: int = _int("QC_POOR_ACCURACY_M", 50)
    gps_auth_cache_seconds: int = _int("GPS_AUTH_CACHE_SECONDS", 300)
    gps_backfill_delay_seconds: int = _int("GPS_BACKFILL_DELAY_SECONDS", 900)
    gps_daily_min_points: int = _int("GPS_DAILY_MIN_POINTS", 1000)
    gps_daily_edge_coverage_hours: int = _int("GPS_DAILY_EDGE_COVERAGE_HOURS", 3)
    gps_daily_max_gap_seconds: int = _int("GPS_DAILY_MAX_GAP_SECONDS", 7200)
    questionnaire_evening_cutoff_hour: int = _int("QUESTIONNAIRE_EVENING_CUTOFF_HOUR", 12)
    qc_day_close_hour: int = _int("QC_DAY_CLOSE_HOUR", 18)
    daily_qc_interval_seconds: int = _int("DAILY_QC_INTERVAL_SECONDS", 3600)
    gps_tile_url: str = os.getenv(
        "GPS_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    ).strip()
    gps_tile_attribution: str = os.getenv(
        "GPS_TILE_ATTRIBUTION", "&copy; OpenStreetMap contributors"
    ).strip()

    def __post_init__(self) -> None:
        if self.runtime_env == "prod":
            if self.light_storage_backend != "oss" or self.oss_credential_mode != "ecs_ram_role":
                raise RuntimeError("PROD requires OSS storage with an ECS RAM role")
            test_key = os.getenv("CREDENTIAL_ENCRYPTION_KEY_TEST", "").strip() or os.getenv("CREDENTIAL_ENCRYPTION_KEY", "").strip()
            if self.credential_encryption_key and self.credential_encryption_key == test_key:
                raise RuntimeError("PROD credential encryption key must differ from TEST")
            test_role = os.getenv("OSS_ROLE_NAME_TEST", "").strip() or os.getenv("OSS_ROLE_NAME", "").strip()
            if not self.oss_role_name or self.oss_role_name == test_role:
                raise RuntimeError("PROD requires a separate OSS_ROLE_NAME_PROD with a PROD-only RAM policy")
            test_admin = os.getenv("ADMIN_TOKEN_TEST", "").strip() or os.getenv("ADMIN_TOKEN", "").strip()
            if self.admin_token == test_admin:
                raise RuntimeError("PROD ADMIN_TOKEN must differ from TEST")
        key = self.credential_encryption_key.strip()
        if not key or key.startswith("CHANGE_ME"):
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required; run the platform setup script")
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.light_storage_backend != "oss":
            return
        if not self.oss_bucket or not (self.oss_region or self.oss_endpoint):
            raise RuntimeError("OSS Lighting storage requires OSS_BUCKET_TEST / OSS_BUCKET_PROD and OSS_REGION or OSS_ENDPOINT")
        if not self.oss_bucket_test or not self.oss_bucket_prod or self.oss_bucket_test == self.oss_bucket_prod:
            raise RuntimeError("Declare distinct OSS_BUCKET_TEST and OSS_BUCKET_PROD before using OSS")
        expected_bucket = self.oss_bucket_test if self.runtime_env == "test" else self.oss_bucket_prod
        if self.oss_bucket != expected_bucket:
            raise RuntimeError("OSS_BUCKET does not match the selected runtime environment")
        other_bucket = self.oss_bucket_prod if self.runtime_env == "test" else self.oss_bucket_test
        if self.backup_oss_bucket == other_bucket:
            raise RuntimeError("Backup bucket must not point to the other environment's Lighting bucket")
        if self.oss_credential_mode == "access_key" and (not self.oss_access_key_id or not self.oss_access_key_secret):
            raise RuntimeError("OSS access_key mode requires OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET")


settings = Settings()
