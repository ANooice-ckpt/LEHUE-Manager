from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.core.state_bundle import create_state_bundle, inspect_state_bundle, restore_state_bundle  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export, inspect, or restore a LEHUE State Bundle")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("destination", type=Path)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("--backup-dir", type=Path, default=Path("data/test/restore_backups"))
    args = parser.parse_args()
    if args.command == "export":
        print(create_state_bundle(args.destination))
    elif args.command == "inspect":
        print(json.dumps(inspect_state_bundle(args.source), ensure_ascii=False, indent=2))
    else:
        rollback, manifest = restore_state_bundle(args.source, args.backup_dir)
        print(json.dumps({
            "restored": str(args.source),
            "rollback_state_bundle": str(rollback),
            "source_environment": manifest["runtime_environment"],
            "credentials_reencrypted": True,
        }))


if __name__ == "__main__":
    main()
