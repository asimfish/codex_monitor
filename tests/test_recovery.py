"""Credential integrity, quota ordering and durable recovery; fabricated accounts only."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import demo, oauth, paths, store, usage
from codex_monitor.identity import identity, now_utc
from codex_monitor.locking import file_lock
from codex_monitor.monitor import Monitor
from codex_monitor.reset_ledger import ResetLedger


class RecoveryRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-recovery-test-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            "CODEX_HOME": str(root / "main"),
            "CODEX_ACCOUNTS_DIR": str(root / "accounts"),
            "CODEX_MONITOR_DEMO": "0",
        })
        env.start()
        self.addCleanup(env.stop)
        self.auth = self.make_auth("first")
        store.import_text("profile", json.dumps(self.auth))
        self.m = Monitor()
        self.m.reload_profiles()
        self.auth_path = store.get_profile("profile").auth_path
        self.quota = {"plan_type": "pro", "rate_limit": {
            "primary_window": {"used_percent": 10, "limit_window_seconds": 18000}}}

    def make_auth(self, account):
        now = now_utc()
        return demo._auth(account + "@example.test", "pro", "acct-" + account,
                          None, now + timedelta(days=3), now)

    def old_exchange(self):
        flow = oauth.BrowserLogin(self.auth_path.parent)
        flow.phase = "waiting"
        flow._server = Mock()
        flow._server.wait.return_value = {"state": flow.state, "code": "old-code"}
        self.m.login, self.m.login_name = flow, "profile"
        worker = threading.Thread(target=flow._wait_and_finish)
        return flow, worker

    def test_cancelled_exchange_cannot_overwrite_new_login(self):
        old_tokens = self.make_auth("old")["tokens"]
        new_tokens = self.make_auth("new")["tokens"]
        entered, release = threading.Event(), threading.Event()

        def exchange(code, verifier):
            if code == "old-code":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("exchange gate not released")
                return old_tokens
            return new_tokens

        old, worker = self.old_exchange()
        with patch.object(oauth, "exchange_code", side_effect=exchange), patch.object(self.m, "refresh_all"):
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.m.cancel_login()
                new = oauth.BrowserLogin(self.auth_path.parent)
                new._server = Mock()
                new._server.wait.return_value = {"state": new.state, "code": "new-code"}

                def start_new():
                    new.phase = "waiting"
                    new._wait_and_finish()

                with patch("codex_monitor.monitor.BrowserLogin", return_value=new), patch.object(new, "start", side_effect=start_new):
                    self.m.start_login("profile", relogin=True)
                self.assertEqual(identity(store.read_json(self.auth_path))["account_id"], "acct-new")
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(old.phase, "cancelled")
        self.assertEqual(identity(store.read_json(self.auth_path))["account_id"], "acct-new")

    def test_cancelled_exchange_cannot_recreate_a_removed_profile(self):
        entered, release = threading.Event(), threading.Event()

        def exchange(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("exchange gate not released")
            return self.make_auth("old")["tokens"]

        _, worker = self.old_exchange()
        with patch.object(oauth, "exchange_code", side_effect=exchange):
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.m.cancel_login()
                self.m.remove_account("profile", store.removal_revision("profile"))
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.auth_path.parent.exists())

    def test_failed_device_relogin_is_not_success(self):
        before = self.auth_path.read_bytes()
        flow = oauth.DeviceCodeLogin(self.auth_path.parent)
        flow.phase = "waiting"
        flow._proc = SimpleNamespace(stdout=[b"authorization failed\n"], wait=lambda: 1)
        flow._pump()
        self.assertEqual(flow.phase, "failed")
        self.assertEqual(self.auth_path.read_bytes(), before)

    def device_flow(self, returncode):
        flow = oauth.DeviceCodeLogin(self.auth_path.parent)
        process = Mock(stdout=[], wait=Mock(return_value=returncode), poll=Mock(return_value=returncode))
        with patch.object(oauth, "find_codex", return_value="fixture-codex"), patch.object(oauth.subprocess, "Popen", return_value=process), patch.object(oauth.threading, "Thread"):
            flow.start()
        self.assertEqual(flow.phase, "waiting")
        staged = Path(flow._staging.name) / "auth.json"
        self.assertNotEqual(staged, self.auth_path)
        return flow, staged

    def test_successful_device_login_publishes_staged_credentials(self):
        flow, staged = self.device_flow(0)
        fresh = self.make_auth("new")
        store.atomic_write(staged, json.dumps(fresh).encode())
        self.assertEqual(store.read_json(self.auth_path), self.auth)
        flow._pump()
        self.assertEqual(flow.phase, "success")
        self.assertEqual(store.read_json(self.auth_path), fresh)
        self.assertFalse(staged.parent.exists())

    def test_device_failure_preserves_credentials_even_if_cli_wrote_a_file(self):
        flow, staged = self.device_flow(1)
        store.atomic_write(staged, json.dumps(self.make_auth("new")).encode())
        flow._pump()
        self.assertEqual(flow.phase, "failed")
        self.assertEqual(store.read_json(self.auth_path), self.auth)
        self.assertFalse(staged.parent.exists())

    def test_cancelled_device_login_cannot_publish_late_credentials(self):
        flow, staged = self.device_flow(0)
        flow.cancel()
        store.atomic_write(staged, json.dumps(self.make_auth("new")).encode())
        flow._pump()
        self.assertEqual(flow.phase, "cancelled")
        self.assertEqual(store.read_json(self.auth_path), self.auth)
        self.assertFalse(staged.parent.exists())

    def test_import_main_name_does_not_hide_account(self):
        store.atomic_write(paths.main_auth_path(), json.dumps(self.make_auth("active")).encode())
        store.import_text("main", json.dumps(self.make_auth("archived")))
        self.m.reload_profiles()
        accounts = self.m.snapshot()["accounts"]
        self.assertCountEqual([a["account_id"] for a in accounts], ["acct-active", "acct-archived", "acct-first"])
        self.assertEqual(len({a["name"] for a in accounts}), 3)
        current = next(a for a in accounts if a["is_main"])
        self.assertEqual(json.loads(self.m.auth_text(current["name"])), store.read_json(paths.main_auth_path()))
        self.assertEqual(json.loads(self.m.auth_text("main"))["tokens"]["account_id"], "acct-archived")

    def test_out_of_order_poll_cannot_overwrite_new_usage(self):
        for fail_old in (False, True):
            with self.subTest(fail_old=fail_old):
                entered, release = threading.Event(), threading.Event()

                def fetch(*args, **kwargs):
                    if threading.current_thread() is old:
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("poll gate not released")
                        if fail_old:
                            raise usage.UsageError("old network failure")
                        return {**self.quota, "marker": "old"}, self.auth, False
                    return {**self.quota, "marker": "new"}, self.auth, False

                old = threading.Thread(target=self.m.refresh_one, args=("profile",))
                with patch.object(usage, "fetch_usage_auto", side_effect=fetch), patch.object(usage, "fetch_reset_credits", return_value=None):
                    old.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        self.m.refresh_one("profile")
                    finally:
                        release.set()
                        old.join(5)
                self.assertFalse(old.is_alive())
                self.assertEqual(self.m.states["profile"].usage["marker"], "new")
                self.assertIsNone(self.m.states["profile"].error)
                self.assertEqual(usage.load_cache("acct-first")[0]["marker"], "new")

    def test_completed_poll_is_published_while_a_newer_poll_is_pending(self):
        first_entered, second_entered = threading.Event(), threading.Event()
        release_first, release_second = threading.Event(), threading.Event()
        failures = []

        def fetch(*args, **kwargs):
            if threading.current_thread() is first:
                first_entered.set()
                gate, marker = release_first, "first"
            else:
                second_entered.set()
                gate, marker = release_second, "second"
            if not gate.wait(5):
                raise AssertionError("poll gate not released")
            return {**self.quota, "marker": marker}, self.auth, False

        def poll():
            try:
                self.m.refresh_one("profile")
            except Exception as error:
                failures.append(error)

        first, second = threading.Thread(target=poll), threading.Thread(target=poll)
        with patch.object(usage, "fetch_usage_auto", side_effect=fetch), patch.object(usage, "fetch_reset_credits", return_value=None):
            first.start()
            try:
                self.assertTrue(first_entered.wait(5))
                second.start()
                self.assertTrue(second_entered.wait(5))
                release_first.set()
                first.join(5)
                self.assertEqual(self.m.states["profile"].usage["marker"], "first")
            finally:
                release_first.set()
                release_second.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(self.m.states["profile"].usage["marker"], "second")

    def test_parallel_auto_refresh_updates_copies_with_one_exchange(self):
        now = now_utc()
        expired = demo._auth("first@example.test", "pro", "acct-first", None, now - timedelta(days=1), now)
        store.import_text("profile", json.dumps(expired), force=True)
        store.import_text("alias", json.dumps(expired))
        self.m.reload_profiles()
        fresh = self.make_auth("first")["tokens"]
        fresh["refresh_token"] = "rotated-fixture"
        barrier, failures = threading.Barrier(2), []

        def fetch(auth):
            if auth["tokens"]["refresh_token"] != "rotated-fixture":
                barrier.wait(5)
                raise usage.UsageError("expired", 401)
            return self.quota

        def poll(name):
            try:
                self.m.refresh_one(name)
            except Exception as error:
                failures.append(error)

        with patch.object(usage, "fetch_usage", side_effect=fetch), patch.object(oauth, "refresh_tokens", return_value=fresh) as refresh, patch.object(usage, "fetch_reset_credits", return_value=None):
            workers = [threading.Thread(target=poll, args=(name,)) for name in ("profile", "alias")]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(refresh.call_count, 1)
        for account in self.m.snapshot()["accounts"]:
            self.assertFalse(account["access_expired"])
            self.assertEqual(account["windows"][0]["remaining_percent"], 90)
            self.assertEqual(store.get_profile(account["name"]).auth["tokens"]["refresh_token"], "rotated-fixture")

    def test_refresh_keeps_aliases_current_and_preserves_independent_sessions(self):
        store.import_text("alias", json.dumps(self.auth))
        independent = self.make_auth("first")
        independent["tokens"]["refresh_token"] = "independent-fixture"
        independent_path = store.import_text("independent", json.dumps(independent)).auth_path
        independent_before = independent_path.read_bytes()
        self.m.reload_profiles()
        fresh = self.make_auth("first")["tokens"]
        fresh["refresh_token"] = "rotated-fixture"
        with patch("codex_monitor.monitor.refresh_tokens", return_value=fresh), patch.object(self.m, "refresh_all"):
            self.m.refresh_token("profile")
        self.assertEqual(store.get_profile("alias").auth["tokens"]["refresh_token"], "rotated-fixture")
        self.assertEqual(independent_path.read_bytes(), independent_before)

    def test_active_refresh_does_not_adopt_into_an_independent_session(self):
        independent = self.make_auth("first")
        independent["tokens"]["refresh_token"] = "independent-fixture"
        independent_path = store.import_text("a-independent", json.dumps(independent)).auth_path
        before = independent_path.read_bytes()
        store.atomic_write(paths.main_auth_path(), json.dumps(self.auth).encode())
        self.m.reload_profiles()
        fresh = self.make_auth("first")["tokens"]
        fresh["refresh_token"] = "rotated-fixture"
        with patch("codex_monitor.monitor.refresh_tokens", return_value=fresh), patch.object(self.m, "refresh_all"):
            self.m.refresh_token("profile")
        self.assertEqual(independent_path.read_bytes(), before)
        self.assertEqual(store.read_json(paths.main_auth_path())["tokens"]["refresh_token"], "rotated-fixture")

    def test_ambiguous_main_adoption_preserves_independent_sessions(self):
        independent = self.make_auth("first")
        independent["tokens"]["refresh_token"] = "independent-fixture"
        independent_path = store.import_text("a-independent", json.dumps(independent)).auth_path
        fresh = self.make_auth("first")
        fresh["tokens"]["refresh_token"] = "externally-rotated-fixture"
        fresh["last_refresh"] = "2099-01-01T00:00:00Z"
        store.atomic_write(paths.main_auth_path(), json.dumps(fresh).encode())
        before = [path.read_bytes() for path in (self.auth_path, independent_path)]
        store.adopt()
        self.assertEqual([path.read_bytes() for path in (self.auth_path, independent_path)], before)

    def test_main_adoption_updates_matching_alias_copies(self):
        store.import_text("alias", json.dumps(self.auth))
        fresh = self.make_auth("first")
        fresh["tokens"]["refresh_token"] = "main-rotated-fixture"
        fresh["last_refresh"] = "2099-01-01T00:00:00Z"
        store.atomic_write(paths.main_auth_path(), json.dumps(fresh).encode())
        store.adopt()
        for name in ("alias", "profile"):
            self.assertEqual(store.get_profile(name).auth["tokens"]["refresh_token"], "main-rotated-fixture")

    def unknown_reset(self):
        request_id = str(uuid.uuid4())
        with patch.object(usage, "consume_reset_credit", side_effect=usage.UsageError("unknown outcome")):
            with self.assertRaises(usage.UsageError):
                self.m.use_reset_credit("profile", request_id)
        return request_id

    def test_unknown_reset_survives_service_restart(self):
        request_id = self.unknown_reset()
        restarted = Monitor()
        restarted.reload_profiles()
        self.assertEqual(restarted.snapshot()["accounts"][0]["pending_reset_request_id"], request_id)

    def test_unknown_reset_restart_blocks_a_new_spend(self):
        request_id = self.unknown_reset()
        restarted = Monitor()
        restarted.reload_profiles()
        with patch.object(usage, "consume_reset_credit") as consume:
            with self.assertRaises(store.StoreError):
                restarted.use_reset_credit("profile", str(uuid.uuid4()))
        consume.assert_not_called()
        self.assertEqual(restarted.snapshot()["accounts"][0]["pending_reset_request_id"], request_id)

    def test_unknown_reset_remains_pending_if_retry_credentials_expire(self):
        request_id = self.unknown_reset()
        with patch.object(usage, "consume_reset_credit", side_effect=usage.UsageError("expired", 401)):
            with self.assertRaises(usage.UsageError):
                self.m.use_reset_credit("profile", request_id)
        self.assertEqual(self.m.snapshot()["accounts"][0]["pending_reset_request_id"], request_id)

    def test_completed_reset_receipt_survives_restart(self):
        request_id = str(uuid.uuid4())
        result = {"code": "reset", "windows_reset": 1}
        with patch.object(usage, "consume_reset_credit", return_value=result), patch.object(self.m, "refresh_one"):
            self.m.use_reset_credit("profile", request_id)
        restarted = Monitor()
        restarted.reload_profiles()
        with patch.object(usage, "consume_reset_credit") as consume:
            self.assertEqual(restarted.use_reset_credit("profile", request_id), result)
        consume.assert_not_called()
        # Model a crash after receipt persistence but before invalidating the old cache.
        usage.save_cache("acct-first", self.quota, None, now_utc() - timedelta(minutes=1))
        recovered = Monitor()
        recovered.reload_profiles()
        self.assertEqual(recovered.snapshot()["accounts"][0]["windows"], [])

    def test_operation_is_persisted_before_network_and_locked_across_processes(self):
        request_id = str(uuid.uuid4())
        script = """
