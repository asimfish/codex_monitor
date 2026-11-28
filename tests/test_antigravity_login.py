import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform != "win32":
    from codex_monitor.antigravity_login import LoginError, _auth_state, _valid_code, _valid_plan_tier
    from codex_monitor import antigravity_login


@unittest.skipIf(sys.platform == "win32", "isolated sign-in requires a POSIX terminal")
class AntigravityLoginTests(unittest.TestCase):
    state = "login-attempt-fixture-8e6bf2"

    def test_only_official_pkce_login_can_start(self):
        query = {
            "state": self.state,
            "response_type": "code",
            "code_challenge": "fixture-challenge",
            "code_challenge_method": "S256",
            "redirect_uri": "https://antigravity.google/oauth-callback",
        }
        oauth = "https://accounts.google.com/o/oauth2/auth?"
        self.assertEqual(_auth_state(oauth + urlencode(query)), self.state)
        query["redirect_uri"] = "https://unrelated.example/callback"
        with self.assertRaises(LoginError):
            _auth_state(oauth + urlencode(query))

    def test_only_one_authorization_code_can_reach_terminal(self):
        self.assertTrue(_valid_code("4/fixture-code-123456"))
        self.assertFalse(_valid_code("4/short"))
        self.assertFalse(_valid_code("4/fixture-code-123456\nsecond input"))
        self.assertFalse(_valid_code("https://example.invalid/auth"))
        self.assertFalse(_valid_code("4/" + "A" * 1023))

    def test_relogin_stages_fresh_credentials_and_preserves_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            token = profile / "antigravity-cli" / "antigravity-oauth-token"
            token.parent.mkdir(parents=True)
            token.write_bytes(b"previous fixture")

            def authorize(_command, staging, _work):
                self.assertNotEqual(staging, profile)
                self.assertEqual(list(staging.iterdir()), [])
                new_token = staging / "antigravity-cli" / token.name
                new_token.parent.mkdir()
                new_token.write_bytes(b"new fixture")
                return new_token

            with patch.multiple(antigravity_login, _profile_directory=lambda _id: profile,
                                _check_executable=lambda _command: None), \
                    patch.object(antigravity_login.signal, "signal"), patch.object(antigravity_login.os, "umask"):
                with patch.object(antigravity_login, "_login", side_effect=LoginError("cancelled")):
                    with self.assertRaises(LoginError):
                        antigravity_login.run("fixture", "agy")
                self.assertEqual(token.read_bytes(), b"previous fixture")
                with patch.object(antigravity_login, "_login", side_effect=authorize):
                    antigravity_login.run("fixture", "agy")
                self.assertEqual(token.read_bytes(), b"new fixture")
                self.assertEqual(token.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(profile.glob(".sign-in-*")), [])

    def test_identity_does_not_fall_back_to_system_email_for_unsigned_profile(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.multiple(antigravity_login, _profile_directory=lambda _id: Path(directory),
                               _check_executable=lambda _command: None), \
                patch.object(antigravity_login, "_terminal") as terminal:
            self.assertIsNone(antigravity_login.read_identity("agy", "fixture"))
            terminal.assert_not_called()

    def test_valid_plan_tier_validation(self):
        self.assertEqual(_valid_plan_tier("Google AI Ultra"), "Google AI Ultra")
        self.assertEqual(_valid_plan_tier("  Google AI Pro  "), "Google AI Pro")
        self.assertEqual(_valid_plan_tier("Gemini Enterprise Agent Platform"), "Gemini Enterprise Agent Platform")
        self.assertEqual(_valid_plan_tier("Google AI Pro (Trial)"), "Google AI Pro (Trial)")
        self.assertIsNone(_valid_plan_tier(""))
        self.assertIsNone(_valid_plan_tier("   "))
        self.assertIsNone(_valid_plan_tier(None))
        self.assertIsNone(_valid_plan_tier("Ultra\nInjected"))
        self.assertIsNone(_valid_plan_tier("A" * 49))
        self.assertIsNone(_valid_plan_tier("-invalid"))

    def test_read_identity_waits_for_delayed_plan_tier_and_discards_extra_fields(self):
        events = [
            {"product": "antigravity", "email": "user@example.com", "model": {"id": "Gemini 4"},
             "cwd": "/secret/path"},
            {"product": "antigravity", "email": "user@example.com", "plan_tier": "Google AI Ultra",
             "model": {"id": "Gemini 4"}, "cycle_mode": "auto",
             "transcript_path": "/secret/transcript.jsonl"},
        ]

        class FakeChild:
            def poll(self):
                return None

        hook_holder = {}
        tick = {"count": 0}

        @contextlib.contextmanager
        def fake_terminal(_args, work, _env):
            hook_holder["hook"] = work / "status.py"
            hook_holder["capture"] = work / "identity"
            yield FakeChild(), 0

        def fake_read(_master, _timeout):
            idx = tick["count"]
            tick["count"] += 1
            if idx < len(events):
                subprocess.run([sys.executable, str(hook_holder["hook"])],
                               input=json.dumps(events[idx]), text=True, check=True)
                captured = json.loads(hook_holder["capture"].read_text(encoding="utf-8"))
                self.assertEqual(set(captured.keys()), {"email", "plan_tier", "ready"})
            return b""

        with patch.multiple(antigravity_login, _check_executable=lambda _cmd: None,
                            _terminal=fake_terminal, _read_terminal=fake_read,
                            _IDENTITY_TIMEOUT=5.0, _PLAN_TIER_WAIT=2.0, _PLAN_TIER_READY_GRACE=0.05):
            result = antigravity_login.read_identity("agy")
            self.assertEqual(result, {"email": "user@example.com", "plan_tier": "Google AI Ultra"})
            self.assertEqual(tick["count"], 2)

    def test_read_identity_returns_email_promptly_when_ready_without_plan_tier(self):
        class FakeChild:
            def poll(self):
                return None

        hook_holder = {}
        tick = {"count": 0}

        @contextlib.contextmanager
        def fake_terminal(_args, work, _env):
            hook_holder["hook"] = work / "status.py"
            yield FakeChild(), 0

        def fake_read(_master, _timeout):
            if tick["count"] == 0:
                payload = {"product": "antigravity", "email": "free@example.com", "cycle_mode": "auto"}
                subprocess.run([sys.executable, str(hook_holder["hook"])],
                               input=json.dumps(payload), text=True, check=True)
            else:
                time.sleep(0.02)
            tick["count"] += 1
            return b""

        with patch.multiple(antigravity_login, _check_executable=lambda _cmd: None,
                            _terminal=fake_terminal, _read_terminal=fake_read,
                            _IDENTITY_TIMEOUT=5.0, _PLAN_TIER_WAIT=3.0, _PLAN_TIER_READY_GRACE=0.03):
            started = time.monotonic()
            result = antigravity_login.read_identity("agy")
            elapsed = time.monotonic() - started
            self.assertEqual(result, {"email": "free@example.com", "plan_tier": None})
            self.assertLess(elapsed, 1.0)

    def test_read_identity_terminates_child_process_group_on_sigterm(self):
        import os
        import signal

        with tempfile.TemporaryDirectory() as directory:
            child_pid_file = Path(directory) / "child.pid"
            fake_agy = Path(directory) / "agy"
            fake_agy.write_text(
                f"#!{sys.executable}\n"
                "import os, time\n"
                f"open({str(child_pid_file)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            fake_agy.chmod(0o700)
            script = (
                f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); "
                "from unittest.mock import patch; "
                "from codex_monitor import antigravity_login; "
                "patch.object(antigravity_login, '_check_executable', lambda _c: None).start(); "
                f"antigravity_login.read_identity({str(fake_agy)!r})"
            )
            proc = subprocess.Popen([sys.executable, "-c", script],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 5.0
                while not child_pid_file.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(child_pid_file.is_file(), "fake agy child never started")
                child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
                proc.terminate()
                proc.wait(timeout=5)
                deadline = time.monotonic() + 3.0
                alive = True
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                        time.sleep(0.05)
                    except ProcessLookupError:
                        alive = False
                        break
                self.assertFalse(alive, "child agy process was leaked after SIGTERM")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    unittest.main()
