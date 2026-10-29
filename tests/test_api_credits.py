"""Official Costs contract, secret boundaries and grant-estimate semantics."""
import copy
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import api_credits, paths, store
from codex_monitor.identity import format_iso, now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server

KEY = "sk-admin-fabricated-test-key"


class ApiCreditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="api-credit-tests-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {"CODEX_HOME": str(root / "main"), "CODEX_ACCOUNTS_DIR": str(root / "accounts")})
        env.start(); self.addCleanup(env.stop)
        self.manager = api_credits.ApiCredits()
        self.now = now_utc()
        self.body = {"label": "Example", "organization": "org-fixture", "admin_key": KEY,
                     "period_start": format_iso(self.now - timedelta(days=2)),
                     "period_end": format_iso(self.now + timedelta(days=28)), "grant_total": "100.10"}

    def add(self, **updates):
        return self.manager.save({**self.body, **updates})

    def result(self, cost="0.10"):
        return {"cost_usd": cost, "days": [], "queried_until": format_iso(self.now)}

    def test_secret_projection_and_private_permissions(self):
        identifier = self.add()
        public = json.dumps(self.manager.snapshot())
        self.assertNotIn(KEY, public)
        self.assertNotIn('"admin_key"', public)
        self.assertEqual(self.manager.snapshot()["accounts"][0]["id"], identifier)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.manager.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.manager.root.stat().st_mode), 0o700)
        self.assertFalse(paths.main_auth_path().exists())
        self.assertEqual(store.profiles(), [])

    def test_ordinary_project_key_is_rejected_without_echo(self):
        key = "sk-proj-fabricated-project-key"
        with self.assertRaises(store.StoreError) as error:
            self.add(admin_key=key)
        self.assertNotIn(key, str(error.exception))
        self.assertFalse(self.manager.path.exists())

    def test_unknown_and_zero_amounts_are_different(self):
        self.add(admin_key="", grant_total="")
        row = self.manager.snapshot()["accounts"][0]
        self.assertIsNone(row["grant_total"])
        self.assertIsNone(row["costs"])
        self.assertIsNone(row["estimated_remaining"])
        self.assertFalse(row["has_admin_key"])
        with patch.object(api_credits, "fetch_costs") as fetch:
            self.manager.refresh(force=True)
        fetch.assert_not_called()

    def test_exact_decimal_cost_and_estimate(self):
        self.add()
        with patch.object(api_credits, "fetch_costs", return_value=self.result()):
            self.manager.refresh(force=True)
        row = self.manager.snapshot()["accounts"][0]
        self.assertEqual(row["estimated_remaining"], "100.00")
        self.assertFalse(row["stale"])
        self.assertIsNotNone(row["last_success"])
        self.assertIsNotNone(row["next_refresh"])

    def test_failed_refresh_keeps_last_success_and_exposes_stale(self):
        self.add()
        with patch.object(api_credits, "fetch_costs", return_value=self.result("0")):
            self.manager.refresh(force=True)
        previous = self.manager.snapshot()["accounts"][0]
        with patch.object(api_credits, "now_utc", return_value=self.now + timedelta(minutes=10)), patch.object(api_credits, "fetch_costs", side_effect=store.StoreError("Costs unavailable")):
            self.manager.refresh(force=True)
        row = self.manager.snapshot()["accounts"][0]
        self.assertEqual(row["costs"], previous["costs"])
        self.assertEqual(row["last_success"], previous["last_success"])
        self.assertTrue(row["stale"])
        self.assertEqual(row["estimated_remaining"], "100.10")

    def test_period_change_invalidates_usage_and_preserves_key(self):
        identifier = self.add()
        with patch.object(api_credits, "fetch_costs", return_value=self.result()):
            self.manager.refresh(force=True)
        self.manager.save({**self.body, "id": identifier, "admin_key": "", "period_start": format_iso(self.now)})
        row = self.manager.snapshot()["accounts"][0]
        self.assertTrue(row["has_admin_key"])
        self.assertIsNone(row["costs"])

    def test_expiry_does_not_invent_new_grant(self):
        self.add(period_start=format_iso(self.now-timedelta(days=31)), period_end=format_iso(self.now-timedelta(days=1)))
        with patch.object(api_credits, "fetch_costs", return_value=self.result()):
            self.manager.refresh(force=True)
        row = self.manager.snapshot()["accounts"][0]
        self.assertTrue(row["expired"])
        self.assertIsNone(row["estimated_remaining"])
        self.assertEqual(row["grant_total"], "100.10")

    def test_duplicate_org_and_invalid_fields_are_rejected(self):
        self.add()
        with self.assertRaises(store.StoreError):
            self.add(label="Another label")
        cases = [{"grant_total": "NaN"}, {"grant_total": "Infinity"}, {"grant_total": -1},
                 {"period_start": []}, {"period_end": "unknown"}, {"refresh_interval": True},
                 {"organization": "https://example.test"}, {"label": "bad\nlabel"}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(store.StoreError):
                self.add(**values)

    def test_removed_account_is_not_recreated_by_late_refresh(self):
        identifier = self.add()
        entered, release = threading.Event(), threading.Event()
        def fetch(*args):
            entered.set()
            if not release.wait(3): raise AssertionError("gate not released")
            return self.result()
        with patch.object(api_credits, "fetch_costs", side_effect=fetch):
            worker = threading.Thread(target=self.manager.refresh)
            worker.start()
            self.assertTrue(entered.wait(3))
            self.manager.remove(identifier)
            release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.manager.snapshot()["accounts"], [])

    def test_costs_are_read_only_paginated_and_decimal(self):
        def page(stamp, value, more, cursor=None):
            return {"data": [{"start_time": stamp, "results": [{"amount": {"value": value, "currency": "usd"}}]}],
                    "has_more": more, "next_page": cursor}
        opener = Mock()
        def response(payload):
            value = Mock()
            value.__enter__ = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
            value.__exit__ = Mock(return_value=False)
            return value
        opener.open.side_effect = [response(page(100, 0.1, True, "next")), response(page(200, 0.2, False))]
        with patch.object(api_credits.urllib.request, "build_opener", return_value=opener):
            value = api_credits.fetch_costs(KEY, "org-fixture", self.now-timedelta(days=2), self.now)
        self.assertEqual(value["cost_usd"], "0.3")
        for call in opener.open.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(request.full_url.startswith("https://api.openai.com/v1/organization/costs?"))
            self.assertNotIn(KEY, request.full_url)
        self.assertIn("page=next", opener.open.call_args_list[-1].args[0].full_url)

    def test_upstream_errors_and_redirects_do_not_leak_secrets(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://api.openai.com/", 403, KEY, {}, io.BytesIO(KEY.encode()))
        with patch.object(api_credits.urllib.request, "build_opener", return_value=opener), self.assertRaises(store.StoreError) as error:
            api_credits.fetch_costs(KEY, "org-fixture", self.now-timedelta(days=1), self.now)
        self.assertNotIn(KEY, str(error.exception))
        self.assertIsNone(api_credits.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_authenticated_web_crud_and_page(self):
        monitor = Monitor()
        server = make_server(monitor, port=0, token="fixture-dashboard-token")
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        root = "http://127.0.0.1:%d" % server.server_port
        for endpoint in ("/api/credits", "/api-credits"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(root+endpoint)
            self.assertEqual(error.exception.code, 403)
        request = urllib.request.Request(root+"/api/credits/save", data=json.dumps(self.body).encode(),
                                         headers={"X-Token": "fixture-dashboard-token"})
        saved = json.load(urllib.request.urlopen(request))
        request = urllib.request.Request(root+"/api/credits", headers={"X-Token": "fixture-dashboard-token"})
        raw = urllib.request.urlopen(request).read().decode()
        self.assertNotIn(KEY, raw)
        self.assertEqual(json.loads(raw)["accounts"][0]["id"], saved["id"])
        page = urllib.request.urlopen(root+"/api-credits?token=fixture-dashboard-token").read().decode()
        self.assertIn('id="apiKey" type="password"', page)
        self.assertIn("Estimated balance", page)
        self.assertNotIn(KEY, page)

    def test_cli_passes_credentials_via_stdin_only(self):
        command = [sys.executable, "bin/codex-monitor", "api-credits", "save"]
        run = subprocess.run(command, input=json.dumps(self.body), text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(KEY, run.stdout+run.stderr)
        self.assertEqual(len(json.loads(run.stdout)["accounts"]), 1)

    def test_corrupt_file_is_preserved(self):
        self.add()
        self.manager.path.write_text("{broken")
        with self.assertRaises(store.StoreError): self.manager.snapshot()
        with self.assertRaises(store.StoreError): self.add(label="Another")
        self.assertEqual(self.manager.path.read_text(), "{broken")

    def test_changed_organization_cannot_inherit_other_org_costs(self):
        identifier = self.add()
        with patch.object(api_credits, "fetch_costs", return_value=self.result()):
            self.manager.refresh(force=True)
        self.manager.save({**self.body, "id": identifier, "admin_key": "", "organization": "org-other"})
        self.assertIsNone(self.manager.snapshot()["accounts"][0]["costs"])

    def test_late_result_cannot_overwrite_edited_period(self):
        identifier = self.add()
        def fetch(*args):
            self.manager.save({**self.body, "id": identifier, "admin_key": "", "period_start": format_iso(self.now)})
            return self.result()
        with patch.object(api_credits, "fetch_costs", side_effect=fetch):
            self.manager.refresh(force=True)
        row = self.manager.snapshot()["accounts"][0]
        self.assertIsNone(row["costs"])
        self.assertIsNone(row["last_success"])

    def test_future_period_and_refresh_interval_do_not_query_early(self):
        self.add(period_start=format_iso(self.now+timedelta(days=1)))
        with patch.object(api_credits, "fetch_costs") as fetch:
            self.manager.refresh(force=True)
        fetch.assert_not_called()
        identifier = self.manager.snapshot()["accounts"][0]["id"]
        self.manager.save({**self.body, "id": identifier})
        with patch.object(api_credits, "fetch_costs", return_value=self.result()) as fetch:
            self.manager.refresh(force=True)
            self.manager.refresh(force=True)
            api_credits.ApiCredits().refresh(force=True)
        self.assertEqual(fetch.call_count, 1)

    def test_invalid_cost_pages_do_not_become_a_zero_balance(self):
        values = [{"data": [], "has_more": True, "next_page": None},
                  {"data": [{"start_time": 1, "results": [{"amount": {"currency": "eur", "value": 1}}]}], "has_more": False},
                  {"data": [{"start_time": 1, "results": [{"amount": {"currency": "usd", "value": "NaN"}}]}], "has_more": False},
                  {"data": [], "has_more": "false"}]
        for payload in values:
            response = Mock()
            response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
            response.__exit__ = Mock(return_value=False)
            opener = Mock(); opener.open.return_value = response
            with self.subTest(payload=payload), patch.object(api_credits.urllib.request, "build_opener", return_value=opener), self.assertRaises(store.StoreError):
                api_credits.fetch_costs(KEY, "org-fixture", self.now-timedelta(days=1), self.now)

    def test_key_only_connect_discovers_org_and_saves_first_costs(self):
        response = io.BytesIO(b'{"data":[],"has_more":false}')
        response.headers = {"openai-organization": "org-discovered"}
        opener = Mock(); opener.open.return_value = response
        with patch.object(api_credits.urllib.request, "build_opener", return_value=opener):
            self.manager.save({"admin_key": KEY}, verify=True)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertIsNone(request.get_header("Openai-organization"))
        row = self.manager.snapshot()["accounts"][0]
        self.assertEqual((row["label"], row["organization"], row["period_mode"]), ("OpenAI API", "org-discovered", "calendar_month"))
        self.assertEqual(row["costs"]["cost_usd"], "0")
        self.assertIsNone(row["grant_total"])
        self.assertIsNone(row["estimated_remaining"])
        self.assertFalse(row["stale"])
        self.assertNotIn(KEY, json.dumps(row))

    def test_connect_failure_or_missing_org_saves_nothing(self):
        for code in (401, 403, 429):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("https://api.openai.com/", code, KEY, {}, io.BytesIO(KEY.encode()))
            with self.subTest(code=code), patch.object(api_credits.urllib.request, "build_opener", return_value=opener), self.assertRaises(store.StoreError) as error:
                self.manager.save({"admin_key": KEY}, verify=True)
            self.assertNotIn(KEY, str(error.exception))
            self.assertFalse(self.manager.path.exists())
        with patch.object(api_credits, "fetch_costs", return_value=self.result()), self.assertRaisesRegex(store.StoreError, "ID was not returned"):
            self.manager.save({"admin_key": KEY}, verify=True)
        self.assertFalse(self.manager.path.exists())
        with patch.object(api_credits, "fetch_costs", return_value=self.result()):
            self.manager.save({"admin_key": KEY, "organization": "org-entered"}, verify=True)
        self.assertEqual(self.manager.snapshot()["accounts"][0]["organization"], "org-entered")

    def test_org_mismatch_is_rejected_without_saving(self):
        response = io.BytesIO(b'{"data":[],"has_more":false}')
        response.headers = {"openai-organization": "org-other"}
        opener = Mock(); opener.open.return_value = response
        with patch.object(api_credits.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(store.StoreError, "does not match"):
            self.manager.save({"admin_key": KEY, "organization": "org-entered"}, verify=True)
        self.assertFalse(self.manager.path.exists())

    def test_month_rollover_hides_old_costs_and_never_renews_grant(self):
        with patch.object(api_credits, "fetch_costs", return_value=self.result("10")):
            self.manager.save({"organization": "org-month", "admin_key": KEY}, verify=True)
        next_month = (self.now.replace(day=1) + timedelta(days=32)).replace(day=2)
        with patch.object(api_credits, "now_utc", return_value=next_month):
            row = self.manager.snapshot()["accounts"][0]
            self.assertIsNone(row["costs"])
            self.assertTrue(row["stale"])
            self.assertFalse(row["expired"])
            self.assertIsNone(row["grant_total"])
            with patch.object(api_credits, "fetch_costs", return_value=self.result("2")) as fetch:
                self.manager.refresh()
            self.assertEqual(fetch.call_args.args[2].day, 1)
            self.assertEqual(fetch.call_args.args[2].month, next_month.month)
            self.assertEqual(self.manager.snapshot()["accounts"][0]["costs"]["cost_usd"], "2")
        with self.assertRaisesRegex(store.StoreError, "actual grant dates"):
            self.manager.save({"organization": "org-other", "grant_total": "100"})

    def test_failed_key_replacement_and_late_connect_preserve_edits(self):
        identifier = self.add()
        before = self.manager.path.read_bytes()
        with patch.object(api_credits, "fetch_costs", side_effect=store.StoreError("Access denied")), self.assertRaises(store.StoreError):
            self.manager.save({**self.body, "id": identifier, "admin_key": "sk-admin-fabricated-replacement"}, verify=True)
        self.assertEqual(self.manager.path.read_bytes(), before)
        def fetch(*args):
            self.manager.save({**self.body, "id": identifier, "label": "Concurrent edit", "admin_key": ""})
            return self.result()
        with patch.object(api_credits, "fetch_costs", side_effect=fetch), self.assertRaisesRegex(store.StoreError, "changed during verification"):
            self.manager.save({**self.body, "id": identifier}, verify=True)
        self.assertEqual(self.manager.snapshot()["accounts"][0]["label"], "Concurrent edit")

    def test_connect_route_requires_dashboard_authentication(self):
        server = make_server(Monitor(), port=0, token="fixture-connect-token")
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/api/credits/connect" % server.server_port
        body = json.dumps({"admin_key": KEY}).encode()
        with patch.object(api_credits, "fetch_costs", return_value={**self.result("1.25"), "organization": "org-connected"}) as fetch:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, data=body))
            self.assertEqual(error.exception.code, 403)
            fetch.assert_not_called()
            request = urllib.request.Request(url, data=body, headers={"X-Token": "fixture-connect-token"})
            raw = urllib.request.urlopen(request).read().decode()
        self.assertTrue(json.loads(raw)["ok"])
        self.assertNotIn(KEY, raw)
        self.assertEqual(self.manager.snapshot()["accounts"][0]["costs"]["cost_usd"], "1.25")


if __name__ == "__main__":
    unittest.main()
