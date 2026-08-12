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
if [[ "$fail" -ne 0 ]]; then exit "$fail"; fi

echo '[INFO] Building the candidate API image from the current checkout...'
if docker compose build api; then
  CANDIDATE_IMAGE_ID="$(docker image inspect lehue-manager-api:local --format '{{.Id}}' 2>/dev/null || true)"
  [[ -n "$CANDIDATE_IMAGE_ID" ]] && echo "[OK]   candidate image ${CANDIDATE_IMAGE_ID#sha256:}" || { echo '[FAIL] candidate image was not created' >&2; fail=1; }
else
  echo '[FAIL] candidate image build' >&2
  fail=1
fi

if [[ "$fail" -eq 0 ]]; then
  config_output="$(docker compose run --rm --no-deps --entrypoint python api -c 'from cryptography.fernet import Fernet; from app.core.config import settings; Fernet(settings.credential_encryption_key.encode("ascii")); from app.main import app; print(settings.runtime_env)' 2>&1)" || {
    echo '[FAIL] candidate image startup/configuration' >&2
    printf '%s\n' "$config_output" >&2
    fail=1
  }
  [[ "$fail" -ne 0 ]] || echo '[OK]   candidate image startup and Fernet key'
fi
if [[ "$fail" -eq 0 ]]; then
  candidate_name="lehue-api-candidate-check-$$"
  docker rm -f "$candidate_name" >/dev/null 2>&1 || true
  candidate_output="$(docker compose run -d --no-deps --name "$candidate_name" -e DATA_ROOT=/tmp/lehue-candidate api 2>&1)" || {
    echo '[FAIL] candidate API process could not be created' >&2
    printf '%s\n' "$candidate_output" >&2
    fail=1
  }
  if [[ "$fail" -eq 0 ]]; then
    candidate_ready=0
    for _ in {1..15}; do
      if docker exec "$candidate_name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" >/dev/null 2>&1; then
        candidate_ready=1
        break
      fi
      sleep 2
    done
    if [[ "$candidate_ready" -eq 1 ]]; then
      echo '[OK]   candidate API process health'
    else
      echo '[FAIL] candidate API process did not become healthy' >&2
      docker logs --tail=100 "$candidate_name" >&2 || true
      fail=1
    fi
  fi
  docker rm -f "$candidate_name" >/dev/null 2>&1 || true
fi
if docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  if docker compose exec -T api python -c 'import sqlite3; from app.core.config import settings; a=sqlite3.connect(settings.db_path).execute("PRAGMA integrity_check").fetchone()[0]; b=sqlite3.connect(settings.identity_db_path).execute("PRAGMA integrity_check").fetchone()[0]; raise SystemExit(0 if a==b=="ok" else 1)' >/dev/null 2>&1; then echo '[OK]   SQLite databases'; else echo '[FAIL] SQLite databases' >&2; fail=1; fi
else
  echo '[INFO] API is not running; run server_smoke_test.sh after startup for HTTPS/Portal/RAM Role/OSS checks.'
fi
exit "$fail"
