"""Logging a ChatGPT account in without `codex login`'s auto-opened browser.

Browser mode reproduces Codex CLI's own OAuth client (authorization code + PKCE, loopback
redirect on port 1455) and writes an auth.json in exactly the shape Codex writes. Device-code
mode wraps `codex login --device-auth` inside an isolated CODEX_HOME.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
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
from .identity import access_token_expired, format_iso, identity, jwt_claims, now_utc
from .locking import file_lock
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


def _token_response(data: bytes) -> dict:
    try:
        tokens = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise OAuthError("authorization server returned invalid JSON") from error
    if (not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str)
            or not tokens["access_token"].strip()
            or any(tokens.get(key) is not None and not isinstance(tokens[key], str)
                   for key in ("id_token", "refresh_token"))):
        raise OAuthError("authorization server returned invalid tokens")
    return tokens


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
            return _token_response(r.read())
    except urllib.error.HTTPError as e:
        raise OAuthError(f"token exchange failed (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OAuthError(f"token exchange failed: {e}") from e


def refresh_tokens(refresh_token: str) -> dict:
    """Rotate a refresh token on user request or after rejected credentials."""
    body = json.dumps({"client_id": paths.OAUTH_CLIENT_ID, "grant_type": "refresh_token",
                       "refresh_token": refresh_token, "scope": "openid profile email"}).encode("utf-8")
    req = urllib.request.Request(f"{paths.OAUTH_ISSUER}/oauth/token", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": paths.USER_AGENT})
    try:
        with net.urlopen(req, timeout=30) as r:
            return _token_response(r.read())
    except urllib.error.HTTPError as e:
        raise OAuthError(f"refresh failed (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OAuthError(f"refresh failed: {e}") from e


def build_auth_json(tokens: dict, now: Optional[datetime] = None) -> bytes:
    if not isinstance(tokens, dict):
        raise OAuthError("authorization server returned invalid tokens")
    id_token, access_token = tokens.get("id_token"), tokens.get("access_token")
    if not all(isinstance(value, str) and value.strip() for value in (id_token, access_token)):
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


def apply_refreshed(auth_path: Path, new_tokens: dict, expected_refresh_token: Optional[str] = None,
                    expected_account_id: Optional[str] = None) -> bool:
    """Merge refreshed tokens into an existing auth.json, preserving unknown keys."""
    with file_lock(auth_path.parent / ".auth.lock"):
        with open(auth_path, "r", encoding="utf-8") as f:
            root = json.load(f)
        if expected_account_id is not None and identity(root).get("account_id") != expected_account_id:
            return False
        tokens = root.setdefault("tokens", {})
        if expected_refresh_token is not None and tokens.get("refresh_token") != expected_refresh_token:
            return False
        for k in ("id_token", "access_token", "refresh_token"):
            if new_tokens.get(k):
                tokens[k] = new_tokens[k]
        if not tokens.get("account_id"):
            auth = jwt_claims(tokens.get("id_token")).get("https://api.openai.com/auth") or {}
            if auth.get("chatgpt_account_id"):
                tokens["account_id"] = auth["chatgpt_account_id"]
        root["last_refresh"] = format_iso(now_utc())
        atomic_write(auth_path, json.dumps(root, indent=2, sort_keys=True).encode("utf-8"), create_parent=False)
        return True


def sync_refreshed_copies(auth_path: Path, previous: dict, new_tokens: dict, also_main: bool = False) -> None:
    """Update only copies of the rotated session, preserving independent logins."""
    from . import store
    account = identity(previous).get("account_id")
    refresh_token = (previous.get("tokens") or {}).get("refresh_token")
    if not account or not refresh_token:
        return
    candidates = store.profiles()
    main = store.main_profile() if also_main else None
    if main:
        candidates.append(main)
    for profile in candidates:
        if profile.auth_path == auth_path or profile.account_id != account:
            continue
        try:
            apply_refreshed(profile.auth_path, new_tokens, expected_refresh_token=refresh_token,
                            expected_account_id=account)
        except FileNotFoundError:
            continue  # A concurrently removed archive must not be recreated.


def describe_loopback_listener(port: int) -> Optional[str]:
    """Best-effort `comm (pid N)` for whoever is listening on 127.0.0.1:`port` (Linux /proc)."""
    if not paths.IS_LINUX:
        return None
    needle = f"0100007F:{port:04X}"
    try:
        rows = Path("/proc/net/tcp").read_text(encoding="ascii", errors="replace").splitlines()[1:]
    except OSError:
        return None
    inode = None
    for row in rows:
        cols = row.split()
        if len(cols) < 10:
            continue
        if cols[1].upper() == needle and cols[3] == "0A":
            inode = cols[9]
            break
    if not inode:
        return None
    marker = f"socket:[{inode}]"
    try:
        procs = Path("/proc").iterdir()
    except OSError:
        return None
    for proc in procs:
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                try:
                    if os.readlink(fd) == marker:
                        comm = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip() or "process"
                        cmd = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
                        extra = " app-server" if "app-server" in cmd else ""
                        return f"{comm}{extra} (pid {proc.name})"
                except OSError:
                    continue
        except OSError:
            continue
    return None


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
            self._send(200, _FAILURE_HTML % html.escape(q.get("error_description") or q["error"]))
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
        self._event.set()
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
            who = describe_loopback_listener(paths.OAUTH_PORT)
            held = f"; held by {who}" if who else f" ({e})"
            self.phase, self.error = (
                "failed",
                f"cannot listen on 127.0.0.1:{paths.OAUTH_PORT}{held}. "
                "Official Codex app-server uses this port; quit that Codex session or use device-code login.",
            )
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
            with self._lock:
                if self.phase != "exchanging":
                    return
                self.directory.mkdir(parents=True, exist_ok=True)
                atomic_write(self.directory / "auth.json", data)
                self.result_identity = identity(json.loads(data))
                self.phase = "success"
        except (OAuthError, OSError, ValueError) as e:
            with self._lock:
                if self.phase != "cancelled":
                    self.phase, self.error = "failed", str(e)
        finally:
            self._server.stop()

    def cancel(self) -> None:
        with self._lock:
            if self.phase in ("starting", "waiting", "exchanging"):
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
    """Run the CLI in a private staging home; publish credentials only on success."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.phase = "starting"
        self.error: Optional[str] = None
        self.url: Optional[str] = None
        self.code: Optional[str] = None
        self.transcript = ""
        self.result_identity: Optional[dict] = None
        self._proc: Optional[subprocess.Popen] = None
        self._staging: Optional[tempfile.TemporaryDirectory] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        codex = find_codex()
        if not codex:
            self.phase, self.error = "failed", "codex CLI not found on PATH (npm i -g @openai/codex)"
            return
        with self._lock:
            if self.phase == "cancelled":
                return
            try:
                self.directory.parent.mkdir(parents=True, exist_ok=True)
                self._staging = tempfile.TemporaryDirectory(prefix=".codex-login-", dir=str(self.directory.parent))
                env = dict(os.environ, CODEX_HOME=self._staging.name, NO_COLOR="1", TERM="dumb")
                self._proc = subprocess.Popen([codex, "login", "--device-auth"], stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                              cwd=self._staging.name)
            except OSError as error:
                if self._staging:
                    self._staging.cleanup()
                self.phase, self.error = "failed", f"could not start codex: {error}"
                return
            self.phase = "waiting"
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                self.transcript = (self.transcript + raw.decode("utf-8", "replace"))[-65536:]
                if self.url is None:
                    parsed = parse_device_output(self.transcript)
                    if parsed:
                        self.url, self.code = parsed
            rc = self._proc.wait()
            with self._lock:
                if self.phase == "cancelled":
                    return
                if rc != 0 or self._staging is None:
                    tail = "\n".join(_ANSI.sub("", self.transcript).splitlines()[-6:])
                    raise OAuthError(f"codex login exited with status {rc} without a successful login\n{tail}")
                data = (Path(self._staging.name) / "auth.json").read_bytes()
                ident = identity(json.loads(data))
                if not ident.get("has_tokens") or access_token_expired(ident):
                    raise OAuthError("codex login did not produce valid credentials")
                atomic_write(self.directory / "auth.json", data)
                self.result_identity, self.phase = ident, "success"
        except (OAuthError, OSError, ValueError, TypeError, AttributeError) as error:
            with self._lock:
                if self.phase != "cancelled":
                    self.phase, self.error = "failed", str(error)
        finally:
            if self._staging:
                self._staging.cleanup()

    def cancel(self) -> None:
        with self._lock:
            if self.phase in ("starting", "waiting", "exchanging"):
                self.phase = "cancelled"
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def wait(self, poll: float = 0.5) -> str:
        while self.phase in ("starting", "waiting", "exchanging"):
            time.sleep(poll)
        return self.phase
