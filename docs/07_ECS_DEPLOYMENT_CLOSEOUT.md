# ECS deployment closeout

This is the short operational path for TEST or PROD on ECS. Existing TEST/PROD
data roots, OSS policies, SQLite constraints and Web Admin permissions remain
unchanged.

## First setup

Install Docker Engine plus the Compose plugin, clone the repository, then run:

```bash
cd /opt/LEHUE-Manager
bash scripts/server_setup.sh test
```

The script asks only for the first PI username and password. It creates `.env`
when needed, generates `CREDENTIAL_ENCRYPTION_KEY` and the legacy internal
`ADMIN_TOKEN`, initializes the selected database, and creates the PI. Operators
do not record or use `ADMIN_TOKEN`. To initialize the isolated PROD identity
database, configure the required PROD OSS settings first, then run
`bash scripts/server_setup.sh prod` and supply the intended PROD PI credentials.

Edit `.env` only for deployment settings such as `DOMAIN` and OSS. ECS OSS
deployments use `OSS_CREDENTIAL_MODE=ecs_ram_role`; PROD still rejects local
Lighting storage and long-lived access keys.

```bash
bash scripts/server_start.sh test
```

Startup builds the API image from the current checkout, then doctor validates
that exact candidate image, constructs a real Fernet instance, imports the app
and starts an isolated candidate API health probe. Only after those checks pass
does Compose replace the running container. An existing instance does not need
setup again for ordinary updates.

The normal TEST update path is only:

```bash
git pull --ff-only
bash scripts/server_start.sh test
```

## ECS doctor and real smoke test

```bash
bash scripts/server_doctor.sh test
LEHUE_ENV=test bash scripts/server_smoke_test.sh
```

Doctor checks only hard deployment dependencies: Docker/Compose, `.env`, public
HTTPS hostname, the newly built candidate image/Fernet key/API process and
(when running) SQLite integrity.

The smoke test is intentionally TEST-only and rotates the selected synthetic
participant's GPS password and Portal link. It then logs in through HTTPS,
opens the Portal and sends a real OwnTracks point. In OSS mode it uses the
production RAM Role/storage adapter. It first performs an internal-endpoint SDK
PutObject/HEAD using the ECS credential, then separately validates the public
browser CORS preflight and V4 presigned PUT/HEAD. Failures report a sanitized
stage, endpoint host, HTTP status, OSS XML Code/Message and RequestId; URL query
signatures and credentials are never printed. The GPS smoke point remains
normal TEST data.

All repository shell scripts are invoked with `bash scripts/...`; executable
bits are therefore not a deployment prerequisite on Windows-origin checkouts.

## Portable TEST snapshot

Export:

```bash
bash scripts/server_snapshot.sh export
```

Copy the ZIP from `server/data/snapshots/` between Windows TEST and ECS TEST,
then restore with:

```bash
bash scripts/server_snapshot.sh restore LEHUE_TEST_snapshot_YYYYMMDD_HHMMSSZ.zip
```

The archive contains both SQLite databases, GPS raw JSONL, version/environment
metadata, the Lighting canonical-object manifest, and the credential encryption
key needed to keep recoverable GPS/Portal credentials usable. It is sensitive.
Lighting raw bytes are not copied; OSS object keys remain canonical.

Restore is hard-coded to `LEHUE_ENV=test`, validates ZIP structure, project,
format, version compatibility, Fernet key and SQLite integrity, and creates a
rollback snapshot under `server/data/test/restore_backups/` before replacing
anything. A TEST snapshot cannot be restored into PROD.

Windows can use the same Python command directly (with `LEHUE_ENV=test`):

```powershell
Push-Location server
.venv\Scripts\python.exe scripts\test_snapshot.py export data\snapshots\windows-test.zip
.venv\Scripts\python.exe scripts\test_snapshot.py restore data\snapshots\ecs-test.zip --env-file ..\.env
Pop-Location
```

The Windows restore command updates the internal encryption key atomically;
restart TEST afterward.
