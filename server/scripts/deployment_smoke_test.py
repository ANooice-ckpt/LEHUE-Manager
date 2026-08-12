from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import sys
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="Destructive ECS TEST deployment smoke test")
    parser.add_argument("--base", required=True)
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("Smoke base must use HTTPS")
    if args.credentials_stdin:
        parts = sys.stdin.buffer.read().split(b"\0")
        if len(parts) < 4:
            raise SystemExit("Expected PI username, password and TEST participant on stdin")
        user, password, participant = (part.decode() for part in parts[:3])
    else:
        user, password = os.environ["SMOKE_PI_USERNAME"], os.environ["SMOKE_PI_PASSWORD"]
        participant = os.environ["SMOKE_PARTICIPANT_ID"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path: str, method: str = "GET", data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, body, method=method, headers=headers or {})
        with opener.open(req, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None

    status, health = request("/health")
    assert status == 200 and health["status"] in {"ok", "degraded"} and health["runtime_environment"] == "test"
    status, login = request("/api/v1/web/login", "POST", {"username": user, "password": password}, {"Content-Type": "application/json"})
    assert status == 200
    write_headers = {"Content-Type": "application/json", "X-CSRF-Token": login["csrf_token"]}
    _, gps = request(f"/api/v1/web/subjects/{participant}/gps-credential", "POST", {}, write_headers)
    _, portal = request(f"/api/v1/web/subjects/{participant}/portal", "POST", {}, write_headers)
    portal_token = portal["path"].removeprefix("/p/")
    status, state = request(f"/api/v1/portal/{portal_token}")
    assert status == 200 and state["participant_id"] == participant
    now = int(time.time())
    auth = base64.b64encode(f"{participant}:{gps['password']}".encode()).decode()
    payload = {"_type": "location", "_id": f"ecs-smoke-{now}", "tst": now, "created_at": now, "lat": 39.999, "lon": 116.326, "acc": 8, "source": "ecs-deployment-smoke"}
    status, _ = request("/api/v1/gps/owntracks", "POST", payload, {"Content-Type": "application/json", "Authorization": "Basic " + auth, "X-Limit-U": participant, "X-Limit-D": "ecs-smoke"})
    assert status == 200
    if health["light_storage_backend"] == "oss":
        lighting = f"Photopic Lux,Melanopic,Is Saturate\n{now % 997},80,No\n".encode()
        digest = hashlib.sha256(lighting).hexdigest()
        from app.core.config import settings
        from app.modules.light.storage import get_light_storage
        storage = get_light_storage()
        object_key = f"raw/lighting/_deployment_smoke/{now}-{digest[:12]}.csv"
        signed = storage.presign_put(object_key, digest, settings.oss_upload_url_seconds)
        assert signed["url"].startswith("https://")
        preflight = urllib.request.Request(
            signed["url"], method="OPTIONS", headers={
                "Origin": base,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "x-oss-meta-sha256",
            },
        )
        try:
            with urllib.request.urlopen(preflight, timeout=30) as response:
                allowed_origin = response.headers.get("Access-Control-Allow-Origin", "")
                assert allowed_origin in {base, "*"}, "OSS CORS does not allow the Portal origin"
            put = urllib.request.Request(signed["url"], lighting, method="PUT", headers=signed["headers"])
            with urllib.request.urlopen(put, timeout=60) as response:
                assert 200 <= response.status < 300
            head = storage.head(object_key)
            assert head.size_bytes == len(lighting) and head.sha256 == digest
        finally:
            storage.delete(object_key)
    print(json.dumps({"ok": True, "https": True, "database": health["database"], "gps": True, "portal": True, "oss_presigned_upload": health["light_storage_backend"] == "oss"}))


if __name__ == "__main__":
    main()
