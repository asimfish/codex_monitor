#!/usr/bin/env python3
"""Dashboard ordering, persistent annotations and authenticated HTTP regression checks."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex_monitor import demo, store, usage
from codex_monitor.annotations import Annotations
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server
from dashboard_fixture import fixture_monitor


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="codex-dashboard-test-")
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"),
            "CODEX_ACCOUNTS_DIR": str(self.root / "accounts"),
            "CODEX_MONITOR_DEMO": "0",
        })
        self.environment.start()
        self.browser = patch("codex_monitor.browsers.find_private_browser", return_value=None)
        self.browser.start()
        self.monitor = fixture_monitor()

    def tearDown(self):
        self.browser.stop()
        self.environment.stop()
        self.directory.cleanup()

    def accounts(self, monitor=None):
        return (monitor or self.monitor).snapshot()["accounts"]

    def test_usable_first_even_when_current_login_is_invalid(self):
        accounts = self.accounts()
        self.assertEqual([a["name"] for a in accounts[:3]], ["erin", "alice", "bob"])
        self.assertEqual([a["availability"] for a in accounts[:5]], ["ready"] * 3 + ["checking", "limited"])
        by_name = {a["name"]: a for a in accounts}
        self.assertEqual(by_name["dave"]["availability"], "error")
        self.assertTrue(by_name["dave"]["active"])
        self.assertEqual(by_name["finn"]["availability"], "expired")
        self.assertEqual(by_name["guest"]["availability"], "signed_out")
        self.assertEqual(accounts[-1]["availability"], "manual")
        self.assertEqual(by_name["bob"]["availability"], "ready", "An old subscription snapshot does not revoke valid credentials")

    def test_all_quota_windows_and_server_denial_affect_readiness(self):
        bob = self.monitor.states["bob"]
        bob.usage["rate_limit"]["secondary_window"]["used_percent"] = 100
        self.assertEqual(next(a for a in self.accounts() if a["name"] == "bob")["availability"], "limited")
        bob.usage["rate_limit"]["secondary_window"]["used_percent"] = 5
        bob.usage["rate_limit"]["allowed"] = False
        self.assertEqual(next(a for a in self.accounts() if a["name"] == "bob")["availability"], "limited")

    def test_active_ready_account_precedes_other_ready_accounts(self):
        self.monitor.states["bob"].active = True
        self.assertEqual(self.accounts()[0]["name"], "bob")

    def test_tags_survive_restart_relogin_and_logout_without_changing_auth(self):
        directory = self.root / "accounts" / "personal"
        now = datetime.now(timezone.utc)
        auth = demo._auth("old@example.com", "plus", "acct-old", None,
                          now + timedelta(days=5), now)
        store.atomic_write(directory / "auth.json", json.dumps(auth).encode())
        monitor = Monitor()
        monitor.reload_profiles()
        before = (directory / "auth.json").read_bytes()
        monitor.set_annotations("personal", [" 未删除但用不了 ", "未删除但用不了"], True)
        self.assertEqual((directory / "auth.json").read_bytes(), before)
        restarted = Monitor()
        restarted.reload_profiles()
        self.assertEqual(self.accounts(restarted)[0]["tags"], ["未删除但用不了"])
        auth = demo._auth("new@example.com", "plus", "acct-new", None,
                          now + timedelta(days=5), now)
        store.atomic_write(directory / "auth.json", json.dumps(auth).encode())
        restarted.reload_profiles()
        self.assertEqual(self.accounts(restarted)[0]["tags"], ["未删除但用不了"])
        (directory / "auth.json").unlink()
        restarted.reload_profiles()
        self.assertEqual(self.accounts(restarted)[0]["tags"], ["未删除但用不了"])
        restarted.set_annotations("personal", [], False)
        self.assertEqual(self.accounts(restarted)[0]["availability"], "signed_out")
        if os.name != "nt":
            self.assertEqual(restarted.annotations.path.stat().st_mode & 0o777, 0o600)

    def test_tags_are_notes_unless_manually_marked_unavailable(self):
        self.monitor.set_annotations("erin", ["账号被删除"], False)
        self.assertEqual(self.accounts()[0]["availability"], "ready")
        self.monitor.set_annotations("erin", ["账号被删除"], True)
        accounts = self.accounts()
        erin = next(a for a in accounts if a["name"] == "erin")
        self.assertEqual(erin["availability"], "manual")
        self.assertGreater(accounts.index(erin), next(i for i, a in enumerate(accounts) if a["name"] == "dave"))
        self.monitor.set_annotations("erin", [], False)
        self.assertEqual(self.accounts()[0]["name"], "erin")

    def test_invalid_tags_and_unknown_names_cannot_overwrite_metadata(self):
        before = self.monitor.annotations.path.read_bytes()
        cases = [(None, False), ([""], False), (["x" * 31], False),
                 (["x"] * 9, False), ([5], False), (["x\x00y"], False), (["\ud800"], False), (["x"], "true")]
        for tags, flag in cases:
            with self.subTest(tags=repr(tags), flag=flag):
                with self.assertRaises(store.StoreError):
                    self.monitor.set_annotations("alice", tags, flag)
        for name in [None, "../outside", "unknown"]:
            with self.assertRaises(store.StoreError):
                self.monitor.set_annotations(name, ["x"], False)
        self.assertEqual(self.monitor.annotations.path.read_bytes(), before)
        self.assertFalse((self.root / "outside").exists())

    def test_corrupt_metadata_is_visible_and_preserved(self):
        self.monitor.annotations.path.write_text("{broken", encoding="utf-8")
        self.assertIsNotNone(self.monitor.snapshot()["annotation_error"])
        with self.assertRaises(store.StoreError):
            self.monitor.set_annotations("alice", ["new"], False)
        self.assertEqual(self.monitor.annotations.path.read_text(), "{broken")

    def test_main_only_tags_follow_auto_archived_profile(self):
        alice = self.monitor.states["alice"]
        self.monitor.annotations.path.unlink()  # No pre-existing profile annotation.
        alice.is_main = True
        self.monitor.set_annotations("alice", ["主账号备注"], False)
        alice.is_main = False
        self.assertEqual(next(a for a in self.accounts() if a["name"] == "alice")["tags"], ["主账号备注"])

    def test_live_only_extra_quota_is_visible_without_duplicates(self):
        alice = self.monitor.states["alice"]
        self.assertEqual([a["name"] for a in alice.view()["extras"]], ["GPT-5.3-Codex-Spark", "Code review"])
        alice.usage["additional_rate_limits"].append({"metered_feature": "review", "limit_name": "Code review", "rate_limit": {}})
        extras = alice.view()["extras"]
        self.assertEqual(len(extras), 2)
        self.assertEqual(extras[-1]["windows"][0]["remaining_percent"], 90)

    def test_credit_balance_preserves_zero_unknown_and_unlimited(self):
        alice = self.monitor.states["alice"]
        for credits, balance, unlimited in [
            ({"has_credits": True, "balance": "62500.0000000000"}, "62500.0000000000", False),
            ({"has_credits": False, "balance": 0}, 0, False),
            ({"has_credits": False, "balance": "0"}, "0", False),
            ({"has_credits": True, "unlimited": True, "balance": None}, None, True),
            ({}, None, False),
        ]:
            with self.subTest(credits=credits):
                alice.usage["credits"] = credits
                view = alice.view()
                self.assertEqual(view["credits_balance"], balance)
                self.assertEqual(view["credits_unlimited"], unlimited)
                lines = "\n".join(usage.format_view(view))
                if unlimited:
                    self.assertIn("credit balance unlimited", lines)
                elif balance is not None:
                    self.assertIn("credit balance " + str(balance), lines)
                else:
                    self.assertNotIn("credit balance", lines)

    def test_authenticated_api_validation_and_persistence(self):
        server = make_server(self.monitor, port=0, token="fixture-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)

        def request(body, token="fixture-token"):
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            req = urllib.request.Request(base + "/api/annotations", data=payload,
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        try:
            valid = {"name": "alice", "tags": ["备注", "<b>literal</b>"], "unavailable": True}
            self.assertEqual(request(valid, "wrong")[0], 403)
            for body in [b"{invalid", [], {**valid, "name": "../x"}, {**valid, "tags": [1]}, b" " * 65537]:
                self.assertEqual(request(body)[0], 400)
            self.assertEqual(request(valid)[0], 200)
            fresh = Annotations(self.root / "accounts")
            self.assertEqual(fresh.read()["profile:alice"], {"tags": valid["tags"], "unavailable": True})
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
