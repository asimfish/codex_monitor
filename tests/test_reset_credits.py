"""Reset-card contract and spending guards. All accounts and network responses are fabricated."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import demo, paths, store, usage
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server


class ResetCreditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-reset-test-")
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            "CODEX_HOME": str(Path(self.tmp.name) / "main"),
            "CODEX_ACCOUNTS_DIR": str(Path(self.tmp.name) / "accounts"),
            "CODEX_MONITOR_DEMO": "0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        now = now_utc()
        self.auth = demo._auth("reset@example.test", "pro", "acct-reset", None,
                               now + timedelta(days=3), now)
        profile = store.import_text("reset", json.dumps(self.auth))
        self.auth_before = profile.auth_path.read_bytes()
        self.monitor = Monitor()
        self.monitor.reload_profiles()
        self.request_id = str(uuid.uuid4())
        self.quota = {"rate_limit": {"allowed": True, "limit_reached": False,
                      "primary_window": {"used_percent": 0, "limit_window_seconds": 18000,
                                         "reset_at": (now + timedelta(hours=5)).timestamp()}},
                      "rate_limit_reset_credits": {"available_count": 2}}
        self.fetch_usage = patch.object(usage, "fetch_usage_auto", return_value=(self.quota, self.auth, False))
        self.fetch_usage.start()
        self.addCleanup(self.fetch_usage.stop)
        self.fetch_credits = patch.object(usage, "fetch_reset_credits", return_value={"available_count": 2, "credits": []})
        self.credits_mock = self.fetch_credits.start()
        self.addCleanup(self.fetch_credits.stop)

    def test_transport_uses_official_body_and_account(self):
        response = io.BytesIO(b'{"code":"reset","windows_reset":2}')
        with patch.object(usage.net, "urlopen", return_value=response) as send:
            result = usage.consume_reset_credit(self.auth, self.request_id)
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/wham/rate-limit-reset-credits/consume"))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.auth["tokens"]["access_token"])
        self.assertEqual(request.get_header("Chatgpt-account-id"), "acct-reset")
        self.assertEqual(json.loads(request.data), {"redeem_request_id": self.request_id})
        self.assertEqual(result, {"code": "reset", "windows_reset": 2})

    def test_transport_does_not_retry_a_failed_spend(self):
        with patch.object(usage.net, "urlopen", side_effect=urllib.error.URLError("connection lost")) as send:
            with self.assertRaises(usage.UsageError):
                usage.consume_reset_credit(self.auth, self.request_id)
        self.assertEqual(send.call_count, 1)

    def test_expired_auth_is_rejected_before_spending(self):
        expired = demo._auth("reset@example.test", "pro", "acct-reset", None,
                             now_utc() - timedelta(days=1), now_utc())
        with patch.object(usage.net, "urlopen") as send:
            with self.assertRaises(usage.UsageError) as error:
                usage.consume_reset_credit(expired, self.request_id)
        self.assertEqual(error.exception.status, 401)
        send.assert_not_called()

    def test_reset_refreshes_quota_and_credits_and_replay_does_not_spend(self):
        state = self.monitor.states["reset"]
        state.credits = {"available_count": 3}
        state.usage = {"rate_limit_reset_credits": {"available_count": 3}}
        self.monitor._credits_fetched["acct-reset"] = now_utc()
        state.live = usage.LiveEvent(timestamp=now_utc(), limit_id="codex", limit_name=None,
                                     primary={"used_percent": 99, "limit_window_seconds": 18000,
                                              "reset_at": now_utc().timestamp() + 100}, secondary=None,
                                     plan_type="pro", limit_reached=False)
        with patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 2}) as consume:
            first = self.monitor.use_reset_credit("reset", self.request_id)
            replay = self.monitor.use_reset_credit("reset", self.request_id)
        self.assertEqual(first, replay)
        self.assertEqual(consume.call_count, 1)
        self.assertIsNone(state.live)
        self.assertEqual(state.view()["reset_credits"], 2)
        self.assertEqual(state.view()["windows"][0]["remaining_percent"], 100)
        self.credits_mock.assert_called_once()
        self.assertEqual(store.get_profile("reset").auth_path.read_bytes(), self.auth_before)

    def test_non_reset_results_are_not_reported_as_a_reset(self):
        for code in ("nothing_to_reset", "no_credit", "already_redeemed"):
            with self.subTest(code=code), patch.object(usage, "consume_reset_credit", return_value={"code": code, "windows_reset": 0}):
                result = self.monitor.use_reset_credit("reset", str(uuid.uuid4()))
                self.assertEqual(result["code"], code)
                self.assertEqual(result["windows_reset"], 0)

    def test_unknown_result_is_an_error(self):
        with patch.object(usage.net, "urlopen", return_value=io.BytesIO(b'{"code":"unexpected"}')):
            with self.assertRaises(usage.UsageError):
                usage.consume_reset_credit(self.auth, self.request_id)

    def test_unknown_outcome_requires_retry_with_the_same_request(self):
        with patch.object(usage, "consume_reset_credit", side_effect=[usage.UsageError("connection lost"), {"code": "reset", "windows_reset": 2}]) as consume:
            with self.assertRaises(usage.UsageError):
                self.monitor.use_reset_credit("reset", self.request_id)
            self.assertEqual(self.monitor.snapshot()["accounts"][0]["pending_reset_request_id"], self.request_id)
            with self.assertRaises(store.StoreError):
                self.monitor.use_reset_credit("reset", str(uuid.uuid4()))
            self.assertEqual(self.monitor.use_reset_credit("reset", self.request_id)["code"], "reset")
            self.assertIsNone(self.monitor.snapshot()["accounts"][0]["pending_reset_request_id"])
        self.assertEqual(consume.call_count, 2)
        self.assertEqual([call.args[1] for call in consume.call_args_list], [self.request_id, self.request_id])

    def test_concurrent_requests_do_not_spend_twice(self):
        entered, release = threading.Event(), threading.Event()
        results = []

        def spend(*args):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release reset request")
            return {"code": "reset", "windows_reset": 2}

        def run():
            try:
                results.append(self.monitor.use_reset_credit("reset", self.request_id))
            except Exception as e:
                results.append(e)

        with patch.object(usage, "consume_reset_credit", side_effect=spend) as consume:
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(store.StoreError):
                    self.monitor.use_reset_credit("reset", str(uuid.uuid4()))
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertEqual(consume.call_count, 1)
        self.assertEqual(results, [{"code": "reset", "windows_reset": 2}])

    def test_invalid_target_or_request_id_never_spends(self):
        with patch.object(usage, "consume_reset_credit") as consume:
            for name, request_id in [("missing", self.request_id), ("reset", ""), ("reset", "bad-id")]:
                with self.subTest(name=name, request_id=request_id), self.assertRaises(store.StoreError):
                    self.monitor.use_reset_credit(name, request_id)
        consume.assert_not_called()

    def test_success_is_preserved_when_quota_refresh_fails(self):
        with patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 2}) as consume:
            with patch.object(usage, "fetch_usage_auto", side_effect=usage.UsageError("network unavailable")):
                result = self.monitor.use_reset_credit("reset", self.request_id)
                self.assertEqual(result, {"code": "reset", "windows_reset": 2, "refresh_pending": True})
                self.assertEqual(self.monitor.use_reset_credit("reset", self.request_id), result)
        self.assertEqual(consume.call_count, 1)
        self.assertIsNone(self.monitor.states["reset"].usage)
        self.assertEqual(usage.load_cache("acct-reset")[0], {})

    def test_poll_started_before_reset_cannot_restore_old_quota(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(*args, **kwargs):
            if threading.current_thread() is worker:
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("test did not release usage poll")
                return {"rate_limit_reset_credits": {"available_count": 3}}, self.auth, False
            return self.quota, self.auth, False

        failures = []
        def poll():
            try:
                self.monitor.refresh_one("reset")
            except Exception as error:
                failures.append(error)
        worker = threading.Thread(target=poll)
        with patch.object(usage, "fetch_usage_auto", side_effect=fetch):
            with patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 2}):
                worker.start()
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    self.monitor.use_reset_credit("reset", self.request_id)
                finally:
                    release.set()
                    worker.join(timeout=5)
        self.assertEqual(self.monitor.states["reset"].view()["windows"][0]["remaining_percent"], 100)
        self.assertEqual(usage.load_cache("acct-reset")[0], self.quota)
        self.assertEqual(failures, [], "the old poll must finish normally before its result is discarded")

    def test_old_rollout_events_cannot_restore_usage_after_reset(self):
        state = self.monitor.states["reset"]
        sessions = Path(state.auth_path).parent / "sessions"
        sessions.mkdir()
        old_event = usage.LiveEvent(timestamp=now_utc() - timedelta(seconds=30), limit_id="codex", limit_name=None,
                                    primary={"used_percent": 99, "limit_window_seconds": 18000,
                                             "reset_at": now_utc().timestamp() + 100}, secondary=None,
                                    plan_type="pro", limit_reached=False)
        tailer = Mock()
        tailer.poll_latest.return_value = old_event, {"codex_spark": old_event}
        self.monitor._tailers[str(sessions)] = tailer
        with patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 2}):
            self.monitor.use_reset_credit("reset", self.request_id)
        self.monitor.poll_live()
        self.assertIsNone(state.live)
        self.assertEqual(state.live_extras, {})
        self.assertEqual(state.view()["windows"][0]["remaining_percent"], 100)

    def test_api_requires_dashboard_auth_and_explicit_confirmation(self):
        server = make_server(self.monitor, port=0, token="reset-api-test")
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_port}/api/reset/use"

        def request(body, authorized=True):
            headers = {"Content-Type": "application/json"}
            if authorized:
                headers["X-Token"] = "reset-api-test"
            return urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")

        body = {"name": "reset", "request_id": self.request_id, "confirmed": True}
        try:
            with patch.object(usage, "consume_reset_credit", return_value={"code": "reset", "windows_reset": 2}) as consume:
                for submitted, authorized, expected in [(body, False, 403), ({**body, "confirmed": False}, True, 400), ({**body, "confirmed": "yes"}, True, 400)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        client.open(request(submitted, authorized), timeout=5)
                    self.assertEqual(error.exception.code, expected)
                consume.assert_not_called()
                with client.open(request(body), timeout=5) as response:
                    result = json.load(response)
                self.assertEqual(result, {"ok": True, "code": "reset", "windows_reset": 2})
                self.assertEqual(consume.call_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
