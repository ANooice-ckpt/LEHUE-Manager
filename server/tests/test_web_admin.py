import importlib
import io
import base64
import json
import shutil
import tempfile
import zipfile
from pathlib import Path


def test_admin_field_ui_keeps_running_cards_groups_qc_and_defaults_ra():
    web_dir = Path(__file__).parents[1] / "app" / "web"
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")

    assert "$('pRa').value=me.username||''" in app_js
    assert "subject.status!=='running'" in app_js
    assert "subject.gps_last_received_at_utc" in app_js
    assert "actions.querySelector('[data-onboarding]')" in app_js
    assert "className='qc-date-group'" in app_js
    assert ".qc-date-group td" in style_css


def test_subject_list_filters_keep_id_bound_actions_and_use_consolidated_flows():
    web_dir = Path(__file__).parents[1] / "app" / "web"
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    index_html = (web_dir / "index.html").read_text(encoding="utf-8")

    assert '<option value="all" selected>全部状态</option>' in index_html
    assert 'data-participant-id="${esc(x.participant_id)}"' in app_js
    assert 'data-end-study="${esc(x.participant_id)}"' in app_js
    assert "data-end-study" in app_js and "openEndStudy(b.dataset.endStudy)" in app_js
    assert 'id="editReplaceDeviceBtn"' in index_html
    assert 'id="endStudyType"' in index_html
    assert '提前终止（被试退出等）' in index_html
    final_renderer = app_js[app_js.rfind("renderSubjectRows=function()") :]
    assert "data-complete" not in final_renderer
    assert "terminateSubject" not in final_renderer


def test_configuration_card_consolidates_credentials_and_running_progress():
    web_dir = Path(__file__).parents[1] / "app" / "web"
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    index_html = (web_dir / "index.html").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")

    assert 'id="credentialDialog"' not in index_html
    assert 'id="onboardingFacts"' not in index_html
    assert 'id="credGpsRotate"' in index_html
    assert 'id="credPortalRotate"' in index_html
    assert "function studyProgress(subject)" in app_js
    assert "subject.status!=='running'" in app_js
    assert "row.cells[3].innerHTML=studyProgress(subject)" in app_js
    final_renderer = app_js[app_js.rfind("const progressRenderSubjectRows") :]
    assert "subject?.status!=='running'" in final_renderer
    assert "actions.querySelector('[data-onboarding]')" in final_renderer
    assert "button.textContent='配置卡'" in final_renderer
    assert ".study-progress-track" in style_css


