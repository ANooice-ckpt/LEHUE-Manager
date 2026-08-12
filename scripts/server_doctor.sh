#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ENVIRONMENT="${1:-test}"
case "$ENVIRONMENT" in test|prod) ;; *) echo "Usage: $0 [test|prod]" >&2; exit 2;; esac
fail=0
check() { if "$@" >/dev/null 2>&1; then printf '[OK]   %s\n' "$*"; else printf '[FAIL] %s\n' "$*" >&2; fail=1; fi; }
check docker --version
check docker compose version
[[ -f .env ]] || { echo '[FAIL] .env is missing; run scripts/server_setup.sh' >&2; exit 1; }
DOMAIN="$(awk -F= '$1=="DOMAIN"{print $2}' .env | tail -1 | tr -d '\r')"
if [[ -z "$DOMAIN" || "$DOMAIN" == "localhost" || "$DOMAIN" == "127.0.0.1" ]]; then echo '[FAIL] DOMAIN must be the public ECS hostname for HTTPS' >&2; fail=1; else echo "[OK]   HTTPS domain: $DOMAIN"; fi
export LEHUE_ENV="$ENVIRONMENT"
if docker compose config --quiet >/dev/null 2>&1; then echo '[OK]   compose environment'; else echo '[FAIL] compose environment/configuration' >&2; fail=1; fi
if docker compose run --rm --no-deps api python -c 'from app.core.config import settings; print(settings.runtime_env)' >/dev/null 2>&1; then echo '[OK]   application startup configuration'; else echo '[FAIL] application startup configuration' >&2; fail=1; fi
if docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  if docker compose exec -T api python -c 'import sqlite3; from app.core.config import settings; a=sqlite3.connect(settings.db_path).execute("PRAGMA integrity_check").fetchone()[0]; b=sqlite3.connect(settings.identity_db_path).execute("PRAGMA integrity_check").fetchone()[0]; raise SystemExit(0 if a==b=="ok" else 1)' >/dev/null 2>&1; then echo '[OK]   SQLite databases'; else echo '[FAIL] SQLite databases' >&2; fail=1; fi
else
  echo '[INFO] API is not running; run server_smoke_test.sh after startup for HTTPS/Portal/RAM Role/OSS checks.'
fi
exit "$fail"
