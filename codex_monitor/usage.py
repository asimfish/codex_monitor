"""Quota data, explicit reset-card redemption, and real-time Codex session events."""
from __future__ import annotations

import json
import math
from contextlib import nullcontext
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import net, paths
from .identity import (access_token_expired, epoch, fmt_countdown, fmt_local, fmt_percent, identity,
                       now_utc, parse_iso, plan_label, window_label)


class UsageError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _object(value: Any) -> dict:
    if not isinstance(value, dict):
        raise UsageError("invalid quota response: expected an object")
    return value


def _number(value: Any) -> None:
    try:
        valid = not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        valid = False
    if not valid:
        raise UsageError("invalid quota response: expected a finite number")


def validate_reset_credits(data: Any) -> dict:
    data = _object(data)
    count = data.get("available_count")
    if count is not None and (type(count) is not int or count < 0):
        raise UsageError("invalid reset-card count")
    credits = data.get("credits") if data.get("credits") is not None else []
    if not isinstance(credits, list):
        raise UsageError("invalid reset-card list")
    for credit in credits:
        credit = _object(credit)
        expiry = credit.get("expires_at")
        if expiry is not None and (not isinstance(expiry, str) or parse_iso(expiry) is None):
            raise UsageError("invalid reset-card expiry")
    return data


def validate_usage(data: Any) -> dict:
    """Validate fields consumed by views; preserve unknown provider fields."""
    data = _object(data)
    plan = data.get("plan_type")
    if plan is not None and not isinstance(plan, str):
        raise UsageError("invalid quota plan")
    extra = data.get("additional_rate_limits") if data.get("additional_rate_limits") is not None else []
    if not isinstance(extra, list):
        raise UsageError("invalid additional quota list")
    limits = [data.get("rate_limit")]
    for entry in extra:
        entry = _object(entry)
        for key in ("metered_feature", "limit_name"):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                raise UsageError("invalid additional quota name")
        limits.append(entry.get("rate_limit"))
    for limit in limits:
        if limit is None:
            continue
        limit = _object(limit)
        for key in ("primary_window", "secondary_window", "primary", "secondary"):
            window = limit.get(key)
            if window is None:
                continue
            window = _object(window)
            for field in ("used_percent", "reset_at", "resets_at"):
                if window.get(field) is not None:
                    _number(window[field])
            for field in ("limit_window_seconds", "window_minutes"):
                value = window.get(field)
                if value is not None and (type(value) is not int or value < 0):
                    raise UsageError("invalid quota window duration")
    if data.get("credits") is not None:
        credits = _object(data["credits"])
        if credits.get("balance") is not None:
            _number(credits["balance"])
    if data.get("rate_limit_reset_credits") is not None:
        validate_reset_credits(data["rate_limit_reset_credits"])
    return data