def _reload_stack(monkeypatch, td: str, domain: str = "localhost"):
    monkeypatch.setenv("DATA_DIR", td)
    monkeypatch.setenv("DATA_ROOT", td)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token-for-test")
    monkeypatch.setenv("DOMAIN", domain)

    import app.core.config as config; importlib.reload(config)
    import app.core.db as dbmod; importlib.reload(dbmod)
    import app.core.identity_db as idb; importlib.reload(idb)
    import app.core.security as sec; importlib.reload(sec)
    import app.core.web_security as ws; importlib.reload(ws)
    import app.core.state_bundle as state_bundle; importlib.reload(state_bundle)
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
            assert setup.json() == {"initialized": False, "setup_token_required": False, "setup_cli_required": False}

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
            edited = client.put(f'/api/v1/web/candidates/{cuid}', json={'name':'Edited','phone_os':'Android','availability':'周末'}, headers=h)
            assert edited.status_code == 200
            candidate = next(x for x in client.get('/api/v1/web/candidates').json() if x['candidate_uid'] == cuid)
            assert candidate['name'] == 'Edited' and candidate['phone_os'] == 'Android'
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
            ios_response = client.get('/api/v1/web/subjects/001/owntracks/ios')
            android_response = client.get('/api/v1/web/subjects/001/owntracks/android')
            assert ios_response.status_code == android_response.status_code == 200
            ios_config, android_config = ios_response.json(), android_response.json()
            expected_common = {
                '_type': 'configuration', 'mode': 3, 'auth': True,
                'url': 'http://127.0.0.1:8085/api/v1/gps/owntracks',
                'username': '001', 'password': gps_password, 'deviceId': '001', 'tid': '01',
                'monitoring': 2, 'locatorInterval': 10, 'locatorDisplacement': 100,
                'ignoreInaccurateLocations': 0, 'ignoreStaleLocations': 0,
                'extendedData': True, 'cmd': False, 'remoteConfiguration': False,
            }
            assert expected_common.items() <= ios_config.items()
            assert expected_common.items() <= android_config.items()
            assert ios_config['adapt'] == ios_config['downgrade'] == 0
            assert android_config['moveModeLocatorInterval'] == 10
            assert android_config['autostartOnBoot'] is True
            assert 'LEHUE_001_iOS.otrc' in ios_response.headers['content-disposition']
            rotated_gps = client.post('/api/v1/web/subjects/001/gps-credential', json={}, headers=h).json()['password']
            rotated_portal = client.post('/api/v1/web/subjects/001/portal', json={}, headers=h).json()['path']
            assert rotated_gps != gps_password
            assert rotated_portal != portal.json()['path']
            credentials2 = client.get('/api/v1/web/subjects/001/credentials').json()
            assert credentials2['gps_password'] == rotated_gps
            assert credentials2['portal_path'] == rotated_portal
            fresh_config = client.get('/api/v1/web/subjects/001/owntracks/ios').json()
            assert fresh_config['password'] == rotated_gps
            assert gps_password not in json.dumps(fresh_config)
            old_token = portal.json()['path'].removeprefix('/p/')
            assert client.get(f'/api/v1/portal/{old_token}').status_code == 404
            # Readiness itself is covered end-to-end in test_experiment_readiness;
            # this broader admin test continues from the formal-start boundary.
            with dbmod.db() as conn:
                conn.execute("UPDATE study_subjects SET status='ready',pack_id='D01' WHERE participant_id='001'")
                conn.execute("UPDATE device_packs SET status='in_use',current_participant_id='001' WHERE pack_id='D01'")
            started = client.post('/api/v1/web/subjects/001/start', json={'pack_id':'D01','start_date':'2026-09-01','end_date':'2026-09-14'}, headers=h)
            assert started.status_code == 200
            card = started.json()
            assert card['start_date'] == '2026-09-01' and card['pack_id'] == 'D01'
            assert card['portal_url'].startswith('http://127.0.0.1:8085/p/')
            assert card['owntracks_config']['mode'] == 3
            assert card['owntracks_config']['url'] == 'http://127.0.0.1:8085/api/v1/gps/owntracks'
            assert card['owntracks_config']['password'] == rotated_gps
            assert card['owntracks_uri'].startswith('owntracks:///config?inline=')
            inline = card['owntracks_uri'].split('inline=', 1)[1]
            assert base64.b64decode(inline).decode() == card['owntracks_config_json']
            assert 'LEHUE 入组信息' in card['contact_text']
            assert client.get('/api/v1/web/subjects/001/onboarding').json()['owntracks_config'] == card['owntracks_config']
            portal_token = rotated_portal.removeprefix('/p/')
            portal_state = client.get(f"/api/v1/portal/{portal_token}").json()
            assert portal_state['owntracks']['available'] is True
            assert portal_state['owntracks']['recommended_platform'] == 'android'
            portal_ios = client.get(f'/api/v1/portal/{portal_token}/owntracks/ios')
            portal_android = client.get(f'/api/v1/portal/{portal_token}/owntracks/android')
            assert portal_ios.json()['password'] == portal_android.json()['password'] == rotated_gps
            assert client.get('/api/v1/portal/not-a-valid-token/owntracks/ios').status_code == 404
            assert client.post('/api/v1/web/subjects/001/start', json={'pack_id':'D01','start_date':'2026-09-01','end_date':'2026-09-14'}, headers=h).status_code == 400
            assert client.post('/api/v1/web/subjects/001/start', json={'pack_id':'D01','start_date':'bad','end_date':'2026-09-14'}, headers=h).status_code == 400
            edited_subject = client.post('/api/v1/web/subjects/001', json={'batch_id':'B2','assigned_ra':'ra01','planned_start':'2026-09-01','planned_end':'2026-09-15','notes':'late return'}, headers=h)
            assert edited_subject.status_code == 200
            subject = next(x for x in client.get('/api/v1/web/subjects').json() if x['participant_id'] == '001')
            assert subject['batch_id'] == 'B2' and subject['start_date'] == '2026-09-01' and subject['end_date'] == '2026-09-15'
            assert 'study_day' in subject
            device = next(x for x in client.get('/api/v1/web/devices').json() if x['pack_id'] == 'D01')
            assert device['issued_date'] == '2026-09-01' and device['expected_return_date'] == '2026-09-15'
            assert client.post('/api/v1/web/devices', json={'pack_id':'D01','status':'repair'}, headers=h).status_code == 400
            invalid_period = client.post('/api/v1/web/subjects/001', json={'planned_start':'2026-09-16','planned_end':'2026-09-15'}, headers=h)
            assert invalid_period.status_code == 400
            track = client.get('/api/v1/web/subjects/001/gps-track?hours=12')
            assert track.status_code == 200
            assert track.json()['total_point_count'] == 0
            assert client.get('/api/v1/web/subjects/001/gps-track?hours=3').status_code == 400
            light = client.post(
                '/api/v1/web/lighting/upload?participant_id=001&date_local=2026-09-01&filename=001_20260901_LIGHT.csv',
                content=b'Photopic Lux,Melanopic,Is Saturate\n100,80,No\n', headers=h,
            )
            assert light.status_code == 200 and light.json()['quality'] == 'insufficient'
            rerun = client.post(f"/api/v1/web/lighting/{light.json()['upload_uid']}/qc", json={}, headers=h)
            assert rerun.status_code == 200 and rerun.json()['quality'] == 'insufficient'
            assert len(client.get('/api/v1/web/lighting').json()) == 1
            assert client.get('/api/v1/web/daily-qc').status_code == 200
            assert client.post('/api/v1/web/incidents', json={'participant_id':'001','date_local':'2026-09-02','source':'GPS','incident_type':'offline','summary':'GPS offline'}, headers=h).status_code == 200
            assert client.post('/api/v1/web/incidents', json={'participant_id':'001','date_local':'2026-09-02','source':'Lighting','incident_type':'missing','summary':'Lighting missing'}, headers=h).status_code == 200
            incident_group = client.get('/api/v1/web/incidents').json()[0]
            assert incident_group['participant_id'] == '001' and incident_group['date_local'] == '2026-09-02'
            assert incident_group['open_count'] == 2 and incident_group['issue_count'] == 2
            assert '有 2 项需要确认' in incident_group['contact_text']
            assert 'GPS offline' in incident_group['contact_text']
            assert 'Lighting missing' in incident_group['contact_text']
            incident_uid = incident_group['issues'][0]['incident_uid']
            assert client.post(f'/api/v1/web/incidents/{incident_uid}/status', json={'status':'handling'}, headers=h).status_code == 400
            with dbmod.db() as conn:
                conn.execute("UPDATE incidents SET status='handling' WHERE incident_uid=?", (incident_uid,))
            dbmod.init_db()
            assert client.get('/api/v1/web/incidents').json()[0]['status'] == 'open'
            with dbmod.db() as conn:
                conn.execute("UPDATE incidents SET status='resolved' WHERE incident_uid=?", (incident_uid,))
            dbmod.init_db()
            assert client.get('/api/v1/web/incidents').json()[0]['status'] == 'open'
            assert client.post(f'/api/v1/web/incidents/{incident_uid}/status', json={'status':'closed'}, headers=h).status_code == 200
            assert client.post(f'/api/v1/web/incidents/{incident_uid}/status', json={'status':'open'}, headers=h).status_code == 200
            d = client.get('/api/v1/web/dashboard').json()
            assert d['metrics']['running'] == 1 and d['metrics']['open_incidents'] == 2
            assert client.get('/api/v1/web/data-sources').status_code == 404
            assert client.get('/admin').status_code == 200
            assert client.get('/admin/vendor/leaflet.css').status_code == 200
            assert client.get('/admin/vendor/leaflet.js').status_code == 200

            with dbmod.db() as conn:
                conn.execute("UPDATE study_subjects SET final_end='2026-09-15',awaiting_final_morning=1 WHERE participant_id='001'")
                conn.execute("UPDATE device_packs SET status='returning' WHERE pack_id='D01'")
                conn.execute("INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES('001','2026-09-15',15,'morning','{}','now')")
                conn.execute("INSERT INTO questionnaire_responses(participant_id,date_local,study_day,form_key,answers_json,submitted_at_utc) VALUES('001','2026-09-15',15,'s2','{}','now')")
            completed = client.post('/api/v1/web/subjects/001/complete', json={}, headers=h)
            assert completed.status_code == 200 and completed.json()['status'] == 'completed'
            assert client.post('/api/v1/web/subjects/001/complete', json={}, headers=h).status_code == 400
            with dbmod.db() as conn:
                completed_subject = conn.execute("SELECT status FROM study_subjects WHERE participant_id='001'").fetchone()
                returned_device = conn.execute("SELECT status,current_participant_id,returned_date FROM device_packs WHERE pack_id='D01'").fetchone()
                credential = conn.execute("SELECT is_active FROM participants WHERE participant_id='001'").fetchone()
            assert completed_subject['status'] == 'completed'
            assert returned_device['status'] == 'returning' and returned_device['current_participant_id'] == '001' and not returned_device['returned_date']
            assert credential['is_active'] == 0
            portal_token = rotated_portal.removeprefix('/p/')
            portal_state = client.get(f'/api/v1/portal/{portal_token}')
            assert portal_state.status_code == 200 and portal_state.json()['read_only'] is True
            assert portal_state.json()['owntracks']['available'] is False
            gps_headers = {'Authorization': 'Basic ' + base64.b64encode(f'001:{rotated_gps}'.encode()).decode()}
            assert client.post('/api/v1/gps/owntracks', json={'_type':'location','_id':'after-complete','tst':1786276022,'lat':1,'lon':1}, headers=gps_headers).status_code == 403

            raw_path = config.settings.raw_archive_dir / '2026-08-11.jsonl'
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text('{"_type":"location"}\n', encoding='utf-8')
            backup = client.get('/api/v1/web/backup')
            assert backup.status_code == 200
            with zipfile.ZipFile(io.BytesIO(backup.content)) as zf:
                assert {'lehue.sqlite3', 'lehue_identity.sqlite3', 'manifest.json'} <= set(zf.namelist())
                assert 'gps_raw/2026-08-11.jsonl' in zf.namelist()

            import app.modules.admin.backup as backup_service
            importlib.reload(backup_service)
            archive_text, temp_dir_text = backup_service.create_system_backup(include_gps_raw=True)
            try:
                with zipfile.ZipFile(archive_text) as zf:
                    assert 'gps_raw/2026-08-11.jsonl' in zf.namelist()
            finally:
                shutil.rmtree(temp_dir_text)

            with idb.identity_db() as conn:
                conn.execute("UPDATE candidates SET name='changed-after-bundle' WHERE candidate_uid=?", (cuid,))
            restored = client.post(
                '/api/v1/web/state-bundle/restore', content=backup.content,
                headers={**h, 'Content-Type': 'application/zip'},
            )
            assert restored.status_code == 200
            assert restored.json()['credentials_reencrypted'] is True
            assert Path(restored.json()['rollback_state_bundle']).exists()
            with idb.identity_db() as conn:
                assert conn.execute("SELECT name FROM candidates WHERE candidate_uid=?", (cuid,)).fetchone()['name'] == 'Edited'

            client.post('/api/v1/web/logout', json={}, headers=h)
            r = client.post('/api/v1/web/login', json={'username':'ra01','password':ra_password})
            assert r.status_code == 200
            ra_csrf = r.json()['csrf_token']; rh = {'X-CSRF-Token': ra_csrf}
            assert client.get('/api/v1/web/users').status_code == 403
            assert client.get('/api/v1/web/backup').status_code == 403
            assert client.post(
                '/api/v1/web/state-bundle/restore', content=backup.content,
                headers={**rh, 'Content-Type': 'application/zip'},
            ).status_code == 403
            assert client.post('/api/v1/web/users', json={'username':'x','role':'ra'}, headers=rh).status_code == 403


