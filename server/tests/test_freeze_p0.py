"""Regressions for concrete field-experiment failures found before freezing."""
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from test_web_admin import _reload_stack


MORNING = {
    "bedtime": "23:15", "wake_time": "07:10", "alertness": "alert",
    "sleep_quality": 1, "sleep_recovery": 2, "sleep_continuity": 1,
    "sleep_sufficiency": 2, "sleep_onset_ease": 1, "wake_ease": 2,
    "sleep_influences": ["none"],
}
EVENING = {
    "alertness": "alert", "day_energy": 1, "day_mood": 1,
    "day_activation": 0, "nap_duration": "none",
    "device_status": {"gps": "normal", "lighting": "normal"},
}


def _clock(monkeypatch, portal, day_time):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat(day_time).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(portal, "datetime", Clock)


def _subject(dbmod, start="2026-09-01", end="2026-09-14"):
    with dbmod.db() as conn:
        conn.execute(
            """INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc)
               VALUES('001','running',?,?,'now','now')""", (start, end),
        )


def test_final_morning_keeps_explicit_day_and_is_not_available_early(monkeypatch, tmp_path):
    _, dbmod, _, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.participant.service as portal
    _subject(dbmod)
    token = portal.generate_portal_token("001")
    _clock(monkeypatch, portal, "2026-09-14T22:00:00")
    svc.end_exposure("001", {"final_end": "2026-09-14"}, "pi")
    closing = portal.portal_state(token)
    assert closing["mode"] == "closing"
    assert not closing["field_reminders"]["morning_restart"]["show"]
    assert not any(f["key"] == "morning" and f["date_local"] == "2026-09-14" for f in closing["forms"])
    assert not any(f["date_local"] == "2026-09-14" for f in closing["lighting_tasks"])
    for task in closing["lighting_tasks"]:
        assert portal._lighting_participant(token, task["date_local"]) == ("001", False)
        assert "无需开启新记录" in task["time_scope"]["explanation"]
    with pytest.raises(ValueError, match="尚未到"):
        portal.submit_questionnaire(token, "morning", MORNING, "2026-09-14")
    # Missing evening remains fillable, and an older morning is not rebound.
    portal.submit_questionnaire(token, "evening", EVENING, "2026-09-14")
    result = portal.submit_questionnaire(token, "morning", MORNING, "2026-09-13")
    assert result["date_local"] == "2026-09-13" and result["study_day"] == 13
    _clock(monkeypatch, portal, "2026-09-15T08:00:00")
    final = portal.submit_questionnaire(token, "morning", MORNING, "2026-09-14")
    assert final["study_day"] == 14 and final["calendar_date_local"] == "2026-09-15"
    with pytest.raises(ValueError, match="已经提交"):
        portal.submit_questionnaire(token, "morning", MORNING, "2026-09-14")
    for _ in range(2):
        dbmod.init_db()
    with dbmod.db() as conn:
        rows = conn.execute("SELECT date_local,study_day FROM questionnaire_responses WHERE form_key='morning' ORDER BY date_local").fetchall()
    assert [(r["date_local"], r["study_day"]) for r in rows] == [("2026-09-13", 13), ("2026-09-14", 14)]


def test_open_task_retains_day_across_midnight_cutoff_and_end_exposure(monkeypatch, tmp_path):
    _, dbmod, _, _, _, _ = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.participant.service as portal
    _subject(dbmod)
    token = portal.generate_portal_token("001")
    _clock(monkeypatch, portal, "2026-09-12T23:59:00")
    task = next(f for f in portal.portal_state(token)["forms"] if f["key"] == "evening" and f["is_makeup"])
    assert task["date_local"] == "2026-09-11"
    _clock(monkeypatch, portal, "2026-09-13T15:00:00")
    assert portal.submit_questionnaire(token, "evening", EVENING, task["date_local"])["date_local"] == "2026-09-11"


def test_legacy_morning_migrates_only_when_calendar_column_is_added(monkeypatch, tmp_path):
    _, dbmod, _, _, _, _ = _reload_stack(monkeypatch, str(tmp_path))
    _subject(dbmod)
    with dbmod.db() as conn:
        conn.execute("ALTER TABLE questionnaire_responses DROP COLUMN calendar_date_local")
        conn.execute("""INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc)
                        VALUES('001','2026-09-03',3,'morning','{}','original-time')""")
    dbmod.init_db()
    with dbmod.db() as conn:
        row = conn.execute("SELECT * FROM questionnaire_responses").fetchone()
        assert (row["date_local"], row["study_day"], row["calendar_date_local"]) == ("2026-09-02", 2, "2026-09-03")
        assert row["submitted_at_utc"] == "original-time"
        conn.execute("""INSERT INTO questionnaire_responses(participant_id,date_local,calendar_date_local,study_day,form_key,answers_json,submitted_at_utc)
                        VALUES('001','2026-09-04','2026-09-04',4,'morning','{}','original-time')""")
    dbmod.init_db()
    with dbmod.db() as conn:
        assert conn.execute("SELECT 1 FROM questionnaire_responses WHERE date_local='2026-09-04' AND study_day=4").fetchone()


