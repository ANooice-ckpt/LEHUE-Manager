from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,
    secret_salt TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    secret_ciphertext TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL,
    event_uid TEXT NOT NULL,
    owntracks_event_id TEXT,
    message_type TEXT NOT NULL,
    recorded_at_utc TEXT,
    created_at_utc TEXT,
    received_at_utc TEXT NOT NULL,
    headers_user TEXT,
    headers_device TEXT,
    raw_json TEXT NOT NULL,
    archive_ok INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(participant_id) REFERENCES participants(participant_id),
    UNIQUE(participant_id, event_uid)
);

CREATE INDEX IF NOT EXISTS idx_raw_participant_received
ON raw_events(participant_id, received_at_utc);

CREATE TABLE IF NOT EXISTS gps_locations (
    raw_event_id INTEGER PRIMARY KEY,
    participant_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    created_at_utc TEXT,
    received_at_utc TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy_m REAL,
    altitude_m REAL,
    vertical_accuracy_m REAL,
    velocity_kmh REAL,
    course_deg REAL,
    battery_pct INTEGER,
    battery_status INTEGER,
    connection TEXT,
    monitoring_mode INTEGER,
    trigger TEXT,
    source TEXT,
    owntracks_tid TEXT,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(id) ON DELETE CASCADE,
    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
);

CREATE INDEX IF NOT EXISTS idx_gps_participant_recorded
ON gps_locations(participant_id, recorded_at_utc);

CREATE TABLE IF NOT EXISTS study_subjects (
    participant_id TEXT PRIMARY KEY,
    candidate_uid TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    batch_id TEXT NOT NULL DEFAULT '',
    expected_start TEXT NOT NULL DEFAULT '',
    expected_end TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    final_end TEXT NOT NULL DEFAULT '',
    pack_id TEXT NOT NULL DEFAULT '',
    assigned_ra TEXT NOT NULL DEFAULT '',
    s1_status TEXT NOT NULL DEFAULT '',
    latest_data_status TEXT NOT NULL DEFAULT '',
    valid_days INTEGER NOT NULL DEFAULT 0,
    completion_type TEXT NOT NULL DEFAULT '',
    compensation TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    awaiting_final_morning INTEGER NOT NULL DEFAULT 0,
    close_notes TEXT NOT NULL DEFAULT '',
    portal_token_id TEXT NOT NULL DEFAULT '',
    portal_token_salt TEXT NOT NULL DEFAULT '',
    portal_token_hash TEXT NOT NULL DEFAULT '',
    portal_token_ciphertext TEXT NOT NULL DEFAULT '',
    portal_token_created_at_utc TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questionnaire_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL,
    date_local TEXT NOT NULL,
    study_day INTEGER NOT NULL DEFAULT 0,
    form_key TEXT NOT NULL,
    form_version TEXT NOT NULL DEFAULT 'test_v1',
    answers_json TEXT NOT NULL,
    submitted_at_utc TEXT NOT NULL,
    FOREIGN KEY(participant_id) REFERENCES study_subjects(participant_id) ON DELETE CASCADE,
    UNIQUE(participant_id, date_local, form_key)
);

CREATE INDEX IF NOT EXISTS idx_questionnaire_participant_date
ON questionnaire_responses(participant_id, date_local);

CREATE TABLE IF NOT EXISTS lighting_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_uid TEXT NOT NULL UNIQUE,
    participant_id TEXT NOT NULL,
    date_local TEXT NOT NULL,
    calendar_date_local TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    storage_backend TEXT NOT NULL DEFAULT 'local',
    object_key TEXT NOT NULL DEFAULT '',
    file_size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at_utc TEXT NOT NULL,
    uploaded_by TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL,
    records_expected INTEGER NOT NULL DEFAULT 7200,
    records_total INTEGER NOT NULL DEFAULT 0,
    records_valid INTEGER NOT NULL DEFAULT 0,
    records_saturated INTEGER NOT NULL DEFAULT 0,
    valid_pct REAL NOT NULL DEFAULT 0,
    quality TEXT NOT NULL,
    photopic_mean REAL,
    photopic_median REAL,
    photopic_max REAL,
    melanopic_mean REAL,
    melanopic_median REAL,
    melanopic_max REAL,
    parse_error TEXT NOT NULL DEFAULT '',
    upload_status TEXT NOT NULL DEFAULT 'qc',
    FOREIGN KEY(participant_id) REFERENCES study_subjects(participant_id) ON DELETE CASCADE,
    UNIQUE(participant_id, date_local, sha256)
);

