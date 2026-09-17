"""codex-monitor / codex-acct command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import List, Optional

from . import __version__, autostart, browsers, paths, store, usage
from .identity import fmt_countdown, fmt_local, identity, now_utc, plan_label, to_json_value
from .oauth import BrowserLogin, DeviceCodeLogin, OAuthError, apply_refreshed, refresh_tokens
from .store import StoreError


def _utf8_stdout() -> None:
    """Windows consoles default to a legacy code page; make sure we never crash on a symbol."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _wlen(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _table(rows: List[List[str]], header: List[str]) -> str:
    widths = [_wlen(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], _wlen(c))
    pad = lambda s, w: s + " " * (w - _wlen(s))  # noqa: E731
    out = ["  ".join(pad(h, widths[i]) for i, h in enumerate(header)), "  ".join("-" * w for w in widths)]
    out += ["  ".join(pad(c, widths[i]) for i, c in enumerate(r)) for r in rows]
    return "\n".join(out)


def _token_state(ident: dict) -> str:
    if not ident["has_tokens"]:
        return "API key" if ident["auth_mode"] == "apikey" else "none"
    exp = ident["access_expires"]
    if not exp:
        return "?"
    if exp < now_utc():
        return f"expired {fmt_local(exp)}"
    return f"{fmt_local(exp)} ({fmt_countdown(exp)} left)"


def _serial(p: store.Profile, active: bool) -> dict:
    return to_json_value({
        "name": p.name, "path": str(p.auth_path), "active": active, "email": p.ident["email"], "plan": p.ident["plan"],
        "account_id": p.ident["account_id"], "subscription_until": p.ident["subscription_until"],
        "access_token_expires": p.ident["access_expires"], "last_refresh": p.ident["last_refresh"],
    })


# ---------------------------------------------------------------- account commands

def cmd_list(a: argparse.Namespace) -> None:
    msg = store.adopt()
    if msg and not a.json:
        print("~ " + msg)
    main = store.main_profile()
    main_acct = main.account_id if main else ""
    rows, items, matched = [], [], False
    for p in store.profiles():
        active = bool(main_acct and p.account_id == main_acct)
        matched = matched or active
        items.append(_serial(p, active))
        rows.append([("* " if active else "  ") + p.name, p.ident["email"] or "-", plan_label(p.ident["plan"]), _token_state(p.ident),
                     fmt_local(p.ident["subscription_until"]), fmt_local(p.ident["last_refresh"], "%Y-%m-%d %H:%M")])
    if main and main.auth and not matched:
        items.insert(0, _serial(main, True))
        rows.insert(0, ["* (main, not archived)", main.ident["email"] or "-", plan_label(main.ident["plan"]), _token_state(main.ident),
                        fmt_local(main.ident["subscription_until"]), fmt_local(main.ident["last_refresh"], "%Y-%m-%d %H:%M")])
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("no accounts yet. `codex-monitor add <name>` signs one in; `codex-monitor save <name>` archives the current ~/.codex login.")
        return
    print(_table(rows, ["account", "email", "plan", "token", "subscription", "last refresh"]))
    print(f"\n* = the account currently in {paths.display_path(paths.main_auth_path())}")


def cmd_add(a: argparse.Namespace) -> None:
    name = store.sanitize(a.name)
    directory = paths.accounts_dir() / name
    if store.has_stored_credentials(directory) and not a.force:
        die(f"account '{name}' already exists; pick another name or use --force to log in again")
    _run_login(directory, name, device=a.device, open_browser=not a.no_open)


def cmd_relogin(a: argparse.Namespace) -> None:
    if a.name == "main":
        directory = paths.codex_home()
    else:
        directory = store.get_profile(a.name).directory
    _run_login(directory, a.name, device=a.device, open_browser=not a.no_open)


