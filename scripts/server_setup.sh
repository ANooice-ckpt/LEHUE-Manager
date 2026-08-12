#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ENVIRONMENT="${1:-test}"
case "$ENVIRONMENT" in test|prod) ;; *) echo "Usage: $0 [test|prod]" >&2; exit 2;; esac
command -v docker >/dev/null || { echo "Docker is not installed." >&2; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose plugin is not installed." >&2; exit 1; }

read -r -p "PI username: " PI_USERNAME
read -r -s -p "PI password (at least 10 characters): " PI_PASSWORD; echo
read -r -s -p "Repeat PI password: " PI_PASSWORD_CONFIRM; echo
[[ "$PI_PASSWORD" == "$PI_PASSWORD_CONFIRM" ]] || { echo "Passwords do not match." >&2; exit 2; }
[[ ${#PI_PASSWORD} -ge 10 ]] || { echo "Password must contain at least 10 characters." >&2; exit 2; }

if [[ ! -f .env ]]; then cp .env.example .env; fi
chmod 600 .env
python_bin=""
for candidate in python3 python; do if command -v "$candidate" >/dev/null; then python_bin="$candidate"; break; fi; done
[[ -n "$python_bin" ]] || { echo "Python is required to generate server keys." >&2; exit 1; }
existing_env() { awk -F= -v name="$1" '$1==name{sub(/^[^=]*=/, ""); print; exit}' .env | tr -d '\r'; }
ADMIN_TOKEN="$(existing_env ADMIN_TOKEN)"
CREDENTIAL_KEY="$(existing_env CREDENTIAL_ENCRYPTION_KEY)"
if [[ -z "$ADMIN_TOKEN" || "$ADMIN_TOKEN" == CHANGE_ME* ]]; then ADMIN_TOKEN="$($python_bin -c 'import secrets; print(secrets.token_urlsafe(48))')"; fi
if [[ -z "$CREDENTIAL_KEY" || "$CREDENTIAL_KEY" == CHANGE_ME* ]]; then CREDENTIAL_KEY="$($python_bin -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"; fi

set_env() {
  local name="$1" value="$2" temporary
  temporary="$(mktemp)"
  awk -v name="$name" -v value="$value" 'BEGIN{done=0} $0 ~ "^" name "=" {if(!done){print name "=" value; done=1}; next} {print} END{if(!done) print name "=" value}' .env > "$temporary"
  mv "$temporary" .env
  chmod 600 .env
}
set_env ADMIN_TOKEN "$ADMIN_TOKEN"
set_env CREDENTIAL_ENCRYPTION_KEY "$CREDENTIAL_KEY"

export LEHUE_ENV="$ENVIRONMENT"
docker compose build api
docker compose run --rm --no-deps api python -c 'from app.core.test_seed import install_test_seed_if_empty; from app.core.db import init_db; from app.core.identity_db import init_identity_db; install_test_seed_if_empty(); init_db(); init_identity_db()'
printf '%s\n' "$PI_PASSWORD" | docker compose run -T --rm --no-deps api python scripts/bootstrap_admin.py "$PI_USERNAME" --role pi --password-stdin
unset PI_PASSWORD PI_PASSWORD_CONFIRM ADMIN_TOKEN CREDENTIAL_KEY
echo "${ENVIRONMENT^^} setup complete. Review DOMAIN/OSS settings in .env, then run ./scripts/server_start.sh $ENVIRONMENT"
