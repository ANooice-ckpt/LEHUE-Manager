from __future__ import annotations

from urllib.parse import urlencode

from app.core.config import settings


TRACCAR_ACCURACY = "highest"
TRACCAR_DISTANCE_METERS = 0
TRACCAR_INTERVAL_SECONDS = 5
TRACCAR_HEARTBEAT_SECONDS = 0
TRACCAR_BUFFER = True
TRACCAR_WAKELOCK = True
TRACCAR_STOP_DETECTION = False
TRACCAR_PREFER_PLATFORM_PROVIDERS = False


def gps_endpoint() -> str:
    domain = settings.domain.strip().rstrip("/")
    if domain in {"", "localhost", "127.0.0.1"}:
        return "http://127.0.0.1:8085/api/v1/gps/traccar"
    return f"https://{domain}/api/v1/gps/traccar"


def tracker_id(participant_id: str, secret: str) -> str:
    return f"{participant_id}.{secret}"


def config_parameters(participant_id: str, secret: str) -> dict[str, str | int]:
    return {
        "url": gps_endpoint(),
        "id": tracker_id(participant_id, secret),
        "accuracy": TRACCAR_ACCURACY,
        "distance": TRACCAR_DISTANCE_METERS,
        "interval": TRACCAR_INTERVAL_SECONDS,
        "heartbeat": TRACCAR_HEARTBEAT_SECONDS,
        "buffer": str(TRACCAR_BUFFER).lower(),
        "wakelock": str(TRACCAR_WAKELOCK).lower(),
        "stop_detection": str(TRACCAR_STOP_DETECTION).lower(),
        "prefer_platform_providers": str(TRACCAR_PREFER_PLATFORM_PROVIDERS).lower(),
    }


def build_config_uri(participant_id: str, secret: str) -> str:
    return "org.traccar.client://config?" + urlencode(config_parameters(participant_id, secret))


def public_config(participant_id: str, secret: str) -> dict[str, object]:
    """Participant-facing config. The URI contains the GPS credential and must not be logged."""
    return {
        "available": True,
        "platform": "android",
        "uri": build_config_uri(participant_id, secret),
        "settings": {
            "accuracy": TRACCAR_ACCURACY,
            "distance_m": TRACCAR_DISTANCE_METERS,
            "interval_s": TRACCAR_INTERVAL_SECONDS,
            "heartbeat_s": TRACCAR_HEARTBEAT_SECONDS,
            "offline_buffering": TRACCAR_BUFFER,
            "wake_lock": TRACCAR_WAKELOCK,
            "stop_detection": TRACCAR_STOP_DETECTION,
            "prefer_platform_providers": TRACCAR_PREFER_PLATFORM_PROVIDERS,
        },
    }
