"""A phone file must not silently complete two different exposure days."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.modules.light.storage import LightObjectHead, LocalLightStorage
from test_web_admin import _reload_stack


DAY = "2026-09-10"
NEXT_DAY = "2026-09-11"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("LIGHT_STORAGE_BACKEND", "local")
    config, dbmod, _, _, _, _ = _reload_stack(monkeypatch, str(tmp_path / "runtime"))
    from app.modules.light import service as light
    with dbmod.db() as conn:
        for participant in ("001", "002"):
            conn.execute(
                """INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc)
                   VALUES(?,'running','2026-09-01','2026-09-14','now','now')""",
                (participant,),
            )
    return config, dbmod, light


@pytest.fixture
def full_day(tmp_path):
    start = datetime(2026, 9, 10, 8)
    path = tmp_path / "NSP32_record.csv"
    path.write_text(
        "Modify Time,Photopic Lux,Melanopic,Is Saturate\n"
        + "".join(f"{start + timedelta(seconds=10 * i)},100,80,No\n" for i in range(7200)),
        encoding="utf-8",
    )
    return path


def _rows(dbmod):
    with dbmod.db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM lighting_files ORDER BY id")]


class FakeOSS(LocalLightStorage):
    backend = "oss"

    def head(self, object_key):
        raw = self._path(object_key).read_bytes()
        return LightObjectHead(len(raw), hashlib.sha256(raw).hexdigest())

    def presign_put(self, object_key, sha256, expires_seconds):
        return {"url": f"https://oss.test.invalid/{object_key}", "headers": {"x-oss-meta-sha256": sha256}}


def _oss(monkeypatch, runtime, tmp_path):
    config, _, light = runtime
    storage = FakeOSS(tmp_path / "oss")
    monkeypatch.setattr(light, "settings", SimpleNamespace(**{**vars(config.settings), "light_storage_backend": "oss"}))
    monkeypatch.setattr(light, "get_light_storage", lambda: storage)
    return storage


def _prepare(light, source, day, *, is_test=False):
    return light.prepare_direct_upload(
        "001", day, source.name, source.stat().st_size,
        hashlib.sha256(source.read_bytes()).hexdigest(), is_test=is_test,
    )


def _put_complete(light, dbmod, storage, source, prepared):
    row = next(row for row in _rows(dbmod) if row["upload_uid"] == prepared["upload_uid"])
    storage.save(source, row["object_key"])
    return light.complete_direct_upload("001", row["upload_uid"])


def test_local_cross_midnight_file_retries_only_on_its_assigned_day(runtime, full_day):
    _, dbmod, light = runtime
    original = light.store_upload_path("001", DAY, full_day.name, full_day, "pi")
    assert original["records_expected"] == original["records_valid"] == 7200
    assert light.VALID_THRESHOLD_PCT == 90
    assert original["record_start"] == "2026-09-10T08:00:00"
    assert original["record_end"] == "2026-09-11T03:59:50"
    assert light.is_eligible(original)
    retried = light.store_upload_path("001", DAY, "renamed.csv", full_day, "pi")
    assert retried["duplicate"] and retried["upload_uid"] == original["upload_uid"]
    before = _rows(dbmod)
    with pytest.raises(ValueError, match=DAY):
        light.store_upload_path("001", NEXT_DAY, full_day.name, full_day, "pi")
    assert _rows(dbmod) == before
    assert light.get_light_storage()._path(before[0]["object_key"]).read_bytes() == full_day.read_bytes()
    # This is an identity rule within a participant, not a global content ban.
    light.store_upload_path("002", DAY, full_day.name, full_day, "pi")
    assert len(_rows(dbmod)) == 2


def test_local_wrong_first_day_keeps_raw_and_allows_correct_submission(runtime, full_day):
    _, dbmod, light = runtime
    wrong = light.store_upload_path("001", NEXT_DAY, full_day.name, full_day, "pi")
    assert wrong["parse_error"] == light.CONTENT_DATE_WARNING
    assert light.portal_light_state("001", NEXT_DAY)["status"] == "needs_review"
    before = _rows(dbmod)[0]
    corrected = light.store_upload_path("001", DAY, full_day.name, full_day, "pi")
    assert corrected["upload_uid"] != wrong["upload_uid"]
    assert light.portal_light_state("001", DAY)["status"] == "done"
    rows = _rows(dbmod)
    assert rows[0] == before  # No relocation, deletion or rewriting of the wrong raw.
    assert sum(light.is_eligible(row) for row in rows) == 1
    for row in rows:
        assert light.get_light_storage()._path(row["object_key"]).read_bytes() == full_day.read_bytes()


@pytest.mark.parametrize("backend", ["local", "oss"])
def test_test_file_cannot_be_reused_as_formal_data_on_another_day(monkeypatch, runtime, full_day, tmp_path, backend):
    _, dbmod, light = runtime
    if backend == "oss":
        _oss(monkeypatch, runtime, tmp_path)
        upload = lambda day, test: _prepare(light, full_day, day, is_test=test)
    else:
        upload = lambda day, test: light.store_upload_path("001", day, full_day.name, full_day, "pi", is_test=test)
    original = upload(DAY, True)
    assert upload(DAY, True)["upload_uid"] == original["upload_uid"]
    for day in (DAY, NEXT_DAY):
        with pytest.raises(ValueError, match="另一采集阶段"):
            upload(day, False)
    assert len(_rows(dbmod)) == 1 and _rows(dbmod)[0]["is_test"] == 1


def test_oss_blocks_cross_day_prepare_and_preserves_same_day_completion(monkeypatch, runtime, full_day, tmp_path):
    _, dbmod, light = runtime
    storage = _oss(monkeypatch, runtime, tmp_path)
    prepared = _prepare(light, full_day, DAY)
    assert _prepare(light, full_day, DAY)["upload_uid"] == prepared["upload_uid"]
    with pytest.raises(ValueError, match=DAY):
        _prepare(light, full_day, NEXT_DAY)
    completed = _put_complete(light, dbmod, storage, full_day, prepared)
    assert light.is_eligible(completed)
    retried = _prepare(light, full_day, DAY)
    assert retried["status"] == "qc" and retried["duplicate"] and retried["uploaded"]
    assert retried["upload_uid"] == prepared["upload_uid"]
    assert light.complete_direct_upload("001", prepared["upload_uid"])["duplicate"]
    before = _rows(dbmod)
    with pytest.raises(ValueError, match=DAY):
        _prepare(light, full_day, NEXT_DAY)
    assert _rows(dbmod) == before
    assert storage._path(before[0]["object_key"]).read_bytes() == full_day.read_bytes()


def test_oss_wrong_first_day_needs_qc_before_correct_day_can_be_registered(monkeypatch, runtime, full_day, tmp_path):
    _, dbmod, light = runtime
    storage = _oss(monkeypatch, runtime, tmp_path)
    wrong = _prepare(light, full_day, NEXT_DAY)
    with pytest.raises(ValueError, match=NEXT_DAY):
        _prepare(light, full_day, DAY)
    result = _put_complete(light, dbmod, storage, full_day, wrong)
    assert result["parse_error"] == light.CONTENT_DATE_WARNING
    before = _rows(dbmod)[0]
    correct = _prepare(light, full_day, DAY)
    _put_complete(light, dbmod, storage, full_day, correct)
    rows = _rows(dbmod)
    assert rows[0] == before and len(rows) == 2
    assert sum(light.is_eligible(row) for row in rows) == 1
    assert storage._path(before["object_key"]).read_bytes() == full_day.read_bytes()


@pytest.mark.parametrize("same_day", [False, True])
def test_concurrent_local_uploads_recheck_identity_after_raw_copy(monkeypatch, runtime, tmp_path, same_day):
    _, dbmod, light = runtime
    source = tmp_path / "without_times.csv"
    source.write_bytes(b"Photopic Lux,Melanopic,Is Saturate\n" + b"100,80,No\n" * 6480)
    storage = light.get_light_storage()
    original_save = storage.save
    both_copied = Barrier(2)

    def save(path, key):
        head = original_save(path, key)
        both_copied.wait(timeout=10)  # Both requests passed the initial empty lookup.
        return head

    monkeypatch.setattr(storage, "save", save)
    monkeypatch.setattr(light, "get_light_storage", lambda: storage)

    def upload(day):
        try:
            return light.store_upload_path("001", day, source.name, source, "pi")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [DAY, DAY if same_day else NEXT_DAY]))
    saved = [result for result in results if isinstance(result, dict)]
    assert len(saved) == (2 if same_day else 1)
    assert len({result["upload_uid"] for result in saved}) == 1
    if same_day:
        assert sum(result["duplicate"] for result in saved) == 1
    else:
        assert any("已归属于" in result for result in results if isinstance(result, str))
    rows = _rows(dbmod)
    assert len(rows) == 1 and len(list((storage.root / "raw" / "lighting").rglob("*.csv"))) == 1
    assert storage._path(rows[0]["object_key"]).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("same_day", [False, True])
def test_concurrent_oss_prepares_reserve_one_day(monkeypatch, runtime, full_day, tmp_path, same_day):
    _, dbmod, light = runtime
    _oss(monkeypatch, runtime, tmp_path)
    original_validate = light._validate_upload
    both_ready = Barrier(2)

    def validate(*args):
        result = original_validate(*args)
        both_ready.wait(timeout=10)
        return result

    monkeypatch.setattr(light, "_validate_upload", validate)

    def prepare(day):
        try:
            return _prepare(light, full_day, day)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, [DAY, DAY if same_day else NEXT_DAY]))
    saved = [result for result in results if isinstance(result, dict)]
    assert len(saved) == (2 if same_day else 1)
    assert len({result["upload_uid"] for result in saved}) == 1
    assert len(_rows(dbmod)) == 1


def test_existing_legacy_cross_day_rows_are_not_merged_or_deleted(runtime, full_day):
    _, dbmod, light = runtime
    light.store_upload_path("001", DAY, full_day.name, full_day, "pi")
    original = _rows(dbmod)[0]
    legacy = {key: value for key, value in original.items() if key != "id"}
    legacy.update(upload_uid="legacy_duplicate", date_local=NEXT_DAY)
    with dbmod.db() as conn:
        conn.execute(
            f"INSERT INTO lighting_files({','.join(legacy)}) VALUES({','.join('?' for _ in legacy)})",
            tuple(legacy.values()),
        )
    before = _rows(dbmod)
    dbmod.init_db()
    assert light.store_upload_path("001", DAY, full_day.name, full_day, "pi")["duplicate"]
    with pytest.raises(ValueError, match="已归属于"):
        light.store_upload_path("001", "2026-09-12", full_day.name, full_day, "pi")
    assert _rows(dbmod) == before
    assert light.get_light_storage()._path(original["object_key"]).read_bytes() == full_day.read_bytes()