def _get(path: str, token: str, account_id: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(paths.base_url() + path, headers={
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-Id": account_id,
        "User-Agent": paths.USER_AGENT,
        "Accept": "application/json",
        "originator": "codex_cli_rs",
    })
    try:
        with net.urlopen(req, timeout=timeout) as r:
            return _object(json.loads(r.read().decode("utf-8")))
    except (ValueError, UnicodeError) as e:
        raise UsageError("invalid quota response: malformed JSON") from e
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        if e.code == 401:
            raise UsageError(f"token rejected (401): {body}", 401) from e
        raise UsageError(f"HTTP {e.code}: {body}", e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise UsageError(f"network error: {e}") from e


def fetch_usage(auth: dict) -> dict:
    """GET /wham/usage. Raises UsageError."""
    tokens = auth.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise UsageError("auth.json has invalid tokens; re-login this account")
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise UsageError("no access_token (API-key mode?)")
    try:
        ident = identity(auth)
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        raise UsageError("auth.json has invalid identity fields; re-login this account") from None
    if access_token_expired(ident):
        raise UsageError(f"access token expired at {fmt_local(ident['access_expires'])}; refresh or re-login", 401)
    return validate_usage(_get("/wham/usage", token, ident["account_id"]))


def auto_refresh_enabled() -> bool:
    return os.environ.get("CODEX_MONITOR_NO_AUTO_REFRESH") != "1"


def fetch_usage_auto(auth_path: Path, also_main: bool = False, allow_refresh: bool = True, refresh_lock: Any = None) -> Tuple[dict, dict, bool]:
    """Fetch usage for the auth.json at `auth_path`. When the server rejects the token (401) or it
    has expired locally, exchange the refresh token once - exactly what Codex itself does - write
    the new tokens back (also to ~/.codex/auth.json when `also_main`), and retry.

    Returns (usage, auth, refreshed). Raises UsageError; a failed refresh raises a UsageError whose
    message says the session was revoked."""
    from .store import read_json
    auth = read_json(auth_path) or {}
    try:
        return fetch_usage(auth), auth, False
    except UsageError as e:
        if e.status != 401 or not allow_refresh or not auto_refresh_enabled():
            raise
        with refresh_lock if refresh_lock is not None else nullcontext():
            current = read_json(auth_path) or {}
            if current != auth:
                return fetch_usage(current), current, False
            rt = (auth.get("tokens") or {}).get("refresh_token")
            if not rt:
                raise
            from .oauth import OAuthError, apply_refreshed, refresh_tokens, sync_refreshed_copies
            try:
                new = refresh_tokens(rt)
            except OAuthError as oe:
                raise UsageError(f"session revoked: token rejected and refresh failed ({oe}); re-login this account", 401) from oe
            if not apply_refreshed(auth_path, new, expected_refresh_token=rt, expected_account_id=identity(auth).get("account_id")):
                raise UsageError("credentials changed during refresh; retry with the current login")
            sync_refreshed_copies(auth_path, auth, new, also_main=also_main)
            auth = read_json(auth_path) or {}
            return fetch_usage(auth), auth, True


def fetch_reset_credits(auth: dict) -> Optional[dict]:
    tokens = auth.get("tokens") or {}
    token = tokens.get("access_token")
    if not token:
        return None
    try:
        return validate_reset_credits(_get("/wham/rate-limit-reset-credits", token, identity(auth)["account_id"]))
    except UsageError:
        return None


def consume_reset_credit(auth: dict, request_id: str) -> dict:
    """Spend one banked reset on explicit request. Never retry the POST automatically.

    Matches OpenAI's backend-client/src/client/rate_limit_resets.rs. The backend
    uses redeem_request_id for idempotency and selects an applicable credit.
    """
    ident = identity(auth)
    token = (auth.get("tokens") or {}).get("access_token")
    if not token or not ident["account_id"]:
        raise UsageError("this account has no ChatGPT access token or account id", 401)
    if access_token_expired(ident):
        raise UsageError("access token expired; refresh the token or re-login before using a reset card", 401)
    req = urllib.request.Request(paths.base_url() + "/wham/rate-limit-reset-credits/consume",
                                 data=json.dumps({"redeem_request_id": request_id}).encode("utf-8"),
                                 method="POST", headers={
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-Id": ident["account_id"],
        "User-Agent": paths.USER_AGENT,
        "originator": "codex_cli_rs",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with net.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Do not surface raw upstream response bodies containing account data.
        raise UsageError(f"reset-card request failed (HTTP {e.code})", e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise UsageError("reset-card outcome could not be confirmed; retry the same request") from e
    except (ValueError, UnicodeError) as e:
        raise UsageError("reset-card response could not be read; retry the same request") from e
    if not isinstance(result, dict) or result.get("code") not in {"reset", "nothing_to_reset", "no_credit", "already_redeemed"}:
        raise UsageError("unknown reset-card outcome; retry the same request")
    windows = result.get("windows_reset", 0)
    if not isinstance(windows, int) or isinstance(windows, bool) or windows < 0:
        raise UsageError("invalid reset-card outcome; retry the same request")
    return {"code": result["code"], "windows_reset": windows}


# ---------------------------------------------------------------- windows / views

def _window_view(w: Optional[dict], now: datetime) -> Optional[dict]:
    if not w or w.get("used_percent") is None:
        return None
    used = float(w["used_percent"])
    remaining = max(0.0, min(100.0, 100.0 - used))
    secs = w.get("limit_window_seconds")
    if secs is None and w.get("window_minutes") is not None:
        secs = int(w["window_minutes"]) * 60
    reset_at = epoch(w["reset_at"]) if w.get("reset_at") is not None else (epoch(w["resets_at"]) if w.get("resets_at") is not None else None)
    return {
        "label": window_label(secs),
        "window_seconds": secs,
        "used_percent": used,
        "remaining_percent": remaining,
        "reset_at": reset_at,
        "reset_in": fmt_countdown(reset_at, now) if reset_at else "",
    }


def windows_of(rate_limit: Optional[dict], now: datetime) -> List[dict]:
    if not rate_limit:
        return []
    ws = [_window_view(rate_limit.get("primary_window") or rate_limit.get("primary"), now),
          _window_view(rate_limit.get("secondary_window") or rate_limit.get("secondary"), now)]
    ws = [w for w in ws if w]
    return sorted(ws, key=lambda w: w["window_seconds"] or 0)


def reached_detail(v: Any) -> Optional[str]:
    """rate_limit_reached_type is a string in some responses and {type, details} in others."""
    if v is None:
        return None
    if isinstance(v, str):
        return None if v == "rate_limit_reached" else v
    if isinstance(v, dict):
        t = v.get("type")
        if t and t != "rate_limit_reached":
            return str(t)
        d = v.get("details")
        return None if d in (None, "default") else str(d)
    return None


@dataclass
class LiveEvent:
    timestamp: datetime
    limit_id: Optional[str]
    limit_name: Optional[str]
    primary: Optional[dict]
    secondary: Optional[dict]
    plan_type: Optional[str]
    limit_reached: bool

    @property
    def is_main(self) -> bool:
        return self.limit_id in (None, "codex")

    def as_rate_limit(self) -> dict:
        return {"allowed": not self.limit_reached, "limit_reached": self.limit_reached,
                "primary_window": self.primary, "secondary_window": self.secondary}


_TOKEN_COUNT = re.compile(rb'"token_count"')


def parse_rollout_line(line: bytes) -> Optional[LiveEvent]:
    if b'"rate_limits"' not in line or not _TOKEN_COUNT.search(line):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    payload = obj.get("payload") if isinstance(obj, dict) else None
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rl = payload.get("rate_limits")
    ts = parse_iso(obj.get("timestamp"))
    if not isinstance(rl, dict) or not ts:
        return None

    def window(w: Any) -> Optional[dict]:
        if not isinstance(w, dict) or w.get("used_percent") is None:
            return None
        minutes = w.get("window_minutes")
        resets = w.get("resets_at")
        return {
            "used_percent": float(w["used_percent"]),
            "limit_window_seconds": int(minutes) * 60 if minutes is not None else None,
            "reset_at": resets,
        }

    primary, secondary = window(rl.get("primary")), window(rl.get("secondary"))
    if primary is None and secondary is None:
        return None
    return LiveEvent(timestamp=ts, limit_id=rl.get("limit_id"), limit_name=rl.get("limit_name"),
                     primary=primary, secondary=secondary, plan_type=rl.get("plan_type"),
                     limit_reached=rl.get("rate_limit_reached_type") is not None)


class RolloutTailer:
    """Incrementally reads bytes appended to recently modified rollout files under one
    `sessions` directory. Cheap enough to run every couple of seconds."""

    def __init__(self, sessions_dir: Path, recent_window: float = 15 * 60, initial_tail_bytes: int = 512 * 1024,
                 full_scan_every: float = 10.0):
        self.sessions_dir = Path(sessions_dir)
        self.recent_window = recent_window
        self.initial_tail_bytes = initial_tail_bytes
        self.full_scan_every = full_scan_every
        self._offsets: Dict[str, int] = {}
        self._partial: Dict[str, bytes] = {}
        self._candidates: List[Path] = []
        self._last_full_scan = 0.0

    def _full_scan(self, cutoff: float) -> List[Path]:
        """Every rollout file (any date directory) modified after `cutoff`. Threads can live for
        weeks, so their file sits under the day they were *created*, not today; ~1000 files scan
        in well under 100 ms."""
        out: List[Path] = []
        try:
            years = list(os.scandir(self.sessions_dir))
        except OSError:
            return out
        for y in years:
            if not y.is_dir():
                continue
            try:
                months = list(os.scandir(y.path))
            except OSError:
                continue
            for m in months:
                if not m.is_dir():
                    continue
                try:
                    days = list(os.scandir(m.path))
                except OSError:
                    continue
                for d in days:
                    if not d.is_dir():
                        continue
                    try:
                        for f in os.scandir(d.path):
                            if f.name.endswith(".jsonl"):
                                try:
                                    if f.stat().st_mtime >= cutoff:
                                        out.append(Path(f.path))
                                except OSError:
                                    pass
                    except OSError:
                        continue
        return out

    def _recent_files(self, now: datetime) -> List[Path]:
        cutoff = now.timestamp() - self.recent_window
        if now.timestamp() - self._last_full_scan >= self.full_scan_every:
            self._candidates = self._full_scan(cutoff)
            self._last_full_scan = now.timestamp()
            return list(self._candidates)
        # Between full scans: re-check known candidates plus anything new in today's directory.
        local_now = now.astimezone()
        today = self.sessions_dir / f"{local_now.year:04d}" / f"{local_now.month:02d}" / f"{local_now.day:02d}"
        seen = {str(p) for p in self._candidates}
        try:
            for f in today.iterdir():
                if f.suffix == ".jsonl" and str(f) not in seen:
                    try:
                        if f.stat().st_mtime >= cutoff:
                            self._candidates.append(f)
                            seen.add(str(f))
                    except OSError:
                        pass
        except OSError:
            pass
        return list(self._candidates)

    def poll(self, now: Optional[datetime] = None) -> List[LiveEvent]:
        now = now or now_utc()
        events: List[LiveEvent] = []
        for f in self._recent_files(now):
            key = str(f)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            offset = self._offsets.get(key)
            if offset is None:
                offset = size - self.initial_tail_bytes if size > self.initial_tail_bytes else 0
            elif offset > size:
                offset = 0
            if size <= offset:
                self._offsets[key] = size
                continue
            try:
                with open(f, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read(min(size - offset, 8 * 1024 * 1024))
            except OSError:
                continue
            self._offsets[key] = offset + len(data)
            buf = self._partial.get(key, b"") + data
            lines = buf.split(b"\n")
            self._partial[key] = lines.pop()
            for line in lines:
                ev = parse_rollout_line(line)
                if ev:
                    events.append(ev)
        return events

    def poll_latest(self, now: Optional[datetime] = None) -> Tuple[Optional[LiveEvent], Dict[str, LiveEvent]]:
        main: Optional[LiveEvent] = None
        extras: Dict[str, LiveEvent] = {}
        for ev in self.poll(now):
            if ev.is_main:
                if main is None or ev.timestamp > main.timestamp:
                    main = ev
            elif ev.limit_id:
                prev = extras.get(ev.limit_id)
                if prev is None or ev.timestamp > prev.timestamp:
                    extras[ev.limit_id] = ev
        return main, extras


# ---------------------------------------------------------------- app-server event trigger

class AppServerEventWatcher:
    """The Codex desktop app (and CLI threads migrated to SQLite history) no longer append
    rollout files, but the app-server logs one row per `account/rateLimits/updated` event into
    `$CODEX_HOME/logs_*.sqlite`. The row carries no numbers, but it tells us *when* usage just
    changed, so the usage API can be polled immediately instead of waiting for the next tick."""

    EVENT_PREFIX = "app-server event: account/rateLimits/updated"

    def __init__(self, codex_home: Path):
        self.codex_home = Path(codex_home)
        self._db_path: Optional[Path] = None
        self._conn = None
        self._last_id: Optional[int] = None
        self._last_open_attempt = 0.0

    def _find_db(self) -> Optional[Path]:
        try:
            cands = [p for p in self.codex_home.iterdir() if p.name.startswith("logs_") and p.suffix == ".sqlite"]
        except OSError:
            return None
        if not cands:
            return None
        return max(cands, key=lambda p: p.stat().st_mtime if p.exists() else 0)

    def _open(self, now: float) -> bool:
        if self._conn is not None:
            return True
        if now - self._last_open_attempt < 30:
            return False
        self._last_open_attempt = now
        path = self._find_db()
        if not path:
            return False
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5, check_same_thread=False)
            conn.execute("PRAGMA query_only = 1")
            row = conn.execute("SELECT max(id) FROM logs").fetchone()
        except Exception:
            return False
        self._conn, self._db_path = conn, path
        self._last_id = int(row[0]) if row and row[0] is not None else 0
        return True

    def poll(self) -> int:
        """Number of new rateLimits/updated events since the previous poll."""
        import time as _time
        now = _time.time()
        if not self._open(now):
            return 0
        assert self._conn is not None
        try:
            rows = self._conn.execute(
                "SELECT id FROM logs WHERE id > ? AND target = 'codex_app_server::outgoing_message' "
                "AND feedback_log_body LIKE ? ORDER BY id",
                (self._last_id, self.EVENT_PREFIX + "%"),
            ).fetchall()
            max_row = self._conn.execute("SELECT max(id) FROM logs").fetchone()
        except Exception:
            # database rotated or locked hard: reopen later
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            return 0
        if max_row and max_row[0] is not None:
            self._last_id = int(max_row[0])
        return len(rows)


# ---------------------------------------------------------------- per-account snapshot

@dataclass
class AccountState:
    name: str
    display_name: str
    active: bool
    is_main: bool
    auth_path: str
    ident: dict
    usage: Optional[dict] = None
    credits: Optional[dict] = None
    fetched_at: Optional[datetime] = None
    error: Optional[str] = None
    live: Optional[LiveEvent] = None
    live_extras: Dict[str, LiveEvent] = field(default_factory=dict)

    def shows_live(self) -> bool:
        if not self.live:
            return False
        if not self.fetched_at:
            return True
        if self.live.timestamp > self.fetched_at:
            return True
        # The usage endpoint can lag the per-response headers: prefer a recent live event that
        # reports *more* usage for the same window.
        if (self.fetched_at - self.live.timestamp).total_seconds() < 120 and self.usage:
            lp = self.live.primary
            ap = (self.usage.get("rate_limit") or {}).get("primary_window")
            if lp and ap and ap.get("reset_at") is not None and lp.get("reset_at") is not None:
                if abs(float(lp["reset_at"]) - float(ap["reset_at"])) < 60 and lp["used_percent"] > float(ap.get("used_percent", 0)):
                    return True
        return False

    def view(self, now: Optional[datetime] = None) -> dict:
        now = now or now_utc()
        live = self.shows_live()
        rate_limit = self.live.as_rate_limit() if live and self.live else (self.usage or {}).get("rate_limit")
        windows = windows_of(rate_limit, now)
        extras = []
        seen = set()
        for x in (self.usage or {}).get("additional_rate_limits") or []:
            rl = x.get("rate_limit")
            key = x.get("metered_feature")
            lx = self.live_extras.get(key) if key else None
            if lx and (not self.fetched_at or lx.timestamp > self.fetched_at):
                rl = lx.as_rate_limit()
            extras.append({"name": x.get("limit_name") or key or "other", "windows": windows_of(rl, now)})
            if key:
                seen.add(key)
        for key, event in self.live_extras.items():
            if key not in seen:
                extras.append({"name": event.limit_name or key, "windows": windows_of(event.as_rate_limit(), now)})
        reset_credits = None
        if self.credits and self.credits.get("available_count") is not None:
            reset_credits = self.credits["available_count"]
        elif self.usage and (self.usage.get("rate_limit_reset_credits") or {}).get("available_count") is not None:
            reset_credits = self.usage["rate_limit_reset_credits"]["available_count"]
        earliest = None
        for c in (self.credits or {}).get("credits") or []:
            if c.get("status", "available") == "available":
                e = parse_iso(c.get("expires_at"))
                if e and (earliest is None or e < earliest):
                    earliest = e
        plan = (self.usage or {}).get("plan_type") or (self.live.plan_type if self.live else None) or self.ident.get("plan")
        credit_info = (self.usage or {}).get("credits") or {}
        tight = min(windows, key=lambda w: w["remaining_percent"]) if windows else None
        return {
            "name": self.name,
            "display_name": self.display_name,
            "account_id": self.ident.get("account_id") or "",
            "email": self.ident.get("email") or "",
            "plan": plan or "",
            "plan_label": plan_label(plan),
            "active": self.active,
            "is_main": self.is_main,
            "auth_path": self.auth_path,
            "windows": windows,
            "extras": extras,
            "limit_reached": bool(rate_limit and (rate_limit.get("limit_reached") or rate_limit.get("allowed") is False)),
            "limit_reached_detail": reached_detail((self.usage or {}).get("rate_limit_reached_type")) if not live else None,
            "reset_credits": reset_credits,
            "reset_credits_earliest_expiry": earliest,
            "credits_balance": credit_info.get("balance"),
            "credits_unlimited": credit_info.get("unlimited") is True,
            "subscription_until": self.ident.get("subscription_until"),
            "subscription_checked": self.ident.get("subscription_checked"),
            "access_expires": self.ident.get("access_expires"),
            "access_expired": access_token_expired(self.ident),
            "last_refresh": self.ident.get("last_refresh"),
            "has_refresh_token": self.ident.get("has_refresh_token", False),
            "source": ("live" if live else "api") if (live or self.fetched_at) else None,
            "source_at": (self.live.timestamp if live and self.live else self.fetched_at),
            "stale": bool(self.error and self.usage),
            "error": self.error,
            "tightest_remaining": tight["remaining_percent"] if tight else None,
            "now": now,
        }


def format_view(v: dict) -> List[str]:
    """Human-readable lines for the CLI."""
    head = ("* " if v["active"] else "  ") + f"{v['name']}  {v['email'] or '?'}  {v['plan_label']}"
    lines = [head]
    if v["error"] and not v["windows"]:
        lines.append(f"    x {v['error']}")
    for w in v["windows"]:
        lines.append(f"    {w['label']:<7} {fmt_percent(w['remaining_percent']):>5} left   resets {fmt_local(w['reset_at'])} (in {w['reset_in']})")
    if v["limit_reached"]:
        lines.append("    x limit reached" + (f" ({v['limit_reached_detail']})" if v.get("limit_reached_detail") else ""))
    for x in v["extras"]:
        parts = [f"{w['label']} {fmt_percent(w['remaining_percent'])} left" for w in x["windows"]]
        if parts:
            lines.append(f"    {x['name']}: " + ", ".join(parts))
    rc = v["reset_credits"]
    rc_txt = "-" if rc is None else str(rc) + (f" (earliest expiry {fmt_local(v['reset_credits_earliest_expiry'], '%m-%d')})" if v["reset_credits_earliest_expiry"] else "")
    balance = "unlimited" if v.get("credits_unlimited") else v["credits_balance"]
    lines.append(f"    reset credits {rc_txt}" + (f"   credit balance {balance}" if balance is not None else ""))
    lines.append(f"    subscription until {fmt_local(v['subscription_until'])}   token valid until {fmt_local(v['access_expires'])}   last refresh {fmt_local(v['last_refresh'], '%Y-%m-%d %H:%M')}")
    if v["source"]:
        lines.append(f"    source: {v['source']} @ {fmt_local(v['source_at'], '%H:%M:%S')}" + ("  (stale, showing cached data; " + str(v["error"]) + ")" if v["stale"] else ""))
    return lines


# ---------------------------------------------------------------- cache

def cache_path(account_id: str) -> Path:
    return paths.cache_dir() / f"usage-{account_id}.json"


def save_cache(account_id: str, usage: dict, credits: Optional[dict], fetched_at: datetime) -> None:
    if not account_id:
        return
    try:
        from .store import atomic_write
        payload = {"fetchedAt": fetched_at.astimezone(timezone.utc).isoformat(), "usage": usage, "resetCredits": credits}
        atomic_write(cache_path(account_id), json.dumps(payload, indent=2).encode("utf-8"))
    except OSError:
        pass


def load_cache(account_id: str) -> Optional[Tuple[dict, Optional[dict], datetime]]:
    if not account_id:
        return None
    try:
        with open(cache_path(account_id), "r", encoding="utf-8") as f:
            d = json.load(f)
        d = _object(d)
        stamp = d.get("fetchedAt")
        ts = parse_iso(stamp) if isinstance(stamp, str) else None
        if not ts:
            return None
        validate_usage(d.get("usage"))
        if d.get("resetCredits") is not None:
            validate_reset_credits(d["resetCredits"])
        return d["usage"], d.get("resetCredits"), ts
    except (OSError, ValueError, UsageError):
        return None