def _run_login(directory: Path, name: str, device: bool, open_browser: bool) -> None:
    if device:
        flow = DeviceCodeLogin(directory)
        flow.start()
        print(f"-> device-code login into {directory}")
        while flow.phase == "waiting" and not flow.url:
            time.sleep(0.2)
        if flow.url:
            print("   1. open this page (a private/incognito window avoids picking up an already signed-in account):")
            print(f"      {flow.url}")
            print(f"   2. enter the one-time code:  {flow.code}")
            print("   note: ChatGPT -> Settings -> Security -> 'Device code authorization for Codex' must be ON for that account.")
            if open_browser:
                b = browsers.open_private(flow.url) or ("default browser" if browsers.open_default(flow.url) else None)
                if b:
                    print(f"   (opened in {b})")
        phase = flow.wait()
    else:
        flow = BrowserLogin(directory)
        flow.start()
        if flow.phase == "failed":
            die(flow.error or "could not start login")
        print(f"-> browser login into {directory}")
        print("   sign in with the account you want to ADD. Use a private/incognito window; a normal window")
        print("   silently reuses the ChatGPT account that is already signed in.")
        print(f"   {flow.url}")
        if open_browser:
            b = browsers.open_private(flow.url) or ("default browser" if browsers.open_default(flow.url) else None)
            if b:
                print(f"   (opened in {b})")
        print("   waiting for the browser to redirect back to localhost:1455 ... (Ctrl+C to cancel)")
        try:
            phase = flow.wait()
        except KeyboardInterrupt:
            flow.cancel()
            print("\ncancelled")
            return
    if phase != "success":
        die(flow.error or phase)
    ident = flow.result_identity or {}
    print(f"\nOK  account '{name}': {ident.get('email') or '?'} ({plan_label(ident.get('plan'))})")
    print(f"    auth.json: {directory / 'auth.json'}")
    other = store.find_by_account(ident.get("account_id", ""), exclude=name)
    if other:
        print(f"    note: the same ChatGPT account is also stored as '{other.name}'")
    if directory != paths.codex_home():
        print(f"    next: `codex-monitor use {name}` to make it active, or `codex-monitor run {name}` to use it in parallel")


def cmd_import(a: argparse.Namespace) -> None:
    p = store.import_file(a.name, Path(a.file).expanduser(), force=a.force)
    print(f"OK  imported '{p.name}': {p.ident['email'] or '?'} ({plan_label(p.ident['plan'])}), token valid until {fmt_local(p.ident['access_expires'])}")


def cmd_save(a: argparse.Namespace) -> None:
    p = store.save_main(a.name, force=a.force)
    print(f"OK  saved the current {paths.display_path(paths.main_auth_path())} login as '{p.name}': {p.ident['email'] or '?'} ({plan_label(p.ident['plan'])})")


def cmd_use(a: argparse.Namespace) -> None:
    for m in store.activate(a.name):
        print("-> " + m)
    if store.codex_processes_running():
        print("!  a Codex process is still running (CLI or desktop app); restart it to pick up the new account")


def cmd_current(a: argparse.Namespace) -> None:
    main = store.main_profile()
    if not main or not main.auth:
        print(f"{paths.display_path(paths.main_auth_path())} does not exist (not logged in)")
        return
    p = store.find_by_account(main.account_id)
    print(f"{main.ident['email'] or '?'}  {plan_label(main.ident['plan'])}  account: {p.name if p else '(not archived - `codex-monitor save <name>`)'}")
    print(f"token valid until {fmt_local(main.ident['access_expires'])}; subscription until {fmt_local(main.ident['subscription_until'])}; last refresh {fmt_local(main.ident['last_refresh'], '%Y-%m-%d %H:%M')}")


def cmd_status(a: argparse.Namespace) -> None:
    store.adopt()
    main = store.main_profile()
    main_acct = main.account_id if main else ""
    targets: List[store.Profile] = []
    seen_main = False
    for p in store.profiles():
        if a.name and p.name != a.name:
            continue
        targets.append(p)
        seen_main = seen_main or (bool(main_acct) and p.account_id == main_acct)
    if not a.name and main and main.auth and not seen_main:
        targets.insert(0, main)
    if a.name and not targets:
        die(f"no account named {a.name!r}")
    views = []
    for p in targets:
        st = usage.AccountState(name=p.name, display_name=p.display_name, active=bool(main_acct and p.account_id == main_acct),
                                is_main=p.is_main, auth_path=str(p.auth_path), ident=p.ident)
        try:
            st.usage, auth, refreshed = usage.fetch_usage_auto(p.auth_path, also_main=st.active and not p.is_main)
            if refreshed:
                st.ident = identity(auth)
                print(f"~ token for '{p.name}' was rejected; refreshed it automatically", file=sys.stderr)
            st.credits = usage.fetch_reset_credits(auth)
            st.fetched_at = now_utc()
        except usage.UsageError as e:
            st.error = str(e)
        views.append(st.view())
    if a.json:
        print(json.dumps(to_json_value(views), ensure_ascii=False, indent=2))
        return
    for v in views:
        print("\n".join(usage.format_view(v)))
        print()


