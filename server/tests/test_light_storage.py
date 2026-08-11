import os
import hashlib
import uuid

import pytest

from app.modules.light.storage import LocalLightStorage, OSSLightStorage


def test_local_light_storage_round_trip(tmp_path):
    source = tmp_path / "source.csv"
    source.write_bytes(b"Photopic Lux,Melanopic\n100,80\n")
    storage = LocalLightStorage(tmp_path / "canonical")
    key = "raw/lighting/001/2026-08-11/light_test.csv"

    saved = storage.save(source, key)
    assert saved.size_bytes == source.stat().st_size
    assert storage.exists(key)
    assert storage.head(key).size_bytes == source.stat().st_size

    downloaded = storage.download_to_temp(key)
    try:
        assert downloaded.read_bytes() == source.read_bytes()
    finally:
        downloaded.unlink(missing_ok=True)

    storage.delete(key)
    assert not storage.exists(key)


@pytest.mark.skipif(os.getenv("RUN_OSS_INTEGRATION") != "1", reason="real OSS integration is opt-in")
def test_real_oss_round_trip(tmp_path):
    storage = OSSLightStorage(os.environ["OSS_BUCKET"])
    source = tmp_path / "source.csv"
    source.write_bytes(b"LEHUE OSS integration test\n")
    key = f"integration-test/lighting/{uuid.uuid4().hex}.csv"
    try:
        import requests
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        signed = storage.presign_put(key, digest, 300)
        response = requests.put(signed["url"], data=source.read_bytes(), headers=signed["headers"], timeout=30)
        response.raise_for_status()
        assert storage.exists(key)
        assert storage.head(key).size_bytes == source.stat().st_size
        assert storage.head(key).sha256 == digest
        downloaded = storage.download_to_temp(key)
        try:
            assert downloaded.read_bytes() == source.read_bytes()
        finally:
            downloaded.unlink(missing_ok=True)
    finally:
        storage.delete(key)
