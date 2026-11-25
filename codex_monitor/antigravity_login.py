"""Pass a user-submitted authorization code to one isolated agy login."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid


class LoginError(Exception):
    """Messages must not include provider URLs, credentials, or terminal output."""


_TOKEN_NAME = "antigravity-oauth-token"
_DATA_DIR = "antigravity-cli"
_TOKEN_LIMIT = 64 * 1024


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


def _valid_code(code):
    return re.fullmatch(r"4/[A-Za-z0-9._/~-]{16,1022}", code) is not None


@contextlib.contextmanager
def _terminal(args, work, env):
    master, slave = pty.openpty()
    try:
        child = subprocess.Popen(args, cwd=work, env=env,
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


def _terminal_environment(work):
    opener = work / "open"
    opener.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    opener.chmod(0o700)
    return dict(_environment(), PATH=f"{work}:/usr/bin:/bin:/usr/sbin:/sbin", TERM="xterm-256color",
                BROWSER=str(opener), AGY_CLI_DISABLE_AUTO_UPDATE="true")


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


def _read_code(child):
    deadline = time.monotonic() + 300
    parent = os.getppid()
    data = b""
    while time.monotonic() < deadline:
        if child.poll() is not None or os.getppid() != parent:
            raise LoginError("本次登录已结束，请重新登录。")
        if not select.select([sys.stdin], [], [], 1)[0]:
            continue
        part = os.read(sys.stdin.fileno(), 1025)
        if not part:
            raise LoginError("登录已取消。")
        data += part
        if len(data) > 1025:
            break
        if b"\n" in data:
            try:
                code = data.split(b"\n", 1)[0].decode("ascii")
            except UnicodeDecodeError:
                break
            if _valid_code(code):
                return code
            break
    else:
        raise TimeoutError
    raise LoginError("授权码格式不正确，请重新登录后复制 Google 页面给出的新授权码。")


def _login(executable, profile, work):
    env = dict(_terminal_environment(work), AGY_CLI_FORCE_FILE_TOKEN_STORAGE="1")
    args = [executable, "acp", "--login", "--gemini-dir", str(profile), "--app-data-dir", "antigravity-cli"]
    with _terminal(args, work, env) as (child, master):
        url, _ = _login_url(child, master)
        if not url:
            raise LoginError("agy 未开启新的 Google 授权，请重试。")
        # Local pipe to the native widget. Do not log the URL or the code.
        print(json.dumps({"url": url}), flush=True)
        code = _read_code(child)
        os.write(master, code.encode("ascii") + b"\r")
        deadline = time.monotonic() + 30
        while child.poll() is None and time.monotonic() < deadline:
            _read_terminal(master, .25)
        if child.poll() != 0:
            raise LoginError("授权没有完成。请重新登录，并使用新页面给出的授权码。")
    token = profile / _DATA_DIR / _TOKEN_NAME
    if not _valid_token_file(token):
        raise LoginError("没有找到此账号的登录结果，请重试。")
    return token


def _check_executable(executable):
    allowed = [Path.home() / ".local/bin/agy", Path("/opt/homebrew/bin/agy"), Path("/usr/local/bin/agy")]
    if Path(executable) not in allowed or not os.access(executable, os.X_OK):
        raise LoginError("找不到已验证的官方 agy。")


def _profile_directory(profile_id):
    root = Path.home() / "Library/Application Support/CodexMonitor/Antigravity"
    try:
        profile = root / str(uuid.UUID(profile_id)).upper()
    except ValueError:
        raise LoginError("Antigravity 账号 ID 无效。") from None
    if any(path.is_symlink() for path in (root, profile, profile / _DATA_DIR)):
        raise LoginError("Antigravity 账号目录不能是符号链接。")
    return profile


def _valid_token_file(token):
    try:
        info = token.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and 0 < info.st_size <= _TOKEN_LIMIT)


def read_identity(executable, profile_id=None):
    _check_executable(executable)
    profile = _profile_directory(profile_id) if profile_id is not None else None
    if profile is not None and not _valid_token_file(profile / _DATA_DIR / _TOKEN_NAME):
        return None
    with tempfile.TemporaryDirectory(prefix="codex-monitor-agy-identity-") as tmp:
        work = Path(tmp)
        hook = work / "status.py"
        capture = work / "identity"
        # Request only the documented email field. The hook's input can also
        # contain workspace and conversation metadata, which is discarded.
        hook.write_text(
            "import json, os, sys\n"
            "state = json.load(sys.stdin)\n"
            "email = state.get('email')\n"
            "tier = state.get('plan_tier')\n"
            "if state.get('product') == 'antigravity' and isinstance(email, str):\n"
            "    path = " + repr(str(capture)) + "\n"
            "    part = path + '.' + str(os.getpid())\n"
            "    with open(part, 'w') as out:\n"
            "        json.dump({'email': email, 'plan_tier': tier}, out)\n"
            "    os.replace(part, path)\n", encoding="utf-8")
        command = shlex.join([sys.executable, str(hook)])
        settings = json.dumps({"statusLine": {"type": "command", "command": command, "enabled": True}})
        args = [executable, "--settings", settings]
        env = _terminal_environment(work)
        if profile is not None:
            args += ["--gemini_dir", str(profile), "--app_data_dir", _DATA_DIR]
            env["AGY_CLI_FORCE_FILE_TOKEN_STORAGE"] = "1"
        with _terminal(args, work, env) as (child, master):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                # Startup can briefly show a sign-in prompt while loading the
                # system keychain. Wait for the authoritative status payload.
                _read_terminal(master, .25)
                if capture.is_file():
                    state = json.loads(capture.read_text(encoding="utf-8"))
                    email = state.get("email")
                    if isinstance(email, str) and re.fullmatch(r"[^\s@]{1,100}@[^\s@]{1,150}", email):
                        tier = state.get("plan_tier")
                        if not isinstance(tier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,29}", tier):
                            tier = None
                        return {"email": email, "plan_tier": tier}
                if child.poll() is not None:
                    break
    return None


def run(profile_id, executable):
    profile = _profile_directory(profile_id)
    _check_executable(executable)
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    os.umask(0o077)

    def stop(_signum, _frame):
        raise LoginError("登录已取消。")

    signal.signal(signal.SIGTERM, stop)
    try:
        with tempfile.TemporaryDirectory(prefix=".sign-in-", dir=profile) as tmp:
            work = Path(tmp)
            staging = work / "profile"
            staging.mkdir(mode=0o700)
            token = _login(executable, staging, work)
            # agy can reuse an existing session. Authorize in an empty profile
            # so cancellation or failed sign-in leaves the existing login intact.
            destination = _profile_directory(profile_id) / _DATA_DIR
            destination.mkdir(mode=0o700, exist_ok=True)
            os.chmod(token, 0o600)
            os.replace(token, destination / _TOKEN_NAME)
    except TimeoutError:
        raise LoginError("Google 授权等待超时，可以重新登录。") from None
    except (OSError, ValueError, KeyError):
        raise LoginError("无法完成浏览器登录，请重试。") from None