@pytest.mark.parametrize("client_name", ["owntracks", "traccar"])
def test_gps_closeout_accepts_backlog_but_not_new_records_and_reset_revokes(monkeypatch, tmp_path, client_name):
    _, dbmod, _, _, svc, main = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.gps.service as gps
    import app.modules.gps.traccar_service as traccar
    importlib.reload(traccar)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    _subject(dbmod, (today - timedelta(days=2)).isoformat(), today.isoformat())
    password = svc.create_or_rotate_gps_credential("001", "pi")
    svc.end_exposure("001", {"final_end": today.isoformat()}, "pi")
    recorded = datetime.now(timezone.utc) - timedelta(hours=2)

    def send(client, stamp):
        if client_name == "owntracks":
            return client.post("/api/v1/gps/owntracks", auth=("001", password), json={"_type": "location", "tst": stamp, "lat": 39.9, "lon": 116.4})
        return client.post("/api/v1/gps/traccar", data={"id": f"001.{password}", "timestamp": str(stamp), "lat": "39.9", "lon": "116.4"})

    with TestClient(main.app) as client:
        assert send(client, recorded.timestamp()).status_code == 200
        assert send(client, recorded.timestamp()).status_code == 200
        # Acknowledged without storage so a new point cannot block the old queue.
        assert send(client, (datetime.now(timezone.utc) + timedelta(seconds=1)).timestamp()).status_code == 200
        with dbmod.db() as conn:
            rows = conn.execute("SELECT * FROM gps_locations").fetchall()
            assert len(rows) == 1
            assert rows[0]["recorded_at_utc"] == gps.iso_utc(recorded)
            assert rows[0]["received_at_utc"] > rows[0]["recorded_at_utc"]
        svc.create_or_rotate_gps_credential("001", "pi")
        assert send(client, recorded.timestamp()).status_code == 403
        svc.complete_subject("001", {"force": True, "reason": "test closeout"}, "pi")
        assert not gps.authenticate_participant("001", password)


def test_morning_lighting_target_stays_yesterday_and_keeps_7200(monkeypatch, tmp_path):
    _, dbmod, _, _, svc, _ = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.participant.service as portal
    import app.modules.light.service as light
    _subject(dbmod)
    token = portal.generate_portal_token("001")
    for clock in ("2026-09-12T08:00:00", "2026-09-12T20:00:00"):
        _clock(monkeypatch, portal, clock)
        state = portal.portal_state(token)
        assert state["lighting"]["date_local"] == "2026-09-11"
        assert all(d["lighting"] == "not_due" for d in state["progress"]["days"] if d["date_local"] == "2026-09-12")
        assert portal._lighting_participant(token, "2026-09-11") == ("001", False)
        with pytest.raises(ValueError):
            portal._lighting_participant(token, "2026-09-12")
        assert svc.list_subjects()[0]["lighting_target_date"] == "2026-09-11"
    assert light.EXPECTED_SAMPLES == 7200
    parsed = light.parse_light_bytes("raw.csv", b"Photopic Lux,Melanopic,Is Saturate\n" + b"100,80,No\n" * 6480)
    assert parsed["valid_pct"] == 90 and parsed["quality"] == "valid"
    just_short = light.parse_light_bytes("raw.csv", b"Photopic Lux,Melanopic,Is Saturate\n" + b"100,80,No\n" * 6479)
    assert just_short["quality"] == "insufficient"  # Display rounding must not turn 6479 into 6480.


def test_lighting_test_and_wrong_date_do_not_count_and_correct_reupload_wins(monkeypatch, tmp_path):
    _, dbmod, _, _, _, _ = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.participant.service as portal
    import app.modules.light.service as light
    _subject(dbmod)
    token = portal.generate_portal_token("001")
    _clock(monkeypatch, portal, "2026-09-15T08:00:00")
    raw = b"Photopic Lux,Melanopic,Is Saturate\n" + b"100,80,No\n" * 7200
    path = tmp_path / "test.csv"
    path.write_bytes(raw)
    test = light.store_upload_path("001", "2026-09-14", "test.csv", path, "pi", is_test=True)
    assert light.best_for_date("001", "2026-09-14") is None
    with pytest.raises(ValueError, match="另一采集阶段"):
        light.store_upload("001", "2026-09-14", "data.csv", raw, "pi")
    wrong_raw = b"Modify Time,Photopic Lux,Melanopic,Is Saturate\n" + b"2026-09-01 08:00:00,100,80,No\n" * 7200
    wrong = light.store_upload("001", "2026-09-14", "device.csv", wrong_raw, "pi")
    assert light.portal_light_state("001", "2026-09-14")["status"] == "needs_review"
    assert portal.portal_state(token)["progress"]["days"][-1]["lighting"] == "pending"
    assert any(i["type"] == "wrong_day_light" for r in light.daily_qc_rows() if r["date_local"] == "2026-09-14" for i in r["issues"])
    for name in ("002_20260914_LIGHT.csv", "001_20260913_LIGHT.csv"):
        with pytest.raises(ValueError, match="不一致"):
            light.store_upload("001", "2026-09-14", name, raw, "pi")
    good = light.store_upload("001", "2026-09-14", "001_20260914_LIGHT.csv", b"Photopic Lux,Melanopic,Is Saturate\n" + b"100,80,No\n" * 6480, "pi")
    assert light.best_for_date("001", "2026-09-14")["upload_uid"] == good["upload_uid"]
    assert portal.portal_state(token)["progress"]["days"][-1]["lighting"] == "done"
    with dbmod.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lighting_files").fetchone()[0] == 3
        assert conn.execute("SELECT is_test FROM lighting_files WHERE upload_uid=?", (test["upload_uid"],)).fetchone()[0] == 1
        assert conn.execute("SELECT parse_error FROM lighting_files WHERE upload_uid=?", (wrong["upload_uid"],)).fetchone()[0] == light.CONTENT_DATE_WARNING