CREATE INDEX IF NOT EXISTS idx_lighting_participant_date
ON lighting_files(participant_id, date_local, uploaded_at_utc);

CREATE TABLE IF NOT EXISTS device_packs (
    pack_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'available',
    current_participant_id TEXT NOT NULL DEFAULT '',
    light_serial TEXT NOT NULL DEFAULT '',
    ax3_serial TEXT NOT NULL DEFAULT '',
    issued_date TEXT NOT NULL DEFAULT '',
    expected_return_date TEXT NOT NULL DEFAULT '',
    returned_date TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_uid TEXT NOT NULL UNIQUE,
    participant_id TEXT NOT NULL DEFAULT '',
    date_local TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    incident_type TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open',
    assigned_ra TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, participant_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at_utc TEXT NOT NULL,
    operator_username TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""

PORTAL_COLUMNS = {
    "portal_token_id": "TEXT NOT NULL DEFAULT ''",
    "portal_token_salt": "TEXT NOT NULL DEFAULT ''",
    "portal_token_hash": "TEXT NOT NULL DEFAULT ''",
    "portal_token_ciphertext": "TEXT NOT NULL DEFAULT ''",
    "portal_token_created_at_utc": "TEXT NOT NULL DEFAULT ''",
}

PARTICIPANT_COLUMNS = {
    "secret_ciphertext": "TEXT NOT NULL DEFAULT ''",
}

SUBJECT_COLUMNS = {
    **PORTAL_COLUMNS,
    "awaiting_final_morning": "INTEGER NOT NULL DEFAULT 0",
    "close_notes": "TEXT NOT NULL DEFAULT ''",
}

LIGHTING_COLUMNS = {
    "storage_backend": "TEXT NOT NULL DEFAULT 'local'",
    "object_key": "TEXT NOT NULL DEFAULT ''",
    "upload_status": "TEXT NOT NULL DEFAULT 'qc'",
}

QUESTIONNAIRE_COLUMNS = {
    "calendar_date_local": "TEXT NOT NULL DEFAULT ''",
}


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_subject_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(study_subjects)")}
    for name, declaration in SUBJECT_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE study_subjects ADD COLUMN {name} {declaration}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_portal_token_id "
        "ON study_subjects(portal_token_id) WHERE portal_token_id <> ''"
    )


def _ensure_participant_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(participants)")}
    for name, declaration in PARTICIPANT_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE participants ADD COLUMN {name} {declaration}")


def _ensure_lighting_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(lighting_files)")}
    for name, declaration in LIGHTING_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE lighting_files ADD COLUMN {name} {declaration}")
    conn.execute("UPDATE lighting_files SET object_key=stored_path WHERE object_key='' AND stored_path<>''")


def _ensure_questionnaire_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(questionnaire_responses)")}
    for name, declaration in QUESTIONNAIRE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE questionnaire_responses ADD COLUMN {name} {declaration}")
    conn.execute("UPDATE questionnaire_responses SET calendar_date_local=date_local WHERE calendar_date_local=''")


def _normalize_incident_statuses(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE incidents SET status='open' WHERE status='handling'")
    conn.execute("UPDATE incidents SET status='closed' WHERE status='resolved'")


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_archive_dir.mkdir(parents=True, exist_ok=True)
    if settings.light_storage_backend == "local":
        settings.raw_light_dir.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _ensure_participant_columns(conn)
        _ensure_subject_columns(conn)
        _ensure_lighting_columns(conn)
        _ensure_questionnaire_columns(conn)
        _normalize_incident_statuses(conn)
        conn.commit()
    finally:
        conn.close()
