from __future__ import annotations

from app.core.state_bundle import create_temporary_state_bundle


def create_system_backup(*, include_gps_raw: bool = True) -> tuple[str, str]:
    """Compatibility wrapper; every system backup is now a complete State Bundle."""
    return create_temporary_state_bundle()
