"""Local web dashboard: `codex-monitor serve`. Binds to 127.0.0.1 only; every API call must
carry the per-run token so other websites open in your browser cannot drive it."""
from __future__ import annotations

import json
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from . import __version__, autostart, browsers, paths, store
from .monitor import Monitor
from .oauth import OAuthError
from .store import StoreError
from .usage import UsageError
from .web_i18n import EN, ZH

_ASSETS = Path(__file__).with_name("web_static")
PAGE = (_ASSETS / "dashboard.html").read_text(encoding="utf-8")
PAGE = PAGE.replace("__STYLE__", (_ASSETS / "dashboard.css").read_text(encoding="utf-8"))
PAGE = PAGE.replace("__SCRIPT__", (_ASSETS / "dashboard.js").read_text(encoding="utf-8"))


def _page() -> bytes:
    html = PAGE.replace("__EN__", json.dumps(EN, ensure_ascii=False)).replace("__ZH__", json.dumps(ZH, ensure_ascii=False))
    return html.encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = f"CodexMonitor/{__version__}"
    monitor: Monitor
    token: str
    quiet: bool = True

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ helpers

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return self.headers.get("X-Token") == self.token or query.get("token", [None])[0] == self.token

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise StoreError("Invalid request length") from error
        if n < 0:
            raise StoreError("Invalid request length")
        if n > 262144:
            raise ValueError("request body too large")
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, RecursionError) as error:
            raise StoreError("Invalid JSON request") from error
        if not isinstance(data, dict):
            raise StoreError("Request body must be a JSON object")
        return data

    # ------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            if not self._authorized():
                self._json(403, {"error": "open the dashboard through the URL printed by `codex-monitor serve` (it carries the access token)"})
                return
            body = _page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authorized():
            self._json(403, {"error": "missing or invalid token"})
            return
        if path == "/api/state":
            self._json(200, self.monitor.snapshot())
        elif path == "/api/login/status":
            self.monitor.finish_login_if_done()
            self._json(200, self.monitor.login_status())
        elif path.startswith("/api/auth/"):
            name = urllib.parse.unquote(path[len("/api/auth/"):])
            try:
                text = self.monitor.auth_text(name)
            except (KeyError, OSError):
                self._json(404, {"error": f"unknown account {name!r}"})
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if query.get("download"):
                body = text.encode("utf-8")
                filename = urllib.parse.quote("auth-" + name + ".json", safe="")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=\"auth.json\"; filename*=UTF-8''" + filename)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(200, {"name": name, "text": text})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if not self._authorized():
            self._json(403, {"error": "missing or invalid token"})
            return
        try:
            body = self._body()
        except ValueError as e:
            if str(e) == "request body too large":
                self._json(413, {"error": "request body too large"})
                return
            self._json(400, {"error": "Invalid request"})
            return
        except StoreError as e:
            self._json(400, {"error": str(e)})
            return
        m = self.monitor
        try:
            for key in ("force", "relogin", "private", "confirmed"):
                if key in body and not isinstance(body[key], bool):
                    raise StoreError(f"{key} must be a boolean")
            if "name" in body:
                name = body["name"]
                if not isinstance(name, str) or (name not in m.states and store.sanitize(name) != name):
                    raise StoreError("choose an exact account directory name")
            if path == "/api/refresh":
                name = str(body.get("name") or "").strip()
                if name:
                    if name not in m.states:
                        raise StoreError("unknown account")
                    m.refresh_one(name)
                else:
                    threading.Thread(target=m.refresh_all, kwargs={"force": True}, daemon=True).start()
                self._json(200, {"ok": True})
            elif path == "/api/switch":
                self._json(200, {"ok": True, **m.switch(str(body.get("name", "")))})
            elif path == "/api/save":
                self._json(200, {"ok": True, "name": m.save_main(str(body.get("name", "")), bool(body.get("force")))})
            elif path == "/api/import":
                text = body.get("text")
                if not isinstance(text, str) and isinstance(body.get("auth"), dict):
                    text = json.dumps(body["auth"])
                if not isinstance(text, str) or not text.strip():
                    raise StoreError("paste or upload an auth.json")
                imported = m.import_auth(str(body.get("name", "")), text, bool(body.get("force")))
                self._json(200, {"ok": True, **imported})
            elif path == "/api/sync":
                self._json(200, {"ok": True, "note": m.sync_now()})
            elif path == "/api/settings":
                if body.get("interval") is not None:
                    interval = body["interval"]
                    if isinstance(interval, bool) or not isinstance(interval, int) or not 10 <= interval <= 600:
                        raise StoreError("interval must be an integer from 10 to 600 seconds")
                    m.set_interval(interval)
                self._json(200, {"ok": True, "interval": m.interval, "autostart": autostart.status()})
            elif path == "/api/autostart":
                action = str(body.get("action") or "status")
                extra = ["--interval", str(m.interval)]
                token_file = paths.dashboard_token_path()
                if token_file.is_file():
                    extra += ["--token", str(token_file)]
                if action == "install":
                    message = autostart.install(extra)
                elif action == "remove":
                    message = autostart.remove()
                else:
                    message = autostart.status()
                self._json(200, {"ok": True, "status": autostart.status(), "message": message})
            elif path == "/api/annotations":
                self._json(200, {"ok": True, "annotations": m.set_annotations(body.get("name"), body.get("tags"), body.get("unavailable"))})
            elif path == "/api/rename":
                self._json(200, {"ok": True, "name": m.rename_account(body.get("name"), body.get("new_name"), body.get("revision"))})
            elif path == "/api/token/refresh":
                self._json(200, {"ok": True, "result": m.refresh_token(str(body.get("name", "")))})
            elif path == "/api/reset/use":
                if body.get("confirmed") is not True:
                    raise StoreError("confirm using one reset card first")
                result = m.use_reset_credit(str(body.get("name", "")), str(body.get("request_id", "")))
                self._json(200, {"ok": True, **result})
            elif path == "/api/login/start":
                self._json(200, m.start_login(str(body.get("name", "")), str(body.get("mode", "browser")), bool(body.get("relogin"))))
            elif path == "/api/login/open":
                self._json(200, {"ok": True, "browser": m.open_login_url(bool(body.get("private", True)))})
            elif path == "/api/login/cancel":
                m.cancel_login()
                self._json(200, {"ok": True})
            elif path == "/api/remove":
                self._json(200, {"ok": True, "backup": m.remove_account(body.get("name"), body.get("revision"))})
            else:
                self._json(404, {"error": "not found"})
        except UsageError as e:
            self._json(400, {"error": str(e), "upstream_status": e.status})
        except (StoreError, OAuthError, KeyError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # keep the server alive; surface the message
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


class TokenSpecError(ValueError):
    """`--token` pointed at a missing, empty, or unreadable file."""


def resolve_dashboard_token(spec: Optional[str] = None) -> Optional[str]:
    """Resolve a `--token` argument.

    * omitted / blank → `None` (caller generates a per-run token, original behavior)
    * existing file → first non-empty line
    * path-like string that is not a file → error
    * any other string → use as the token itself
    """
    if spec is None:
        return None
    raw = spec.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise TokenSpecError(f"cannot read token file {path}: {error}") from error
        for line in text.splitlines():
            token = line.strip()
            if token:
                return token
        raise TokenSpecError(f"token file {path} is empty")
    if path.is_dir():
        raise TokenSpecError(f"token path {path} is a directory")
    if raw.startswith("~") or any(sep in raw for sep in ("/", "\\")):
        raise TokenSpecError(f"token file {path} does not exist")
    return raw


def make_server(monitor: Monitor, host: str = "127.0.0.1", port: int = 7860, token: Optional[str] = None,
                quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (DashboardHandler,), {"monitor": monitor, "token": token or secrets.token_urlsafe(24), "quiet": quiet})
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def dashboard_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}/?token={server.RequestHandlerClass.token}"  # type: ignore[attr-defined]


def serve(port: int = 7860, interval: int = 30, open_browser: str = "tab", host: str = "127.0.0.1", quiet: bool = True,
          token: Optional[str] = None) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: binding to a non-loopback address exposes auth.json contents to your network", file=sys.stderr)
    try:
        resolved = resolve_dashboard_token(token)
    except TokenSpecError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    monitor = Monitor(interval=interval)
    server = make_server(monitor, host=host, port=port, token=resolved, quiet=quiet)
    url = dashboard_url(server)
    monitor.start_background()
    print(f"Codex Monitor dashboard: {url}", flush=True)
    print(f"accounts: {paths.accounts_dir()}   codex home: {paths.codex_home()}   refresh every {monitor.interval}s", flush=True)
    print("press Ctrl+C to stop", flush=True)
    if open_browser == "app":
        browsers.open_app_window(url)
    elif open_browser == "tab":
        browsers.open_default(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()
