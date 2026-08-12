from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import settings

IDENTITY_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admin_users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('pi','ra')),
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    session_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    csrf_token TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(username) REFERENCES admin_users(username) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_uid TEXT PRIMARY KEY,
    linked_participant_id TEXT,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT '',
    age_group TEXT NOT NULL DEFAULT '',
    beijing_based TEXT NOT NULL DEFAULT '',
    identity_type TEXT NOT NULL DEFAULT '',
    light_type TEXT NOT NULL DEFAULT '',
    education TEXT NOT NULL DEFAULT '',
    health_rating TEXT NOT NULL DEFAULT '',
    work_schedule TEXT NOT NULL DEFAULT '',
    chronotype TEXT NOT NULL DEFAULT '',
    activity_mode TEXT NOT NULL DEFAULT '',
    fixed_position_ratio TEXT NOT NULL DEFAULT '',
    screen_time_ratio TEXT NOT NULL DEFAULT '',
    indoor_daylight TEXT NOT NULL DEFAULT '',
    artificial_light_reliance TEXT NOT NULL DEFAULT '',
    outdoor_time TEXT NOT NULL DEFAULT '',
    exposure_mechanism TEXT NOT NULL DEFAULT '',
    work_district TEXT NOT NULL DEFAULT '',
    home_district TEXT NOT NULL DEFAULT '',
    commute_mode TEXT NOT NULL DEFAULT '',
    commute_duration TEXT NOT NULL DEFAULT '',
    phone_os TEXT NOT NULL DEFAULT '',
    willingness TEXT NOT NULL DEFAULT '',
    pickup_method TEXT NOT NULL DEFAULT '',
    availability TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    source_seq TEXT NOT NULL DEFAULT '',
    in_latest_snapshot INTEGER NOT NULL DEFAULT 1,
    s0_import_uid TEXT NOT NULL DEFAULT '',
    s0_raw_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS s0_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_uid TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    file_bytes BLOB NOT NULL,
    row_count INTEGER NOT NULL,
    imported_count INTEGER NOT NULL,
    filtered_count INTEGER NOT NULL,
    imported_at_utc TEXT NOT NULL,
    imported_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contact_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_uid TEXT,
    participant_id TEXT,
    contacted_at_utc TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    operator_username TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(candidate_uid) REFERENCES candidates(candidate_uid) ON DELETE SET NULL
);
"""

CANDIDATE_COLUMNS = {
    "beijing_based": "TEXT NOT NULL DEFAULT ''",
    "education": "TEXT NOT NULL DEFAULT ''",
    "health_rating": "TEXT NOT NULL DEFAULT ''",
    "work_schedule": "TEXT NOT NULL DEFAULT ''",
    "chronotype": "TEXT NOT NULL DEFAULT ''",
    "activity_mode": "TEXT NOT NULL DEFAULT ''",
    "fixed_position_ratio": "TEXT NOT NULL DEFAULT ''",
    "screen_time_ratio": "TEXT NOT NULL DEFAULT ''",
    "indoor_daylight": "TEXT NOT NULL DEFAULT ''",
    "artificial_light_reliance": "TEXT NOT NULL DEFAULT ''",
    "outdoor_time": "TEXT NOT NULL DEFAULT ''",
    "exposure_mechanism": "TEXT NOT NULL DEFAULT ''",
    "commute_mode": "TEXT NOT NULL DEFAULT ''",
    "commute_duration": "TEXT NOT NULL DEFAULT ''",
    "willingness": "TEXT NOT NULL DEFAULT ''",
    "source_seq": "TEXT NOT NULL DEFAULT ''",
    "in_latest_snapshot": "INTEGER NOT NULL DEFAULT 1",
    "s0_import_uid": "TEXT NOT NULL DEFAULT ''",
    "s0_raw_json": "TEXT NOT NULL DEFAULT '{}'",
}


def connect_identity() -> sqlite3.Connection:
    settings.identity_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.identity_db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def identity_db():
    conn = connect_identity()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_identity_db() -> None:
    conn = connect_identity()
    try:
        conn.executescript(IDENTITY_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
        for name, declaration in CANDIDATE_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {declaration}")
        conn.commit()
    finally:
        conn.close()
