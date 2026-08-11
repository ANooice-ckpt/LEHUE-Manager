from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.core.config import settings  # noqa: E402
from app.core.oss_client import oss_bucket  # noqa: E402
from app.modules.admin.backup import create_system_backup  # noqa: E402


def main() -> None:
    if not settings.backup_oss_bucket:
        raise RuntimeError("BACKUP_OSS_BUCKET is required")

    zip_path_text, temp_dir_text = create_system_backup(include_gps_raw=True)
    zip_path, temp_dir = Path(zip_path_text), Path(temp_dir_text)
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        parts = [part for part in (settings.backup_oss_prefix, settings.runtime_env, stamp, zip_path.name) if part]
        object_key = "/".join(parts)
        bucket = oss_bucket(settings.backup_oss_bucket)
        bucket.put_object_from_file(object_key, str(zip_path))
        print(json.dumps({"bucket": settings.backup_oss_bucket, "object_key": object_key}, ensure_ascii=False))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
