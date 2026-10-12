from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import urllib.error
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = REPO_ROOT / "server" / "scripts" / "deployment_smoke_test.py"


def _load_smoke():
    spec = spec_from_file_location("deployment_smoke_test", SMOKE_PATH)
    module = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_smoke_script_is_self_contained_without_pythonpath(tmp_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(SMOKE_PATH), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_oss_http_failure_is_structured_and_does_not_leak_url():
    smoke = _load_smoke()
    body = b"<Error><Code>SignatureDoesNotMatch</Code><Message>bad canonical request</Message><RequestId>req-123</RequestId></Error>"
    error = urllib.error.HTTPError(
        "https://bucket.example/object?x-oss-signature=SECRET",
        403,
        "Forbidden",
        {},
        io.BytesIO(body),
    )
    message = smoke.oss_http_error(error, stage="public-presigned-put", endpoint="bucket.example")
    assert "HTTP=403" in message
    assert "Code=SignatureDoesNotMatch" in message
    assert "RequestId=req-123" in message
    assert "public-presigned-put" in message
    assert "SECRET" not in message
    assert "x-oss-signature" not in message


def test_doctor_builds_and_validates_candidate_before_start_replacement():
    doctor = (REPO_ROOT / "scripts" / "server_doctor.sh").read_text(encoding="utf-8")
    start = (REPO_ROOT / "scripts" / "server_start.sh").read_text(encoding="utf-8")
    assert doctor.index("docker compose build api") < doctor.index("docker compose run --rm --no-deps")
    assert "Fernet(settings.credential_encryption_key" in doctor
    assert "candidate API process health" in doctor
    assert "docker compose up -d --no-build --wait" in start
    assert "recent API logs follow" in start


def test_setup_checks_upgrade_and_admin_state_before_pi_prompt():
    setup = (REPO_ROOT / "scripts" / "server_setup.sh").read_text(encoding="utf-8")
    assert setup.index('DEPLOYMENT_STATE="upgrade"') < setup.index('read -r -p "PI username: "')
    assert setup.index('ADMIN_COUNT="$(') < setup.index('read -r -p "PI username: "')
    assert "Existing CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key" in setup


@pytest.mark.parametrize("scenario", ["success", "restore-fails", "wrong-environment", "still-running"])
def test_restore_script_stops_writers_and_never_restarts_after_failed_restore(tmp_path, scenario):
    bash = shutil.which("bash")
    if not bash and (git := shutil.which("git")):
        bundled = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if bundled.exists():
            bash = str(bundled)
    if not bash:
        pytest.skip("Bash is required for deployment script execution tests")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(REPO_ROOT / "scripts" / "server_snapshot.sh", scripts / "server_snapshot.sh")
    snapshots = tmp_path / "server" / "data" / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / "test.zip").write_bytes(b"mock archive; real validation is covered by state-bundle tests")
    wrapper = tmp_path / "run-test.sh"
    wrapper.write_text('''#!/usr/bin/env bash
set -euo pipefail
docker() {
  printf '%s\\n' "$*" >> "$MOCK_LOG"
  if [[ "$*" == *"ps --status running"* ]]; then
    if [[ ! -f "$MOCK_STOPPED" || "$SCENARIO" == still-running ]]; then echo mock-api; fi
  elif [[ "$*" == *"exec -T api"* ]]; then
    if [[ "$SCENARIO" == wrong-environment ]]; then echo prod; else echo test; fi
  elif [[ "$*" == *"stop api"* ]]; then
    touch "$MOCK_STOPPED"
  elif [[ "$*" == *"run --rm"* ]]; then
    [[ -f "$MOCK_STOPPED" ]] || return 90
    [[ "$SCENARIO" != restore-fails ]] || return 23
    echo '{"rollback_state_bundle":"/mock/rollback.zip"}'
  fi
}
python3() { "$MOCK_PYTHON" "$@"; }
export -f docker python3
bash "$MOCK_SCRIPT" test restore test.zip
''', encoding="utf-8")
    env = {**os.environ, "SCENARIO": scenario, "MOCK_LOG": (tmp_path / "calls.txt").as_posix(),
           "MOCK_STOPPED": (tmp_path / "stopped").as_posix(), "MOCK_SCRIPT": (scripts / "server_snapshot.sh").as_posix(),
           "MOCK_PYTHON": Path(sys.executable).as_posix()}
    result = subprocess.run([bash, wrapper.as_posix()], env=env, capture_output=True, text=True, timeout=20)
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    runs = [x for x in calls if "run --rm" in x]
    starts = [x for x in calls if "up -d" in x]
    if scenario == "success":
        assert result.returncode == 0, result.stderr
        assert len(runs) == len(starts) == 1
        assert calls.index("compose stop api") < calls.index(runs[0]) < calls.index(starts[0])
        assert "--api-stopped" in runs[0] and "/data/test/restore_backups" in runs[0]
    else:
        assert result.returncode != 0
        assert not starts
        if scenario in {"wrong-environment", "still-running"}:
            assert not runs
        if scenario == "wrong-environment":
            assert "compose stop api" not in calls
