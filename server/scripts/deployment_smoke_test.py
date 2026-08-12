from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, TypeVar


# Make this script work from any cwd, including `python /app/scripts/...`,
# without a caller-provided PYTHONPATH.
SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


T = TypeVar("T")


class SmokeFailure(RuntimeError):
    pass


def run_stage(name: str, operation: Callable[[], T]) -> T:
    try:
        result = operation()
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}", file=sys.stderr, flush=True)
        raise SmokeFailure(name) from None
    print(f"[OK]   {name}", flush=True)
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def endpoint_host(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return parsed.netloc or "<missing>"


def oss_http_error(exc: urllib.error.HTTPError, *, stage: str, endpoint: str) -> str:
    body = exc.read(64 * 1024)
    code = message = request_id = ""
    try:
        root = ET.fromstring(body)
        code = root.findtext("Code", "")
        message = root.findtext("Message", "")
        request_id = root.findtext("RequestId", "")
    except ET.ParseError:
        pass
    request_id = request_id or exc.headers.get("x-oss-request-id", "")
    return (
        f"stage={stage} endpoint={endpoint} HTTP={exc.code} "
        f"Code={code or '<missing>'} Message={message or '<missing>'} "
        f"RequestId={request_id or '<missing>'}"
    )


def oss_sdk_error(exc: Exception, *, stage: str, endpoint: str) -> str:
    return (
        f"stage={stage} endpoint={endpoint} HTTP={getattr(exc, 'status', '<missing>')} "
        f"Code={getattr(exc, 'code', '<missing>')} "
        f"Message={getattr(exc, 'message', '<missing>')} "
        f"RequestId={getattr(exc, 'request_id', '<missing>')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Destructive ECS TEST deployment smoke test")
    parser.add_argument("--base", required=True)
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    if not base.startswith("https://"):
        print("[FAIL] input: smoke base must use HTTPS", file=sys.stderr)
        return 1
    if args.credentials_stdin:
        parts = sys.stdin.buffer.read().split(b"\0")
        if len(parts) < 4:
            print("[FAIL] input: expected PI username, password and TEST participant on stdin", file=sys.stderr)
            return 1
        user, password, participant = (part.decode() for part in parts[:3])
    else:
        try:
            user, password = os.environ["SMOKE_PI_USERNAME"], os.environ["SMOKE_PI_PASSWORD"]
            participant = os.environ["SMOKE_PARTICIPANT_ID"]
        except KeyError as exc:
            print(f"[FAIL] input: missing {exc.args[0]}", file=sys.stderr)
            return 1
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path: str, method: str = "GET", data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, body, method=method, headers=headers or {})
        try:
            with opener.open(req, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read(64 * 1024)
            detail = ""
            try:
                detail = json.loads(raw).get("detail", "")
            except (json.JSONDecodeError, AttributeError):
                pass
            raise RuntimeError(f"HTTP {exc.code}{': ' + str(detail) if detail else ''}") from None

    try:
        def check_health():
            status, value = request("/health")
            require(status == 200, f"HTTP {status}")
            require(value["status"] in {"ok", "degraded"}, f"health={value['status']}")
            require(value["runtime_environment"] == "test", "server is not TEST")
            return value

        health = run_stage("HTTPS health", check_health)

        def check_login():
            status, value = request("/api/v1/web/login", "POST", {"username": user, "password": password}, {"Content-Type": "application/json"})
            require(status == 200, f"HTTP {status}")
            return value

        login = run_stage("PI login", check_login)
        write_headers = {"Content-Type": "application/json", "X-CSRF-Token": login["csrf_token"]}
        def generate_gps():
            status, value = request(f"/api/v1/web/subjects/{participant}/gps-credential", "POST", {}, write_headers)
            require(status == 200 and bool(value.get("password")), "response has no GPS password")
            return value

        def generate_portal():
            status, value = request(f"/api/v1/web/subjects/{participant}/portal", "POST", {}, write_headers)
            require(status == 200 and str(value.get("path", "")).startswith("/p/"), "response has no Portal path")
            return value

        gps = run_stage("GPS credential generation", generate_gps)
        portal = run_stage("Portal entry generation", generate_portal)
        portal_token = portal["path"].removeprefix("/p/")
        def check_portal():
            status, value = request(f"/api/v1/portal/{portal_token}")
            require(status == 200 and value["participant_id"] == participant, "Portal participant mismatch")
            return value

        run_stage("Portal access", check_portal)

        now = int(time.time())
        auth = base64.b64encode(f"{participant}:{gps['password']}".encode()).decode()
        payload = {"_type": "location", "_id": f"ecs-smoke-{now}", "tst": now, "created_at": now, "lat": 39.999, "lon": 116.326, "acc": 8, "source": "ecs-deployment-smoke"}
        def gps_write():
            status, value = request("/api/v1/gps/owntracks", "POST", payload, {"Content-Type": "application/json", "Authorization": "Basic " + auth, "X-Limit-U": participant, "X-Limit-D": "ecs-smoke"})
            require(status == 200, f"HTTP {status}")
            return value

        run_stage("GPS OwnTracks write", gps_write)

        if health["light_storage_backend"] == "oss":
            run_oss_smoke(base, now)
        else:
            print("[OK]   OSS checks skipped (Lighting backend is local)", flush=True)
    except SmokeFailure:
        return 1
    except Exception as exc:
        print(f"[FAIL] validation: {exc}", file=sys.stderr, flush=True)
        return 1

    print(json.dumps({"ok": True, "https": True, "database": health["database"], "gps": True, "portal": True, "oss_presigned_upload": health["light_storage_backend"] == "oss"}))
    return 0


def run_oss_smoke(base: str, now: int) -> None:
    from app.core import config
    from app.core.oss_client import oss_endpoint
    from app.modules.light.storage import get_light_storage

    settings = config.settings
    storage = get_light_storage()
    internal_endpoint = endpoint_host(oss_endpoint())
    public_key = f"raw/lighting/_deployment_smoke/public-{now}.csv"
    sdk_key = f"raw/lighting/_deployment_smoke/sdk-{now}.csv"
    lighting = f"Photopic Lux,Melanopic,Is Saturate\n{now % 997},80,No\n".encode()
    digest = hashlib.sha256(lighting).hexdigest()
    print(
        f"[INFO] OSS credential={settings.oss_credential_mode} region={settings.oss_region} "
        f"internal_endpoint={internal_endpoint} signing=v4",
        flush=True,
    )

    def sdk_round_trip() -> None:
        failure = ""
        try:
            storage.bucket.put_object(sdk_key, lighting, headers={"x-oss-meta-sha256": digest})
            head = storage.head(sdk_key)
            require(head.size_bytes == len(lighting) and head.sha256 == digest, "SDK object metadata mismatch")
        except Exception as exc:
            failure = oss_sdk_error(exc, stage="internal-sdk-put/head", endpoint=internal_endpoint)
        try:
            storage.delete(sdk_key)
        except Exception as exc:
            cleanup_failure = oss_sdk_error(exc, stage="internal-sdk-delete", endpoint=internal_endpoint)
            failure = f"{failure}; {cleanup_failure}" if failure else cleanup_failure
        if failure:
            raise RuntimeError(failure)

    run_stage("OSS ECS credential + internal SDK PutObject", sdk_round_trip)
    def make_presign():
        value = storage.presign_put(public_key, digest, settings.oss_upload_url_seconds)
        require(value["url"].startswith("https://"), "presigned URL is not HTTPS")
        return value

    signed = run_stage("OSS V4 presign", make_presign)
    public_endpoint = endpoint_host(signed["url"])
    header_names = ",".join(sorted(name.lower() for name in signed["headers"]))
    print(f"[INFO] OSS public_endpoint={public_endpoint} signed_headers={header_names}", flush=True)

    def cors_preflight() -> None:
        preflight = urllib.request.Request(
            signed["url"], method="OPTIONS", headers={
                "Origin": base,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": header_names,
            },
        )
        try:
            with urllib.request.urlopen(preflight, timeout=30) as response:
                allowed_origin = response.headers.get("Access-Control-Allow-Origin", "")
                require(allowed_origin in {base, "*"}, "Portal origin is not allowed")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(oss_http_error(exc, stage="cors-options", endpoint=public_endpoint)) from None

    run_stage("OSS public CORS OPTIONS", cors_preflight)

    def presigned_put() -> None:
        put = urllib.request.Request(signed["url"], lighting, method="PUT", headers=signed["headers"])
        try:
            with urllib.request.urlopen(put, timeout=60) as response:
                require(200 <= response.status < 300, f"HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            detail = oss_http_error(exc, stage="public-presigned-put", endpoint=public_endpoint)
            raise RuntimeError(detail + "; internal SDK PutObject already passed, so RAM PutObject permission is available; inspect public endpoint/region and the reported V4/header error") from None

    try:
        run_stage("OSS public presigned PUT", presigned_put)
        def verify_head():
            head = storage.head(public_key)
            require(head.size_bytes == len(lighting) and head.sha256 == digest, "presigned object metadata mismatch")

        run_stage("OSS uploaded object HEAD", verify_head)
    finally:
        try:
            storage.delete(public_key)
            print("[OK]   OSS smoke object cleanup", flush=True)
        except Exception as exc:
            print(f"[FAIL] OSS smoke object cleanup: {oss_sdk_error(exc, stage='cleanup', endpoint=internal_endpoint)}", file=sys.stderr)
            raise SmokeFailure("OSS smoke object cleanup") from None


if __name__ == "__main__":
    raise SystemExit(main())
