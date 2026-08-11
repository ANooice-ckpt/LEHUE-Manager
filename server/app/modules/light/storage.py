from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core import config


@dataclass(frozen=True)
class LightObjectHead:
    size_bytes: int


def _safe_key(object_key: str) -> str:
    key = PurePosixPath(str(object_key or ""))
    if not object_key or key.is_absolute() or ".." in key.parts:
        raise ValueError("invalid Lighting object key")
    return key.as_posix()


def _temp_path(object_key: str) -> Path:
    suffix = PurePosixPath(object_key).suffix
    fd, name = tempfile.mkstemp(prefix="lehue-light-qc-", suffix=suffix)
    os.close(fd)
    return Path(name)


class LocalLightStorage:
    backend = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, object_key: str) -> Path:
        return self.root.joinpath(*PurePosixPath(_safe_key(object_key)).parts)

    def save(self, source_path: Path, object_key: str) -> LightObjectHead:
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        shutil.copyfile(source_path, temporary)
        temporary.replace(destination)
        return self.head(object_key)

    def download_to_temp(self, object_key: str) -> Path:
        temporary = _temp_path(object_key)
        try:
            shutil.copyfile(self._path(object_key), temporary)
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).is_file()

    def head(self, object_key: str) -> LightObjectHead:
        return LightObjectHead(size_bytes=self._path(object_key).stat().st_size)

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)


class OSSLightStorage:
    backend = "oss"

    def __init__(self, bucket_name: str, endpoint: str, access_key_id: str, access_key_secret: str):
        if not all((bucket_name, endpoint, access_key_id, access_key_secret)):
            raise RuntimeError(
                "OSS Lighting storage requires OSS_BUCKET, OSS_REGION or OSS_ENDPOINT, "
                "OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET"
            )
        try:
            import oss2
        except ImportError as exc:
            raise RuntimeError("OSS Lighting storage requires the oss2 package") from exc
        self.bucket = oss2.Bucket(oss2.Auth(access_key_id, access_key_secret), endpoint, bucket_name)

    def save(self, source_path: Path, object_key: str) -> LightObjectHead:
        key = _safe_key(object_key)
        self.bucket.put_object_from_file(key, str(source_path))
        return self.head(key)

    def download_to_temp(self, object_key: str) -> Path:
        key = _safe_key(object_key)
        temporary = _temp_path(key)
        try:
            self.bucket.get_object_to_file(key, str(temporary))
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def exists(self, object_key: str) -> bool:
        return bool(self.bucket.object_exists(_safe_key(object_key)))

    def head(self, object_key: str) -> LightObjectHead:
        result = self.bucket.head_object(_safe_key(object_key))
        return LightObjectHead(size_bytes=int(result.content_length))

    def delete(self, object_key: str) -> None:
        self.bucket.delete_object(_safe_key(object_key))


def get_light_storage():
    settings = config.settings
    if settings.light_storage_backend == "local":
        return LocalLightStorage(settings.data_dir)
    endpoint = settings.oss_endpoint or (f"https://oss-{settings.oss_region}.aliyuncs.com" if settings.oss_region else "")
    return OSSLightStorage(
        settings.oss_bucket,
        endpoint,
        settings.oss_access_key_id,
        settings.oss_access_key_secret,
    )
