from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform != "win32":
    from codex_monitor.antigravity_login import LoginError, _auth_state, _valid_code
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
            self.assertIsNone(antigravity_login.read_system_email("agy", "fixture"))
            terminal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
