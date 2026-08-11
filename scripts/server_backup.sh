#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose exec -T api python scripts/backup_to_oss.py
