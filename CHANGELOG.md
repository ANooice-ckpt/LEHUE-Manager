# Changelog

## 0.3.1 - 2026-08-10

- Replaced command-line-first account bootstrap with a browser first-run setup flow.
- Public deployments require the server ADMIN_TOKEN for the one-time first PI bootstrap; after the first account exists, bootstrap is permanently closed.
- Added PI-only Web account management: create PI/RA accounts, disable/enable accounts and reset passwords.
- Added PI-only one-click system-state backup using SQLite online backup; active web sessions and raw/gps JSONL mirror are excluded.
- Added tzdata as a runtime dependency and Windows timezone diagnostics for Asia/Shanghai.
- Expanded tests for public-bootstrap protection, PI/RA permission boundaries and backup generation.

## 0.3.0 - 2026-08-10

- Added a simple Web Admin while keeping scientific analysis local.
- Preserved the V5 operational flow: candidate → participant ID/schedule → device pack → running → incident handling.
- Added dashboard, subjects, candidates, device packs, incidents, data sources and architecture views.
- Added PI/RA accounts, HttpOnly sessions, CSRF checks, security headers and audit logging.
- Split identity/contact data into `lehue_identity.sqlite3`; operational/GPS data remain in `lehue.sqlite3`.
- Added one-time OwnTracks credential creation from the Web Admin.
- Added V5 `state.json` operational migration script.
- Added tests for Web Admin workflow while retaining GPS API tests.

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

## 0.2.0

- First FastAPI/SQLite/OwnTracks GPS prototype with authentication, raw JSONL mirror, QC and CSV export.
