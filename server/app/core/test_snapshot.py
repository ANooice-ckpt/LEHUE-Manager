from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.state_bundle import create_state_bundle, inspect_state_bundle, restore_state_bundle


def _require_test() -> None:
    if settings.runtime_env != "test":
        raise RuntimeError("TEST snapshot restore is forbidden unless LEHUE_ENV=test")


def create_test_snapshot(destination: Path) -> Path:
    _require_test()
    return create_state_bundle(destination)


def inspect_test_snapshot(source: Path) -> tuple[dict, None]:
    _require_test()
    return inspect_state_bundle(source), None


def restore_test_snapshot(source: Path, backup_dir: Path) -> tuple[Path, dict]:
    _require_test()
    return restore_state_bundle(source, backup_dir)