from unittest.mock import patch
from codex_monitor import store, usage
from codex_monitor.monitor import Monitor
m=Monitor();m.reload_profiles()
with patch.object(usage,'consume_reset_credit',side_effect=AssertionError('second spend reached network')):
    try:m.use_reset_credit('profile',REQUEST_ID)
    except store.StoreError:print('blocked')
""".replace("REQUEST_ID", repr(request_id))

        def consume(auth, operation):
            self.assertEqual(ResetLedger(paths.accounts_dir()).status("acct-first")["pending"], request_id)
            child = subprocess.run([sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), "blocked")
            return {"code": "reset", "windows_reset": 1}

        with patch.object(usage, "consume_reset_credit", side_effect=consume), patch.object(self.m, "refresh_one"):
            self.m.use_reset_credit("profile", request_id)

    def test_moving_locked_profile_preserves_credentials_and_process_lock(self):
        destination = self.auth_path.parent.with_name("moved")
        script = """
import sys
from pathlib import Path
from codex_monitor.locking import LockBusyError, file_lock
try:
    with file_lock(Path(sys.argv[1]), blocking=False): print('acquired')
except LockBusyError: print('blocked')
"""
        def probe():
            child = subprocess.run([sys.executable, "-c", script, str(destination / ".auth.lock")],
                                   cwd=str(Path(__file__).resolve().parent.parent),
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            return child.stdout.strip()

        original = self.auth_path.read_bytes()
        with file_lock(self.auth_path.parent / ".auth.lock"):
            self.auth_path.parent.rename(destination)
            self.assertEqual((destination / "auth.json").read_bytes(), original)
            self.assertEqual(probe(), "blocked")
        self.assertFalse(self.auth_path.parent.exists())
        self.assertEqual(probe(), "acquired")

    def test_persistence_failure_prevents_network_spend(self):
        with patch("codex_monitor.reset_ledger.atomic_write", side_effect=OSError("fixture disk full")), patch.object(usage, "consume_reset_credit") as consume:
            with self.assertRaises(OSError):
                self.m.use_reset_credit("profile", str(uuid.uuid4()))
        consume.assert_not_called()

    def test_lost_receipt_write_keeps_durable_pending_request(self):
        request_id = str(uuid.uuid4())
        original_write = store.atomic_write
        calls = 0

        def write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture disk full after response")
            original_write(*args, **kwargs)

        with patch("codex_monitor.reset_ledger.atomic_write", side_effect=write), patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 1}):
            with self.assertRaises(OSError):
                self.m.use_reset_credit("profile", request_id)
        restarted = Monitor()
        restarted.reload_profiles()
        self.assertEqual(restarted.snapshot()["accounts"][0]["pending_reset_request_id"], request_id)
        with patch.object(usage, "consume_reset_credit", return_value={"code": "already_redeemed", "windows_reset": 0}) as consume, patch.object(restarted, "refresh_one"):
            restarted.use_reset_credit("profile", request_id)
        self.assertEqual(consume.call_args.args[1], request_id)

    def test_corrupt_reset_record_is_preserved_and_blocks_spending(self):
        self.unknown_reset()
        record = next((paths.accounts_dir() / "_reset_requests").glob("*.json"))
        for content in (b"broken JSON", b'{"version":1,"pending":null,"results":{},"last_reset_at":[]}'):
            with self.subTest(content=content):
                record.write_bytes(content)
                account = self.m.snapshot()["accounts"][0]
                self.assertTrue(account["reset_recovery_error"])
                with patch.object(usage, "consume_reset_credit") as consume:
                    with self.assertRaises(store.StoreError):
                        self.m.use_reset_credit("profile", str(uuid.uuid4()))
                consume.assert_not_called()
                self.assertEqual(record.read_bytes(), content)

    def test_invalid_usage_response_does_not_break_all_cards(self):
        payloads = [b'["invalid usage"]', b'not JSON', b'{"rate_limit":[]}',
                    b'{"rate_limit":{"primary_window":{"used_percent":"oops"}}}',
                    b'{"rate_limit":{"primary_window":{"used_percent":NaN}}}',
                    b'{"additional_rate_limits":[{"metered_feature":[]}]}',
                    b'{"credits":[]}', b'{"plan_type":{}}']
        for payload in payloads:
            with self.subTest(payload=payload):
                self.m.states["profile"].usage = self.quota
                with patch.object(usage.net, "urlopen", return_value=io.BytesIO(payload)), patch.object(usage, "fetch_reset_credits", return_value=None):
                    self.m.refresh_one("profile")
                account = self.m.snapshot()["accounts"][0]
                self.assertIn("invalid", account["error"])
                self.assertEqual(account["windows"][0]["remaining_percent"], 90)

    def test_invalid_oauth_responses_are_rejected(self):
        for payload in (b"bad JSON", b"[]", b"{}", b'{"access_token":[]}',
                        b'{"access_token":"fixture","refresh_token":{}}'):
            with self.subTest(payload=payload):
                with patch.object(oauth.net, "urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaises(oauth.OAuthError):
                        oauth.refresh_tokens("fixture-refresh")

    def test_external_invalid_identity_does_not_hide_other_accounts(self):
        bad = self.make_auth("bad")
        bad["tokens"]["account_id"] = ["invalid"]
        bad_path = paths.accounts_dir() / "bad" / "auth.json"
        data = json.dumps(bad).encode()
        store.atomic_write(bad_path, data)
        self.m.reload_profiles()
        accounts = {account["name"]: account for account in self.m.snapshot()["accounts"]}
        self.assertEqual(accounts["bad"]["availability"], "signed_out")
        self.assertEqual(accounts["profile"]["account_id"], "acct-first")
        self.assertEqual(bad_path.read_bytes(), data)

    def test_invalid_cache_is_ignored(self):
        path = usage.cache_path("acct-first")
        path.parent.mkdir(parents=True, exist_ok=True)
        for payload in ([], {"fetchedAt": [], "usage": {}},
                        {"fetchedAt": now_utc().isoformat(), "usage": {"rate_limit": []}}):
            with self.subTest(payload=payload):
                path.write_text(json.dumps(payload))
                self.assertIsNone(usage.load_cache("acct-first"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
