from __future__ import annotations

import json

from app.core.config import settings


def gps_endpoint() -> str:
    domain = settings.domain.strip().rstrip("/")
    if domain in {"", "localhost", "127.0.0.1"}:
        return "http://127.0.0.1:8085/api/v1/gps/owntracks"
    return f"https://{domain}/api/v1/gps/owntracks"


def build_config(participant_id: str, password: str, platform: str) -> dict:
    platform = platform.lower()
    if platform not in {"ios", "android"}:
        raise ValueError("platform must be ios or android")
    config = {
        "_type": "configuration",
        "mode": 3,
        "auth": True,
        "url": gps_endpoint(),
        "username": participant_id,
        "password": password,
        "deviceId": participant_id,
        "tid": participant_id[-2:],
        "monitoring": 2,
        "locatorInterval": 10,
        "locatorDisplacement": 100,
    }
    if platform == "android":
        config["moveModeLocatorInterval"] = 10
        config.update({
            "ignoreInaccurateLocations": 0,
            "ignoreStaleLocations": 0,
            "extendedData": True,
            "autostartOnBoot": True,
            "cmd": False,
            "remoteConfiguration": False,
        })
    else:
        config.update({
            "ignoreInaccurateLocations": 0,
            "ignoreStaleLocations": 0,
            "extendedData": True,
            "adapt": 0,
            "downgrade": 0,
            "cmd": False,
            "remoteConfiguration": False,
        })
    return config


def config_bytes(participant_id: str, password: str, platform: str) -> bytes:
    return json.dumps(
        build_config(participant_id, password, platform),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def config_filename(participant_id: str, platform: str) -> str:
    label = "iOS" if platform.lower() == "ios" else "Android"
    return f"LEHUE_{participant_id}_{label}.otrc"
