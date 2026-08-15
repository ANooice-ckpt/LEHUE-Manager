# Changelog

## Unreleased

- Added dynamically generated iOS and Android OwnTracks `.otrc` downloads in Admin and Participant Portal. Both use the participant's current GPS credential and configured public domain without persisting plaintext configuration files; Portal highlights the S0-known phone platform and includes concise GPS/Lighting tutorials.
- Added a minimal, mobile-friendly public academic landing page for the Light Exposure Histories in Urban Environments (LEHUE) study, with participant access guidance and Tsinghua research-team contact information.
- Reduced public `/health` output to a status-only probe and removed the environment response header so public endpoints no longer disclose operational metadata.
- Closeout is now two-step: end formal exposure, retain Portal access for the final morning/S2/makeup, then complete or force-close with a recorded reason.
- Added preparation cancellation and lawful early termination while preserving acquired data and device return facts.
- Removed the S1 onboarding questionnaire from LEHUE; it is completed externally in Wenjuanxing. Ready now requires only a real GPS return and a parser-readable Lighting test with actual records, with no formal-day duration or coverage threshold. Existing historical S1 response rows are ignored.
- Device replacement now works in preparation, Ready, and active running phases; formal start no longer accepts a silent pack override.
- Running `start_date` is locked in ordinary edits; end dates remain adjustable.
- Participant Portal offers prior-day Lighting reupload when the best raw is insufficient, unreadable, or clearly short.
- Admin Lighting follows the current evening exposure target date; charging remains a reminder without a false confirmation record.
- Added a minimal candidate contact-log workflow and lightweight Ready-dialog polling.

- Update S0 import and candidate controls to the August 12 recruitment form:
  structure demographics/schedules, exposure-mechanism variables, commute and
  participation operations; derive the fixed-position/daylight four-quadrant
  category and retire `light_type` from active use without dropping the column.

- Unify Admin backup downloads, portable TEST snapshots, and timed OSS backups
  as one State Bundle containing both SQLite databases, GPS raw, manifest, and
  credential metadata while retaining Lighting as canonical OSS references.
- Validate ZIP/SQLite/version/environment and Lighting portability before
  restore, create a rollback bundle, and re-encrypt imported credentials with
  the target server key without replacing infrastructure configuration.

- Restrict GPS ingest to running participants and make study lifecycle changes
  explicit: scheduled → running → completed, with atomic device return and GPS
  credential deactivation while the completed Portal remains read-only.
- Use the configured study timezone for Admin defaults, return HTTP 503 when the
  database health check fails, and run the existing idempotent Daily QC hourly.
- Put the existing dynamic OwnTracks configuration in the Participant Portal,
  attach Portal links to QC/incident contact text, and remove the unimplemented
  personal-report and non-acquisition cohort placeholders.
- Generate a non-persistent Onboarding Card when a study starts, including the
  existing Portal/GPS credentials, study dates, devices, copyable fixed contact
  text, and an official OwnTracks HTTP `.otrc`/inline configuration.
- Add a lightweight participant “My Study / Help & Settings” view using existing
  study, device, Study Day and GPS state, plus fixed operational tutorials.
- Add copyable fixed-context contact text to onboarding, daily QC and incidents;
  no messaging workflow or additional stored state is introduced.
- Remove Lighting filename identity/date gates; Portal assignment is canonical
  and filenames are provenance only. Raw uploads now remain accepted when QC
  finds unreadable, insufficient, or clearly wrong-day content.
- Reuse parsed Lighting record timestamps to warn participants and the existing
  daily QC/incident flow only when an entire reliably dated file is clearly
  unrelated to the assigned experiment day, while allowing midnight crossover.
- Build and health-probe the current candidate API image before replacing a
  running ECS container; validate the configured Fernet key with Fernet itself.
- Preserve existing PI accounts during upgrades and refuse to replace an
  invalid encryption key when runtime data already exists.
- Make the deployment smoke test self-contained, stage-oriented, and able to
  isolate internal RAM Role SDK writes from public V4 presigned PUT failures.
- Pin `Content-Type` in OSS V4 PUT signatures and report sanitized OSS error
  fields without exposing presigned queries or credentials.

## v0.5.5 - ECS deployment closeout

- Fail application startup when `CREDENTIAL_ENCRYPTION_KEY` is missing or invalid;
  Web Admin credential actions now surface backend failures explicitly.
- Add minimal ECS setup/doctor and a TEST-only real HTTPS/GPS/Portal/RAM Role/OSS
  presigned-upload smoke test. Server-internal keys are generated automatically.
- Remove the administrator from the versioned TEST seed while preserving its
  other synthetic data.
- Add portable TEST snapshot export/restore with automatic rollback, compatibility
  checks, GPS raw and Lighting OSS references; PROD restore is forbidden.

## 0.5.4 - 2026-08-11

- Added a unified participant credential dialog for viewing and copying the OwnTracks endpoint, username/password and participant portal link at any time.
- Kept the existing salted hashes for authentication and added Fernet-encrypted recoverable copies in the same two existing tables; no new table was introduced.
- Added one-click GPS password and portal-link rotation. Credentials created before v0.5.4 require one reset before they become recoverable.
- Added automatic SQLite column migration, audit events for credential viewing/rotation and local encryption-key setup.

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
