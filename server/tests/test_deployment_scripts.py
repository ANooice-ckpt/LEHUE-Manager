from __future__ import annotations

import io
import os
import subprocess
import sys
import urllib.error
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


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
