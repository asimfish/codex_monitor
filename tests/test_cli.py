#!/usr/bin/env python3
"""Sandboxed end-to-end test for bin/codex-acct.

Runs against throw-away CODEX_HOME / CODEX_ACCOUNTS_DIR directories with fake JWTs, so it never
touches ~/.codex. Network calls are not exercised (status/refresh are skipped).
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "codex-acct"


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def fake_jwt(payload: dict) -> str:
    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(payload)}.sig"


def make_auth(email: str, account_id: str, last_refresh: datetime, plan: str = "pro") -> dict:
    now = datetime.now(timezone.utc)
    auth_claims = {
        "chatgpt_account_id": account_id,
        "chatgpt_plan_type": plan,
        "chatgpt_user_id": "user-" + account_id[:8],
        "chatgpt_subscription_active_until": (now + timedelta(days=20)).isoformat(),
    }
    id_token = fake_jwt({"email": email, "exp": int((now + timedelta(hours=1)).timestamp()),
                         "https://api.openai.com/auth": auth_claims})
    access_token = fake_jwt({"exp": int((now + timedelta(days=9)).timestamp()), "iat": int(now.timestamp()),
                             "https://api.openai.com/auth": auth_claims,
                             "https://api.openai.com/profile": {"email": email}})
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {"id_token": id_token, "access_token": access_token,
                   "refresh_token": "rt.fake." + account_id[:6], "account_id": account_id},
        "last_refresh": last_refresh.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
    }


def write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))
    os.chmod(path, 0o600)


def read(path: Path) -> dict:
    return json.loads(path.read_text())


class Sandbox:
    def __init__(self, base: Path):
        self.home = base / "codex"
        self.accounts = base / "accounts"
        self.home.mkdir()
        self.accounts.mkdir()
        self.env = dict(os.environ, CODEX_HOME=str(self.home), CODEX_ACCOUNTS_DIR=str(self.accounts))

    def run(self, *args, check=True, stdin=None) -> subprocess.CompletedProcess:
        cp = subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True,
                            text=True, input=stdin)
        if check and cp.returncode != 0:
            raise AssertionError(f"codex-acct {' '.join(args)} failed ({cp.returncode}):\n{cp.stdout}\n{cp.stderr}")
        return cp

    @property
    def main(self) -> Path:
        return self.home / "auth.json"

    def profile(self, name: str) -> Path:
        return self.accounts / name / "auth.json"


def account_of(path: Path) -> str:
    return read(path)["tokens"]["account_id"]


def main() -> None:
    now = datetime.now(timezone.utc)
    acct_a, acct_b, acct_c = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002", "cccccccc-0000-0000-0000-000000000003"
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(Path(tmp))

        # main holds A (fresh), profile a holds A (stale), profile b holds B
        write(sb.main, make_auth("a@example.com", acct_a, now))
        write(sb.profile("a"), make_auth("a@example.com", acct_a, now - timedelta(hours=3)))
        write(sb.profile("b"), make_auth("b@example.com", acct_b, now - timedelta(days=1), plan="plus"))
        stale_a = read(sb.profile("a"))

        # 1. list adopts the fresher main copy back into profile a
        out = sb.run("list").stdout
        assert "a@example.com" in out and "b@example.com" in out, out
        assert read(sb.profile("a")) == read(sb.main), "adopt did not copy fresher main into profile a"
        assert read(sb.profile("a")) != stale_a
        js = json.loads(sb.run("list", "--json").stdout)
        active = [p["name"] for p in js if p["active"]]
        assert active == ["a"], js

        # 2. use b: main becomes B, nothing backed up (A was archived)
        sb.run("use", "b")
        assert account_of(sb.main) == acct_b
        assert not (sb.accounts / "_backup").exists(), "unexpected backup for archived account"
        assert "b@example.com" in sb.run("current").stdout

        # 3. main replaced externally by an unarchived account C, then use a -> C gets backed up
        write(sb.main, make_auth("c@example.com", acct_c, now))
        sb.run("use", "a")
        assert account_of(sb.main) == acct_a
        backups = list((sb.accounts / "_backup").glob("auth-c_at_example.com-*.json"))
        assert len(backups) == 1, backups
        assert account_of(backups[0]) == acct_c
        if os.name != "nt":
            assert oct(sb.main.stat().st_mode & 0o777) == "0o600"

        # 4. codex refreshed main (newer last_refresh) -> sync copies it into profile a
        refreshed = make_auth("a@example.com", acct_a, now + timedelta(minutes=5))
        refreshed["tokens"]["refresh_token"] = "rt.fake.rotated"
        write(sb.main, refreshed)
        out = sb.run("sync").stdout
        assert read(sb.profile("a"))["tokens"]["refresh_token"] == "rt.fake.rotated", out
        # second sync is a no-op
        out2 = sb.run("sync").stdout
        assert out2 != out, "second sync should report nothing to do"

        # 5. save current main under a new name, export / import / remove round trip
        sb.run("save", "a-copy")
        assert account_of(sb.profile("a-copy")) == acct_a
        exported = Path(tmp) / "exported.json"
        sb.run("export", "b", str(exported))
        assert account_of(exported) == acct_b
        if os.name != "nt":
            assert oct(exported.stat().st_mode & 0o777) == "0o600"
        sb.run("import", "b2", str(exported))
        assert account_of(sb.profile("b2")) == acct_b
        sb.run("remove", "b2", "-y")
        assert not sb.profile("b2").exists()

        # 6. duplicate name is refused without --force; unknown profile fails cleanly
        cp = sb.run("save", "a-copy", check=False)
        assert cp.returncode != 0
        cp = sb.run("use", "nope", check=False)
        assert cp.returncode != 0 and "nope" in (cp.stderr + cp.stdout)

        # 7. env / path helpers
        env_output = sb.run("env", "b").stdout.strip()
        if os.name == "nt":
            assert env_output == f'$env:CODEX_HOME = "{sb.accounts / "b"}"   # PowerShell;  cmd: set CODEX_HOME={sb.accounts / "b"}'
        else:
            assert env_output == f"export CODEX_HOME={sb.accounts / 'b'}"
        assert sb.run("path", "b").stdout.strip() == str(sb.profile("b"))
        # env creates shared symlinks only for items that exist in CODEX_HOME
        (sb.home / "config.toml").write_text("model = 'x'\n")
        sb.run("env", "b")
        shared = sb.accounts / "b" / "config.toml"
        assert shared.read_text() == "model = 'x'\n"
        if os.name != "nt":
            assert shared.is_symlink()

        # 8. hidden / underscore dirs are ignored as profiles
        write(sb.accounts / "_backup" / "junk" / "auth.json", make_auth("x@example.com", "x" * 36, now))
        write(sb.accounts / ".cache" / "auth.json", make_auth("y@example.com", "y" * 36, now))
        names = [p["name"] for p in json.loads(sb.run("list", "--json").stdout)]
        assert set(names) == {"a", "a-copy", "b"}, names

    print("codex-acct sandbox test: all checks passed")


if __name__ == "__main__":
    main()
