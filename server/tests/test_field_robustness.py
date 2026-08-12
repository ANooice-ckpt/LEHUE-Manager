import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from test_web_admin import _reload_stack


def _timed_light(start: datetime, minutes: int) -> bytes:
    end = start + timedelta(minutes=minutes)
    return (
        "Modify Time,Photopic Lux,Melanopic,Is Saturate\n"
        f"{start:%Y-%m-%d %H:%M:%S},100,80,No\n"
        f"{end:%Y-%m-%d %H:%M:%S},100,80,No\n"
    ).encode()


def test_lighting_time_range_and_short_acquisition_warning(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        config, dbmod, _, _, _, _ = _reload_stack(monkeypatch, td)
        import app.modules.light.service as light

        zone = ZoneInfo(config.settings.study_timezone)
        today = datetime.now(zone).date()
        exposure = today - timedelta(days=2)
        start = datetime.combine(exposure, datetime.min.time()).replace(hour=8)
        parsed = light.parse_light_bytes("short.csv", _timed_light(start, 120), exposure.isoformat())
        assert parsed["record_start"].startswith(exposure.isoformat())
        assert parsed["record_end"].startswith(exposure.isoformat())
        assert parsed["coverage_minutes"] == 120.0

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with dbmod.db() as conn:
            conn.execute(
                """INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc)
                   VALUES('001','running',?,?,?,?)""",
                (exposure.isoformat(), today.isoformat(), now, now),
            )
            for form in ("morning", "evening"):
                conn.execute(
                    """INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc)
                       VALUES('001',?,1,?,'{}',?)""",
                    (exposure.isoformat(), form, now),
                )
        light.store_upload("001", exposure.isoformat(), "short.csv", _timed_light(start, 120), "test")
        row = next(item for item in light.daily_qc_rows() if item["date_local"] == exposure.isoformat())
        assert row["light_coverage_minutes"] == 120.0
        assert any(item["type"] == "short_light_coverage" for item in row["issues"])


def test_portal_charge_and_morning_reminders_are_non_gate(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        config, dbmod, _, _, _, main = _reload_stack(monkeypatch, td)
        import app.modules.light.service as light
        import app.modules.participant.service as portal
        from fastapi.testclient import TestClient

        zone = ZoneInfo(config.settings.study_timezone)
        local_now = datetime.now(zone)
        morning_date = portal.form_target_date("morning", local_now)
        evening_date = portal.form_target_date("evening", local_now)
        start = min(morning_date, evening_date) - timedelta(days=1)
        end = max(morning_date, evening_date) + timedelta(days=1)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with dbmod.db() as conn:
            conn.execute(
                """INSERT INTO study_subjects(participant_id,status,start_date,end_date,created_at_utc,updated_at_utc)
                   VALUES('001','running',?,?,?,?)""",
                (start.isoformat(), end.isoformat(), now, now),
            )
            for form, target in (("morning", morning_date), ("evening", evening_date)):
                conn.execute(
                    """INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc)
                       VALUES('001',?,1,?,'{}',?)""",
                    (target.isoformat(), form, now),
                )
        raw_start = datetime.combine(evening_date, datetime.min.time()).replace(hour=8)
        light.store_upload("001", evening_date.isoformat(), "day.csv", _timed_light(raw_start, 600), "test")
        token = portal.generate_portal_token("001")

        with TestClient(main.app, base_url="http://127.0.0.1:8085") as client:
            state = client.get(f"/api/v1/portal/{token}").json()
            assert state["field_reminders"]["morning_restart"]["show"] is True
            assert state["field_reminders"]["morning_restart"]["completed"] is True
            charge = state["field_reminders"]["evening_charge"]
            assert charge["show"] is True
            assert "confirmed" not in charge and "date_local" not in charge


def test_running_device_replacement_preserves_history(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, dbmod, _, _, _, main = _reload_stack(monkeypatch, td)
        from fastapi.testclient import TestClient

        with TestClient(main.app, base_url="http://127.0.0.1:8085") as client:
            setup = client.post("/api/v1/web/setup", json={"username": "pi", "password": "strong-password-01"}).json()
            headers = {"X-CSRF-Token": setup["csrf_token"]}
            with dbmod.db() as conn:
                conn.execute(
                    """INSERT INTO study_subjects(participant_id,status,start_date,end_date,pack_id,created_at_utc,updated_at_utc)
                       VALUES('001','running','2026-09-01','2026-09-14','D03','now','now')"""
                )
                for pack, status, holder in (("D03", "in_use", "001"), ("D11", "available", ""), ("D12", "in_use", "002")):
                    conn.execute(
                        "INSERT INTO device_packs(pack_id,status,current_participant_id,updated_at_utc) VALUES(?,?,?,'now')",
                        (pack, status, holder),
                    )
                conn.execute(
                    """INSERT INTO device_assignments(participant_id,pack_id,effective_from_date,effective_from_utc,reason,assigned_by,created_at_utc)
                       VALUES('001','D03','2026-09-01','2026-09-01T00:00:00Z','initial','pi','now')"""
                )

            occupied = client.post(
                "/api/v1/web/subjects/001/replace-device",
                json={"pack_id": "D12", "effective_date": "2026-09-07", "reason": "fault"},
                headers=headers,
            )
            assert occupied.status_code == 400
            changed = client.post(
                "/api/v1/web/subjects/001/replace-device",
                json={"pack_id": "D11", "effective_date": "2026-09-07", "reason": "Lighting fault"},
                headers=headers,
            )
            assert changed.status_code == 200
            history = sorted(client.get("/api/v1/web/device-assignments?participant_id=001").json(), key=lambda row: row["effective_from_date"])
            assert [(row["pack_id"], row["effective_from_date"], row["effective_to_date"]) for row in history] == [
                ("D03", "2026-09-01", "2026-09-06"),
                ("D11", "2026-09-07", ""),
            ]
            devices = {row["pack_id"]: row for row in client.get("/api/v1/web/devices").json()}
            assert devices["D03"]["status"] == "returning"
            assert devices["D11"]["status"] == "in_use" and devices["D11"]["current_participant_id"] == "001"
