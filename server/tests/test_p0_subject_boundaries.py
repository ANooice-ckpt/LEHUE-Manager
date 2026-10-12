"""Protect real participant identity and the recorded end of exposure."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading

import pytest

from test_web_admin import _reload_stack


def test_old_candidate_page_cannot_assign_a_second_participant(monkeypatch, tmp_path):
    _, dbmod, idb, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    uid = svc.add_candidate({"name": "Alice", "phone": "13800000001"}, "pi")
    svc.promote_candidate(uid, {"participant_id": "001"}, "pi")

    with pytest.raises(ValueError, match="已绑定被试 001"):
        svc.promote_candidate(uid, {"participant_id": "002"}, "ra")

    with dbmod.db() as conn:
        assert [row[0] for row in conn.execute("SELECT participant_id FROM study_subjects")] == ["001"]
    with idb.identity_db() as conn:
        assert conn.execute("SELECT linked_participant_id FROM candidates WHERE candidate_uid=?", (uid,)).fetchone()[0] == "001"
    assert svc.list_subjects()[0]["phone"] == "13800000001"


def test_two_promotions_after_both_read_the_unbound_candidate(monkeypatch, tmp_path):
    _, dbmod, idb, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    uid = svc.add_candidate({"name": "Alice"}, "pi")
    barrier = threading.Barrier(2)
    local = threading.local()
    original_db = svc.db

    @contextmanager
    def simultaneous_db():
        if not getattr(local, "started", False):
            local.started = True
            # Both requests have already read the candidate before either writes.
            barrier.wait(timeout=5)
        with original_db() as conn:
            yield conn

    monkeypatch.setattr(svc, "db", simultaneous_db)

    def promote(sid):
        try:
            return svc.promote_candidate(uid, {"participant_id": sid}, "pi"), None
        except ValueError as exc:
            return None, str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(promote, sid) for sid in ("001", "002")]
        results = [future.result(timeout=10) for future in futures]
    winners = [sid for sid, error in results if not error]
    assert len(winners) == 1
    assert "已绑定被试" in next(error for _, error in results if error)
    with dbmod.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_subjects").fetchone()[0] == 1
    with idb.identity_db() as conn:
        assert conn.execute("SELECT linked_participant_id FROM candidates WHERE candidate_uid=?", (uid,)).fetchone()[0] == winners[0]


def test_subject_candidate_constraint_preserves_legacy_conflicts_without_stopping_startup(monkeypatch, tmp_path, caplog):
    _, dbmod, _, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    uid = svc.add_candidate({"name": "Alice"}, "pi")
    svc.promote_candidate(uid, {"participant_id": "001"}, "pi")
    insert = "INSERT INTO study_subjects(participant_id,candidate_uid,created_at_utc,updated_at_utc) VALUES(?,?,?,?)"
    with pytest.raises(sqlite3.IntegrityError):
        with dbmod.db() as conn:
            conn.execute(insert, ("002", uid, "now", "now"))
    with dbmod.db() as conn:
        for sid, candidate in (("003", None), ("004", None), ("005", ""), ("006", "")):
            conn.execute(insert, (sid, candidate, "now", "now"))
        conn.execute("DROP INDEX idx_subject_candidate_uid")
        conn.execute(insert, ("002", uid, "now", "now"))
    with caplog.at_level("WARNING"):
        dbmod.init_db()
    assert uid in caplog.text
    assert "001, 002" in caplog.text
    assert "不会自动合并或删除" in caplog.text
    with dbmod.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_subjects").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM study_subjects WHERE candidate_uid=?", (uid,)).fetchone()[0] == 2
        conn.execute("UPDATE study_subjects SET candidate_uid=candidate_uid,notes='preserved' WHERE participant_id='001'")
    with pytest.raises(sqlite3.IntegrityError, match="candidate already assigned"):
        with dbmod.db() as conn:
            conn.execute(insert, ("007", uid, "now", "now"))
    with pytest.raises(sqlite3.IntegrityError, match="candidate already assigned"):
        with dbmod.db() as conn:
            conn.execute("UPDATE study_subjects SET candidate_uid=? WHERE participant_id='003'", (uid,))
    with pytest.raises(ValueError) as caught:
        svc.promote_candidate(uid, {"participant_id": "007"}, "pi")
    assert "已绑定被试 001, 002" in str(caught.value)
    assert "身份库 001，研究库 001, 002" in str(caught.value)

    # Once the conflict has been explicitly corrected, startup can install the
    # normal unique index; it does not leave a second constraint mechanism behind.
    with dbmod.db() as conn:
        conn.execute("UPDATE study_subjects SET candidate_uid=NULL WHERE participant_id='002'")
    dbmod.init_db()
    with dbmod.db() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_subject_candidate_uid'").fetchone()
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name LIKE 'prevent_duplicate_subject_candidate_%'").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM study_subjects").fetchone()[0] == 6


def test_promote_reports_identity_binding_even_when_missing_in_study_database(monkeypatch, tmp_path):
    _, dbmod, idb, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    uid = svc.add_candidate({"name": "Alice"}, "pi")
    with idb.identity_db() as conn:
        conn.execute("UPDATE candidates SET linked_participant_id='001' WHERE candidate_uid=?", (uid,))
    with pytest.raises(ValueError, match="身份库 001，研究库 无"):
        svc.promote_candidate(uid, {"participant_id": "002"}, "pi")
    with dbmod.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_subjects").fetchone()[0] == 0


def _running_subject(dbmod, svc, monkeypatch):
    monkeypatch.setattr(svc, "local_today", lambda: "2026-09-14")
    with dbmod.db() as conn:
        conn.execute(
            """INSERT INTO study_subjects(participant_id,status,start_date,end_date,pack_id,created_at_utc,updated_at_utc)
               VALUES('001','running','2026-09-01','2026-09-14','D01','now','now')"""
        )
        conn.execute(
            """INSERT INTO device_packs(pack_id,status,current_participant_id,issued_date,expected_return_date,updated_at_utc)
               VALUES('D01','in_use','001','2026-09-01','2026-09-14','now')"""
        )
        conn.execute(
            """INSERT INTO device_assignments(participant_id,pack_id,effective_from_date,effective_from_utc,created_at_utc)
               VALUES('001','D01','2026-09-01','2026-09-01T00:00:00Z','now')"""
        )
        conn.execute(
            """INSERT INTO participants(participant_id,secret_salt,secret_hash,is_active,created_at_utc)
               VALUES('001','salt','hash',1,'now')"""
        )


def _facts(dbmod):
    with dbmod.db() as conn:
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("study_subjects", "device_packs", "device_assignments", "participants")
        }


@pytest.mark.parametrize("completed", [False, True])
def test_ended_exposure_rejects_date_edits_but_allows_notes(monkeypatch, tmp_path, completed):
    _, dbmod, _, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    _running_subject(dbmod, svc, monkeypatch)
    svc.end_exposure("001", {"final_end": "2026-09-14"}, "pi")
    if completed:
        svc.complete_subject("001", {"force": True, "reason": "participant withdrew"}, "pi")
    before = _facts(dbmod)
    for changes in ({"planned_end": "2026-09-16"}, {"planned_start": "2026-09-02"}):
        with pytest.raises(ValueError, match="曝光已结束"):
            svc.update_subject("001", {**changes, "notes": "must not be saved"}, "pi")
        assert _facts(dbmod) == before

    svc.update_subject("001", {
        "planned_start": "2026-09-01", "planned_end": "2026-09-14",
        "notes": "collection arranged", "assigned_ra": "ra02", "batch_id": "autumn",
    }, "pi")
    after = _facts(dbmod)
    for table in ("device_packs", "device_assignments", "participants"):
        assert after[table] == before[table]
    expected = before["study_subjects"][0] | {
        "notes": "collection arranged", "assigned_ra": "ra02", "batch_id": "autumn",
        "updated_at_utc": after["study_subjects"][0]["updated_at_utc"],
    }
    assert after["study_subjects"] == [expected]


def test_active_exposure_can_still_be_extended(monkeypatch, tmp_path):
    _, dbmod, _, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    _running_subject(dbmod, svc, monkeypatch)
    before = _facts(dbmod)
    svc.update_subject("001", {"planned_end": "2026-09-16"}, "pi")
    after = _facts(dbmod)
    assert after["study_subjects"][0]["end_date"] == "2026-09-16"
    assert after["device_packs"][0]["expected_return_date"] == "2026-09-16"
    assert after["study_subjects"][0]["status"] == "running"
    assert after["device_packs"][0]["status"] == "in_use"
    assert after["device_assignments"] == before["device_assignments"]
    assert after["participants"] == before["participants"]
