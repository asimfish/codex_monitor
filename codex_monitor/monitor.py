"""Background state shared by the web dashboard and the CLI: account discovery, API polling,
real-time rollout tailing, and the user-triggered actions."""
from __future__ import annotations

import collections
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__, browsers, paths, store, usage
from .annotations import Annotations, account_order, availability
from .identity import fmt_local, identity, now_utc, to_json_value
from .oauth import BrowserLogin, DeviceCodeLogin, OAuthError, apply_refreshed, refresh_tokens, sync_refreshed_copies
from .store import StoreError
from .reset_ledger import ResetLedger
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
        self._token_locks: Dict[str, threading.Lock] = {}
        self.reset_ledger = ResetLedger(paths.accounts_dir())
        self._poll_sequence: Dict[str, int] = {}
        self._published_poll_sequence: Dict[str, int] = {}
        self._quota_generation: Dict[str, int] = {}
        self._last_quota_reset: Dict[str, datetime] = {}
        self._event_watchers: Dict[str, usage.AppServerEventWatcher] = {}
        self._last_triggered_fetch: Dict[str, datetime] = {}
        self.triggered_fetches = 0
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self.login: Optional[object] = None
        self.login_name: Optional[str] = None
        self.login_mode: Optional[str] = None
        self.login_relogin = False
        self._finalized_login = None
        self.annotations = Annotations(paths.accounts_dir())

    # ------------------------------------------------------------ logging

    def note(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log.appendleft(f"{stamp} {message}")

    # ------------------------------------------------------------ profiles

    def _signature_now(self) -> str:
        main = store.main_profile()
        parts = []
        for p in ([main] if main else []) + store.profiles():
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
                if acct:
                    try:
                        reset_at = self.reset_ledger.status(acct)["last_reset_at"]
                    except StoreError:
                        reset_at = None
                    if reset_at:
                        self._last_quota_reset[acct] = reset_at
                        if st.fetched_at and st.fetched_at < reset_at:
                            st.usage, st.credits, st.fetched_at = None, None, None
                        if st.live and st.live.timestamp < reset_at:
                            st.live = None
                        st.live_extras = {key: value for key, value in st.live_extras.items() if value.timestamp >= reset_at}

            for p in profs:
                st = AccountState(name=p.name, display_name=p.display_name, active=bool(main_account and p.account_id == main_account),
                                  is_main=False, auth_path=str(p.auth_path), ident=p.ident)
                carry(st)
                new_states[p.name] = st
                order.append(p.name)
            if main and main.auth and not any(p.account_id == main_account for p in profs):
                st = AccountState(name="_main", display_name=main.display_name, active=True, is_main=True,
                                  auth_path=str(main.auth_path), ident=main.ident)
                carry(st)
                new_states[st.name] = st
                order.insert(0, st.name)
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

    def _token_lock(self, account: str) -> threading.Lock:
        with self.lock:
            return self._token_locks.setdefault(account, threading.Lock())

    def refresh_one(self, name: str) -> None:
        with self.lock:
            st = self.states.get(name)
            if not st:
                return
            auth = store.read_json(Path(st.auth_path))
            acct = st.ident.get("account_id") or name
            generation = self._quota_generation.get(acct, 0)
            sequence = self._poll_sequence[acct] = self._poll_sequence.get(acct, 0) + 1
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
                                                        allow_refresh=allow_refresh, refresh_lock=self._token_lock(acct))
            if refreshed:
                self._auto_refresh_at[acct] = now_utc()
                self.note(f"token for {st.display_name} was rejected; refreshed it automatically (like Codex does)")
                with self.lock:
                    for alias in self.states.values():
                        if alias.ident.get("account_id") == acct:
                            try:
                                updated = identity(store.read_json(Path(alias.auth_path)))
                                if updated.get("account_id") == acct:
                                    alias.ident = updated
                            except (AttributeError, TypeError, ValueError, OverflowError):
                                pass  # External file changes are reconciled by profile discovery.
            usage.validate_usage(u)
            credits = usage.fetch_reset_credits(auth) if need_credits else None
            fetched = now_utc()
            with self.lock:
                if (self.states.get(name) is not st or generation != self._quota_generation.get(acct, 0)
                        or sequence < self._published_poll_sequence.get(acct, 0)):
                    return
                if identity(auth).get("account_id") != st.ident.get("account_id"):
                    raise UsageError("account credentials changed; reload before refreshing")
                self._published_poll_sequence[acct] = sequence
                if refreshed:
                    st.ident = identity(auth)
                st.usage, st.fetched_at, st.error = u, fetched, None
                for alias in self.states.values():
                    if alias is not st and alias.ident.get("account_id") == acct:
                        alias.usage, alias.fetched_at = u, fetched
                        if credits is not None:
                            alias.credits = credits
                self.last_refresh = fetched
                if credits is not None:
                    st.credits = credits
                    self._credits_fetched[acct] = fetched
                usage.save_cache(st.ident.get("account_id", ""), u, st.credits, fetched)
        except (UsageError, OSError) as e:
            with self.lock:
                if (self.states.get(name) is not st or generation != self._quota_generation.get(acct, 0)
                        or sequence < self._published_poll_sequence.get(acct, 0)):
                    return
                self._published_poll_sequence[acct] = sequence
                st.error = str(e)
                if isinstance(e, UsageError) and e.status == 401 and allow_refresh:
                    self._auto_refresh_at[acct] = now_utc()
                if isinstance(e, UsageError) and e.status == 429:
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
                    reset_at = self._last_quota_reset.get(st.ident.get("account_id", ""))
                    if main_ev and reset_at and main_ev.timestamp < reset_at:
                        main_ev = None
                    if main_ev and not (is_main_dir and main_mtime and main_ev.timestamp < main_mtime):
                        if st.live is None or main_ev.timestamp > st.live.timestamp:
                            st.live = main_ev
                            used = (main_ev.primary or main_ev.secondary or {}).get("used_percent")
                            self.note(f"live event for {st.display_name}: used {used}% at {fmt_local(main_ev.timestamp, '%H:%M:%S')}")
                    for lid, ev in extras.items():
                        if reset_at and ev.timestamp < reset_at:
                            continue
                        if is_main_dir and main_mtime and ev.timestamp < main_mtime:
                            continue
                        prev = st.live_extras.get(lid)
                        if prev is None or ev.timestamp > prev.timestamp:
                            st.live_extras[lid] = ev

    def poll_app_server_events(self) -> None:
        """Desktop-app usage: a new `account/rateLimits/updated` row in the app-server log means the
        numbers just changed -> fetch the affected account right away (debounced to one fetch per
        3 s per account)."""
        with self.lock:
            items = [(n, s) for n, s in self.states.items()]
        for name, st in items:
            homes = []
            if st.active:
                homes.append(paths.codex_home())
            if not st.is_main:
                own = Path(st.auth_path).parent
                if (own / "logs_2.sqlite").exists() or any(own.glob("logs_*.sqlite")):
                    homes.append(own)
            for home in homes:
                key = str(home)
                w = self._event_watchers.get(key)
                if w is None:
                    w = usage.AppServerEventWatcher(home)
                    self._event_watchers[key] = w
                n = w.poll()
                if n <= 0:
                    continue
                last = self._last_triggered_fetch.get(name)
                if last and (now_utc() - last).total_seconds() < 3:
                    continue
                self._last_triggered_fetch[name] = now_utc()
                self.triggered_fetches += 1
                self.note(f"app-server reported a rate-limit update for {st.display_name}; fetching now")
                threading.Thread(target=self.refresh_one, args=(name,), daemon=True).start()

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
            while not self._stop.wait(1):
                try:
                    self.poll_live()
                except Exception as e:  # never let the tailer kill the loop
                    self.note(f"live tail error: {e}")
                try:
                    self.poll_app_server_events()
                except Exception as e:
                    self.note(f"app-server event watch error: {e}")

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

    def rename_account(self, name: object, new_name: object, revision: object) -> str:
        with self.lock:
            if not isinstance(name, str) or name not in self.states or self.states[name].is_main:
                raise StoreError("Choose a stored account; current main login cannot be renamed")
            if not isinstance(revision, str) or not revision or len(revision) > 512:
                raise StoreError("Refresh the list before renaming")
            if self.login_name == name and getattr(self.login, "phase", "") in ("starting", "waiting", "exchanging"):
                raise StoreError("Finish or cancel this account's login before renaming")
            directory = store.directory_for_removal(name)
            if Path(self.states[name].auth_path).parent.resolve() != directory:
                raise StoreError("Account location changed; refresh the list")
            current_identity = identity(store.read_json(directory / "auth.json"))
            if any(current_identity.get(key, "") != self.states[name].ident.get(key, "")
                   for key in ("account_id", "email", "auth_mode")):
                raise StoreError("Account identity changed; refresh the list before renaming")
            renamed = store.rename(name, new_name, expected_revision=revision)
            self.note("renamed account '%s' to '%s'" % (name, renamed))
            self.reload_profiles()
            return renamed

    def remove_account(self, name: object, revision: object) -> str:
        with self.lock:
            if not isinstance(name, str) or name not in self.states or self.states[name].is_main:
                raise StoreError("Choose a stored account; current main login cannot be deleted")
            if not isinstance(revision, str) or not revision or len(revision) > 512:
                raise StoreError("Refresh the list before deleting")
            if self.login_name == name and getattr(self.login, "phase", "") in ("starting", "waiting", "exchanging"):
                raise StoreError("Finish or cancel this account's login before deleting")
            directory = store.directory_for_removal(name)
            if Path(self.states[name].auth_path).parent.resolve() != directory:
                raise StoreError("Account location changed; refresh the list")
            current_identity = identity(store.read_json(directory / "auth.json"))
            if any(current_identity.get(key, "") != self.states[name].ident.get(key, "")
                   for key in ("account_id", "email", "auth_mode")):
                raise StoreError("Account identity changed; refresh the list before deleting")
            destination = store.remove(name, expected_revision=revision)
            self.note("removed account '%s' to _deleted backup" % name)
            self.reload_profiles()
            return str(destination)

    def switch(self, name: str) -> dict:
        prev = next((s for s in self.states.values() if s.active), None)
        msgs = store.activate(name)
        target = name if name in self.states else store.sanitize(name)
        undo = None
        marker = "archived current account as '"
        for m in msgs:
            if m.startswith(marker) and m.endswith("'"):
                undo = m[len(marker):-1]
                break
        if undo is None and prev is not None and not prev.is_main and prev.name != target:
            undo = prev.name
        running = store.codex_processes_running()
        for m in msgs:
            self.note(m)
        if running:
            self.note("a Codex process is still running; restart it to pick up the new account")
        self.reload_profiles()
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()
        return {"messages": msgs, "codex_running": running, "undo": undo}

    def sync_now(self) -> str:
        msg = store.adopt()
        if msg:
            self.note(msg)
            self.reload_profiles()
            return msg
        return ""

    def save_main(self, name: str, force: bool = False) -> str:
        p = store.save_main(name, force=force)
        self.note(f"saved current login as account '{p.name}'")
        self.reload_profiles()
        return p.name

    def import_auth(self, name: str, text: str, force: bool = False) -> dict:
        p = store.import_text(name, text, force=force)
        other = store.find_by_account(p.account_id, exclude=p.name)
        self.note(f"imported auth.json as account '{p.name}'")
        self.reload_profiles()
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()
        return {
            "name": p.name,
            "email": p.ident.get("email") or "",
            "plan": p.ident.get("plan") or "",
            "directory": paths.display_path(p.directory),
            "duplicate": other.name if other else None,
        }

    def set_interval(self, seconds: int) -> int:
        self.interval = max(10, min(600, int(seconds)))
        self.note(f"refresh interval set to {self.interval}s")
        return self.interval

    def auth_text(self, name: str) -> str:
        st = self.states[name]
        return Path(st.auth_path).read_text(encoding="utf-8")

    def refresh_token(self, name: str) -> str:
        with self.lock:
            st = self.states[name]
            token_lock = self._token_lock(st.ident.get("account_id") or name)
        if not token_lock.acquire(blocking=False):
            raise StoreError("this account is refreshing; wait for it to finish")
        try:
            auth = self._auth_for(name)
            rt = (auth.get("tokens") or {}).get("refresh_token")
            if not rt:
                raise StoreError("this account has no refresh_token")
            new = refresh_tokens(rt)
            if not apply_refreshed(Path(st.auth_path), new, expected_refresh_token=rt, expected_account_id=st.ident.get("account_id")):
                raise StoreError("credentials changed during refresh; reload the account")
            sync_refreshed_copies(Path(st.auth_path), auth, new, also_main=st.active and not st.is_main)
            self.note(f"refreshed tokens for {st.display_name}")
            self.reload_profiles()
        finally:
            token_lock.release()
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()
        return "refreshed"

    def use_reset_credit(self, name: str, request_id: str) -> dict:
        """Apply a confirmed reset; retain recovery information across process restarts."""
        from . import demo
        if demo.enabled():
            raise StoreError("reset cards cannot be used in demo mode")
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise StoreError("a valid reset request id is required") from None
        with self.lock:
            st = self.states.get(name)
            if st is None:
                raise StoreError("unknown account")
            acct = st.ident.get("account_id")
            if not acct:
                raise StoreError("this account has no ChatGPT account id")
        with self.reset_ledger.operation(acct, request_id) as operation:
            if operation.result is not None:
                return dict(operation.result)
            try:
                auth = self._auth_for(name)
                if identity(auth).get("account_id") != acct:
                    raise StoreError("account credentials changed; reload before using a reset card")
            except (StoreError, KeyError):
                if not operation.retrying:
                    operation.reject()
                raise
            try:
                result = usage.consume_reset_credit(auth, request_id)
            except UsageError as error:
                if 400 <= error.status < 500 and not operation.retrying:
                    operation.reject()
                raise  # Transport/5xx failures retain the durable UUID.
            operation.complete(result)
            with self.lock:
                self._credits_fetched.pop(acct, None)
                self._quota_generation[acct] = self._quota_generation.get(acct, 0) + 1
                if result["code"] in ("reset", "already_redeemed"):
                    self._last_quota_reset[acct] = self.reset_ledger.status(acct)["last_reset_at"]
                    usage.save_cache(acct, {}, None, now_utc())
                for state in self.states.values():
                    if state.ident.get("account_id") == acct:
                        state.credits = None
                        if result["code"] in ("reset", "already_redeemed"):
                            state.live = None
                            state.live_extras.clear()
                            state.usage, state.fetched_at = None, None
            self.note(f"reset-card result for {st.display_name}: {result['code']}")
            self.refresh_one(name)
            with self.lock:
                current = self.states.get(name)
                if current is not None and current.ident.get("account_id") == acct:
                    for alias in self.states.values():
                        if alias is not current and alias.ident.get("account_id") == acct:
                            alias.usage, alias.credits, alias.fetched_at, alias.error = current.usage, current.credits, current.fetched_at, current.error
                    if current.error:
                        result = {**result, "refresh_pending": True}
                        operation.complete(result)
            return result

    def start_login(self, name: str, mode: str = "browser", relogin: bool = False) -> dict:
        with self.lock:
            if mode not in ("browser", "device"):
                raise StoreError("login mode must be browser or device")
            phase = getattr(self.login, "phase", "") if self.login is not None else ""
            if phase in ("starting", "waiting", "exchanging"):
                raise StoreError("a login is already in progress")
            self.login = None
            if relogin:
                st = self.states.get(name)
                if not st:
                    raise StoreError(f"unknown account {name!r}")
                directory = Path(st.auth_path).parent
            else:
                name = store.resolve_name(name)
                directory = paths.accounts_dir() / name
                if store.has_stored_credentials(directory):
                    raise StoreError(f"account '{name}' already exists")
            flow = BrowserLogin(directory) if mode == "browser" else DeviceCodeLogin(directory)
            self.login, self.login_name, self.login_mode, self.login_relogin = flow, name, mode, relogin
            flow.start()
            if flow.phase == "failed":
                self.note(f"login failed for '{name}': {flow.error}")
            else:
                self.note(f"login started for '{name}' ({mode})")
            return self.login_status()

    def login_status(self) -> dict:
        with self.lock:
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
        with self.lock:
            if self.login is not None:
                self.login.cancel()
                self.note("login cancelled")
            self.login = None
            self.login_name = None
            self.login_mode = None
            self.login_relogin = False

    def finish_login_if_done(self) -> None:
        """Called by the web layer after a successful login so the new account shows up."""
        with self.lock:
            flow = self.login
            if flow is None or flow.phase != "success" or self._finalized_login is flow:
                return
            self.reload_profiles()
            self._finalized_login = flow
        threading.Thread(target=self.refresh_all, kwargs={"force": True}, daemon=True).start()

    # ------------------------------------------------------------ snapshot

    def _annotation_key(self, name: str) -> str:
        state = self.states[name]
        # A main-only login can change identity; stored profiles keep their labels on re-login.
        return "main:" + state.ident.get("account_id", "") if state.is_main else "profile:" + name

    def set_annotations(self, name: object, tags: object, unavailable: object) -> dict:
        with self.lock:
            if not isinstance(name, str) or name not in self.states:
                raise StoreError("Unknown account for tags")
            return self.annotations.save(self._annotation_key(name), tags, unavailable)

    def snapshot(self) -> dict:
        now = now_utc()
        with self.lock:
            annotation_error = None
            try:
                annotations = self.annotations.read()
            except StoreError as error:
                annotations = {}
                annotation_error = str(error)
            accounts = []
            for name in self.order:
                state = self.states[name]
                account = state.view(now)
                labels = annotations.get(self._annotation_key(name))
                if labels is None:
                    labels = annotations.get("main:" + state.ident.get("account_id", ""), {"tags": [], "unavailable": False})
                account["tags"] = labels["tags"]
                account["manual_unavailable"] = labels["unavailable"]
                account["pending_reset_request_id"] = None
                account["reset_recovery_error"] = None
                if account["account_id"]:
                    try:
                        account["pending_reset_request_id"] = self.reset_ledger.status(account["account_id"])["pending"]
                    except StoreError as error:
                        account["reset_recovery_error"] = str(error)
                        account["error"] = account["error"] or str(error)
                account["availability"] = availability(account, state.ident.get("has_tokens", False))
                account["has_tokens"] = state.ident.get("has_tokens", False)
                account["removal_revision"] = None
                if not state.is_main:
                    try:
                        account["removal_revision"] = store.removal_revision(name)
                    except (StoreError, OSError):
                        pass
                accounts.append(account)
            accounts.sort(key=account_order)
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
                "annotation_error": annotation_error,
            }
        return to_json_value(data)