def cmd_run(a: argparse.Namespace) -> None:
    p = store.get_profile(a.name)
    for n in store.ensure_shared_links(p.directory):
        print("note: " + n, file=sys.stderr)
    env = dict(os.environ, CODEX_HOME=str(p.directory))
    args = list(a.args)
    if args and args[0] == "--":
        args = args[1:]
    codex = shutil.which("codex")
    if not codex:
        die("codex CLI not found on PATH")
    print(f"-> CODEX_HOME={p.directory}  ({p.ident['email'] or '?'}, {plan_label(p.ident['plan'])})", file=sys.stderr)
    if paths.IS_WINDOWS:
        import subprocess
        sys.exit(subprocess.call([codex] + args, env=env))
    os.execvpe(codex, [codex] + args, env)


def cmd_env(a: argparse.Namespace) -> None:
    p = store.get_profile(a.name)
    store.ensure_shared_links(p.directory)
    if paths.IS_WINDOWS:
        print(f'$env:CODEX_HOME = "{p.directory}"   # PowerShell;  cmd: set CODEX_HOME={p.directory}')
    else:
        print(f"export CODEX_HOME={p.directory}")


def cmd_path(a: argparse.Namespace) -> None:
    print(store.get_profile(a.name).auth_path)


def cmd_export(a: argparse.Namespace) -> None:
    dst = store.export(a.name, Path(a.dest).expanduser() if a.dest else None)
    p = store.get_profile(a.name)
    print(f"OK  exported '{a.name}' to {dst} ({p.ident['email'] or '?'}, token valid until {fmt_local(p.ident['access_expires'])})")
    print("    drop it in as ~/.codex/auth.json on the other machine. Whichever machine refreshes first may invalidate the other's refresh token.")


