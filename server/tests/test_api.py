import base64
import importlib
import os
import tempfile
from pathlib import Path


def test_end_to_end(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("DATA_DIR", td)
        monkeypatch.setenv("DB_PATH", str(Path(td)/"test.sqlite3"))
        monkeypatch.setenv("RAW_ARCHIVE_DIR", str(Path(td)/"raw"))
        monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")

        import app.core.config as config
        importlib.reload(config)
        import app.core.db as dbmod
        importlib.reload(dbmod)
        import app.core.security as sec
        importlib.reload(sec)
        import app.modules.gps.service as service
        importlib.reload(service)
        import app.modules.gps.router as router
        importlib.reload(router)
        import app.main as main
        importlib.reload(main)

        dbmod.init_db()
        salt, digest = sec.hash_secret("pw")
        with dbmod.db() as conn:
            conn.execute("INSERT INTO participants VALUES(?,?,?,?,?)", ("TEST01",salt,digest,1,"2026-01-01T00:00:00Z"))

        from fastapi.testclient import TestClient
        with TestClient(main.app) as client:
            auth = "Basic " + base64.b64encode(b"TEST01:pw").decode()
            payload={"_type":"location","_id":"abc123","tst":1786276022,"created_at":1786276022,"lat":30.4,"lon":103.4,"acc":6}
            r=client.post("/api/v1/gps/owntracks",json=payload,headers={"Authorization":auth})
            assert r.status_code==200
            r2=client.post("/api/v1/gps/owntracks",json=payload,headers={"Authorization":auth})
            assert r2.status_code==200
            h=client.get("/health").json()
            assert h["gps_location_count"]==1
            s=client.get("/api/v1/admin/gps/status/TEST01",headers={"Authorization":"Bearer admin-test-token"})
            assert s.status_code==200
            assert s.json()["location_count"]==1
