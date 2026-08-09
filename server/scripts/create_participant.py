from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from datetime import datetime, timezone

from app.core.db import db, init_db
from app.core.security import generate_secret, hash_secret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("participant_id", help="e.g. TEST01 or P001")
    parser.add_argument("--secret", help="optional fixed secret; random if omitted")
    args = parser.parse_args()

    participant_id = args.participant_id.strip()
    secret = args.secret or generate_secret()
    salt, digest = hash_secret(secret)
    init_db()
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM participants WHERE participant_id=?", (participant_id,)
        ).fetchone()
        if exists:
            raise SystemExit(f"Participant already exists: {participant_id}")
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,is_active,created_at_utc) VALUES(?,?,?,?,?)",
            (
                participant_id, salt, digest, 1,
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )

    print("Participant created")
    print(f"participant_id = {participant_id}")
    print(f"password       = {secret}")
    print("Save this password now. Only its hash is stored in the database.")


if __name__ == "__main__":
    main()
