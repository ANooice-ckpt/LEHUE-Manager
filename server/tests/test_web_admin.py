import importlib
import tempfile
from pathlib import Path


def test_web_admin_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("DATA_DIR", td)
        monkeypatch.setenv("DB_PATH", str(Path(td)/"main.sqlite3"))
        monkeypatch.setenv("IDENTITY_DB_PATH", str(Path(td)/"identity.sqlite3"))
        monkeypatch.setenv("RAW_ARCHIVE_DIR", str(Path(td)/"raw"))
        monkeypatch.setenv("ADMIN_TOKEN", "admin-token")
        monkeypatch.setenv("DOMAIN", "localhost")

        import app.core.config as config; importlib.reload(config)
        import app.core.db as dbmod; importlib.reload(dbmod)
        import app.core.identity_db as idb; importlib.reload(idb)
        import app.core.security as sec; importlib.reload(sec)
        import app.core.web_security as ws; importlib.reload(ws)
        import app.modules.gps.service as gps; importlib.reload(gps)
        import app.modules.admin.service as svc; importlib.reload(svc)
        import app.modules.admin.router as admin_router; importlib.reload(admin_router)
        import app.modules.gps.router as gps_router; importlib.reload(gps_router)
        import app.main as main; importlib.reload(main)

        dbmod.init_db(); idb.init_identity_db(); ws.create_admin_user("pi","pw","pi","PI")
        from fastapi.testclient import TestClient
        with TestClient(main.app) as client:
            r=client.post('/api/v1/web/login',json={'username':'pi','password':'pw'})
            assert r.status_code==200
            csrf=r.json()['csrf_token']; h={'X-CSRF-Token':csrf}
            r=client.post('/api/v1/web/candidates',json={'name':'Test','phone':'123','phone_os':'iOS'},headers=h)
            assert r.status_code==200; cuid=r.json()['candidate_uid']
            r=client.post('/api/v1/web/devices',json={'pack_id':'D01','status':'available','light_serial':'L01','ax3_serial':'A01'},headers=h)
            assert r.status_code==200
            r=client.post(f'/api/v1/web/candidates/{cuid}/promote',json={'participant_id':'001','expected_start':'2026-09-01','expected_end':'2026-09-14','pack_id':'D01','assigned_ra':'ra1'},headers=h)
            assert r.status_code==200
            r=client.post('/api/v1/web/subjects/001/gps-credential',json={},headers=h)
            assert r.status_code==200 and r.json()['password']
            r=client.post('/api/v1/web/subjects/001/start',json={'pack_id':'D01','start_date':'2026-09-01','end_date':'2026-09-14'},headers=h)
            assert r.status_code==200
            r=client.post('/api/v1/web/incidents',json={'participant_id':'001','date_local':'2026-09-02','source':'GPS','incident_type':'offline','summary':'GPS offline'},headers=h)
            assert r.status_code==200
            d=client.get('/api/v1/web/dashboard').json()
            assert d['metrics']['running']==1 and d['metrics']['open_incidents']==1
            assert client.get('/api/v1/web/data-sources').status_code==200
            assert client.get('/api/v1/web/architecture').status_code==200
            assert client.get('/admin').status_code==200