def test_public_first_setup_requires_server_cli(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, _, _, _, _, main = _reload_stack(monkeypatch, td, domain='gps.example.com')
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url='https://gps.example.com') as client:
            s = client.get('/api/v1/web/setup-status').json()
            assert s == {"initialized": False, "setup_token_required": False, "setup_cli_required": True}
            denied = client.post('/api/v1/web/setup', json={
                'username':'pi', 'password':'strong-password-01', 'setup_token':'wrong'
            })
            assert denied.status_code == 403


def test_ready_start_creates_complete_onboarding_without_prior_credentials(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, dbmod, _, _, _, main = _reload_stack(monkeypatch, td)
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url='http://127.0.0.1:8085') as client:
            setup = client.post('/api/v1/web/setup', json={
                'username': 'pi', 'password': 'strong-password-01'
            }).json()
            headers = {'X-CSRF-Token': setup['csrf_token']}
            with dbmod.db() as conn:
                conn.execute(
                    "INSERT INTO study_subjects(participant_id,status,pack_id,created_at_utc,updated_at_utc) VALUES('002','ready','D02','now','now')"
                )
                conn.execute(
                    "INSERT INTO device_packs(pack_id,status,current_participant_id,updated_at_utc) VALUES('D02','in_use','002','now')"
                )
            response = client.post('/api/v1/web/subjects/002/start', json={
                'pack_id': 'D02', 'start_date': '2026-09-01', 'end_date': '2026-09-14'
            }, headers=headers)
            assert response.status_code == 200
            card = response.json()
            assert card['gps_password']
            assert card['portal_url'].startswith('http://127.0.0.1:8085/p/')
            assert card['owntracks_config']['password'] == card['gps_password']
            credentials = client.get('/api/v1/web/subjects/002/credentials').json()
            assert credentials['gps_exists'] is True and credentials['portal_exists'] is True


def test_credential_generation_runtime_failure_is_explicit(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        _, _, _, _, svc, main = _reload_stack(monkeypatch, td)
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url='http://127.0.0.1:8085') as client:
            setup = client.post('/api/v1/web/setup', json={'username':'pi','password':'strong-password-01'}).json()
            headers = {'X-CSRF-Token': setup['csrf_token']}
            with main.db() as conn:
                conn.execute("INSERT INTO study_subjects(participant_id,created_at_utc,updated_at_utc) VALUES('001','now','now')")
            monkeypatch.setattr(svc, 'create_or_rotate_gps_credential', lambda *_: (_ for _ in ()).throw(RuntimeError('kms unavailable')))
            response = client.post('/api/v1/web/subjects/001/gps-credential', json={}, headers=headers)
            assert response.status_code == 503
            assert response.json()['detail'] == 'GPS credential generation failed: kms unavailable'
