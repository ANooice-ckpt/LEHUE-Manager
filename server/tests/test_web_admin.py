import importlib
import io
import base64
import tempfile
import zipfile
from pathlib import Path


def _reload_stack(monkeypatch, td: str, domain: str = "localhost"):
    monkeypatch.setenv("DATA_DIR", td)
    monkeypatch.setenv("DB_PATH", str(Path(td) / "main.sqlite3"))
    monkeypatch.setenv("IDENTITY_DB_PATH", str(Path(td) / "identity.sqlite3"))
    monkeypatch.setenv("RAW_ARCHIVE_DIR", str(Path(td) / "raw"))
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token-for-test")
    monkeypatch.setenv("DOMAIN", domain)

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.core.security as sec; importlib.reload(sec)
    import app.core.web_security as ws; importlib.reload(ws)
    import app.modules.light.service as light; importlib.reload(light)
    import app.modules.questionnaire.s0_import as s0; importlib.reload(s0)
    import app.modules.gps.service as gps; importlib.reload(gps)
    import app.modules.participant.service as portal; importlib.reload(portal)
    import app.modules.participant.router as portal_router; importlib.reload(portal_router)
    import app.modules.admin.service as svc; importlib.reload(svc)
    import app.modules.admin.router as admin_router; importlib.reload(admin_router)
    import app.modules.gps.router as gps_router; importlib.reload(gps_router)
    import app.main as main; importlib.reload(main)
    dbmod.init_db(); idb.init_identity_db()
    return config, dbmod, idb, ws, svc, main


