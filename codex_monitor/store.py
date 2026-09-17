"""The account store: ~/.codex/auth.json ("main") plus one directory per account under
~/.codex-accounts. Every directory doubles as an isolated CODEX_HOME."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import paths
from .identity import identity, now_utc


class StoreError(Exception):
    pass


@dataclass
class Profile:
    name: str
    directory: Path
    auth: Optional[dict]
    is_main: bool = False
    ident: dict = field(default_factory=dict)

    @property
    def auth_path(self) -> Path:
        return self.directory / "auth.json"

    @property
    def account_id(self) -> str:
        return self.ident.get("account_id", "")

    @property
    def display_name(self) -> str:
        if self.ident.get("email"):
            return self.ident["email"]
        if self.auth and self.auth.get("OPENAI_API_KEY"):
            return f"{self.name} (API key)"
        return self.name

    def file_mtime(self) -> Optional[datetime]:
        try:
            return datetime.fromtimestamp(self.auth_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None


def read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def has_stored_credentials(directory: Path) -> bool:
    # A file left by a failed login is not a completed profile. Preserve real
    # credentials even when expired; server validity belongs to re-login.
    try:
        data = (directory / "auth.json").read_bytes()
    except FileNotFoundError:
        return False
    try:
        auth = json.loads(data)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(auth, dict):
        return False
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    return any(isinstance(value, str) and bool(value.strip()) for value in
               (auth.get("OPENAI_API_KEY"), tokens.get("access_token"), tokens.get("refresh_token")))


def sanitize(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "._-") else "-" for c in name.strip()).strip("._-")
    if not keep:
        raise StoreError(f"invalid account name: {name!r}")
    return keep


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load(name: str, directory: Path, is_main: bool) -> Profile:
    auth = read_json(directory / "auth.json")
    return Profile(name=name, directory=directory, auth=auth, is_main=is_main, ident=identity(auth))


def main_profile() -> Optional[Profile]:
    p = paths.main_auth_path()
    if not p.exists():
        return None
    return _load("main", paths.codex_home(), True)


def profiles() -> List[Profile]:
    root = paths.accounts_dir()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if (d / "auth.json").exists():
            out.append(_load(d.name, d, False))
    return out


def find_by_account(account_id: str, exclude: Optional[str] = None) -> Optional[Profile]:
    if not account_id:
        return None
    for p in profiles():
        if p.name != exclude and p.account_id == account_id:
            return p
    return None


def get_profile(name: str) -> Profile:
    d = paths.accounts_dir() / name
    if not (d / "auth.json").exists():
        raise StoreError(f"no account named {name!r} ({d / 'auth.json'} does not exist)")
    return _load(name, d, False)


def adopt() -> Optional[str]:
    """If ~/.codex/auth.json belongs to a stored account and is newer, copy it back so the stored
    copy never holds a stale refresh token. Returns a message when something was copied."""
    main = main_profile()
    if not main or not main.auth:
        return None
    target = find_by_account(main.account_id)
    if not target:
        return None
    m_ts = main.ident["last_refresh"] or main.file_mtime() or datetime.min.replace(tzinfo=timezone.utc)
    t_ts = target.ident["last_refresh"] or target.file_mtime() or datetime.min.replace(tzinfo=timezone.utc)
    if m_ts <= t_ts:
        return None
    data = main.auth_path.read_bytes()
    if target.auth_path.read_bytes() == data:
        return None
    atomic_write(target.auth_path, data)
    return f"synced refreshed tokens from {paths.display_path(main.auth_path)} into account '{target.name}'"


def archive_main_as_profile(main: Profile) -> Profile:
    if not main.account_id or not main.auth:
        raise StoreError("current login has no usable credentials")
    base = sanitize((main.ident.get("email") or "account-" + main.account_id[:8]).split("@", 1)[0])
    name = base or "account-" + main.account_id[:8]
    suffix = 2
    while (paths.accounts_dir() / name).exists():
        name = f"{base}-{suffix}"
        suffix += 1
    dst = paths.accounts_dir() / name / "auth.json"
    atomic_write(dst, main.auth_path.read_bytes())
    return get_profile(name)


def backup_main(main: Profile) -> Path:
    bdir = paths.backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    tag = (main.ident.get("email") or main.account_id or "unknown").replace("@", "_at_")
    dst = bdir / f"auth-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    atomic_write(dst, main.auth_path.read_bytes())
    return dst


def activate(name: str) -> List[str]:
    """Make an account the active login by copying it over ~/.codex/auth.json."""
    target = get_profile(name)
    messages: List[str] = []
    msg = adopt()
    if msg:
        messages.append(msg)
    main = main_profile()
    if main and main.auth and not find_by_account(main.account_id):
        archived = archive_main_as_profile(main)
        messages.append(f"archived current account as '{archived.name}'")
        dst = backup_main(main)
        messages.append(f"backed up current {paths.display_path(main.auth_path)} to {dst}")
    atomic_write(paths.main_auth_path(), target.auth_path.read_bytes())
    messages.append(f"switched: {paths.display_path(paths.main_auth_path())} -> '{name}' ({target.display_name})")
    return messages


def save_main(name: str, force: bool = False) -> Profile:
    name = sanitize(name)
    main = main_profile()
    if not main or not main.auth:
        raise StoreError(f"{paths.main_auth_path()} does not exist or is not valid JSON")
    dst = paths.accounts_dir() / name / "auth.json"
    if dst.exists() and not force:
        raise StoreError(f"account '{name}' already exists (use --force to overwrite)")
    atomic_write(dst, main.auth_path.read_bytes())
    return get_profile(name)


def import_file(name: str, src: Path, force: bool = False) -> Profile:
    name = sanitize(name)
    auth = read_json(src)
    if not auth or not ((auth.get("tokens") or {}).get("access_token") or auth.get("OPENAI_API_KEY")):
        raise StoreError(f"{src} is not a valid Codex auth.json")
    dst = paths.accounts_dir() / name / "auth.json"
    if dst.exists() and not force:
        raise StoreError(f"account '{name}' already exists (use --force to overwrite)")
    atomic_write(dst, src.read_bytes())
    return get_profile(name)


def export(name: str, dest: Optional[Path]) -> Path:
    p = get_profile(name)
    dst = dest if dest else Path.cwd() / f"auth-{name}.json"
    if dst.is_dir():
        dst = dst / f"auth-{name}.json"
    atomic_write(dst, p.auth_path.read_bytes())
    return dst


def remove(name: str) -> None:
    p = get_profile(name)
    shutil.rmtree(p.directory)


def ensure_shared_links(directory: Path) -> List[str]:
    """Share config/skills with the main install. Symlinks where possible; on Windows (where
    symlinks need privileges) config.toml is copied and directories are skipped."""
    notes: List[str] = []
    for item in paths.SHARED_ITEMS:
        src = paths.codex_home() / item
        dst = directory / item
        if not src.exists() or dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(src, dst, target_is_directory=src.is_dir())
        except (OSError, NotImplementedError):
            if src.is_file():
                shutil.copy2(src, dst)
                notes.append(f"copied {item} (symlinks unavailable)")
            else:
                notes.append(f"skipped {item}/ (symlinks unavailable)")
    return notes


def codex_processes_running() -> bool:
    """Best-effort: is a Codex CLI / desktop process alive? Used only for a warning."""
    try:
        if paths.IS_WINDOWS:
            out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10).stdout.lower()
            return "codex" in out
        out = subprocess.run(["pgrep", "-fl", "codex"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "codex-monitor" in line or "codex-acct" in line or "codex_monitor" in line:
                continue
            if "/bin/codex" in line or "Codex Framework" in line or "app-server" in line:
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def touch_now() -> datetime:
    return now_utc()