def test_s0_source_sequence_collision_and_contact_conflict_rollback(monkeypatch, tmp_path):
    _, _, idb, _, _, _ = _reload_stack(monkeypatch, str(tmp_path))
    import app.modules.questionnaire.s0_import as s0
    header = "序号,您的年龄,您的性别,手机号,微信号\n"
    def raw(rows):
        return (header + rows).encode("utf-8")
    s0.import_s0("a.csv", raw("1,25,女,13800000001,alice\n"), "pi", "survey-a")
    s0.import_s0("b.csv", raw("1,40,男,13800000002,bob\n"), "pi", "survey-b")
    with idb.identity_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates WHERE in_latest_snapshot=1").fetchone()[0] == 2
    # A preceding valid row also rolls back when a later row conflicts.
    with pytest.raises(ValueError, match="冲突"):
        s0.import_s0("bad.csv", raw("2,30,男,13800000003,charlie\n1,50,男,13800000004,eve\n"), "pi", "survey-a")
    with idb.identity_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2
        assert conn.execute("SELECT age_group FROM candidates WHERE s0_source_id='survey-a'").fetchone()[0] == "25"
        assert conn.execute("SELECT COUNT(*) FROM s0_imports").fetchone()[0] == 2
    with pytest.raises(ValueError, match="不同候选人"):
        s0.import_s0("cross.csv", raw("2,25,女,13800000001,bob\n"), "pi", "survey-c")
    s0.import_s0("same.csv", raw("2,25,女,13800000003,charlie\n3,25,女,13800000003,charlie\n"), "pi", "survey-c")
    with idb.identity_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates WHERE phone='13800000003'").fetchone()[0] == 1


def test_environment_binding_fails_on_shared_or_wrong_bucket(monkeypatch, tmp_path):
    config, _, _, _, _, _ = _reload_stack(monkeypatch, str(tmp_path))
    values = dict(light_storage_backend="oss", oss_region="cn-hongkong", oss_credential_mode="ecs_ram_role",
                  oss_bucket_test="test-bucket", oss_bucket_prod="prod-bucket")
    assert config.Settings(**values, oss_bucket="test-bucket").oss_bucket == "test-bucket"
    with pytest.raises(RuntimeError, match="selected runtime"):
        config.Settings(**values, oss_bucket="prod-bucket")
    with pytest.raises(RuntimeError, match="distinct"):
        config.Settings(**{**values, "oss_bucket_prod": "test-bucket"}, oss_bucket="test-bucket")
    with pytest.raises(RuntimeError, match="other environment"):
        config.Settings(**values, oss_bucket="test-bucket", backup_oss_bucket="prod-bucket")
    monkeypatch.setattr(config, "_RUNTIME_ENV", "prod")
    monkeypatch.setenv("OSS_BUCKET", "legacy-test-bucket")
    monkeypatch.setenv("OSS_BUCKET_TEST", "test-bucket")
    monkeypatch.setenv("OSS_BUCKET_PROD", "prod-bucket")
    assert config._isolated_bucket("OSS_BUCKET") == "prod-bucket"
    monkeypatch.setenv("OSS_BUCKET_PROD", "test-bucket")
    with pytest.raises(RuntimeError, match="different"):
        config._isolated_bucket("OSS_BUCKET")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY_PROD", raising=False)
    assert config._environment_value("CREDENTIAL_ENCRYPTION_KEY", legacy_test=True) == ""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY_TEST", "shared-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY_PROD", "shared-key")
    for environment in ("test", "prod"):
        monkeypatch.setattr(config, "_RUNTIME_ENV", environment)
        with pytest.raises(RuntimeError, match="must be different"):
            config._environment_value("CREDENTIAL_ENCRYPTION_KEY", legacy_test=True)
