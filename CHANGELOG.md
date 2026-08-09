# Changelog

## 0.2.2 - 2026-08-10

- Fixed missing `pytest` dependency in Windows setup.
- Split runtime and development dependencies.
- Disabled pip package caching during setup to reduce system-drive usage.
- Added strict native-command exit-code checks and post-install import verification.
- Added startup dependency preflight with actionable error messages.
- Added `windows_doctor.ps1` environment diagnostics.
- Moved application version out of `.env` into code so old local deployment configuration cannot report a stale version.

## 0.2.1 - 2026-08-10

- Rebranded project/runtime identifiers from LightTrace to LEHUE.
- Added repository-root `.env` loading for non-Docker local development.
- Added Windows setup/start/test helper scripts.
- Added study-local calendar-day QC using `Asia/Shanghai` by default while retaining UTC storage.
- Exposed OwnTracks device identifier in GPS QC/export.
- Cleaned generated caches from the distributable package.

## 0.2.0

- First FastAPI/SQLite/OwnTracks GPS prototype with authentication, raw JSONL mirror, QC and CSV export.
