"""Where things live. Everything is overridable through environment variables so tests (and
people with unusual setups) can point the tool anywhere."""
from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_PORT = 1455
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_PORT}/auth/callback"
OAUTH_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
USER_AGENT = "codex_cli_rs/0.153.3 codex-monitor/1.0"

# Items symlinked (or copied on Windows) into an account directory so `codex-monitor run`
# behaves like the main install.
SHARED_ITEMS = ["config.toml", "AGENTS.md", "instructions.md", "skills", "plugins", "agents"]


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def accounts_dir() -> Path:
    raw = os.environ.get("CODEX_ACCOUNTS_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".codex-accounts"


def main_auth_path() -> Path:
    return codex_home() / "auth.json"


def backup_dir() -> Path:
    return accounts_dir() / "_backup"


def cache_dir() -> Path:
    return accounts_dir() / ".cache"


def base_url() -> str:
    """`chatgpt_base_url` from config.toml, or the default backend."""
    cfg = codex_home() / "config.toml"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t.startswith("chatgpt_base_url") and "=" in t:
                value = t.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'")
                if value.startswith("http"):
                    return value.rstrip("/")
    except OSError:
        pass
    return DEFAULT_BASE_URL


def display_path(p: Path) -> str:
    """Shorten a path with ~ for humans."""
    try:
        return "~/" + str(p.relative_to(Path.home())).replace("\\", "/")
    except ValueError:
        return str(p)
