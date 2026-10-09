"""Account renaming with real temporary credentials, labels and HTTP requests."""
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
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import demo, paths, store
from codex_monitor.monitor import Monitor
from codex_monitor.oauth import apply_refreshed
from codex_monitor.web import make_server


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="codex-rename-")
        root = Path(self.sandbox.name)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(root / "codex"),
            "CODEX_ACCOUNTS_DIR": str(root / "accounts"), "CODEX_MONITOR_DEMO": "0"})
        self.environment.start()
        now = datetime.now(timezone.utc)
        self.auth = json.dumps(demo._auth("same@example.com", "plus", "same-account", None,
                              now + timedelta(days=5), now)).encode()
        for name in ("first", "second"):
            store.atomic_write(paths.accounts_dir() / name / "auth.json", self.auth)
        store.atomic_write(paths.main_auth_path(), self.auth)
        self.monitor = Monitor()
        self.monitor.reload_profiles()

    def tearDown(self):
        self.environment.stop()
        self.sandbox.cleanup()

    def revision(self, name="first"):
        return next(a["removal_revision"] for a in self.monitor.snapshot()["accounts"] if a["name"] == name)

    def test_rename_keeps_credentials_sessions_alias_and_annotations(self):
        source = paths.accounts_dir() / "first"
        store.atomic_write(source / "sessions/history.jsonl", b"session-fixture\n")
        self.monitor.set_annotations("first", ["工作账号", "用不了"], True)
        self.monitor.set_annotations("second", ["另一存档"], False)
        renamed = self.monitor.rename_account("first", "账号 A", self.revision())
        self.assertEqual(renamed, "账号 A")
        destination = paths.accounts_dir() / renamed
        self.assertEqual((destination / "auth.json").read_bytes(), self.auth)
        self.assertEqual((destination / "sessions/history.jsonl").read_bytes(), b"session-fixture\n")
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)
        self.assertEqual((paths.accounts_dir() / "second/auth.json").read_bytes(), self.auth)
        self.assertFalse(source.exists())
        self.assertEqual(set(self.monitor.states), {"账号 A", "second"})
        self.assertTrue(self.monitor.states[renamed].active)
        records = self.monitor.annotations.read()
        self.assertNotIn("profile:first", records)
        self.assertEqual(records["profile:账号 A"], {"tags": ["工作账号", "用不了"], "unavailable": True})
        self.assertEqual(records["profile:second"]["tags"], ["另一存档"])
        self.monitor.reload_profiles()
        account = next(a for a in self.monitor.snapshot()["accounts"] if a["name"] == renamed)
        self.assertEqual(account["tags"], ["工作账号", "用不了"])
        self.assertTrue(account["manual_unavailable"])

    def test_empty_and_same_name_are_supported_without_erasing_notes(self):
        (paths.accounts_dir() / "empty").mkdir()
        self.monitor.reload_profiles()
        self.monitor.set_annotations("empty", ["重新登录"], False)
        self.assertEqual(self.monitor.rename_account("empty", "empty", self.revision("empty")), "empty")
        self.monitor.rename_account("empty", "renamed-empty", self.revision("empty"))
        self.assertFalse((paths.accounts_dir() / "renamed-empty/auth.json").exists())
        self.assertIn("renamed-empty", self.monitor.states)
        self.assertEqual(self.monitor.annotations.read()["profile:renamed-empty"]["tags"], ["重新登录"])

    def test_collisions_and_unsafe_names_leave_everything_intact(self):
        (paths.accounts_dir() / "empty").mkdir()
        for target in ("second", "empty", "../outside", ".hidden", "_deleted", "a/b", "a\\b",
                       "main", "CON", "com1.txt", "trailing.", "", "x" * 81, None):
            with self.subTest(target=target), self.assertRaises(store.StoreError):
                self.monitor.rename_account("first", target, self.revision())
            self.assertEqual((paths.accounts_dir() / "first/auth.json").read_bytes(), self.auth)
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)

    def test_stale_selection_and_running_login_are_rejected(self):
        revision = self.revision()
        store.atomic_write(paths.accounts_dir() / "first/auth.json", b"{}")
        with self.assertRaises(store.StoreError): self.monitor.rename_account("first", "new", revision)
        with self.assertRaises(store.StoreError):
            self.monitor.rename_account("first", "new", store.removal_revision("first"))
        self.monitor.reload_profiles()
        revision = self.revision()
        self.monitor.login_name = "first"
        self.monitor.login = SimpleNamespace(phase="waiting")
        with self.assertRaises(store.StoreError): self.monitor.rename_account("first", "new", revision)
        self.assertFalse((paths.accounts_dir() / "new").exists())

    def test_corrupt_annotations_and_failed_save_roll_back(self):
        self.monitor.annotations.path.write_text("invalid", encoding="utf-8")
        with self.assertRaises(store.StoreError): store.rename("first", "new")
        self.assertFalse((paths.accounts_dir() / "new").exists())
        self.monitor.annotations.path.unlink()
        self.monitor.set_annotations("first", ["保留"], False)
        notes = self.monitor.annotations.path.read_bytes()
        with patch("codex_monitor.annotations.atomic_write", side_effect=PermissionError("fixture")):
            with self.assertRaises(OSError): store.rename("first", "new")
        self.assertFalse((paths.accounts_dir() / "new").exists())
        self.assertEqual((paths.accounts_dir() / "first/auth.json").read_bytes(), self.auth)
        self.assertEqual(self.monitor.annotations.path.read_bytes(), notes)

    def test_main_links_and_late_token_write_are_protected(self):
        with self.assertRaises(store.StoreError): self.monitor.rename_account("main", "new", "x")
        if os.name != "nt":
            (paths.accounts_dir() / "linked").symlink_to(paths.codex_home(), target_is_directory=True)
            with self.assertRaises(store.StoreError): store.rename("linked", "new")
            (paths.accounts_dir() / "target-link").symlink_to(Path(self.sandbox.name) / "missing", target_is_directory=True)
            with self.assertRaises(store.StoreError): store.rename("first", "target-link")
        old_path = paths.accounts_dir() / "first/auth.json"
        real_write = store.atomic_write
        def rename_then_write(path, payload, create_parent=True):
            store.rename("first", "renamed")
            return real_write(path, payload, create_parent=create_parent)
        with patch("codex_monitor.oauth.atomic_write", side_effect=rename_then_write):
            with self.assertRaises(OSError): apply_refreshed(old_path, {"access_token": "fake-new-token"})
        self.assertFalse(old_path.parent.exists())
        self.assertEqual((paths.accounts_dir() / "renamed/auth.json").read_bytes(), self.auth)

    def test_authenticated_api_rename_and_stale_request(self):
        server = make_server(self.monitor, port=0, token="rename-fixture")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(body, token="rename-fixture"):
            req = urllib.request.Request("http://127.0.0.1:%d/api/rename" % server.server_port,
                data=json.dumps(body).encode(), headers={"X-Token": token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as error: return error.code, json.load(error)
        try:
            body = {"name": "first", "new_name": "新账号 A", "revision": self.revision()}
            self.assertEqual(request(body, "wrong")[0], 403)
            self.assertEqual(request({**body, "revision": "stale"})[0], 400)
            self.assertEqual(request({**body, "new_name": "second"})[0], 400)
            status, result = request(body)
            self.assertEqual(status, 200)
            self.assertEqual(result["name"], "新账号 A")
            download = urllib.request.Request("http://127.0.0.1:%d/api/auth/%s?download=1" % (
                server.server_port, urllib.parse.quote("新账号 A")), headers={"X-Token": "rename-fixture"})
            with urllib.request.urlopen(download, timeout=5) as response:
                self.assertEqual(response.read(), self.auth)
                self.assertIn("filename*=UTF-8''auth-", response.headers["Content-Disposition"])
            self.assertEqual(request(body)[0], 400)
            self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_inflight_refresh_does_not_recreate_old_name(self):
        revision = self.revision()
        def rename_during_fetch(*args, **kwargs):
            self.monitor.rename_account("first", "renamed", revision)
            raise FileNotFoundError("Account renamed during refresh")
        with patch("codex_monitor.usage.fetch_usage_auto", side_effect=rename_during_fetch):
            self.monitor.refresh_one("first")
        self.assertNotIn("first", self.monitor.states)
        self.assertIn("renamed", self.monitor.states)
        self.assertFalse((paths.accounts_dir() / "first").exists())
        self.assertEqual((paths.accounts_dir() / "renamed/auth.json").read_bytes(), self.auth)


if __name__ == "__main__": unittest.main()
