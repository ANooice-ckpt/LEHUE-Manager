from __future__ import annotations

from urllib.parse import urlencode

from app.core.config import settings


# Traccar SDK 1.0.8 treats HIGHEST as an unconstrained mode and forces the
# effective interval to 0. HIGH preserves the requested Android 5 s interval.
# On iOS, CoreLocation does not expose this interval as a strict scheduling
# control; distance=0 keeps continuous updates and the OS controls cadence.
TRACCAR_ACCURACY = "high"
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


def config_parameters(participant_id: str, secret: str, platform: str = "android") -> dict[str, str | int]:
    if platform not in {"android", "ios"}:
        raise ValueError("unsupported Traccar platform")
    parameters: dict[str, str | int] = {
        "url": gps_endpoint(),
        "id": tracker_id(participant_id, secret),
        "accuracy": TRACCAR_ACCURACY,
        "distance": TRACCAR_DISTANCE_METERS,
        "interval": TRACCAR_INTERVAL_SECONDS,
        "heartbeat": TRACCAR_HEARTBEAT_SECONDS,
        "buffer": str(TRACCAR_BUFFER).lower(),
        "stop_detection": str(TRACCAR_STOP_DETECTION).lower(),
    }
    if platform == "android":
        parameters["wakelock"] = str(TRACCAR_WAKELOCK).lower()
        parameters["prefer_platform_providers"] = str(TRACCAR_PREFER_PLATFORM_PROVIDERS).lower()
    return parameters


def build_config_uri(participant_id: str, secret: str, platform: str = "android") -> str:
    return "org.traccar.client://config?" + urlencode(config_parameters(participant_id, secret, platform))


def public_config(participant_id: str, secret: str) -> dict[str, object]:
    """Participant-facing config. Returned URIs contain the GPS credential and must not be logged."""
    android_uri = build_config_uri(participant_id, secret, "android")
    ios_uri = build_config_uri(participant_id, secret, "ios")
    android_settings = {
        "accuracy": TRACCAR_ACCURACY,
        "distance_m": TRACCAR_DISTANCE_METERS,
        "interval_s": TRACCAR_INTERVAL_SECONDS,
        "interval_mode": "requested",
        "heartbeat_s": TRACCAR_HEARTBEAT_SECONDS,
        "offline_buffering": TRACCAR_BUFFER,
        "wake_lock": TRACCAR_WAKELOCK,
        "stop_detection": TRACCAR_STOP_DETECTION,
        "prefer_platform_providers": TRACCAR_PREFER_PLATFORM_PROVIDERS,
    }
    ios_settings = {
        "accuracy": TRACCAR_ACCURACY,
        "distance_m": TRACCAR_DISTANCE_METERS,
        "interval_s": TRACCAR_INTERVAL_SECONDS,
        "interval_mode": "os_managed",
        "heartbeat_s": TRACCAR_HEARTBEAT_SECONDS,
        "offline_buffering": TRACCAR_BUFFER,
        "stop_detection": TRACCAR_STOP_DETECTION,
    }
    return {
        "available": True,
        # Backward-compatible aliases retained for existing Android callers.
        "platform": "android",
        "uri": android_uri,
        "settings": android_settings,
        "platforms": {
            "android": {"uri": android_uri, "settings": android_settings},
            "ios": {"uri": ios_uri, "settings": ios_settings},
        },
    }
