import base64
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from test_web_admin import _reload_stack


def test_ready_start_and_device_return_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        config, dbmod, _, _, _, main = _reload_stack(monkeypatch, td)
        from fastapi.testclient import TestClient

        with TestClient(main.app, base_url="http://127.0.0.1:8085") as client:
            setup = client.post(
                "/api/v1/web/setup",
                json={"username": "pi", "password": "strong-password-01"},
            ).json()
            headers = {"X-CSRF-Token": setup["csrf_token"]}
            with dbmod.db() as conn:
                for participant_id in ("001", "002"):
                    conn.execute(
                        """INSERT INTO study_subjects(
                               participant_id,status,expected_start,expected_end,created_at_utc,updated_at_utc
                           ) VALUES(?,?,?,?,?,?)""",
                        (participant_id, "scheduled", "2026-09-01", "2026-09-14", "now", "now"),
                    )
                conn.execute(
                    "INSERT INTO device_packs(pack_id,status,updated_at_utc) VALUES('D01','available','now')"
                )

            prepared = client.post(
                "/api/v1/web/subjects/001/prepare",
                json={"pack_id": "D01", "delivery_method": "快递", "tracking_number": "TRACK-1"},
                headers=headers,
            )
            assert prepared.status_code == 200
            card = prepared.json()

            # The formal boundary is closed until both real test returns exist.
            not_ready = client.post(
                "/api/v1/web/subjects/001/start",
                json={"start_date": "2026-09-01", "end_date": "2026-09-14"},
                headers=headers,
            )
            assert not_ready.status_code == 400

            unavailable = client.post(
                "/api/v1/web/subjects/002/prepare", json={"pack_id": "D01"}, headers=headers
            )
            assert unavailable.status_code == 400

            gps_headers = {
                "Authorization": "Basic "
                + base64.b64encode(f"001:{card['gps_password']}".encode()).decode()
            }
            gps = client.post(
                "/api/v1/gps/owntracks",
                json={"_type": "location", "_id": "ready-test", "tst": int(time.time()), "lat": 39.9, "lon": 116.4},
                headers=gps_headers,
            )
            assert gps.status_code == 200

            token = card["portal_url"].rsplit("/", 1)[1]
            today = config.settings.study_timezone and client.get(f"/api/v1/portal/{token}").json()["date_local"]
            unreadable = client.post(
                f"/api/v1/portal/{token}/lighting?date_local={today}&filename=bad.csv",
                content=b"not,a,lighting,file\n",
            )
            assert unreadable.status_code == 200
            assert next(x for x in client.get("/api/v1/web/subjects").json() if x["participant_id"] == "001")["status"] == "scheduled"
            lighting = client.post(
                f"/api/v1/portal/{token}/lighting?date_local={today}&filename=test.csv",
                content=b"Photopic Lux,Melanopic,Is Saturate\n100,80,No\n",
            )
            assert lighting.status_code == 200

            s1 = client.post(
                f"/api/v1/portal/{token}/questionnaires/s1",
                json={"answers": {
                    "birth_month": "1995-01", "height_cm": 170, "weight_kg": 65,
                    "subjective_status": 5, "vision_correction": "none", "baseline_notes": "无",
                }},
            )
            assert s1.status_code == 200

            subject = next(x for x in client.get("/api/v1/web/subjects").json() if x["participant_id"] == "001")
            assert subject["status"] == "ready"
            assert subject["gps_test_received"] and subject["lighting_test_uploaded"]

            assert client.post(
                "/api/v1/web/subjects/001/start",
                json={"pack_id": "D99", "start_date": "2026-09-01", "end_date": "2026-09-14"},
                headers=headers,
            ).status_code == 400
            started = client.post(
                "/api/v1/web/subjects/001/start",
                json={"start_date": "2026-09-01", "end_date": "2026-09-14"},
                headers=headers,
            )
            assert started.status_code == 200

            with dbmod.db() as conn:
                conn.execute("UPDATE study_subjects SET start_date=? WHERE participant_id='001'", (datetime.now(ZoneInfo(config.settings.study_timezone)).date().isoformat(),))
            assert client.post("/api/v1/web/subjects/001/end-exposure", json={"final_end": datetime.now(ZoneInfo(config.settings.study_timezone)).date().isoformat()}, headers=headers).status_code == 200
            closing = client.get(f"/api/v1/portal/{token}").json()
            assert closing["status"] == "running" and closing["read_only"] is False
            assert any(form["key"] == "s2" for form in closing["forms"])
            assert client.post("/api/v1/web/subjects/001/complete", json={}, headers=headers).status_code == 400
            device = next(x for x in client.get("/api/v1/web/devices").json() if x["pack_id"] == "D01")
            assert device["status"] == "returning" and device["current_participant_id"] == "001"
            assert client.post(
                "/api/v1/web/subjects/002/prepare", json={"pack_id": "D01"}, headers=headers
            ).status_code == 400

            returned = client.post(
                "/api/v1/web/devices/D01/flow", json={"action": "confirm-returned"}, headers=headers
            )
            assert returned.status_code == 200 and returned.json()["status"] == "returned"
            assert client.post(
                "/api/v1/web/subjects/002/prepare", json={"pack_id": "D01"}, headers=headers
            ).status_code == 400

            available = client.post(
                "/api/v1/web/devices/D01/flow", json={"action": "make-available"}, headers=headers
            )
            assert available.status_code == 200 and available.json()["status"] == "available"
            assert client.post(
                "/api/v1/web/subjects/002/prepare", json={"pack_id": "D01"}, headers=headers
            ).status_code == 200
            cancelled = client.post(
                "/api/v1/web/subjects/002/cancel-preparation",
                json={"reason": "teaching failed", "device_sent": False}, headers=headers,
            )
            assert cancelled.status_code == 200 and cancelled.json()["pack_status"] == "available"
