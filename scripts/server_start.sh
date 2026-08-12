#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:-}"
if [[ -z "$ENVIRONMENT" ]]; then
  echo "Select LEHUE runtime environment:"
  echo "  1) TEST  - isolated pilot / engineering data"
  echo "  2) PROD  - formal study data"
  read -r -p "Enter 1 or 2: " choice
  case "$choice" in
    1) ENVIRONMENT="test" ;;
    2) ENVIRONMENT="prod" ;;
    *) echo "Startup cancelled: choose TEST or PROD explicitly." >&2; exit 2 ;;
  esac
fi

case "$ENVIRONMENT" in
  test) ;;
  prod)
    echo "WARNING: PROD writes to the formal study database."
    read -r -p "Type PROD to continue: " confirm
    [[ "$confirm" == "PROD" ]] || { echo "PROD startup cancelled." >&2; exit 2; }
    ;;
  *) echo "Usage: $0 [test|prod]" >&2; exit 2 ;;
esac

export LEHUE_ENV="$ENVIRONMENT"
bash ./scripts/server_doctor.sh "$ENVIRONMENT"
echo "LEHUE ENV: ${LEHUE_ENV^^}"
echo "Data dir : ./server/data/$LEHUE_ENV"
echo "Environment is locked until the containers stop."
if docker compose up -d --no-build --wait --wait-timeout 60; then
  echo '[OK]   deployed containers are healthy'
else
  echo '[FAIL] container replacement did not become healthy; recent API logs follow' >&2
  docker compose logs --tail=100 api >&2 || true
  exit 1
fi
