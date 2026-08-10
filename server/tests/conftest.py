import os

# Pytest always uses the isolated TEST profile. Runtime selection remains mandatory
# for real server startup because LEHUE_ENV is never read from .env.
os.environ.setdefault("LEHUE_ENV", "test")
# Unit/integration tests build their own temporary databases and must never load
# the repository's shared manual-testing baseline.
os.environ.setdefault("LOAD_TEST_SEED", "false")
