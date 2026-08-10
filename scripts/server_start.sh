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
echo "LEHUE ENV: ${LEHUE_ENV^^}"
echo "Data dir : ./server/data/$LEHUE_ENV"
echo "Environment is locked until the containers stop."
docker compose up -d --build
