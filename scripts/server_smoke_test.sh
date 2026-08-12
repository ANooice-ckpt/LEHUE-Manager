#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ "${LEHUE_ENV:-test}" == test ]] || { echo "Smoke test rotates credentials and may only run in TEST." >&2; exit 2; }
read -r -p "PI username: " PI_USERNAME
read -r -s -p "PI password: " PI_PASSWORD; echo
read -r -p "Existing TEST participant ID to exercise: " PARTICIPANT_ID
DOMAIN="$(awk -F= '$1=="DOMAIN"{print $2}' .env | tail -1 | tr -d '\r')"
export LEHUE_ENV=test
printf '%s\0%s\0%s\0' "$PI_USERNAME" "$PI_PASSWORD" "$PARTICIPANT_ID" | docker compose exec -T api python scripts/deployment_smoke_test.py --credentials-stdin --base "https://$DOMAIN"
unset PI_PASSWORD
