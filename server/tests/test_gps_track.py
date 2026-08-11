import importlib
from datetime import datetime, timedelta, timezone


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STUDY_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("QC_GAP_WARNING_SECONDS", "300")
    monkeypatch.setenv("QC_POOR_ACCURACY_M", "50")
    monkeypatch.setenv("GPS_DAILY_MIN_POINTS", "5")
    monkeypatch.setenv("GPS_DAILY_EDGE_COVERAGE_HOURS", "2")
    monkeypatch.setenv("GPS_DAILY_MAX_GAP_SECONDS", str(7 * 3600))
    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.modules.gps.service as gps; importlib.reload(gps)
    dbmod.init_db()
    return dbmod, gps


def test_track_diagnostic_scans_qc_and_downsamples(monkeypatch, tmp_path):
    dbmod, gps = _reload(monkeypatch, tmp_path)
    now = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)
    times = [now - timedelta(minutes=30) + timedelta(seconds=5 * i) for i in range(40)]
    times += [times[-1] + timedelta(seconds=600 + 5 * i) for i in range(1, 21)]
    with dbmod.db() as conn:
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,created_at_utc) VALUES('001','salt','hash',?)",
            (gps.iso_utc(now),),
        )
        conn.execute(
            "INSERT INTO study_subjects(participant_id,status,created_at_utc,updated_at_utc) VALUES('001','running',?,?)",
            (gps.iso_utc(now), gps.iso_utc(now)),
        )
        for index, recorded in enumerate(times):
            cur = conn.execute(
                """INSERT INTO raw_events(participant_id,event_uid,message_type,recorded_at_utc,received_at_utc,raw_json)
                   VALUES(?,?,?,?,?,'{}')""",
                ("001", f"track-{index}", "location", gps.iso_utc(recorded), gps.iso_utc(recorded)),
            )
            conn.execute(
                """INSERT INTO gps_locations(raw_event_id,participant_id,recorded_at_utc,received_at_utc,lat,lon,accuracy_m)
                   VALUES(?,?,?,?,?,?,?)""",
                (cur.lastrowid, "001", gps.iso_utc(recorded), gps.iso_utc(recorded), 39.9 + index / 10000, 116.4, 80 if index % 2 else 10),
            )

    result = gps.track_diagnostic("001", 1, now=now)

    assert result["total_point_count"] == len(times)
    assert result["latest_recorded_at_utc"] == gps.iso_utc(times[-1])
    assert result["max_gap_seconds"] == 605.0
    assert result["poor_accuracy_percentage"] == 50.0
    assert len(result["points"]) < len(times)
    assert result["points"][0]["recorded_at_utc"] == gps.iso_utc(times[0])
    assert result["points"][-1]["recorded_at_utc"] == gps.iso_utc(times[-1])
    assert sum(bool(point["break_before"]) for point in result["points"]) == 1


def test_raw_archive_filename_uses_study_timezone_day(monkeypatch, tmp_path):
    _, gps = _reload(monkeypatch, tmp_path)
    received = datetime(2026, 8, 10, 16, 30, tzinfo=timezone.utc)

    assert gps._append_raw_archive({"server_received_at_utc": gps.iso_utc(received)}, received)

    archive = gps.settings.raw_archive_dir / "2026-08-11.jsonl"
    assert archive.is_file()
    assert "2026-08-10T16:30:00Z" in archive.read_text(encoding="utf-8")
    assert not (gps.settings.raw_archive_dir / "2026-08-10.jsonl").exists()


def test_auth_cache_skips_pbkdf2_and_reset_invalidates(monkeypatch, tmp_path):
    dbmod, gps = _reload(monkeypatch, tmp_path)
    import app.core.security as security

    salt, digest = security.hash_secret("old-password")
    with dbmod.db() as conn:
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,created_at_utc) VALUES('001',?,?,?)",
            (salt, digest, gps.iso_utc(datetime.now(timezone.utc))),
        )
    calls = 0
    original = gps.verify_secret

    def counted(secret, stored_salt, stored_hash):
        nonlocal calls
        calls += 1
        return original(secret, stored_salt, stored_hash)

    monkeypatch.setattr(gps, "verify_secret", counted)
    assert gps.authenticate_participant("001", "old-password")
    assert gps.authenticate_participant("001", "old-password")
    assert calls == 1

    new_salt, new_digest = security.hash_secret("new-password")
    with dbmod.db() as conn:
        conn.execute(
            "UPDATE participants SET secret_salt=?,secret_hash=? WHERE participant_id='001'",
            (new_salt, new_digest),
        )
    assert not gps.authenticate_participant("001", "old-password")
    assert gps.authenticate_participant("001", "new-password")
    assert calls == 3


def test_backfill_state_and_daily_acquisition_coverage(monkeypatch, tmp_path):
    dbmod, gps = _reload(monkeypatch, tmp_path)
    now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    backfill = gps.online_state(gps.iso_utc(now), gps.iso_utc(now - timedelta(hours=1)), now=now)
    assert backfill["status"] == "backfilling"
    assert backfill["delivery_delay_seconds"] == 3600
    assert gps.online_state(gps.iso_utc(now), gps.iso_utc(now - timedelta(seconds=20)), now=now)["status"] == "live"

    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    offsets = [0.5, 6, 12, 18, 23.5]
    with dbmod.db() as conn:
        conn.execute(
            "INSERT INTO participants(participant_id,secret_salt,secret_hash,created_at_utc) VALUES('001','s','h',?)",
            (gps.iso_utc(now),),
        )
        for index, hours in enumerate(offsets):
            recorded = start + timedelta(hours=hours)
            cur = conn.execute(
                "INSERT INTO raw_events(participant_id,event_uid,message_type,recorded_at_utc,received_at_utc,raw_json) VALUES(?,?,?,?,?,'{}')",
                ("001", f"coverage-{index}", "location", gps.iso_utc(recorded), gps.iso_utc(recorded)),
            )
            conn.execute(
                "INSERT INTO gps_locations(raw_event_id,participant_id,recorded_at_utc,received_at_utc,lat,lon) VALUES(?,?,?,?,0,0)",
                (cur.lastrowid, "001", gps.iso_utc(recorded), gps.iso_utc(recorded)),
            )
    summary = gps.daily_acquisition_summary("001", gps.iso_utc(start), gps.iso_utc(start + timedelta(days=1)))
    assert summary["complete"] is True
    assert summary["point_count"] == 5
    assert summary["max_gap_seconds"] <= 7 * 3600

    with dbmod.db() as conn:
        conn.execute("DELETE FROM gps_locations WHERE raw_event_id NOT IN (SELECT MIN(raw_event_id) FROM gps_locations)")
    sparse = gps.daily_acquisition_summary("001", gps.iso_utc(start), gps.iso_utc(start + timedelta(days=1)))
    assert sparse["complete"] is False
    assert "low_point_count" in sparse["issues"]
    assert "early_last_point" in sparse["issues"]
