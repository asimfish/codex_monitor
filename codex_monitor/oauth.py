"""Logging a ChatGPT account in without `codex login`'s auto-opened browser.

Browser mode reproduces Codex CLI's own OAuth client (authorization code + PKCE, loopback
redirect on port 1455) and writes an auth.json in exactly the shape Codex writes. Device-code
mode wraps `codex login --device-auth` inside an isolated CODEX_HOME.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import net, paths
from .identity import format_iso, identity, jwt_claims, now_utc
from .store import atomic_write


class OAuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_pkce() -> Tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(challenge: str, state: str) -> str:
    q = urllib.parse.urlencode([
        ("response_type", "code"),
        ("client_id", paths.OAUTH_CLIENT_ID),
        ("redirect_uri", paths.OAUTH_REDIRECT_URI),
        ("scope", paths.OAUTH_SCOPE),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("id_token_add_organizations", "true"),
        ("codex_cli_simplified_flow", "true"),
        ("state", state),
        ("originator", "codex_cli_rs"),
    ])
    return f"{paths.OAUTH_ISSUER}/oauth/authorize?{q}"


def exchange_code(code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": paths.OAUTH_REDIRECT_URI,
        "client_id": paths.OAUTH_CLIENT_ID,
        "code_verifier": verifier,
    }).encode("ascii")
    req = urllib.request.Request(f"{paths.OAUTH_ISSUER}/oauth/token", data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": paths.USER_AGENT,
    })
    try:
        with net.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OAuthError(f"token exchange failed (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OAuthError(f"token exchange failed: {e}") from e


def refresh_tokens(refresh_token: str) -> dict:
    """Explicit refresh (rotates the refresh token server-side). Only called on user request."""
    body = json.dumps({"client_id": paths.OAUTH_CLIENT_ID, "grant_type": "refresh_token",
                       "refresh_token": refresh_token, "scope": "openid profile email"}).encode("utf-8")
    req = urllib.request.Request(f"{paths.OAUTH_ISSUER}/oauth/token", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": paths.USER_AGENT})
    try:
        with net.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OAuthError(f"refresh failed (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OAuthError(f"refresh failed: {e}") from e


def build_auth_json(tokens: dict, now: Optional[datetime] = None) -> bytes:
    id_token, access_token = tokens.get("id_token"), tokens.get("access_token")
    if not id_token or not access_token:
        raise OAuthError("authorization server returned incomplete tokens")
    account_id = None
    for tok in (id_token, access_token):
        auth = jwt_claims(tok).get("https://api.openai.com/auth") or {}
        if auth.get("chatgpt_account_id"):
            account_id = auth["chatgpt_account_id"]
            break
    t = {"id_token": id_token, "access_token": access_token}
    if tokens.get("refresh_token"):
        t["refresh_token"] = tokens["refresh_token"]
    if account_id:
        t["account_id"] = account_id
    root = {"OPENAI_API_KEY": None, "auth_mode": "chatgpt", "tokens": t, "last_refresh": format_iso(now or now_utc())}
    return json.dumps(root, indent=2, sort_keys=True).encode("utf-8")


def apply_refreshed(auth_path: Path, new_tokens: dict) -> None:
    """Merge refreshed tokens into an existing auth.json, preserving unknown keys."""
    with open(auth_path, "r", encoding="utf-8") as f:
        root = json.load(f)
    tokens = root.setdefault("tokens", {})
    for k in ("id_token", "access_token", "refresh_token"):
        if new_tokens.get(k):
            tokens[k] = new_tokens[k]
    if not tokens.get("account_id"):
        auth = jwt_claims(tokens.get("id_token")).get("https://api.openai.com/auth") or {}
        if auth.get("chatgpt_account_id"):
            tokens["account_id"] = auth["chatgpt_account_id"]
    root["last_refresh"] = format_iso(now_utc())
    atomic_write(auth_path, json.dumps(root, indent=2, sort_keys=True).encode("utf-8"))


# ---------------------------------------------------------------- callback server

_SUCCESS_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Codex Monitor</title>
<style>body{font-family:-apple-system,system-ui,Segoe UI,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f7;color:#1d1d1f}
.card{background:#fff;border-radius:16px;padding:40px 48px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}h1{font-size:22px;margin:0 0 8px}p{margin:0;color:#6e6e73}</style></head>
<body><div class="card"><h1>Signed in</h1><p>Codex Monitor saved the credentials. You can close this window.</p></div></body></html>"""

