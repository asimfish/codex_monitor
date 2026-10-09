"""Sandboxed alias removal, recovery, stale selection and authenticated API checks."""
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


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="codex-removal-")
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex"),
            "CODEX_ACCOUNTS_DIR": str(self.root / "accounts"), "CODEX_MONITOR_DEMO": "0"})
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
        self.directory.cleanup()

    def revision(self, name):
        return next(a["removal_revision"] for a in self.monitor.snapshot()["accounts"] if a["name"] == name)

    def test_one_alias_removed_current_login_and_notes_kept_then_restore(self):
        self.monitor.set_annotations("first", ["保留备注"], False)
        notes = self.monitor.annotations.path.read_bytes()
        backup = Path(self.monitor.remove_account("first", self.revision("first")))
        self.assertEqual((backup / "auth.json").read_bytes(), self.auth)
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)
        self.assertEqual((paths.accounts_dir() / "second/auth.json").read_bytes(), self.auth)
        self.assertEqual(self.monitor.annotations.path.read_bytes(), notes)
        self.assertEqual([p.name for p in store.profiles()], ["second"])
        backup.rename(paths.accounts_dir() / "first")
        self.monitor.reload_profiles()
        restored = next(a for a in self.monitor.snapshot()["accounts"] if a["name"] == "first")
        self.assertEqual(restored["tags"], ["保留备注"])

    def test_empty_profile_and_last_active_archive_can_be_removed(self):
        (paths.accounts_dir() / "empty").mkdir()
        self.monitor.reload_profiles()
        self.monitor.remove_account("empty", self.revision("empty"))
        self.monitor.remove_account("first", self.revision("first"))
        self.monitor.remove_account("second", self.revision("second"))
        state = self.monitor.snapshot()["accounts"]
        self.assertEqual(len(state), 1)
        self.assertTrue(state[0]["is_main"])
        self.assertIsNone(state[0]["removal_revision"])
        with self.assertRaises(store.StoreError): self.monitor.remove_account("main", "invalid")
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)

    def test_stale_selection_and_running_login_are_rejected(self):
        revision = self.revision("first")
        store.atomic_write(paths.accounts_dir() / "first/auth.json", b'{"OPENAI_API_KEY":"fake-replacement"}')
        with self.assertRaises(store.StoreError): self.monitor.remove_account("first", revision)
        # Even a new file revision cannot authorize deleting a different identity shown by an old row.
        with self.assertRaises(store.StoreError): self.monitor.remove_account("first", self.revision("first"))
        self.assertTrue((paths.accounts_dir() / "first").exists())
        self.monitor.login_name = "second"
        self.monitor.login = SimpleNamespace(phase="waiting", error=None)
        with self.assertRaises(store.StoreError): self.monitor.remove_account("second", self.revision("second"))

    def test_paths_metadata_and_links_are_protected(self):
        for name in ("../codex", "", "_deleted", ".cache", "first/..", "first\\.."):
            with self.assertRaises(store.StoreError): store.remove(name)
        if os.name != "nt":
            outside = self.root / "outside"; outside.mkdir()
            (paths.accounts_dir() / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(store.StoreError): store.remove("linked")
            (paths.accounts_dir() / "_deleted").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(store.StoreError): store.remove("first")
            self.assertEqual(list(outside.iterdir()), [])

    def test_late_refresh_cannot_recreate_directory(self):
        auth_path = paths.accounts_dir() / "first/auth.json"
        real_write = store.atomic_write
        def remove_then_write(path, payload, create_parent=True):
            store.remove("first")
            return real_write(path, payload, create_parent=create_parent)
        with patch("codex_monitor.oauth.atomic_write", side_effect=remove_then_write):
            with self.assertRaises(OSError): apply_refreshed(auth_path, {"access_token": "fake-new-token"})
        self.assertFalse(auth_path.parent.exists())
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)

    def test_authenticated_api_rejects_bad_or_stale_targets(self):
        server = make_server(self.monitor, port=0, token="removal-fixture")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = "http://127.0.0.1:%d/api/remove" % server.server_port
        def request(body, token="removal-fixture"):
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as error: return error.code, json.load(error)
        try:
            valid = {"name": "first", "revision": self.revision("first")}
            self.assertEqual(request(valid, "wrong")[0], 403)
            for body in ({"name": "../codex", "revision": "x"}, {"name": "first"}, {**valid, "revision": "stale"}):
                self.assertEqual(request(body)[0], 400)
            status, result = request(valid)
            self.assertEqual(status, 200)
            self.assertTrue(Path(result["backup"]).is_dir())
            self.assertEqual(request(valid)[0], 400)
            self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()

    def test_inflight_refresh_finishes_without_resurrecting_removed_profile(self):
        revision = self.revision("first")
        def remove_during_fetch(*args, **kwargs):
            self.monitor.remove_account("first", revision)
            raise FileNotFoundError("Profile removed during refresh")
        with patch("codex_monitor.usage.fetch_usage_auto", side_effect=remove_during_fetch):
            self.monitor.refresh_one("first")
        self.assertNotIn("first", self.monitor.states)
        self.assertFalse((paths.accounts_dir() / "first").exists())
        self.assertEqual(paths.main_auth_path().read_bytes(), self.auth)


if __name__ == "__main__": unittest.main()
