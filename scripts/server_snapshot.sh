#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ENVIRONMENT="${1:-}"
ACTION="${2:-}"
ARGUMENT="${3:-}"
case "$ENVIRONMENT" in test|prod) ;; *) echo "Usage: $0 <test|prod> export [filename.zip] | <test|prod> restore filename.zip" >&2; exit 2;; esac
export LEHUE_ENV="$ENVIRONMENT"
mkdir -p server/data/snapshots "server/data/$ENVIRONMENT/restore_backups"
[[ -z "$ARGUMENT" || "$ARGUMENT" =~ ^[A-Za-z0-9_.-]+\.zip$ ]] || { echo 'Use a ZIP filename without path components.' >&2; exit 2; }

case "$ACTION" in
  export)
    NAME="${ARGUMENT:-LEHUE_${ENVIRONMENT^^}_state_bundle_$(date -u +%Y%m%d_%H%M%SZ).zip}"
    docker compose run --rm --no-deps api python scripts/test_snapshot.py export "/app/data/snapshots/$NAME"
    echo "State Bundle: server/data/snapshots/$NAME"
    ;;
  restore)
    [[ -n "$ARGUMENT" && -f "server/data/snapshots/$ARGUMENT" ]] || { echo "Place the archive in server/data/snapshots and pass its filename." >&2; exit 2; }
    if [[ -n "$(docker compose ps --status running -q api)" ]]; then
      ACTIVE_ENV="$(docker compose exec -T api python -c 'from app.core.config import settings; print(settings.runtime_env)' | tr -d '\r')"
      [[ "$ACTIVE_ENV" == "$ENVIRONMENT" ]] || { echo 'Selected environment differs from the running API; restore cancelled.' >&2; exit 2; }
    fi
    docker compose stop api
    restore_failed() {
      docker compose stop api || true
      echo "Restore did not complete; API remains stopped. Inspect rollback in server/data/$ENVIRONMENT/restore_backups before restarting." >&2
    }
    trap restore_failed ERR
    [[ -z "$(docker compose ps --status running -q api)" ]] || { echo 'API is still running; restore cancelled.' >&2; exit 1; }
    RESULT="$(docker compose run --rm --no-deps api python scripts/test_snapshot.py restore "/app/data/snapshots/$ARGUMENT" --api-stopped --backup-dir "/app/data/$ENVIRONMENT/restore_backups")"
    ROLLBACK="$(printf '%s\n' "$RESULT" | tail -1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["rollback_state_bundle"])')"
    docker compose up -d --no-build --wait api
    trap - ERR
    echo "${ENVIRONMENT^^} State Bundle restored and credentials re-encrypted with this server key; previous state: $ROLLBACK"
    ;;
  *) echo "Usage: $0 <test|prod> export [filename.zip] | <test|prod> restore filename.zip" >&2; exit 2;;
esac
