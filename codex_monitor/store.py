"""The account store: ~/.codex/auth.json ("main") plus one directory per account under
~/.codex-accounts. Every directory doubles as an isolated CODEX_HOME."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import ContextManager, List, Optional

from . import paths
from .identity import identity
from .locking import file_lock


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
    if not keep or len(keep) > 128:
        raise StoreError(f"invalid account name: {name!r}")
    return keep


def auth_lock(path: Path, blocking: bool = True) -> ContextManager[None]:
    """Keep archive locks outside movable account directories, including on Windows."""
    canonical = path.resolve()
    if canonical.parent.parent == paths.accounts_dir().resolve():
        root = canonical.parent.parent / "_auth_locks"
        root.mkdir(mode=0o700, exist_ok=True)
        key = hashlib.sha256(os.path.normcase(str(canonical)).encode("utf-8")).hexdigest()
        lock_path = root / (key + ".lock")
    else:
        lock_path = canonical.parent / ".auth.lock"
    return file_lock(lock_path, blocking=blocking)


def atomic_write(path: Path, data: bytes, create_parent: bool = True, durable: bool = False) -> None:
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    # Refresh's read/check/write and every application credential writer share this lock.
    with auth_lock(path) if path.name == "auth.json" else nullcontext():
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                if durable:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp, path)
            if durable and os.name != "nt":
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _load(name: str, directory: Path, is_main: bool) -> Profile:
    auth = read_json(directory / "auth.json")
    try:
        ident = identity(auth)
        if any(not isinstance(ident.get(key), str) for key in ("account_id", "email", "plan")):
            raise ValueError("invalid credential identity")
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        # One broken external auth file must not hide every other account.
        auth, ident = None, identity(None)
    return Profile(name=name, directory=directory, auth=auth, is_main=is_main, ident=ident)


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
        # Keep empty/incomplete profile directories visible so a failed re-login
        # does not make the account disappear from the list.
        out.append(_load(d.name, d, False))
    return out


def find_by_account(account_id: str, exclude: Optional[str] = None) -> Optional[Profile]:
    if not account_id:
        return None
    for p in profiles():
        if p.name != exclude and p.account_id == account_id:
            return p
    return None


def resolve_name(name: str) -> str:
    """Keep an existing exact folder name, otherwise normalize CLI shorthand."""
    exact = (isinstance(name, str) and bool(name) and not name.startswith((".", "_"))
             and not any(char in name for char in ("/", "\\", "\0")))
    if exact and (paths.accounts_dir() / name).is_dir():
        return name
    return sanitize(name)


def get_profile(name: str) -> Profile:
    name = resolve_name(name)
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
    candidates = [profile for profile in profiles() if profile.account_id == main.account_id]
    if not main.account_id or not candidates:
        return None
    def session_token(profile: Profile) -> Optional[str]:
        value = ((profile.auth or {}).get("tokens") or {}).get("refresh_token")
        return value if isinstance(value, str) and value else None

    main_token = session_token(main)
    matching = [profile for profile in candidates if main_token and session_token(profile) == main_token]
    if matching:
        target = matching[0]
    elif len({session_token(profile) for profile in candidates}) == 1:
        target = candidates[0]
    else:
        # After an external rotation, several independent sessions are ambiguous.
        # Preserve them rather than choosing a destination by directory order.
        return None
    m_ts = main.ident["last_refresh"] or main.file_mtime() or datetime.min.replace(tzinfo=timezone.utc)
    t_ts = target.ident["last_refresh"] or target.file_mtime() or datetime.min.replace(tzinfo=timezone.utc)
    if m_ts <= t_ts:
        return None
    try:
        data = main.auth_path.read_bytes()
        current_account = identity(json.loads(data)).get("account_id")
        unchanged = target.auth_path.read_bytes() == data
    except (FileNotFoundError, ValueError, TypeError, AttributeError):
        return None
    if current_account != main.account_id or unchanged:
        return None
    previous_token = ((target.auth or {}).get("tokens") or {}).get("refresh_token")
    updated = []
    for profile in candidates:
        token = ((profile.auth or {}).get("tokens") or {}).get("refresh_token")
        if profile.name != target.name and (not previous_token or token != previous_token):
            continue
        timestamp = profile.ident["last_refresh"] or profile.file_mtime()
        if timestamp and timestamp > m_ts:
            continue
        try:
            with auth_lock(profile.auth_path):
                # Re-read inside the writer lock so an intervening import is preserved.
                if read_json(profile.auth_path) != profile.auth:
                    continue
                atomic_write(profile.auth_path, data, create_parent=False)
        except FileNotFoundError:
            continue
        updated.append(profile.name)
    if not updated:
        return None
    names = ", ".join("'%s'" % name for name in updated)
    return f"synced refreshed tokens from {paths.display_path(main.auth_path)} into account {names}"


def archive_main_as_profile(main: Profile) -> Profile:
    if not main.account_id or not main.auth:
        raise StoreError("current login has no usable credentials")
    raw = (main.ident.get("email") or "account-" + main.account_id[:8]).split("@", 1)[0]
    try:
        base = sanitize(raw)
    except StoreError:
        base = "account-" + main.account_id[:8]
    name = base
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
    messages.append(f"switched: {paths.display_path(paths.main_auth_path())} -> '{target.name}' ({target.display_name})")
    return messages


def save_main(name: str, force: bool = False) -> Profile:
    name = resolve_name(name)
    main = main_profile()
    if not main or not main.auth:
        raise StoreError(f"{paths.main_auth_path()} does not exist or is not valid JSON")
    dst = paths.accounts_dir() / name / "auth.json"
    if dst.exists() and not force:
        raise StoreError(f"account '{name}' already exists (use --force to overwrite)")
    atomic_write(dst, main.auth_path.read_bytes())
    return get_profile(name)


def import_text(name: str, text: str, force: bool = False) -> Profile:
    """Install a pasted or uploaded auth.json. Same validation as `import_file`."""
    name = resolve_name(name)
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        auth = json.loads(text)
    except ValueError:
        raise StoreError("not valid JSON") from None
    if not isinstance(auth, dict):
        raise StoreError("not a valid Codex auth.json")
    tokens = auth.get("tokens")
    if tokens is not None and not isinstance(tokens, dict):
        raise StoreError("auth tokens must be an object")
    tokens = tokens or {}
    values = [auth.get(key) for key in ("OPENAI_API_KEY", "auth_mode", "last_refresh")]
    values += [tokens.get(key) for key in ("access_token", "id_token", "refresh_token", "account_id")]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise StoreError("auth credential fields must be strings")
    if not ((tokens.get("access_token") or "").strip() or (auth.get("OPENAI_API_KEY") or "").strip()):
        raise StoreError("not a valid Codex auth.json")
    try:
        ident = identity(auth)
        if any(not isinstance(ident[key], str) for key in ("email", "plan", "account_id")):
            raise ValueError
        text.encode("utf-8")
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise StoreError("invalid credential identity or timestamp") from None
    dst = paths.accounts_dir() / name / "auth.json"
    if dst.exists() and not force:
        raise StoreError(f"account '{name}' already exists (use --force to overwrite)")
    atomic_write(dst, text.encode("utf-8"))
    return get_profile(name)


def import_file(name: str, src: Path, force: bool = False) -> Profile:
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as error:
        raise StoreError(f"cannot read {src}: {error}") from error
    try:
        return import_text(name, text, force=force)
    except StoreError as error:
        if str(error) == "not a valid Codex auth.json":
            raise StoreError(f"{src} is not a valid Codex auth.json") from error
        raise


def export(name: str, dest: Optional[Path]) -> Path:
    p = get_profile(name)
    filename = f"auth-{p.name}.json"
    dst = dest if dest else Path.cwd() / filename
    if dst.is_dir():
        dst = dst / filename
    atomic_write(dst, p.auth_path.read_bytes())
    return dst


def directory_for_removal(name: str) -> Path:
    if (not isinstance(name, str) or not name or name.startswith((".", "_"))
            or any(char in name for char in ("/", "\\", "\0"))):
        raise StoreError("Choose a named account directory from the list")
    root = paths.accounts_dir().resolve()
    directory = root / name
    try:
        info = directory.lstat()
    except FileNotFoundError as error:
        raise StoreError("Account no longer exists; refresh the list") from error
    if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
            or directory.resolve().parent != root or directory.resolve() == paths.codex_home().resolve()):
        raise StoreError("Only ordinary account archives may be changed; current login and links are protected")
    return directory


def removal_revision(name: str) -> str:
    directory = directory_for_removal(name)
    info = directory.stat()
    try:
        auth = (directory / "auth.json").stat()
        stamp = "%s:%s:%s" % (auth.st_ino, auth.st_mtime_ns, auth.st_size)
    except FileNotFoundError:
        stamp = "missing"
    return "%s:%s:%s" % (info.st_dev, info.st_ino, stamp)


def remove(name: str, expected_revision: Optional[str] = None) -> Path:
    directory = directory_for_removal(name)
    if expected_revision is not None and removal_revision(name) != expected_revision:
        raise StoreError("Account changed; refresh the list before deleting")
    deleted = paths.accounts_dir().resolve() / "_deleted"
    if deleted.is_symlink() or deleted.resolve() != deleted:
        raise StoreError("Deleted-account backup directory must not be a link")
    deleted.mkdir(mode=0o700, exist_ok=True)
    backup = deleted / uuid.uuid4().hex
    backup.mkdir(mode=0o700)
    destination = backup / name
    # Move only the selected alias; symlinked shared config/session targets stay untouched.
    directory.rename(destination)
    return destination


def rename_name(name: object) -> str:
    if not isinstance(name, str):
        raise StoreError("Enter a new account name")
    clean = name.strip()
    reserved = {"main", "con", "prn", "aux", "nul"} | {prefix + str(i) for prefix in ("com", "lpt") for i in range(1, 10)}
    if (not 1 <= len(clean) <= 80 or clean.startswith((".", "_")) or clean.endswith(".")
            or clean.split(".", 1)[0].casefold() in reserved
            or any(not (char.isalnum() or char in " ._-") for char in clean)):
        raise StoreError("Use 1–80 letters, numbers, spaces, dots, underscores or hyphens; avoid leading dots/underscores and reserved names")
    return clean


def rename(name: str, new_name: object, expected_revision: Optional[str] = None) -> str:
    from .annotations import Annotations

    destination_name = rename_name(new_name)
    directory = directory_for_removal(name)
    destination = directory.parent / destination_name
    with Annotations(directory.parent).renaming(name, destination_name, rollback=lambda: destination.rename(directory)):
        directory = directory_for_removal(name)
        if expected_revision is not None and removal_revision(name) != expected_revision:
            raise StoreError("Account changed; refresh the list before renaming")
        if destination_name == name:
            return name
        if os.path.lexists(destination):
            raise StoreError("Account name already exists: " + destination_name)
        directory.rename(destination)
    return destination_name


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
            out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                low = line.lower()
                if "codex-monitor" in low or "codex-acct" in low or "codex_monitor" in low:
                    continue
                name = low.split()[0] if low.split() else ""
                if name in ("codex.exe", "codex"):
                    return True
                if "codex framework" in low or "app-server" in low:
                    return True
            return False
        out = subprocess.run(["pgrep", "-fl", "codex"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "codex-monitor" in line or "codex-acct" in line or "codex_monitor" in line:
                continue
            if "/bin/codex" in line or "Codex Framework" in line or "app-server" in line:
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False
