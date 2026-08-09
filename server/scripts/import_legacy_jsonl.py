"""Import the original owntracks_locations.jsonl produced by owntracks_test.py.

This is only a migration helper for your current engineering test data.
It intentionally does not reconstruct Basic Auth because the legacy receiver had none.
"""
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.db import init_db
from app.modules.gps.service import ingest, participant_exists


def main():
    p = argparse.ArgumentParser()
    p.add_argument("jsonl")
    p.add_argument("--participant", default="TEST01")
    p.add_argument("--legacy-timezone", default="Asia/Shanghai", help="timezone used by legacy datetime.now() timestamps")
    args = p.parse_args()

    init_db()
    if not participant_exists(args.participant):
        raise SystemExit(
            f"Create {args.participant} first with scripts/create_participant.py"
        )
    stored = duplicate = failed = 0
    for line_no, line in enumerate(Path(args.jsonl).open(encoding="utf-8"), 1):
        try:
            record = json.loads(line)
            payload = record["payload"]
            received_override = None
            legacy_received = record.get("server_received_at")
            if legacy_received:
                received_override = datetime.fromisoformat(legacy_received)
                if received_override.tzinfo is None:
                    received_override = received_override.replace(tzinfo=ZoneInfo(args.legacy_timezone))
            result = ingest(
                args.participant,
                payload,
                record.get("headers_user"),
                record.get("headers_device"),
                received_override=received_override,
            )
            if result["duplicate"]:
                duplicate += 1
            else:
                stored += 1
        except Exception as exc:
            failed += 1
            print(f"line {line_no}: {exc}")
    print({"stored": stored, "duplicate": duplicate, "failed": failed})


if __name__ == "__main__":
    main()
