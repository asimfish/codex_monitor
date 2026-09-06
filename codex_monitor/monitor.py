"""Background state shared by the web dashboard and the CLI: account discovery, API polling,
real-time rollout tailing, and the user-triggered actions."""
from __future__ import annotations

import collections
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__, browsers, paths, store, usage
from .identity import fmt_local, identity, now_utc, to_json_value
from .oauth import BrowserLogin, DeviceCodeLogin, OAuthError, apply_refreshed, refresh_tokens
from .store import StoreError
from .usage import AccountState, RolloutTailer, UsageError


class Monitor:
    def __init__(self, interval: int = 30, credits_max_age: int = 180):
        self.interval = max(10, int(interval))
        self.credits_max_age = credits_max_age
        self.lock = threading.RLock()
        self.states: Dict[str, AccountState] = {}
        self.order: List[str] = []
        self.last_refresh: Optional[datetime] = None
        self.refreshing = False
        self.backoff_until: Optional[datetime] = None
        self.log: collections.deque = collections.deque(maxlen=30)
        self._tailers: Dict[str, RolloutTailer] = {}
        self._main_auth_mtime: Optional[datetime] = None
        self._signature = ""
        self._credits_fetched: Dict[str, datetime] = {}
        self._auto_refresh_at: Dict[str, datetime] = {}
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self.login: Optional[object] = None
        self.login_name: Optional[str] = None
        self.login_mode: Optional[str] = None
        self.login_relogin = False

    # ------------------------------------------------------------ logging

    def note(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log.appendleft(f"{stamp} {message}")

    # ------------------------------------------------------------ profiles

    def _signature_now(self) -> str:
        parts = []
        for p in ([store.main_profile()] if store.main_profile() else []) + store.profiles():
            try:
                st = p.auth_path.stat()
                parts.append(f"{p.auth_path}|{st.st_mtime}|{st.st_size}")
            except OSError:
                parts.append(f"{p.auth_path}|missing")
        return "\n".join(parts)

    def reload_profiles(self) -> None:
        msg = store.adopt()
        if msg:
            self.note(msg)
        main = store.main_profile()
        profs = store.profiles()
        main_account = main.account_id if main else ""
        with self.lock:
            previous = {s.ident.get("account_id"): s for s in self.states.values() if s.ident.get("account_id")}
            new_states: Dict[str, AccountState] = {}
            order: List[str] = []

            def carry(st: AccountState) -> None:
                acct = st.ident.get("account_id")
                prev = previous.get(acct) if acct else None
                if prev:
                    st.usage, st.credits, st.fetched_at, st.error = prev.usage, prev.credits, prev.fetched_at, prev.error
                    st.live, st.live_extras = prev.live, prev.live_extras
                elif acct:
                    cached = usage.load_cache(acct)
                    if cached:
                        st.usage, st.credits, st.fetched_at = cached
                        st.error = "showing cached data"

            for p in profs:
                st = AccountState(name=p.name, display_name=p.display_name, active=bool(main_account and p.account_id == main_account),
                                  is_main=False, auth_path=str(p.auth_path), ident=p.ident)
                carry(st)
                new_states[p.name] = st
                order.append(p.name)
            if main and main.auth and not any(p.account_id == main_account for p in profs):
                st = AccountState(name="main", display_name=main.display_name, active=True, is_main=True,
                                  auth_path=str(main.auth_path), ident=main.ident)
                carry(st)
                new_states["main"] = st
                order.insert(0, "main")
            order.sort(key=lambda n: (not new_states[n].active, n.lower()))
            self.states, self.order = new_states, order
            self._main_auth_mtime = main.file_mtime() if main else None
            self._signature = self._signature_now()

    def _auth_for(self, name: str) -> dict:
        st = self.states[name]
        data = store.read_json(Path(st.auth_path))
        if not data:
            raise StoreError(f"cannot read {st.auth_path}")
        return data

    # ------------------------------------------------------------ fetching

    def refresh_all(self, force: bool = False) -> None:
        from . import demo
        if demo.enabled():
            return
        with self.lock:
            if self.refreshing:
                return
            if not force and self.backoff_until and self.backoff_until > now_utc():
                return
            self.refreshing = True
            names = list(self.order)
        try:
            threads = [threading.Thread(target=self.refresh_one, args=(n,), daemon=True) for n in names]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=40)
        finally:
            with self.lock:
                self.refreshing = False
                self.last_refresh = now_utc()

    def refresh_one(self, name: str) -> None:
        with self.lock:
            st = self.states.get(name)
            if not st:
                return
            auth = store.read_json(Path(st.auth_path))
            acct = st.ident.get("account_id") or name
            need_credits = st.credits is None or (now_utc() - self._credits_fetched.get(acct, datetime.min.replace(tzinfo=timezone.utc))).total_seconds() > self.credits_max_age
        if not auth:
            with self.lock:
                st.error = "auth.json unreadable"
            return
        # at most one automatic refresh-token exchange per account per 10 minutes
        last_try = self._auto_refresh_at.get(acct)
        allow_refresh = last_try is None or (now_utc() - last_try).total_seconds() > 600
        try:
            u, auth, refreshed = usage.fetch_usage_auto(Path(st.auth_path), also_main=st.active and not st.is_main,
                                                        allow_refresh=allow_refresh)
            if refreshed:
                self._auto_refresh_at[acct] = now_utc()
                self.note(f"token for {st.display_name} was rejected; refreshed it automatically (like Codex does)")
                st.ident = identity(auth)
            credits = usage.fetch_reset_credits(auth) if need_credits else None
            fetched = now_utc()
            with self.lock:
                st.usage, st.fetched_at, st.error = u, fetched, None
                if credits is not None:
                    st.credits = credits
                    self._credits_fetched[acct] = fetched
            usage.save_cache(st.ident.get("account_id", ""), u, st.credits, fetched)
        except UsageError as e:
            with self.lock:
                st.error = str(e)
                if e.status == 401 and allow_refresh:
                    self._auto_refresh_at[acct] = now_utc()
                if e.status == 429:
                    self.backoff_until = now_utc() + timedelta(seconds=120)
                    self.note("HTTP 429: pausing automatic refresh for 2 minutes")

    # ------------------------------------------------------------ live events

    def poll_live(self) -> None:
        now = now_utc()
        with self.lock:
            items = [(n, s) for n, s in self.states.items()]
            main_mtime = self._main_auth_mtime
        for name, st in items:
            dirs = []
            if st.active:
                dirs.append((paths.codex_home() / "sessions", True))
            if not st.is_main:
                own = Path(st.auth_path).parent / "sessions"
                if own.is_dir():
                    dirs.append((own, False))
            for d, is_main_dir in dirs:
                tailer = self._tailers.get(str(d))
                if tailer is None:
                    tailer = RolloutTailer(d)
                    self._tailers[str(d)] = tailer
                main_ev, extras = tailer.poll_latest(now)
                with self.lock:
                    if main_ev and not (is_main_dir and main_mtime and main_ev.timestamp < main_mtime):
                        if st.live is None or main_ev.timestamp > st.live.timestamp:
                            st.live = main_ev
                            used = (main_ev.primary or main_ev.secondary or {}).get("used_percent")
                            self.note(f"live event for {st.display_name}: used {used}% at {fmt_local(main_ev.timestamp, '%H:%M:%S')}")
                    for lid, ev in extras.items():
                        if is_main_dir and main_mtime and ev.timestamp < main_mtime:
                            continue
                        prev = st.live_extras.get(lid)
                        if prev is None or ev.timestamp > prev.timestamp:
                            st.live_extras[lid] = ev

    # ------------------------------------------------------------ background

    def start_background(self) -> None:
        from . import demo
        if demo.enabled():
            with self.lock:
                self.states = {s.name: s for s in demo.states()}
                self.order = list(self.states.keys())
                self.last_refresh = now_utc()
            self.note("demo mode: fabricated accounts, no network")
            return
        self.reload_profiles()
        threading.Thread(target=self.refresh_all, daemon=True).start()

        def refresher() -> None:
            while not self._stop.wait(self.interval):
                self.refresh_all()

        def live() -> None:
            while not self._stop.wait(2):
                try:
                    self.poll_live()
                except Exception as e:  # never let the tailer kill the loop
                    self.note(f"live tail error: {e}")

        def files() -> None:
            while not self._stop.wait(10):
                try:
                    if self._signature_now() != self._signature:
                        self.note("auth.json changed on disk; reloading accounts")
                        self.reload_profiles()
                        threading.Thread(target=self.refresh_all, daemon=True).start()
                except Exception as e:
                    self.note(f"file poll error: {e}")

        for fn in (refresher, live, files):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)
        try:
            self.poll_live()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ actions

    def switch(self, name: str) -> List[str]:
        msgs = store.activate(name)
        for m in msgs:
            self.note(m)
        self.reload_profiles()
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()
        return msgs

    def save_main(self, name: str) -> str:
        p = store.save_main(name)
        self.note(f"saved current login as account '{p.name}'")
        self.reload_profiles()
        return p.name

    def auth_text(self, name: str) -> str:
        st = self.states[name]
        return Path(st.auth_path).read_text(encoding="utf-8")

    def refresh_token(self, name: str) -> str:
        auth = self._auth_for(name)
        rt = (auth.get("tokens") or {}).get("refresh_token")
        if not rt:
            raise StoreError("this account has no refresh_token")
        new = refresh_tokens(rt)
        st = self.states[name]
        apply_refreshed(Path(st.auth_path), new)
        if st.active and not st.is_main:
            apply_refreshed(paths.main_auth_path(), new)
        self.note(f"refreshed tokens for {st.display_name}")
        self.reload_profiles()
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()
        return "refreshed"

    def start_login(self, name: str, mode: str = "browser", relogin: bool = False) -> dict:
        if self.login is not None and getattr(self.login, "phase", "") in ("starting", "waiting", "exchanging"):
            raise StoreError("a login is already in progress")
        if relogin:
            st = self.states.get(name)
            if not st:
                raise StoreError(f"unknown account {name!r}")
            directory = Path(st.auth_path).parent
        else:
            name = store.sanitize(name)
            directory = paths.accounts_dir() / name
            if (directory / "auth.json").exists():
                raise StoreError(f"account '{name}' already exists")
        flow = BrowserLogin(directory) if mode == "browser" else DeviceCodeLogin(directory)
        self.login, self.login_name, self.login_mode, self.login_relogin = flow, name, mode, relogin
        flow.start()
        self.note(f"login started for '{name}' ({mode})")
        return self.login_status()

    def login_status(self) -> dict:
        flow = self.login
        if flow is None:
            return {"phase": "idle"}
        out = {"phase": flow.phase, "name": self.login_name, "mode": self.login_mode, "error": flow.error,
               "url": getattr(flow, "url", None), "code": getattr(flow, "code", None)}
        ident = getattr(flow, "result_identity", None)
        if ident:
            out["email"] = ident.get("email")
            out["plan"] = ident.get("plan")
        return out

    def open_login_url(self, private: bool) -> str:
        flow = self.login
        url = getattr(flow, "url", None) if flow else None
        if not url:
            raise StoreError("no login in progress")
        if private:
            name = browsers.open_private(url)
            if name:
                return name
        return "default browser" if browsers.open_default(url) else "none"

    def cancel_login(self) -> None:
        if self.login is not None:
            self.login.cancel()
            self.note("login cancelled")

    def finish_login_if_done(self) -> None:
        """Called by the web layer after a successful login so the new account shows up."""
        flow = self.login
        if flow is not None and flow.phase == "success":
            self.reload_profiles()
            threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> dict:
        now = now_utc()
        with self.lock:
            accounts = [self.states[n].view(now) for n in self.order]
            data = {
                "version": __version__,
                "now": now,
                "interval": self.interval,
                "last_refresh": self.last_refresh,
                "refreshing": self.refreshing,
                "accounts_dir": paths.display_path(paths.accounts_dir()),
                "codex_home": str(paths.codex_home()),
                "private_browser": (browsers.find_private_browser() or (None,))[0],
                "accounts": accounts,
                "login": self.login_status(),
                "log": list(self.log),
            }
        return to_json_value(data)