_FAILURE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Codex Monitor</title>
<style>body{font-family:-apple-system,system-ui,Segoe UI,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f7;color:#1d1d1f}
.card{background:#fff;border-radius:16px;padding:40px 48px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}h1{font-size:22px;margin:0 0 8px}p{margin:0;color:#6e6e73}</style></head>
<body><div class="card"><h1>Login did not complete</h1><p>The authorization server returned: %s. Go back to Codex Monitor to retry.</p></div></body></html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "CodexMonitor/1.0"

    def log_message(self, fmt: str, *args) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/auth/callback":
            self._send(404, "<h1>Not found</h1>")
            return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        server: "CallbackServer" = self.server  # type: ignore[assignment]
        if "error" in q:
            self._send(200, _FAILURE_HTML % (q.get("error_description") or q["error"]))
            server.deliver(q)
        elif q.get("code"):
            self._send(200, _SUCCESS_HTML)
            server.deliver(q)
        else:
            self._send(400, "<h1>Missing code</h1>")

    def _send(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class CallbackServer(HTTPServer):
    """One-shot loopback listener for the OAuth redirect."""
    allow_reuse_address = True

    def __init__(self, port: int = paths.OAUTH_PORT):
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.result: Optional[dict] = None
        self._event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()

    def deliver(self, query: dict) -> None:
        if self.result is None:
            self.result = query
            self._event.set()

    def wait(self, timeout: Optional[float]) -> Optional[dict]:
        self._event.wait(timeout)
        return self.result

    def stop(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass
        self.server_close()


# ---------------------------------------------------------------- flows

class BrowserLogin:
    """Authorization-code login that the caller opens in a browser window of their choice."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.verifier, self.challenge = make_pkce()
        self.state = _b64url(secrets.token_bytes(32))
        self.url = authorize_url(self.challenge, self.state)
        self._server: Optional[CallbackServer] = None
        self.phase = "starting"     # starting | waiting | exchanging | success | failed | cancelled
        self.error: Optional[str] = None
        self.result_identity: Optional[dict] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            self._server = CallbackServer()
        except OSError as e:
            self.phase, self.error = "failed", f"cannot listen on 127.0.0.1:{paths.OAUTH_PORT} ({e}); is another codex login running?"
            return
        self._server.start()
        self.phase = "waiting"
        threading.Thread(target=self._wait_and_finish, daemon=True).start()

    def _wait_and_finish(self) -> None:
        assert self._server is not None
        q = self._server.wait(timeout=15 * 60)
        with self._lock:
            if self.phase != "waiting":
                return
            if q is None:
                self.phase, self.error = "failed", "timed out waiting for the browser (15 minutes)"
                self._server.stop()
                return
            if "error" in q:
                self.phase, self.error = "failed", f"authorization refused: {q.get('error_description') or q['error']}"
                self._server.stop()
                return
            if q.get("state") != self.state:
                self.phase, self.error = "failed", "callback state mismatch (another login flow?)"
                self._server.stop()
                return
            self.phase = "exchanging"
        try:
            tokens = exchange_code(q["code"], self.verifier)
            data = build_auth_json(tokens)
            self.directory.mkdir(parents=True, exist_ok=True)
            atomic_write(self.directory / "auth.json", data)
            self.result_identity = identity(json.loads(data))
            with self._lock:
                self.phase = "success"
        except (OAuthError, OSError, ValueError) as e:
            with self._lock:
                self.phase, self.error = "failed", str(e)
        finally:
            self._server.stop()

    def cancel(self) -> None:
        with self._lock:
            if self.phase in ("starting", "waiting"):
                self.phase = "cancelled"
        if self._server:
            self._server.stop()

    def wait(self, poll: float = 0.5) -> str:
        while self.phase in ("starting", "waiting", "exchanging"):
            time.sleep(poll)
        return self.phase


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s\x1b]+")
_CODE = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{4,8}-[A-Z0-9]{4,8}(?![A-Z0-9])")


def parse_device_output(text: str) -> Optional[Tuple[str, str]]:
    clean = _ANSI.sub("", text)
    u, c = _URL.search(clean), _CODE.search(clean)
    if u and c:
        return u.group(0), c.group(0)
    return None


def find_codex() -> Optional[str]:
    found = shutil.which("codex")
    if found:
        return found
    home = Path.home()
    candidates = []
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        for v in sorted(nvm.iterdir(), reverse=True):
            candidates.append(v / "bin" / "codex")
    candidates += [Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"), home / ".local" / "bin" / "codex",
                   home / ".bun" / "bin" / "codex", home / ".volta" / "bin" / "codex"]
    if paths.IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates += [Path(appdata) / "npm" / "codex.cmd", Path(appdata) / "npm" / "codex"]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


class DeviceCodeLogin:
    """Runs `codex login --device-auth` with CODEX_HOME pointing at the account directory."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.phase = "starting"
        self.error: Optional[str] = None
        self.url: Optional[str] = None
        self.code: Optional[str] = None
        self.transcript = ""
        self.result_identity: Optional[dict] = None
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        codex = find_codex()
        if not codex:
            self.phase, self.error = "failed", "codex CLI not found on PATH (npm i -g @openai/codex)"
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CODEX_HOME=str(self.directory), NO_COLOR="1", TERM="dumb")
        try:
            self._proc = subprocess.Popen([codex, "login", "--device-auth"], stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                          cwd=str(self.directory))
        except OSError as e:
            self.phase, self.error = "failed", f"could not start codex: {e}"
            return
        self.phase = "waiting"
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            self.transcript += raw.decode("utf-8", "replace")
            if self.url is None:
                parsed = parse_device_output(self.transcript)
                if parsed:
                    self.url, self.code = parsed
        rc = self._proc.wait()
        if self.phase == "cancelled":
            return
        auth_path = self.directory / "auth.json"
        if auth_path.exists():
            try:
                with open(auth_path, "r", encoding="utf-8") as f:
                    self.result_identity = identity(json.load(f))
                self.phase = "success"
                return
            except (OSError, ValueError):
                pass
        tail = "\n".join(_ANSI.sub("", self.transcript).splitlines()[-6:])
        self.phase, self.error = "failed", f"codex login exited with status {rc} without writing auth.json\n{tail}"

    def cancel(self) -> None:
        self.phase = "cancelled"
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def wait(self, poll: float = 0.5) -> str:
        while self.phase in ("starting", "waiting"):
            time.sleep(poll)
        return self.phase
