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

## Portable State Bundle

Export:

```bash
bash scripts/server_snapshot.sh export
```

Copy the ZIP from `server/data/snapshots/` between Windows TEST and ECS TEST,
then restore with:

```bash
bash scripts/server_snapshot.sh restore LEHUE_TEST_state_bundle_YYYYMMDD_HHMMSSZ.zip
```

The State Bundle is also the format used by Admin downloads and timed OSS
backups. It contains both SQLite databases, GPS raw JSONL, version/environment
metadata, the Lighting canonical-object manifest, and credential metadata. It
is sensitive. Lighting raw bytes are not copied; bucket/object key/SHA256/size
references remain canonical.

Restore validates ZIP structure, database checksums and SQLite integrity,
project, format, version and exact TEST/PROD environment. It creates a rollback
State Bundle under `restore_backups/` before replacing anything. Source
credentials are decrypted and re-encrypted with the target server's existing
Fernet key; `.env`, DOMAIN, RAM Role, bucket/endpoint configuration and
`ADMIN_TOKEN` are never replaced. A bundle with local-only Lighting references
is rejected when the target uses OSS, and a different canonical bucket is also
rejected until the objects have been copied deliberately.

Windows can use the same Python command directly (with `LEHUE_ENV=test`):

```powershell
Push-Location server
.venv\Scripts\python.exe scripts\test_snapshot.py export data\snapshots\windows-test.zip
.venv\Scripts\python.exe scripts\test_snapshot.py restore data\snapshots\ecs-test.zip
Pop-Location
```

The target's internal encryption key remains unchanged; restart TEST afterward.
