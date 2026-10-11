"""Real dashboard/storage with fabricated credentials and isolated external effects."""
import copy
import json
import os
import sys
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex_monitor import demo, paths, store
from codex_monitor.identity import now_utc
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server


class LoginFixture:
    """Exercise Monitor's real login lifecycle without a provider or browser launch."""
    mode = "browser"

    def __init__(self, directory):
        self.directory = directory
        self.phase, self.error = "starting", None
        self.url = "https://fixture.invalid/authorize"
        self.code = "ABCD-EFGH"

    def start(self):
        self.phase = "failed" if self.directory.name == "login-failure" and self.mode == "browser" else "waiting"
        self.error = "fixture login failure" if self.phase == "failed" else None

    def cancel(self):
        self.phase = "cancelled"


class DeviceFixture(LoginFixture):
    mode = "device"


if __name__ == "__main__":
    # Require caller-owned sandbox paths rather than default account locations.
    root = Path(os.environ["BUTTONS_SANDBOX"])
    os.environ.update(CODEX_HOME=str(root / "codex"), CODEX_ACCOUNTS_DIR=str(root / "accounts"), CODEX_MONITOR_DEMO="0")
    now = now_utc()
    for name in ("alice", "bob", "current"):
        auth = demo._auth(name + "@example.com", "plus", "acct-" + name, None, now + timedelta(days=3), now)
        target = paths.main_auth_path() if name == "current" else paths.accounts_dir() / name / "auth.json"
        store.atomic_write(target, json.dumps(auth).encode())
    (paths.accounts_dir() / "guest").mkdir()
    installed, polls, cards = [False], [0], {}

    def quota(path, **kwargs):
        polls[0] += 1
        data = copy.deepcopy(demo.states()[1].usage)
        data["rate_limit"]["primary_window"]["used_percent"] = polls[0] % 50
        return data, store.read_json(path), False

    def install(extra):
        installed[0] = True
        return "fixture autostart installed"

    def remove():
        installed[0] = False
        return "fixture autostart removed"

    def credits(auth):
        return {"available_count": cards.get(auth["tokens"]["account_id"], 2)}

    def reset(auth, request_id):
        account = auth["tokens"]["account_id"]
        cards[account] = cards.get(account, 2) - 1
        return {"code": "reset", "windows_reset": 2}

    with ExitStack() as stack:
        for target, replacement in [
            ("codex_monitor.usage.fetch_usage_auto", quota),
            ("codex_monitor.usage.fetch_reset_credits", credits),
            ("codex_monitor.usage.consume_reset_credit", reset),
            ("codex_monitor.monitor.BrowserLogin", LoginFixture),
            ("codex_monitor.monitor.DeviceCodeLogin", DeviceFixture),
            ("codex_monitor.browsers.find_private_browser", lambda: ("FixtureBrowser", "fixture")),
            ("codex_monitor.browsers.open_private", lambda url: "FixtureBrowser"),
            ("codex_monitor.browsers.open_default", lambda url: True),
            ("codex_monitor.autostart.status", lambda: "installed" if installed[0] else "not installed"),
            ("codex_monitor.autostart.install", install),
            ("codex_monitor.autostart.remove", remove),
            ("codex_monitor.store.codex_processes_running", lambda: False),
        ]:
            stack.enter_context(patch(target, replacement))
        monitor = Monitor()
        monitor.reload_profiles()
        monitor.refresh_all(force=True)
        server = make_server(monitor, port=0, token="buttons-fixture")
        print(server.server_port, flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
