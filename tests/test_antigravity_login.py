from pathlib import Path
import sys
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform != "win32":
    from codex_monitor.antigravity_login import LoginError, _authorization_code, _auth_state


@unittest.skipIf(sys.platform == "win32", "browser relay requires POSIX process pipes")
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

    def test_callback_is_bound_to_current_attempt_and_official_origin(self):
        query = urlencode({"code": "4/fixture-code", "state": self.state})
        callback = "https://antigravity.google/oauth-callback?" + query
        self.assertEqual(_authorization_code(callback, self.state), "4/fixture-code")
        self.assertIsNone(_authorization_code(callback, "another-login-attempt"))
        self.assertIsNone(_authorization_code(callback + "&code=second", self.state))
        self.assertIsNone(_authorization_code(callback.replace("antigravity.google", "unrelated.example"), self.state))
        self.assertIsNone(_authorization_code(callback.replace("/oauth-callback", "/other"), self.state))


if __name__ == "__main__":
    unittest.main()