def cmd_remove(a: argparse.Namespace) -> None:
    p = store.get_profile(a.name)
    if not a.yes:
        ans = input(f"delete {p.directory}? This only removes local files (no remote logout). [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("cancelled")
            return
    store.remove(a.name)
    print(f"OK  removed '{a.name}'")


def cmd_sync(a: argparse.Namespace) -> None:
    msg = store.adopt()
    print(("~ " + msg) if msg else "nothing to sync: ~/.codex/auth.json matches its account directory (or belongs to no stored account)")


def cmd_refresh(a: argparse.Namespace) -> None:
    p = store.get_profile(a.name)
    rt = ((p.auth or {}).get("tokens") or {}).get("refresh_token")
    if not rt:
        die("this account has no refresh_token")
    if not a.yes:
        print(f"this exchanges the refresh_token of '{a.name}' ({p.ident['email'] or '?'}) for new tokens and rewrites {p.auth_path}.")
        print("the old refresh_token may stop working anywhere else this auth.json was copied to.")
        if input("continue? [y/N] ").strip().lower() not in ("y", "yes"):
            print("cancelled")
            return
    try:
        new = refresh_tokens(rt)
    except OAuthError as e:
        die(str(e))
    apply_refreshed(p.auth_path, new)
    main = store.main_profile()
    if main and main.account_id == p.account_id:
        apply_refreshed(paths.main_auth_path(), new)
        print(f"    (also updated {paths.display_path(paths.main_auth_path())}, which holds this account)")
    p2 = store.get_profile(a.name)
    print(f"OK  refreshed '{a.name}'; new token valid until {fmt_local(p2.ident['access_expires'])}")


# ---------------------------------------------------------------- dashboard / autostart

def cmd_serve(a: argparse.Namespace) -> None:
    from .web import serve
    if a.no_auto_refresh:
        os.environ["CODEX_MONITOR_NO_AUTO_REFRESH"] = "1"
    mode = "none" if a.no_browser else ("app" if a.app else "tab")
    serve(port=a.port, interval=a.interval, open_browser=mode, host=a.host, quiet=not a.verbose)


def cmd_autostart(a: argparse.Namespace) -> None:
    extra: List[str] = []
    if a.port:
        extra += ["--port", str(a.port)]
    if a.interval:
        extra += ["--interval", str(a.interval)]
    if a.action == "install":
        print(autostart.install(extra))
    elif a.action == "remove":
        print(autostart.remove())
    else:
        print(autostart.status())


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codex-monitor", description="Quota dashboard and multi-account manager for OpenAI Codex.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=f"accounts: {paths.accounts_dir()}\nmain credentials: {paths.main_auth_path()}")
    p.add_argument("--version", action="version", version=f"codex-monitor {__version__}")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("serve", help="run the local web dashboard (all platforms)")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--interval", type=int, default=30, help="usage API poll interval in seconds (default 30)")
    s.add_argument("--app", action="store_true", help="open as a chromeless app window (Chromium browsers)")
    s.add_argument("--no-browser", action="store_true", help="do not open a browser")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--no-auto-refresh", action="store_true", help="never exchange refresh tokens, even after a 401 (default: one attempt per account per 10 min)")
    s.set_defaults(fn=cmd_serve)

    s = sp.add_parser("autostart", help="start the dashboard at login (LaunchAgent / systemd / Task Scheduler)")
    s.add_argument("action", choices=["install", "remove", "status"])
    s.add_argument("--port", type=int)
    s.add_argument("--interval", type=int)
    s.set_defaults(fn=cmd_autostart)

    s = sp.add_parser("add", help="sign a new account in (browser login by default)")
    s.add_argument("name")
    s.add_argument("--device", action="store_true", help="device-code login instead of browser (needs the ChatGPT security setting)")
    s.add_argument("--no-open", action="store_true", help="print the URL only, do not open a browser")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_add)

    s = sp.add_parser("relogin", help="log an existing account (or 'main') in again, overwriting its auth.json")
    s.add_argument("name")
    s.add_argument("--device", action="store_true")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_relogin)

    s = sp.add_parser("import", help="import an existing auth.json")
    s.add_argument("name")
    s.add_argument("file")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_import)

    s = sp.add_parser("save", help="archive the current ~/.codex/auth.json as an account")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_save)

    s = sp.add_parser("list", help="list accounts")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sp.add_parser("use", help="make an account active (write it into ~/.codex/auth.json)")
    s.add_argument("name")
    s.set_defaults(fn=cmd_use)

    s = sp.add_parser("current", help="show which account ~/.codex is logged in as")
    s.set_defaults(fn=cmd_current)

    s = sp.add_parser("status", help="fetch quota: remaining %%, reset time, reset credits")
    s.add_argument("name", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sp.add_parser("run", help="run codex with an account without switching (isolated CODEX_HOME, shared config)")
    s.add_argument("name")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_run)

    s = sp.add_parser("env", help="print the CODEX_HOME assignment for an account")
    s.add_argument("name")
    s.set_defaults(fn=cmd_env)

    s = sp.add_parser("path", help="print an account's auth.json path")
    s.add_argument("name")
    s.set_defaults(fn=cmd_path)

    s = sp.add_parser("export", help="copy an account's auth.json out (for another machine)")
    s.add_argument("name")
    s.add_argument("dest", nargs="?")
    s.set_defaults(fn=cmd_export)

    s = sp.add_parser("sync", help="copy a refreshed ~/.codex/auth.json back into its account directory")
    s.set_defaults(fn=cmd_sync)

    s = sp.add_parser("refresh", help="(optional) explicit token refresh for an account")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_refresh)

    s = sp.add_parser("remove", help="delete an account directory (no remote logout)")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_remove)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        paths.accounts_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        args.fn(args)
    except StoreError as e:
        die(str(e))
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
