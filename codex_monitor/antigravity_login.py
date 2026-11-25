"""Relay one official agy sign-in through a disposable Chromium profile."""
from __future__ import annotations

import contextlib
import fcntl
import hmac
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid


class LoginError(Exception):
    """Messages must not include provider URLs, credentials, or terminal output."""


def _environment():
    allowed = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL",
               "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _parameters(url, host, path):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc, parsed.path) != ("https", host, path):
            return {}
        return parse_qs(parsed.query, max_num_fields=32)
    except ValueError:
        return {}


def _auth_state(url):
    query = _parameters(url, "accounts.google.com", "/o/oauth2/auth")
    state = query.get("state", [])
    if (len(state) != 1 or not state[0].isascii() or not 16 <= len(state[0]) <= 256
            or query.get("redirect_uri") != ["https://antigravity.google/oauth-callback"]
            or query.get("response_type") != ["code"]
            or query.get("code_challenge_method") != ["S256"]
            or not query.get("code_challenge")):
        raise LoginError("Google 授权地址格式已变化，请更新 agy 后重试。")
    return state[0]


def _authorization_code(url, state):
    query = _parameters(url, "antigravity.google", "/oauth-callback")
    returned = query.get("state", [])
    if len(returned) != 1 or not returned[0].isascii() or not hmac.compare_digest(returned[0], state):
        return None
    if query.get("error"):
        raise LoginError("Google 授权未完成，可以重新登录。")
    code = query.get("code", [])
    if len(code) == 1 and re.fullmatch(r"[A-Za-z0-9._/~-]{1,1024}", code[0]):
        return code[0]
    return None


def _browser():
    for name, flag in (("Google Chrome", "--incognito"), ("Microsoft Edge", "--inprivate"),
                       ("Brave Browser", "--incognito"), ("Chromium", "--incognito")):
        for base in (Path("/Applications"), Path.home() / "Applications"):
            binary = base / f"{name}.app/Contents/MacOS/{name}"
            if os.access(binary, os.X_OK):
                return str(binary), flag
    raise LoginError("请安装 Chrome、Edge、Brave 或 Chromium，再重新登录。")


