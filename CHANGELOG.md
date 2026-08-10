# Changelog

## 0.5.3 - 2026-08-10

- Added a Git-versioned, synthetic-only `server/test_seed` baseline while keeping mutable `data/test` and all `data/prod` state ignored.
- TEST installs the baseline when both runtime databases are absent or contain no records; existing TEST activity is never overwritten.
- PROD returns before inspecting or copying TEST seed data, and automated tests explicitly disable seed installation.
- Included the seed in Docker images so a fresh TEST server obtains the same simulated starting state after `git pull` and build.

## 0.5.2 - 2026-08-10

- Added mandatory per-start runtime selection between `TEST` and `PROD`; the choice is process-scoped and cannot be changed from the Web UI.
- Split all mutable state into physically separate `data/test` and `data/prod` trees, including both SQLite databases plus raw GPS and Lighting files.
- `LEHUE_ENV` is intentionally ignored in `.env`, so every real server start must explicitly select an environment again.
- Windows startup now prompts for TEST/PROD and requires an extra `PROD` confirmation before formal-study writes.
- Docker deployment refuses to start unless `LEHUE_ENV=test` or `LEHUE_ENV=prod` is supplied; added a small Linux/Docker startup helper.
- Root/health responses and service metadata expose the locked runtime environment for diagnostics.

## 0.5.1 - 2026-08-10

- Replaced the temporary daily questionnaires with the two formal morning and bedtime forms supplied for the study.
- Moved versioned form definitions and validation into an independent, FastAPI/database-free questionnaire module with stable coded answers.
- Kept participant ID, date and Study Day server-bound instead of asking participants to re-enter them.
- Rendered each questionnaire as one mobile-friendly scrolling page with no pagination, including discrete seven-point scales, exclusive multi-select handling and a complete device-status matrix.
- Added definition/validation tests and participant-portal integration coverage for both formal forms.

## 0.5.0 - 2026-08-10

- Migrated the field-tested ANOLighting V5 Lighting parser and acquisition-QC rules: 7,200 expected records, 90% valid threshold, saturation exclusion, both repeated-key and tabular CSV/XLSX/TXT layouts, and best-file selection per participant/day.
- Added token-bound Lighting upload to the Participant Portal and an RA backfill API; raw files live under `raw/lighting`, while parser/QC metadata stays in the existing operations SQLite database.
- Added a derived daily acquisition-QC view using the V5 exposure-day rule (evening questionnaire + Lighting + GPS + next-morning questionnaire), with one-click incident synchronization and `valid_days` refresh.
- Added S0 Wenjuanxing CSV/XLSX cumulative import with stable merging by sequence/phone/WeChat, willingness filtering, manual-correction preservation, import deduplication, and original-file storage in the identity database.
- Expanded V5 state migration to retain candidate raw rows, final-morning/close fields, idempotent operational upserts, and optional raw Lighting import.
- Added read-only historical compatibility tooling. All 26 cached Lighting summaries in the V5 test corpus match the new parser exactly.

## 0.4.0 - 2026-08-10

- Added a token-based Participant Portal at `/p/<token>` inspired by the existing ANOLighting participant task console.
- Added one-click participant portal link generation/rotation from the Web Admin; the participant ID is not embedded in the URL.
- Added native LEHUE morning/evening test questionnaires with automatic participant identity, local date and Study Day binding.
- Added a single `questionnaire_responses` table; questionnaire definitions remain code-configured in v0.4 to avoid over-engineering.
- Added GPS last-seen status and daily questionnaire completion to the participant portal and Admin subject/dashboard views.
- Reclassified Questionnaire from Wenjuanxing manual import to a native connected LEHUE data source.
- Added automatic in-place SQLite migration for portal token fields, preserving existing v0.3.x data.
- Included questionnaire response counts/content in the existing consistent system-state backup.
- Added participant portal integration tests and database migration checks while retaining GPS/Admin tests.

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
