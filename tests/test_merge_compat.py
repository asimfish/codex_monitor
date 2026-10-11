"""Main-branch consumer contracts, using fabricated credentials and temp storage."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor import demo, paths, store
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server


class MainBranchCompatibility(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="codex-compat-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "CODEX_HOME": str(root / "codex"),
            "CODEX_ACCOUNTS_DIR": str(root / "accounts"),
            "CODEX_MONITOR_DEMO": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        refresh = patch.object(Monitor, "refresh_all")
        refresh.start()
        self.addCleanup(refresh.stop)
        running = patch.object(store, "codex_processes_running", return_value=False)
        running.start()
        self.addCleanup(running.stop)
        now = now_utc()
        for name in ("current", "target"):
            auth = demo._auth(name + "@example.test", "plus", "fixture-" + name,
                              None, now + timedelta(days=3), now)
            destination = paths.main_auth_path() if name == "current" else paths.accounts_dir() / name / "auth.json"
            store.atomic_write(destination, json.dumps(auth).encode())
        self.monitor = Monitor()
        self.monitor.reload_profiles()

    def test_unarchived_main_keeps_legacy_name(self):
        current = next(account for account in self.monitor.snapshot()["accounts"] if account["is_main"])
        self.assertEqual(current["name"], "main")
        self.assertEqual(json.loads(self.monitor.auth_text("main"))["tokens"]["account_id"], "fixture-current")

    def test_switch_keeps_legacy_message_list_and_updates_credentials(self):
        messages = self.monitor.switch("target")
        self.assertIsInstance(messages, list)
        self.assertTrue(any("switched:" in message for message in messages))
        self.assertEqual(store.main_profile().account_id, "fixture-target")

    def test_web_switch_keeps_messages_and_adds_undo_information(self):
        server = make_server(self.monitor, port=0, token="compat-fixture")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:%d/api/switch" % server.server_port,
                data=json.dumps({"name": "target"}).encode(),
                headers={"X-Token": "compat-fixture", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
            self.assertTrue(result["ok"])
            self.assertIsInstance(result["messages"], list)
            self.assertFalse(result["codex_running"])
            self.assertEqual(store.get_profile(result["undo"]).account_id, "fixture-current")
            self.assertEqual(store.main_profile().account_id, "fixture-target")
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