def test_web_admin_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        config, dbmod, idb, ws, svc, main = _reload_stack(monkeypatch, td)
        from fastapi.testclient import TestClient

        with TestClient(main.app, base_url='http://127.0.0.1:8085') as client:
            setup = client.get('/api/v1/web/setup-status')
            assert setup.status_code == 200
            assert setup.json() == {"initialized": False, "setup_token_required": False}

            r = client.post('/api/v1/web/setup', json={
                'username': 'pi', 'display_name': 'PI', 'password': 'strong-password-01'
            })
            assert r.status_code == 200
            csrf = r.json()['csrf_token']; h = {'X-CSRF-Token': csrf}

            r2 = client.post('/api/v1/web/setup', json={
                'username': 'other', 'password': 'another-strong-password'
            })
            assert r2.status_code == 409
            assert client.get('/api/v1/web/setup-status').json()['initialized'] is True

            r = client.post('/api/v1/web/users', json={
                'username': 'ra01', 'display_name': 'RA 01', 'role': 'ra', 'password': ''
            }, headers=h)
            assert r.status_code == 200 and len(r.json()['password']) >= 10
            ra_password = r.json()['password']
            users = client.get('/api/v1/web/users').json()
            assert {u['username'] for u in users} == {'pi', 'ra01'}

            s0_csv = '序号,您的年龄,您的性别,16、您是否愿意参与本研究并接受补贴？,19、您的手机号（仅实验负责人可见）：\n1,20-29,女,愿意,13800000001\n'.encode('utf-8-sig')
            s0_result = client.post('/api/v1/web/candidates/import-s0', json={
                'filename': 's0.csv', 'content_b64': base64.b64encode(s0_csv).decode()
            }, headers=h)
            assert s0_result.status_code == 200 and s0_result.json()['imported'] == 1

            r = client.post('/api/v1/web/candidates', json={'name':'Test','phone':'123','phone_os':'iOS'}, headers=h)
            assert r.status_code == 200; cuid = r.json()['candidate_uid']
            assert client.post('/api/v1/web/devices', json={'pack_id':'D01','status':'available','light_serial':'L01','ax3_serial':'A01'}, headers=h).status_code == 200
            assert client.post(f'/api/v1/web/candidates/{cuid}/promote', json={'participant_id':'001','expected_start':'2026-09-01','expected_end':'2026-09-14','pack_id':'D01','assigned_ra':'ra01'}, headers=h).status_code == 200
            r = client.post('/api/v1/web/subjects/001/gps-credential', json={}, headers=h)
            assert r.status_code == 200 and r.json()['password']
            gps_password = r.json()['password']
            # Participant portal can be generated before study start; no ID is embedded in the path.
            portal = client.post('/api/v1/web/subjects/001/portal', json={}, headers=h)
            assert portal.status_code == 200 and portal.json()['path'].startswith('/p/')
            assert '/001' not in portal.json()['path']
            credentials = client.get('/api/v1/web/subjects/001/credentials').json()
            assert credentials['gps_password'] == gps_password
            assert credentials['portal_path'] == portal.json()['path']
            assert credentials['gps_exists'] is True
            assert credentials['portal_exists'] is True
            rotated_gps = client.post('/api/v1/web/subjects/001/gps-credential', json={}, headers=h).json()['password']
            rotated_portal = client.post('/api/v1/web/subjects/001/portal', json={}, headers=h).json()['path']
            assert rotated_gps != gps_password
            assert rotated_portal != portal.json()['path']
            credentials2 = client.get('/api/v1/web/subjects/001/credentials').json()
            assert credentials2['gps_password'] == rotated_gps
            assert credentials2['portal_path'] == rotated_portal
            old_token = portal.json()['path'].removeprefix('/p/')
            assert client.get(f'/api/v1/portal/{old_token}').status_code == 404
            assert client.post('/api/v1/web/subjects/001/start', json={'pack_id':'D01','start_date':'2026-09-01','end_date':'2026-09-14'}, headers=h).status_code == 200
            light = client.post(
                '/api/v1/web/lighting/upload?participant_id=001&date_local=2026-09-01&filename=001_20260901_LIGHT.csv',
                content=b'Photopic Lux,Melanopic,Is Saturate\n100,80,No\n', headers=h,
            )
            assert light.status_code == 200 and light.json()['quality'] == 'insufficient'
            assert len(client.get('/api/v1/web/lighting').json()) == 1
            assert client.get('/api/v1/web/daily-qc').status_code == 200
            assert client.post('/api/v1/web/incidents', json={'participant_id':'001','date_local':'2026-09-02','source':'GPS','incident_type':'offline','summary':'GPS offline'}, headers=h).status_code == 200
            d = client.get('/api/v1/web/dashboard').json()
            assert d['metrics']['running'] == 1 and d['metrics']['open_incidents'] == 1
            assert client.get('/api/v1/web/data-sources').status_code == 200
            assert client.get('/api/v1/web/architecture').status_code == 200
            assert client.get('/admin').status_code == 200

            backup = client.get('/api/v1/web/backup')
            assert backup.status_code == 200
            with zipfile.ZipFile(io.BytesIO(backup.content)) as zf:
                assert {'lehue.sqlite3', 'lehue_identity.sqlite3', 'manifest.json'} <= set(zf.namelist())

            client.post('/api/v1/web/logout', json={}, headers=h)
            r = client.post('/api/v1/web/login', json={'username':'ra01','password':ra_password})
            assert r.status_code == 200
            ra_csrf = r.json()['csrf_token']; rh = {'X-CSRF-Token': ra_csrf}
            assert client.get('/api/v1/web/users').status_code == 403
            assert client.get('/api/v1/web/backup').status_code == 403
            assert client.post('/api/v1/web/users', json={'username':'x','role':'ra'}, headers=rh).status_code == 403


def test_public_first_setup_requires_server_token(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, _, _, _, _, main = _reload_stack(monkeypatch, td, domain='gps.example.com')
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url='https://gps.example.com') as client:
            s = client.get('/api/v1/web/setup-status').json()
            assert s == {"initialized": False, "setup_token_required": True}
            denied = client.post('/api/v1/web/setup', json={
                'username':'pi', 'password':'strong-password-01', 'setup_token':'wrong'
            })
            assert denied.status_code == 403
            ok = client.post('/api/v1/web/setup', json={
                'username':'pi', 'password':'strong-password-01', 'setup_token':'admin-token-for-test'
            })
            assert ok.status_code == 200
