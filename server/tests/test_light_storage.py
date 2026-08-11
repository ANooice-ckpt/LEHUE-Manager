import os
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
    endpoint = os.getenv("OSS_ENDPOINT") or f"https://oss-{os.environ['OSS_REGION']}.aliyuncs.com"
    storage = OSSLightStorage(
        os.environ["OSS_BUCKET"],
        endpoint,
        os.environ["OSS_ACCESS_KEY_ID"],
        os.environ["OSS_ACCESS_KEY_SECRET"],
    )
    source = tmp_path / "source.csv"
    source.write_bytes(b"LEHUE OSS integration test\n")
    key = f"integration-test/lighting/{uuid.uuid4().hex}.csv"
    try:
        storage.save(source, key)
        assert storage.exists(key)
        assert storage.head(key).size_bytes == source.stat().st_size
        downloaded = storage.download_to_temp(key)
        try:
            assert downloaded.read_bytes() == source.read_bytes()
        finally:
            downloaded.unlink(missing_ok=True)
    finally:
        storage.delete(key)
