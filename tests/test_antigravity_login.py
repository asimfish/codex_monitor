from pathlib import Path
import sys
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform != "win32":
    from codex_monitor.antigravity_login import LoginError, _auth_state, _valid_code


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


if __name__ == "__main__":
    unittest.main()
