from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.core.test_snapshot import create_test_snapshot, inspect_test_snapshot, restore_test_snapshot  # noqa: E402


def set_env_value(path: Path, name: str, value: str) -> None:
    path = path.resolve()
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    replacement = f"{name}={value}"
    output, replaced = [], False
    for line in lines:
        if line.startswith(f"{name}="):
            if not replaced:
                output.append(replacement); replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(replacement)
    temporary = path.with_name(f".{path.name}.snapshot-update")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or restore a portable LEHUE TEST snapshot")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("destination", type=Path)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("--backup-dir", type=Path, default=Path("data/test/restore_backups"))
    restore.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        print(create_test_snapshot(args.destination))
    elif args.command == "inspect":
        print(json.dumps(inspect_test_snapshot(args.source)[0], ensure_ascii=False, indent=2))
    else:
        rollback, key = restore_test_snapshot(args.source, args.backup_dir)
        result = {"restored": str(args.source), "rollback_snapshot": str(rollback)}
        if args.env_file:
            set_env_value(args.env_file, "CREDENTIAL_ENCRYPTION_KEY", key)
            result["env_file_updated"] = str(args.env_file)
        else:
            result["credential_encryption_key"] = key
        print(json.dumps(result))


if __name__ == "__main__":
    main()
