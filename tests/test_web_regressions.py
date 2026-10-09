#!/usr/bin/env python3
"""Web audit regressions. All credentials, accounts and HTTP calls are local fixtures."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import demo, paths, store, usage
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.oauth import CallbackServer
from codex_monitor.web import make_server


class WebRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-web-audit-')
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root/'main'), 'CODEX_ACCOUNTS_DIR': str(self.root/'accounts'), 'CODEX_MONITOR_DEMO': '0'})
        self.env.start()
        self.background = patch.object(Monitor, 'refresh_all')
        self.background.start()
        now = now_utc()
        self.auth = demo._auth('fixture@example.com', 'plus', 'acct-a', None, now+timedelta(days=2), now)
        self.write(paths.accounts_dir()/'a'/'auth.json', self.auth)
        self.write(paths.main_auth_path(), self.auth)
        self.monitor = Monitor()
        self.monitor.reload_profiles()
        self.server = make_server(self.monitor, port=0, token='audit-fixture')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(5); self.server.server_close()
        self.background.stop(); self.env.stop(); self.temp.cleanup()

    def write(self, path, auth):
        store.atomic_write(path, json.dumps(auth).encode())

    def request(self, endpoint, body=None, token='audit-fixture'):
        request = urllib.request.Request(self.base+endpoint, data=None if body is None else json.dumps(body).encode(), headers={'X-Token': token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_malformed_import_is_rejected_before_any_write(self):
        malformed = [ {'tokens': []}, {'tokens': ['x']}, {'tokens': {'access_token': 7}},
                      {'tokens': {'access_token': 'fixture', 'id_token': []}},
                      {**self.auth, 'last_refresh': []} ]
        for index, auth in enumerate(malformed):
            with self.subTest(auth=auth):
                name = 'invalid-'+str(index)
                status, _, _ = self.request('/api/import', {'name': name, 'text': json.dumps(auth)})
                self.assertEqual(status, 400)
                self.assertFalse((paths.accounts_dir()/name/'auth.json').exists())

    def test_string_force_flag_cannot_overwrite_credentials(self):
        before = (paths.accounts_dir()/'a'/'auth.json').read_bytes()
        replacement = {**self.auth, 'last_refresh': '2099-01-01T00:00:00Z'}
        status, _, _ = self.request('/api/import', {'name': 'a', 'text': json.dumps(replacement), 'force': 'false'})
        self.assertEqual(status, 400)
        self.assertEqual((paths.accounts_dir()/'a'/'auth.json').read_bytes(), before)

    def test_interval_rejects_fraction_boolean_and_out_of_range(self):
        for value in [True, 10.5, '15.5', 0, 601, {}, []]:
            with self.subTest(value=value):
                self.assertEqual(self.request('/api/settings', {'interval': value})[0], 400)
        self.assertEqual(self.request('/api/settings', {'interval': 15})[0], 200)
        self.assertEqual(self.monitor.interval, 15)

    def test_invalid_login_mode_never_starts_a_flow(self):
        with patch('codex_monitor.monitor.DeviceCodeLogin') as flow:
            self.assertEqual(self.request('/api/login/start', {'name': 'new', 'mode': 'invalid'})[0], 400)
            flow.assert_not_called()

    def test_login_finalization_is_once_per_success(self):
        self.monitor.login = SimpleNamespace(phase='success')
        self.monitor.login_name = 'a'
        with patch.object(self.monitor, 'reload_profiles') as reload:
            self.monitor.finish_login_if_done()
            self.monitor.finish_login_if_done()
            self.assertEqual(reload.call_count, 1)

    def test_token_refresh_does_not_overwrite_a_new_main_identity(self):
        other = demo._auth('other@example.com', 'plus', 'acct-b', None, now_utc()+timedelta(days=2), now_utc())
        def exchange(token):
            self.write(paths.main_auth_path(), other)
            return {'access_token': self.auth['tokens']['access_token'], 'refresh_token': 'new-fixture-refresh'}
        with patch('codex_monitor.monitor.refresh_tokens', side_effect=exchange):
            self.monitor.refresh_token('a')
        self.assertEqual(store.read_json(paths.main_auth_path()), other)

    def test_reset_updates_all_aliases_of_the_account(self):
        self.write(paths.accounts_dir()/'alias'/'auth.json', self.auth)
        self.monitor.reload_profiles()
        for state in self.monitor.states.values():
            state.usage = {'rate_limit': {'primary_window': {'used_percent': 100, 'limit_window_seconds': 18000}}}
            state.credits = {'available_count': 3}
        quota = {'rate_limit': {'primary_window': {'used_percent': 0, 'limit_window_seconds': 18000}}}
        with patch.object(usage, 'consume_reset_credit', return_value={'code':'reset','windows_reset':1}), patch.object(usage, 'fetch_usage_auto', return_value=(quota,self.auth,False)), patch.object(usage, 'fetch_reset_credits', return_value={'available_count':2}):
            self.monitor.use_reset_credit('a',str(uuid.uuid4()))
        for account in self.monitor.snapshot()['accounts']:
            self.assertEqual(account['reset_credits'],2)
            self.assertEqual(account['windows'][0]['remaining_percent'],100)

    def test_auto_refresh_does_not_overwrite_a_new_main_identity(self):
        other = demo._auth('other@example.com','plus','acct-b',None,now_utc()+timedelta(days=2),now_utc())
        def exchange(token):
            self.write(paths.main_auth_path(),other)
            return {'access_token':self.auth['tokens']['access_token'],'refresh_token':'rotated-fixture'}
        with patch.object(usage,'fetch_usage',side_effect=[usage.UsageError('expired',401),{'plan_type':'plus'}]), patch('codex_monitor.oauth.refresh_tokens',side_effect=exchange):
            usage.fetch_usage_auto(paths.accounts_dir()/'a'/'auth.json',also_main=True)
        self.assertEqual(store.read_json(paths.main_auth_path()),other)

    def test_parallel_manual_refresh_only_exchanges_once(self):
        entered,release=threading.Event(),threading.Event();results=[]
        def exchange(token):
            entered.set()
            if not release.wait(5): raise AssertionError('refresh not released')
            return {'access_token':self.auth['tokens']['access_token'],'refresh_token':'rotated-fixture'}
        def first():
            try:results.append(self.monitor.refresh_token('a'))
            except Exception as error:results.append(error)
        with patch('codex_monitor.monitor.refresh_tokens',side_effect=exchange) as refresh:
            worker=threading.Thread(target=first);worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(store.StoreError):self.monitor.refresh_token('a')
            finally:release.set();worker.join(5)
            self.assertEqual(refresh.call_count,1)
        self.assertEqual(results,['refreshed'])

    def test_malformed_existing_profile_does_not_break_dashboard(self):
        self.write(paths.accounts_dir()/'broken'/'auth.json',{'tokens':['invalid']})
        self.monitor.reload_profiles()
        self.monitor.refresh_one('broken')
        status, _, body=self.request('/api/state')
        self.assertEqual(status,200)
        accounts={account['name']:account for account in json.loads(body)['accounts']}
        self.assertEqual(accounts['broken']['availability'],'signed_out')
        self.assertIn('a',accounts)

    def test_existing_account_names_remain_addressable(self):
        name='work@example.com'
        self.write(paths.accounts_dir()/name/'auth.json',self.auth)
        self.monitor.reload_profiles()
        status, _, _=self.request('/api/annotations',{'name':name,'tags':['fixture'],'unavailable':False})
        self.assertEqual(status,200)
        with patch.object(self.monitor,'refresh_one') as refresh:
            self.assertEqual(self.request('/api/refresh',{'name':name})[0],200)
            refresh.assert_called_once_with(name)
        self.assertEqual(self.request('/api/import',{'name':name,'text':json.dumps(self.auth),'force':True})[0],200)
        self.assertFalse((paths.accounts_dir()/'work-example.com').exists())
        with patch.object(store,'codex_processes_running',return_value=False):
            self.assertEqual(self.request('/api/switch',{'name':name})[0],200)
        self.assertEqual(self.request('/api/remove',{'name':name,'revision':store.removal_revision(name)})[0],200)
        self.assertTrue(paths.main_auth_path().exists())

    def test_auth_download_is_not_cacheable(self):
        status, headers, _ = self.request('/api/auth/a?download=1')
        self.assertEqual(status,200)
        self.assertEqual(headers.get('Cache-Control'),'no-store')

    def test_oauth_error_callback_renders_plain_text(self):
        server = CallbackServer(port=0)
        worker = threading.Thread(target=server.handle_request,daemon=True);worker.start()
        try:
            query = urllib.parse.urlencode({'error':'<img src=x onerror=alert(1)>', 'state':'fixture'})
            with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/auth/callback?'+query,timeout=5) as response:
                html = response.read().decode()
            self.assertNotIn('<img',html)
            self.assertIn('&lt;img',html)
        finally:
            worker.join(5);server.server_close()

if __name__ == '__main__': unittest.main(verbosity=2)
