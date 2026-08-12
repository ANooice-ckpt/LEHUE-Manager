#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ACTION="${1:-}"
ARGUMENT="${2:-}"
export LEHUE_ENV=test
mkdir -p server/data/snapshots server/data/test/restore_backups

case "$ACTION" in
  export)
    NAME="${ARGUMENT:-LEHUE_TEST_state_bundle_$(date -u +%Y%m%d_%H%M%SZ).zip}"
    docker compose run --rm --no-deps api python scripts/test_snapshot.py export "/app/data/snapshots/$NAME"
    echo "State Bundle: server/data/snapshots/$NAME"
    ;;
  restore)
    [[ -n "$ARGUMENT" && -f "server/data/snapshots/$ARGUMENT" ]] || { echo "Place the archive in server/data/snapshots and pass its filename." >&2; exit 2; }
    docker compose stop api
    restore_failed() { echo 'Restore did not complete; API remains stopped. Use the rollback snapshot in server/data/test/restore_backups.' >&2; }
    trap restore_failed ERR
    RESULT="$(docker compose run --rm --no-deps api python scripts/test_snapshot.py restore "/app/data/snapshots/$ARGUMENT" --backup-dir /app/data/test/restore_backups)"
    ROLLBACK="$(printf '%s\n' "$RESULT" | tail -1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["rollback_state_bundle"])')"
    docker compose up -d api
    trap - ERR
    echo "TEST State Bundle restored and credentials re-encrypted with this server key; previous state: $ROLLBACK"
    ;;
  *) echo "Usage: $0 export [filename.zip] | restore filename.zip" >&2; exit 2;;
esac
