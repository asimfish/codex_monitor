"""Fabricated API organization data for browser acceptance; no provider calls."""
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor.identity import now_utc, format_iso
from codex_monitor.monitor import Monitor
from codex_monitor.web import make_server
from codex_monitor.store import StoreError

with tempfile.TemporaryDirectory(prefix="cm-api-browser-") as temporary:
    os.environ["CODEX_HOME"] = str(Path(temporary) / "main")
    os.environ["CODEX_ACCOUNTS_DIR"] = str(Path(temporary) / "accounts")
    monitor = Monitor()
    manager = monitor.api_credits
    now = now_utc()
    for index, label in enumerate(["工作账号 <b>literal</b>", "个人 Pro", "研究账号"]):
        identifier = manager.save({"label": label, "organization": "org-demo%d" % index,
                                   "grant_total": "200", "admin_key": "",
                                   "period_start": format_iso(now-timedelta(days=5)),
                                   "period_end": format_iso(now+timedelta(days=25))})
        if index == 0:
            records = manager._load()
            records[identifier]["admin_key"] = "sk-admin-fabricated-browser-only"
            records[identifier]["last_success"] = format_iso(now)
            records[identifier]["last_attempt"] = format_iso(now)
            records[identifier]["costs"] = {"cost_usd": "25.50", "queried_until": format_iso(now), "days": []}
            manager._save(records)
    server = make_server(monitor, port=0, token="api-browser-fixture")
    def fixture_costs(key, organization, start, end):
        if key != "sk-admin-fabricated-connect-test":
            raise StoreError("Costs access denied (403). Use an organization owner's Admin API key and check the organization ID.")
        return {"cost_usd": "3.25", "queried_until": format_iso(end), "days": [], "organization": organization or "org-connected"}
    print("http://127.0.0.1:%d/api-credits?token=api-browser-fixture&lang=zh" % server.server_port, flush=True)
    try:
        with patch("codex_monitor.api_credits.fetch_costs", side_effect=fixture_costs):
            server.serve_forever()
    finally: server.server_close()
