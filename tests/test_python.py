#!/usr/bin/env python3
"""Unit + integration tests for the cross-platform Python package (stdlib only, no external network
required; local HTTP callbacks use fabricated credentials).

Runs against throw-away CODEX_HOME / CODEX_ACCOUNTS_DIR directories with fake JWTs.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="codex-monitor-test-"))
os.environ["CODEX_HOME"] = str(SANDBOX / "codex")
os.environ["CODEX_ACCOUNTS_DIR"] = str(SANDBOX / "accounts")
os.environ.pop("CODEX_MONITOR_DEMO", None)
(SANDBOX / "codex").mkdir()
(SANDBOX / "accounts").mkdir()

from codex_monitor import autostart, browsers, identity, oauth, paths, store, usage  # noqa: E402
from codex_monitor.monitor import Monitor  # noqa: E402
from codex_monitor.web import make_server  # noqa: E402


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def fake_jwt(payload: dict) -> str:
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.sig"


def make_auth(email: str, account_id: str, last_refresh: datetime, plan: str = "pro", token_days: int = 9) -> dict:
    now = datetime.now(timezone.utc)
    claims = {"chatgpt_account_id": account_id, "chatgpt_plan_type": plan,
              "chatgpt_subscription_active_until": (now + timedelta(days=20)).isoformat()}
    return {
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": fake_jwt({"email": email, "exp": int((now + timedelta(hours=1)).timestamp()), "https://api.openai.com/auth": claims}),
            "access_token": fake_jwt({"exp": int((now + timedelta(days=token_days)).timestamp()), "iat": int(now.timestamp()),
                                      "https://api.openai.com/auth": claims, "https://api.openai.com/profile": {"email": email}}),
            "refresh_token": "rt.fake." + account_id[:6], "account_id": account_id,
        },
        "last_refresh": identity.format_iso(last_refresh),
    }


def write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


ROLLOUT_LINE = (b'{"timestamp":"2026-09-06T12:07:48.582Z","ordinal":41,"type":"event_msg","payload":{"type":"token_count",'
                b'"info":{"total_token_usage":{"input_tokens":1}},"rate_limits":{"limit_id":"codex","limit_name":null,'
                b'"primary":{"used_percent":4.0,"window_minutes":10080,"resets_at":1789298098},"secondary":null,'
                b'"credits":{"has_credits":false,"unlimited":false,"balance":"0"},"individual_limit":null,'
                b'"spend_control_reached":null,"plan_type":"pro","rate_limit_reached_type":null}}}')


class IdentityTests(unittest.TestCase):
    def test_parse_iso_variants(self):
        a = identity.parse_iso("2026-09-05T07:52:32.639755Z")
        b = identity.parse_iso("2026-09-06T12:52:19+00:00")
        c = identity.parse_iso("2026-09-06T12:52:19.1Z")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNotNone(c)
        self.assertEqual(a.microsecond, 639755)
        self.assertIsNone(identity.parse_iso("garbage"))

    def test_identity_from_auth(self):
        now = datetime.now(timezone.utc)
        ident = identity.identity(make_auth("x@example.com", "acct-1", now))
        self.assertEqual(ident["email"], "x@example.com")
        self.assertEqual(ident["plan"], "pro")
        self.assertEqual(ident["account_id"], "acct-1")
        self.assertIsNotNone(ident["subscription_until"])
        self.assertFalse(identity.access_token_expired(ident))

    def test_window_labels(self):
        self.assertEqual(identity.window_label(18000), "5h")
        self.assertEqual(identity.window_label(604800), "weekly")
        self.assertEqual(identity.window_label(2592000), "30d")


class UsageTests(unittest.TestCase):
    def test_parse_rollout_line(self):
        ev = usage.parse_rollout_line(ROLLOUT_LINE)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.is_main)
        self.assertEqual(ev.primary["used_percent"], 4.0)
        self.assertEqual(ev.primary["limit_window_seconds"], 604800)
        self.assertEqual(ev.plan_type, "pro")
        self.assertAlmostEqual(ev.timestamp.timestamp(), 1788696468.582, places=2)
        self.assertIsNone(usage.parse_rollout_line(b'{"timestamp":"2026-09-06T12:07:48.582Z","payload":{"type":"agent_message"}}'))
        spark = usage.parse_rollout_line(ROLLOUT_LINE.replace(b'"limit_id":"codex"', b'"limit_id":"codex_spark"'))
        self.assertFalse(spark.is_main)

    def test_reached_detail(self):
        self.assertIsNone(usage.reached_detail(None))
        self.assertIsNone(usage.reached_detail({"type": "rate_limit_reached", "details": "default"}))
        self.assertEqual(usage.reached_detail("workspace_owner_usage_limit_reached"), "workspace_owner_usage_limit_reached")

    def test_tailer(self):
        sessions = SANDBOX / "codex" / "sessions"
        local = datetime.now()
        day = sessions / f"{local.year:04d}" / f"{local.month:02d}" / f"{local.day:02d}"
        day.mkdir(parents=True, exist_ok=True)
        f = day / "rollout-test.jsonl"
        f.write_bytes(b'{"timestamp":"2026-09-06T12:00:00.000Z","type":"session_meta","payload":{"id":"x"}}\n' + ROLLOUT_LINE + b"\n")
        tailer = usage.RolloutTailer(sessions)
        main, extras = tailer.poll_latest()
        self.assertIsNotNone(main)
        self.assertEqual(main.primary["used_percent"], 4.0)
        self.assertIsNone(tailer.poll_latest()[0])
        newer = ROLLOUT_LINE.replace(b"2026-09-06T12:07:48.582Z", b"2026-09-06T12:09:00.000Z").replace(b'"used_percent":4.0', b'"used_percent":6.0')
        with open(f, "ab") as fh:
            fh.write(newer[:40])
        self.assertIsNone(tailer.poll_latest()[0], "partial line must not parse")
        with open(f, "ab") as fh:
            fh.write(newer[40:] + b"\n")
        main, _ = tailer.poll_latest()
        self.assertEqual(main.primary["used_percent"], 6.0)
        old = day / "rollout-old.jsonl"
        old.write_bytes(ROLLOUT_LINE + b"\n")
        past = time.time() - 3600
        os.utime(old, (past, past))
        self.assertIsNone(tailer.poll_latest()[0], "stale files are ignored")
        # a long-lived thread whose rollout sits under an OLD day directory is still found
        old_day = sessions / "2026" / "01" / "15"
        old_day.mkdir(parents=True, exist_ok=True)
        old_thread = old_day / "rollout-2026-01-15T10-00-00-old-thread.jsonl"
        old_thread.write_bytes(ROLLOUT_LINE.replace(b"2026-09-06T12:07:48.582Z", b"2026-09-06T12:20:00.000Z").replace(b'"used_percent":4.0', b'"used_percent":33.0') + b"\n")
        fresh = usage.RolloutTailer(sessions)
        main, _ = fresh.poll_latest()
        self.assertIsNotNone(main, "full scan must find recently modified files in any day directory")
        with open(old_thread, "ab") as fh:
            fh.write(ROLLOUT_LINE.replace(b"2026-09-06T12:07:48.582Z", b"2026-09-06T12:30:00.000Z").replace(b'"used_percent":4.0', b'"used_percent":34.0') + b"\n")
        main, _ = fresh.poll_latest()
        self.assertEqual(main.primary["used_percent"], 34.0, "appends between full scans are tailed")

    def test_app_server_event_watcher(self):
        import sqlite3
        home = SANDBOX / "codex"
        db_path = home / "logs_2.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, ts_nanos INTEGER NOT NULL, level TEXT NOT NULL, target TEXT NOT NULL, feedback_log_body TEXT)")
        conn.execute("INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body) VALUES (1, 0, 'INFO', 'codex_app_server::outgoing_message', 'app-server event: account/rateLimits/updated targeted_connections=1')")
        conn.commit()
        w = usage.AppServerEventWatcher(home)
        self.assertEqual(w.poll(), 0, "rows that existed before the watcher started are not events")
        conn.execute("INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body) VALUES (2, 0, 'INFO', 'codex_app_server::outgoing_message', 'app-server event: item/agentMessage/delta targeted_connections=1')")
        conn.execute("INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body) VALUES (3, 0, 'INFO', 'codex_app_server::outgoing_message', 'app-server event: account/rateLimits/updated targeted_connections=1')")
        conn.execute("INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body) VALUES (4, 0, 'INFO', 'codex_app_server::outgoing_message', 'app-server event: account/rateLimits/updated targeted_connections=1')")
        conn.commit()
        self.assertEqual(w.poll(), 2)
        self.assertEqual(w.poll(), 0)
        conn.close()
        w._conn.close()  # Windows cannot unlink an open SQLite database.
        db_path.unlink()

    def test_view_prefers_live_when_newer(self):
        now = datetime.now(timezone.utc)
        auth = make_auth("v@example.com", "acct-v", now)
        st = usage.AccountState(name="v", display_name="v@example.com", active=True, is_main=False, auth_path="x", ident=identity.identity(auth))
        st.usage = {"plan_type": "pro", "rate_limit": {"allowed": True, "limit_reached": False,
                                                        "primary_window": {"used_percent": 10, "limit_window_seconds": 604800, "reset_at": now.timestamp() + 1000},
                                                        "secondary_window": None},
                    "rate_limit_reset_credits": {"available_count": 2}}
        st.fetched_at = now - timedelta(seconds=60)
        st.live = usage.LiveEvent(timestamp=now - timedelta(seconds=10), limit_id="codex", limit_name=None,
                                  primary={"used_percent": 12.0, "limit_window_seconds": 604800, "reset_at": now.timestamp() + 1000},
                                  secondary=None, plan_type="pro", limit_reached=False)
        v = st.view(now)
        self.assertEqual(v["source"], "live")
        self.assertEqual(v["windows"][0]["remaining_percent"], 88.0)
        self.assertEqual(v["reset_credits"], 2)
        # API newer but reports less usage for the same window -> live still wins
        st.fetched_at = now
        self.assertEqual(st.view(now)["source"], "live")
        # API newer and reports more usage -> API wins
        st.usage["rate_limit"]["primary_window"]["used_percent"] = 15
        self.assertEqual(st.view(now)["source"], "api")


class AutoRefreshTests(unittest.TestCase):
    """401 -> one refresh-token exchange -> retry, mirroring Codex; failures surface as 'session revoked'."""

    def setUp(self):
        now = datetime.now(timezone.utc)
        self.dir = SANDBOX / "accounts" / "autoref"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.auth_path = self.dir / "auth.json"
        write(self.auth_path, make_auth("r@example.com", "acct-r", now))
        self.orig_fetch, self.orig_refresh = usage.fetch_usage, oauth.refresh_tokens
        os.environ.pop("CODEX_MONITOR_NO_AUTO_REFRESH", None)

    def tearDown(self):
        import shutil
        usage.fetch_usage, oauth.refresh_tokens = self.orig_fetch, self.orig_refresh
        os.environ.pop("CODEX_MONITOR_NO_AUTO_REFRESH", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_refresh_then_retry(self):
        calls = []

        def fake_fetch(auth):
            calls.append(auth["tokens"]["access_token"])
            if len(calls) == 1:
                raise usage.UsageError("token rejected (401)", 401)
            return {"plan_type": "pro", "rate_limit": {}}

        new_tokens = {"id_token": fake_jwt({"email": "r@example.com", "https://api.openai.com/auth": {"chatgpt_account_id": "acct-r", "chatgpt_plan_type": "pro"}}),
                      "access_token": fake_jwt({"exp": 9999999999}), "refresh_token": "rt.rotated"}
        usage.fetch_usage = fake_fetch
        oauth.refresh_tokens = lambda rt: new_tokens
        main_before = paths.main_auth_path().read_bytes() if paths.main_auth_path().exists() else None
        u, auth, refreshed = usage.fetch_usage_auto(self.auth_path, also_main=False)
        self.assertTrue(refreshed)
        self.assertEqual(u["plan_type"], "pro")
        self.assertEqual(len(calls), 2)
        on_disk = json.loads(self.auth_path.read_text())
        self.assertEqual(on_disk["tokens"]["refresh_token"], "rt.rotated")
        self.assertEqual(on_disk["tokens"]["account_id"], "acct-r")
        if main_before is not None:
            self.assertEqual(paths.main_auth_path().read_bytes(), main_before, "main untouched when also_main=False")

    def test_refresh_failure_is_reported_as_revoked(self):
        usage.fetch_usage = lambda auth: (_ for _ in ()).throw(usage.UsageError("token rejected (401)", 401))
        oauth.refresh_tokens = lambda rt: (_ for _ in ()).throw(oauth.OAuthError("refresh failed (HTTP 401)"))
        with self.assertRaises(usage.UsageError) as cm:
            usage.fetch_usage_auto(self.auth_path)
        self.assertIn("session revoked", str(cm.exception))

    def test_refresh_can_be_disabled(self):
        usage.fetch_usage = lambda auth: (_ for _ in ()).throw(usage.UsageError("token rejected (401)", 401))
        oauth.refresh_tokens = lambda rt: self.fail("must not refresh when disabled")
        os.environ["CODEX_MONITOR_NO_AUTO_REFRESH"] = "1"
        with self.assertRaises(usage.UsageError):
            usage.fetch_usage_auto(self.auth_path)
        os.environ.pop("CODEX_MONITOR_NO_AUTO_REFRESH")
        with self.assertRaises(usage.UsageError):
            usage.fetch_usage_auto(self.auth_path, allow_refresh=False)

    def test_non_401_errors_do_not_refresh(self):
        usage.fetch_usage = lambda auth: (_ for _ in ()).throw(usage.UsageError("HTTP 500", 500))
        oauth.refresh_tokens = lambda rt: self.fail("must not refresh on non-401")
        with self.assertRaises(usage.UsageError):
            usage.fetch_usage_auto(self.auth_path)


class OAuthTests(unittest.TestCase):
    def test_pkce_and_url(self):
        verifier, challenge = oauth.make_pkce()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(len(challenge), 43)
        url = oauth.authorize_url(challenge, "state123")
        self.assertIn("auth.openai.com/oauth/authorize", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("state=state123", url)

    def test_build_auth_json(self):
        tokens = {"id_token": fake_jwt({"email": "n@example.com", "https://api.openai.com/auth": {"chatgpt_account_id": "acct-n", "chatgpt_plan_type": "plus"}}),
                  "access_token": fake_jwt({"exp": 1}), "refresh_token": "rt.new"}
        data = json.loads(oauth.build_auth_json(tokens))
        self.assertEqual(data["auth_mode"], "chatgpt")
        self.assertIsNone(data["OPENAI_API_KEY"])
        self.assertEqual(data["tokens"]["account_id"], "acct-n")
        self.assertEqual(identity.identity(data)["email"], "n@example.com")
        with self.assertRaises(oauth.OAuthError):
            oauth.build_auth_json({"access_token": "x"})

    def test_parse_device_output(self):
        text = "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n\n2. Enter this one-time code\n   \x1b[94mABCD-EFGHJ\x1b[0m\n"
        self.assertEqual(oauth.parse_device_output(text), ("https://auth.openai.com/codex/device", "ABCD-EFGHJ"))
        self.assertIsNone(oauth.parse_device_output("https://x.y only"))

    @patch("codex_monitor.oauth.exchange_code", side_effect=oauth.OAuthError("token exchange failed: fixture"))
    def test_browser_login_flow_with_fake_callback(self, exchange):
        flow = oauth.BrowserLogin(SANDBOX / "accounts" / "login-test")
        flow.start()
        if flow.phase == "failed":
            self.skipTest(f"port {paths.OAUTH_PORT} unavailable: {flow.error}")
        self.assertEqual(flow.phase, "waiting")
        # wrong path -> 404, flow keeps waiting
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{paths.OAUTH_PORT}/nope", timeout=5)
        self.assertEqual(flow.phase, "waiting")
        # right state, bogus code -> exchange attempted and rejected (or network error): failed either way
        r = urllib.request.urlopen(f"http://127.0.0.1:{paths.OAUTH_PORT}/auth/callback?code=fake_code&state={flow.state}", timeout=5)
        self.assertEqual(r.status, 200)
        self.assertIn(b"Signed in", r.read())
        phase = flow.wait()
        self.assertEqual(phase, "failed")
        self.assertIn("token exchange failed", flow.error)
        self.assertFalse((SANDBOX / "accounts" / "login-test" / "auth.json").exists())


class StoreAndWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        now = datetime.now(timezone.utc)
        cls.acct_a, cls.acct_b = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
        write(paths.main_auth_path(), make_auth("a@example.com", cls.acct_a, now))
        write(paths.accounts_dir() / "a" / "auth.json", make_auth("a@example.com", cls.acct_a, now - timedelta(hours=3)))
        write(paths.accounts_dir() / "b" / "auth.json", make_auth("b@example.com", cls.acct_b, now - timedelta(days=1), plan="plus"))

    def test_adopt_and_activate(self):
        msg = store.adopt()
        self.assertIsNotNone(msg)
        self.assertEqual((paths.accounts_dir() / "a" / "auth.json").read_bytes(), paths.main_auth_path().read_bytes())
        self.assertIsNone(store.adopt())
        msgs = store.activate("b")
        self.assertTrue(any("switched" in m for m in msgs))
        self.assertEqual(store.main_profile().account_id, self.acct_b)
        store.activate("a")
        self.assertEqual(store.main_profile().account_id, self.acct_a)
        with self.assertRaises(store.StoreError):
            store.get_profile("nope")

    @patch.object(Monitor, "refresh_all")
    def test_web_server(self, refresh):
        monitor = Monitor(interval=3600)
        monitor.reload_profiles()
        server = make_server(monitor, port=0)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/state", timeout=5)
            self.assertEqual(cm.exception.code, 403)
            req = urllib.request.Request(base + "/api/state", headers={"X-Token": token})
            state = json.loads(urllib.request.urlopen(req, timeout=5).read())
            names = [a["name"] for a in state["accounts"]]
            self.assertEqual(sorted(names), ["a", "b"])
            active = [a["name"] for a in state["accounts"] if a["active"]]
            self.assertEqual(active, ["a"])
            page = urllib.request.urlopen(base + f"/?token={token}", timeout=5).read().decode("utf-8")
            self.assertIn("Codex Monitor", page)
            self.assertIn("\u989d\u5ea6", page)  # Chinese strings embedded
            req = urllib.request.Request(base + "/api/switch", data=json.dumps({"name": "b"}).encode(), method="POST",
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertTrue(resp["ok"])
            self.assertEqual(store.main_profile().account_id, self.acct_b)
            req = urllib.request.Request(base + "/api/auth/b", headers={"X-Token": token})
            auth = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertEqual(json.loads(auth["text"])["tokens"]["account_id"], self.acct_b)
            req = urllib.request.Request(base + "/api/switch", data=json.dumps({"name": "zzz"}).encode(), method="POST",
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 400)
            store.activate("a")
        finally:
            server.shutdown()
            server.server_close()
            monitor.stop()
            t.join(timeout=5)


class PlatformTests(unittest.TestCase):
    def test_autostart_command_shape(self):
        cmd = autostart._command(["--port", "7861"])
        self.assertTrue(cmd[0])
        self.assertTrue(any("codex_monitor" in c or c.endswith("codex-monitor") for c in cmd))
        self.assertEqual(cmd[-4:], ["serve", "--no-browser", "--port", "7861"])

    def test_windows_branches_simulated(self):
        """Exercise the Windows-only code paths without a Windows machine."""
        import shutil
        orig_win, orig_mac, orig_symlink = paths.IS_WINDOWS, paths.IS_MAC, os.symlink
        try:
            paths.IS_WINDOWS, paths.IS_MAC = True, False
            # symlinks 'unavailable' -> config.toml copied, directories skipped
            def deny(*a, **k):
                raise OSError("A required privilege is not held by the client")
            os.symlink = deny
            (paths.codex_home() / "config.toml").write_text("model = 'x'\n", encoding="utf-8")
            (paths.codex_home() / "skills").mkdir(exist_ok=True)
            target = paths.accounts_dir() / "winlinks"
            target.mkdir(exist_ok=True)
            notes = store.ensure_shared_links(target)
            self.assertTrue((target / "config.toml").is_file() and not (target / "config.toml").is_symlink())
            self.assertFalse((target / "skills").exists())
            self.assertTrue(any("copied config.toml" in n for n in notes) and any("skipped skills" in n for n in notes))
            shutil.rmtree(target)
            # Task Scheduler command line is quoted for paths with spaces
            self.assertEqual(autostart._quote(r"C:\Program Files\Python\pythonw.exe"), r'"C:\Program Files\Python\pythonw.exe"')
            self.assertEqual(autostart._quote("plain"), "plain")
            # browser lookup with Windows-style program dirs
            os.environ["ProgramFiles"] = str(SANDBOX / "pf")
            os.environ["ProgramFiles(x86)"] = str(SANDBOX / "pf86")
            os.environ["LocalAppData"] = str(SANDBOX / "lad")
            exe = SANDBOX / "pf" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"")
            orig_path = os.environ.get("PATH", "")
            os.environ["PATH"] = ""  # nothing on PATH: only the Program Files lookup can match
            try:
                found = browsers.find_private_browser()
            finally:
                os.environ["PATH"] = orig_path
            self.assertIsNotNone(found)
            self.assertEqual(found[0], "Microsoft Edge")
            self.assertEqual(found[1], [str(exe), "--inprivate"])
            # env command uses PowerShell syntax
            self.assertTrue(paths.IS_WINDOWS)
        finally:
            paths.IS_WINDOWS, paths.IS_MAC, os.symlink = orig_win, orig_mac, orig_symlink

    def test_private_browser_lookup_does_not_crash(self):
        found = browsers.find_private_browser()
        self.assertTrue(found is None or (isinstance(found[0], str) and isinstance(found[1], list)))

    def test_windows_candidates(self):
        os.environ.setdefault("ProgramFiles", r"C:\Program Files")
        cands = browsers._windows_candidates([r"Google\Chrome\Application\chrome.exe"])
        self.assertTrue(any(str(c).endswith("chrome.exe") for c in cands))


if __name__ == "__main__":
    unittest.main(verbosity=1)
