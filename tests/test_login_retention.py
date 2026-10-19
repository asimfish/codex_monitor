"""Login cancellation, isolated browser launch and duplicate presentation regressions."""
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import browsers, demo, paths, store
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor


class LoginRetentionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cm-login-retention-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {"CODEX_HOME": str(root / "main"),
                                     "CODEX_ACCOUNTS_DIR": str(root / "accounts"),
                                     "CODEX_MONITOR_DEMO": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.monitor = Monitor()

    def test_cancelled_new_account_stays_visible_and_can_retry(self):
        flow = Mock(phase="waiting", error=None, result_identity=None)
        with patch("codex_monitor.monitor.BrowserLogin", return_value=flow):
            self.monitor.start_login("unfinished")
            self.monitor.cancel_login()
            self.monitor.reload_profiles()
            self.assertIn("unfinished", self.monitor.states)
            self.assertFalse(store.has_stored_credentials(paths.accounts_dir() / "unfinished"))
            self.monitor.start_login("unfinished", relogin=True)
        self.assertTrue((paths.accounts_dir() / "unfinished").is_dir())

    def test_same_login_aliases_have_one_representative_without_deleting_files(self):
        now = now_utc()
        auth = demo._auth("same@example.test", "plus", "workspace", None, now + timedelta(days=2), now)
        for name in ("first", "second"):
            store.import_text(name, json.dumps(auth))
        self.monitor.reload_profiles()
        accounts = self.monitor.snapshot()["accounts"]
        self.assertEqual(sum(not a.get("duplicate_of") for a in accounts), 1)
        self.assertEqual(len(store.profiles()), 2)
        duplicate = next(a for a in accounts if a.get("duplicate_of"))
        self.assertNotEqual(duplicate["name"], duplicate["duplicate_of"])

    def test_browser_open_uses_different_profiles_and_does_not_fall_back(self):
        process = Mock()
        process.wait.return_value = 0
        with patch.object(browsers, "find_private_browser", return_value=("Google Chrome", ["chrome", "--incognito"])), patch.object(browsers.subprocess, "Popen", return_value=process) as launch:
            browsers.open_private("https://example.test/one")
            browsers.open_private("https://example.test/two")
        profiles = [[arg for arg in call.args[0] if arg.startswith("--user-data-dir=")]
                    for call in launch.call_args_list]
        self.assertTrue(all(profiles), "private windows need isolated cookie stores")
        self.assertNotEqual(profiles[0], profiles[1])
        self.assertTrue(all("--incognito" in call.args[0] for call in launch.call_args_list))

    def test_private_open_failure_never_opens_a_regular_window(self):
        self.monitor.login = Mock(url="https://example.test/oauth", phase="waiting")
        with patch.object(browsers, "open_private", return_value=None), patch.object(browsers, "open_default") as normal:
            with self.assertRaises(store.StoreError):
                self.monitor.open_login_url(True)
        normal.assert_not_called()

    def test_different_workspaces_users_and_unknown_profiles_stay_separate(self):
        now = now_utc()
        def profile(name, account, user, email):
            return store.Profile(name, paths.accounts_dir() / name, None, ident={
                "account_id": account, "user_id": user, "email": email,
                "last_refresh": now, "access_expires": now + timedelta(days=1)})
        first = profile("first", "workspace", "user", "same@example.test")
        legacy = profile("legacy", "workspace", "", " SAME@EXAMPLE.TEST ")
        self.assertEqual(len(store.display_profiles([first, legacy])), 1)
        other = profile("other", "workspace", "other-user", "same@example.test")
        self.assertEqual(len(store.display_profiles([first, legacy, other])), 3)
        workspace = profile("workspace", "different", "user", "same@example.test")
        self.assertEqual(len(store.display_profiles([first, workspace])), 2)
        unknown = [profile("unknown1", "", "", ""), profile("unknown2", "", "", "")]
        self.assertEqual(len(store.display_profiles(unknown)), 2)

    def test_cancelled_relogin_preserves_credentials_and_tags(self):
        now = now_utc()
        auth = demo._auth("same@example.test", "plus", "workspace", None, now + timedelta(days=2), now)
        profile = store.import_text("existing", json.dumps(auth))
        self.monitor.reload_profiles()
        self.monitor.set_annotations("existing", ["keep-tag"], False)
        before = profile.auth_path.read_bytes()
        flow = Mock(phase="waiting", error=None, result_identity=None)
        with patch("codex_monitor.monitor.BrowserLogin", return_value=flow):
            self.monitor.start_login("existing", relogin=True)
            self.monitor.cancel_login()
        self.assertEqual(profile.auth_path.read_bytes(), before)
        account = next(a for a in self.monitor.snapshot()["accounts"] if a["name"] == "existing")
        self.assertEqual(account["tags"], ["keep-tag"])


if __name__ == "__main__":
    unittest.main()