@contextlib.contextmanager
def _agy_login(executable, profile, work):
    # The CLI's terminal auth flow provides Google's PKCE URL and accepts the
    # matching authorization code. Keep it out of system browsers and logs here.
    opener = work / "open"
    opener.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    opener.chmod(0o700)
    env = dict(_environment(), PATH=f"{work}:/usr/bin:/bin:/usr/sbin:/sbin", TERM="xterm-256color",
               BROWSER=str(opener), AGY_CLI_FORCE_FILE_TOKEN_STORAGE="1")
    master, slave = pty.openpty()
    try:
        child = subprocess.Popen([executable, "acp", "--login", "--gemini-dir", str(profile),
                                  "--app-data-dir", "antigravity-cli"], cwd=work, env=env,
                                 stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        slave = None
        yield child, master
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)
        if "child" in locals() and child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def _read_terminal(fd, timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return b""
    try:
        return os.read(fd, 8192)
    except OSError:
        return b""


def _login_url(child, master):
    until = time.monotonic() + 35
    transcript = b""
    while time.monotonic() < until:
        transcript = (transcript + _read_terminal(master, .25))[-65536:]
        # OSC hyperlinks and wrapped display lines can repeat the URL. Use the
        # complete URL with the expected PKCE and callback fields.
        for match in re.findall(rb'https://accounts\.google\.com/o/oauth2/auth\?[!-~]+', transcript):
            url = match.decode("ascii")
            try:
                return url, _auth_state(url)
            except LoginError:
                continue
        if child.poll() is not None:
            return None, None
    raise LoginError("agy 未返回 Google 登录链接，请重试。")


class _ChromePipe:
    """Minimal DevTools transport over inherited pipes, with no debugging port."""

    def __init__(self, read_fd, write_fd):
        self.read_fd = read_fd
        self.write_fd = write_fd
        self.parent = os.getppid()
        self.buffer = b""
        self.serial = 0
        self.session = None
        self.frame = None
        self.state = None
        self.code = None

    def _message(self, deadline):
        while b"\0" not in self.buffer:
            wait = deadline - time.monotonic()
            if os.getppid() != self.parent:
                raise LoginError("登录窗口已退出。")
            if wait <= 0:
                raise TimeoutError
            if not select.select([self.read_fd], [], [], min(1, wait))[0]:
                continue
            chunk = os.read(self.read_fd, 65536)
            if not chunk:
                raise LoginError("隐身浏览器已关闭，可以重新登录。")
            self.buffer += chunk
            if len(self.buffer) > 2 * 1024 * 1024:
                raise LoginError("浏览器响应过大，登录已停止。")
        raw, self.buffer = self.buffer.split(b"\0", 1)
        message = json.loads(raw)
        if (self.state and message.get("sessionId") == self.session
                and message.get("method") == "Network.requestWillBeSent"):
            params = message.get("params", {})
            if params.get("type") == "Document" and params.get("frameId") == self.frame:
                self.code = self.code or _authorization_code(params.get("request", {}).get("url", ""),
                                                             self.state)
        return message

    def call(self, method, params=None, session=None):
        self.serial += 1
        request = {"id": self.serial, "method": method, "params": params or {}}
        if session is not None:
            request["sessionId"] = session
        data = memoryview(json.dumps(request).encode("utf-8") + b"\0")
        while data:
            data = data[os.write(self.write_fd, data):]
        end = time.monotonic() + 12
        while True:
            reply = self._message(end)
            if reply.get("id") == self.serial:
                if "error" in reply:
                    raise LoginError("无法建立隔离的浏览器登录，请重试。")
                return reply.get("result", {})

    def authorize(self, url, state):
        # Use a dedicated in-memory context and attach before navigating. The
        # official callback page strips its query as soon as it loads.
        context = self.call("Target.createBrowserContext")["browserContextId"]
        target = self.call("Target.createTarget", {"url": "about:blank", "browserContextId": context})["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.frame = self.call("Page.getFrameTree", session=self.session)["frameTree"]["frame"]["id"]
        self.call("Network.enable", session=self.session)
        self.state = state
        page = self.call("Page.navigate", {"url": url}, session=self.session)
        if page.get("errorText"):
            raise LoginError("无法打开 Google 授权页面，请检查网络后重试。")
        deadline = time.monotonic() + 300
        while self.code is None:
            event = self._message(deadline)
            if (event.get("method") == "Target.detachedFromTarget"
                    and event.get("params", {}).get("sessionId") == self.session):
                raise LoginError("隐身浏览器已关闭，可以重新登录。")
        return self.code


@contextlib.contextmanager
def _private_browser(binary, flag, work):
    to_browser, write_fd = os.pipe()
    read_fd, from_browser = os.pipe()
    pid = None
    try:
        with open(os.devnull, "rb+") as null:
            # Copy to descriptors above both Chrome's fixed pipe ends and the
            # Python pipe ends so dup2 ordering cannot overwrite a source.
            sources = []
            try:
                for descriptor in (to_browser, from_browser, null.fileno()):
                    sources.append(fcntl.fcntl(descriptor, fcntl.F_DUPFD, 10))
                inp, out, quiet = sources
                actions = [(os.POSIX_SPAWN_DUP2, source, target)
                           for source, target in ((inp, 3), (out, 4), (quiet, 0), (quiet, 1), (quiet, 2))]
                actions += [(os.POSIX_SPAWN_CLOSE, fd) for fd in sources]
                args = [binary, f"--user-data-dir={work / 'browser'}", flag, "--no-first-run",
                        "--no-default-browser-check", "--remote-debugging-pipe", "--no-startup-window"]
                pid = os.posix_spawn(binary, args, _environment(), file_actions=actions, setpgroup=0)
            finally:
                for fd in sources:
                    os.close(fd)
        os.close(to_browser)
        os.close(from_browser)
        to_browser = from_browser = None
        yield _ChromePipe(read_fd, write_fd)
    finally:
        for fd in (to_browser, from_browser, read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGTERM)
            for _ in range(20):
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
                time.sleep(.05)
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
                os.waitpid(pid, 0)


def _login(executable, profile, work):
    browser, flag = _browser()
    with _agy_login(executable, profile, work) as (child, master):
        url, state = _login_url(child, master)
        if url:
            with _private_browser(browser, flag, work) as chrome:
                code = chrome.authorize(url, state)
                os.write(master, code.encode("ascii") + b"\r")
                deadline = time.monotonic() + 30
                while child.poll() is None and time.monotonic() < deadline:
                    _read_terminal(master, .25)
        if child.poll() != 0:
            raise LoginError("agy 没有保存登录结果，请重试。")
    token = profile / "antigravity-cli" / "antigravity-oauth-token"
    if token.is_symlink() or not token.is_file():
        raise LoginError("没有找到此账号的登录结果，请重试。")


def run(profile_id, executable):
    # This entry point is only called by the local macOS application.
    if sys.platform != "darwin":
        raise LoginError("Antigravity 浏览器登录目前支持 macOS。")
    root = Path.home() / "Library/Application Support/CodexMonitor/Antigravity"
    try:
        profile = root / str(uuid.UUID(profile_id)).upper()
    except ValueError:
        raise LoginError("Antigravity 账号 ID 无效。") from None
    allowed = [Path.home() / ".local/bin/agy", Path("/opt/homebrew/bin/agy"), Path("/usr/local/bin/agy")]
    if Path(executable) not in allowed or not os.access(executable, os.X_OK):
        raise LoginError("找不到已验证的官方 agy。")
    if root.is_symlink() or profile.is_symlink():
        raise LoginError("Antigravity 账号目录不能是符号链接。")
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    os.umask(0o077)

    def stop(_signum, _frame):
        raise LoginError("登录已取消。")

    signal.signal(signal.SIGTERM, stop)
    try:
        with tempfile.TemporaryDirectory(prefix="codex-monitor-agy-login-") as tmp:
            _login(executable, profile, Path(tmp))
    except TimeoutError:
        raise LoginError("Google 授权等待超时，可以重新登录。") from None
    except (OSError, ValueError, KeyError):
        raise LoginError("无法完成隔离的浏览器登录，请重试。") from None
