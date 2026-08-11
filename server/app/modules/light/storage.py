from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core import config
from app.core.oss_client import oss_bucket


@dataclass(frozen=True)
class LightObjectHead:
    size_bytes: int
    sha256: str = ""


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

    def __init__(self, bucket_name: str):
        self.bucket = oss_bucket(bucket_name)
        self.public_bucket = oss_bucket(bucket_name, public=True)

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
        return LightObjectHead(
            size_bytes=int(result.content_length),
            sha256=str(result.headers.get("x-oss-meta-sha256") or "").lower(),
        )

    def presign_put(self, object_key: str, sha256: str, expires_seconds: int) -> dict:
        key = _safe_key(object_key)
        headers = {"x-oss-meta-sha256": sha256}
        return {
            "url": self.public_bucket.sign_url("PUT", key, expires_seconds, headers=headers, slash_safe=True),
            "headers": headers,
        }

    def delete(self, object_key: str) -> None:
        self.bucket.delete_object(_safe_key(object_key))


def get_light_storage():
    settings = config.settings
    if settings.light_storage_backend == "local":
        return LocalLightStorage(settings.data_dir)
    return OSSLightStorage(settings.oss_bucket)
