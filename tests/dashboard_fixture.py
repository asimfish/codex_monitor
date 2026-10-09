"""Fabricated dashboard data for browser checks; never reads personal credentials."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex_monitor import demo
from codex_monitor.monitor import Monitor
from codex_monitor.usage import LiveEvent
from codex_monitor.web import dashboard_url, make_server


def fixture_monitor() -> Monitor:
    now = datetime.now(timezone.utc)
    alice, bob, carol, dave = demo.states(now)
    alice.active = False
    dave.active = True  # An invalid current login must not displace usable accounts.
    alice.usage["credits"] = {"has_credits": True, "balance": "12.50"}
    alice.ident["subscription_checked"] = now - timedelta(days=2)
    alice.live_extras["review"] = LiveEvent(
        timestamp=now, limit_id="review", limit_name="Code review", primary={
            "used_percent": 10, "limit_window_seconds": 604800,
            "reset_at": (now + timedelta(days=3)).timestamp()},
        secondary=None, plan_type="pro", limit_reached=False)
    bob.ident["subscription_until"] = now - timedelta(days=2)  # Login snapshot, not validity.
    erin = copy.deepcopy(bob)
    erin.name = "erin"
    erin.display_name = "erin.long.email.for.layout@example.com"
    erin.ident = {**bob.ident, "account_id": "acct-erin", "email": erin.display_name}
    erin.usage["rate_limit"]["secondary_window"]["used_percent"] = 25
    finn = copy.deepcopy(bob)
    finn.name, finn.display_name = "finn", "finn@example.com"
    finn.ident = {**bob.ident, "account_id": "acct-finn", "access_expires": now - timedelta(days=1)}
    guest = copy.deepcopy(bob)
    guest.name, guest.display_name = "guest", "guest"
    guest.ident, guest.usage, guest.fetched_at = {}, None, None
    ivy = copy.deepcopy(bob)
    ivy.name, ivy.display_name = "ivy", "ivy@example.com"
    ivy.ident = {**bob.ident, "account_id": "acct-ivy"}
    ivy.usage, ivy.fetched_at = None, None
    zeta = copy.deepcopy(bob)
    zeta.name, zeta.display_name = "zeta", "zeta@example.com"
    zeta.ident = {**bob.ident, "account_id": "acct-zeta"}
    monitor = Monitor()
    values = [dave, carol, zeta, guest, finn, ivy, bob, alice, erin]
    monitor.states = {state.name: state for state in values}
    monitor.order = list(monitor.states)
    monitor.last_refresh = now
    monitor.set_annotations("alice", ["工作主号"], False)
    monitor.set_annotations("zeta", ["账号被删除"], True)
    return monitor


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codex-web-fixture-") as directory:
        os.environ["CODEX_HOME"] = str(Path(directory) / "codex")
        os.environ["CODEX_ACCOUNTS_DIR"] = str(Path(directory) / "accounts")
        os.environ.pop("CODEX_MONITOR_DEMO", None)
        server = make_server(fixture_monitor(), port=0)
        print(dashboard_url(server) + "&lang=zh", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
