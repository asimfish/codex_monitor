"""JWT / timestamp helpers and the identity we derive from an auth.json."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def jwt_claims(token: Optional[str]) -> Dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Accepts 2026-09-05T07:52:32.639755Z, 2026-09-06T12:52:19+00:00, ..."""
    if not s:
        return None
    t = s.strip().replace("Z", "+00:00")
    if "." in t:
        head, tail = t.split(".", 1)
        digits = ""
        rest = tail
        while rest and rest[0].isdigit():
            digits += rest[0]
            rest = rest[1:]
        t = head + "." + (digits[:6].ljust(6, "0") if digits else "000000") + rest
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_iso(dt: datetime) -> str:
    """Codex-style timestamp: 2026-09-05T07:52:32.639755Z"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def epoch(v: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def identity(auth: Optional[dict]) -> dict:
    tokens = (auth or {}).get("tokens") or {}
    idc = jwt_claims(tokens.get("id_token"))
    acc = jwt_claims(tokens.get("access_token"))
    a_id = idc.get("https://api.openai.com/auth") or {}
    a_acc = acc.get("https://api.openai.com/auth") or {}
    prof = acc.get("https://api.openai.com/profile") or {}
    return {
        "email": idc.get("email") or prof.get("email") or "",
        "plan": a_id.get("chatgpt_plan_type") or a_acc.get("chatgpt_plan_type") or "",
        "account_id": tokens.get("account_id") or a_id.get("chatgpt_account_id") or a_acc.get("chatgpt_account_id") or "",
        "subscription_until": parse_iso(a_id.get("chatgpt_subscription_active_until")),
        "subscription_checked": parse_iso(a_id.get("chatgpt_subscription_last_checked")),
        "access_expires": epoch(acc.get("exp")) if acc.get("exp") is not None else None,
        "last_refresh": parse_iso((auth or {}).get("last_refresh")),
        "auth_mode": (auth or {}).get("auth_mode") or ("apikey" if (auth or {}).get("OPENAI_API_KEY") else ""),
        "has_tokens": bool(tokens.get("access_token")),
        "has_refresh_token": bool(tokens.get("refresh_token")),
    }


def access_token_expired(ident: dict) -> bool:
    exp = ident.get("access_expires")
    return bool(exp and exp < now_utc())


PLAN_LABELS = {
    "pro": "Pro", "plus": "Plus", "free": "Free", "team": "Team", "business": "Business",
    "enterprise": "Enterprise", "edu": "Edu", "go": "Go",
}


def plan_label(plan: Optional[str]) -> str:
    if not plan:
        return "-"
    return PLAN_LABELS.get(plan.lower(), plan.capitalize())


def window_label(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "quota"
    if seconds == 18000:
        return "5h"
    if seconds == 86400:
        return "daily"
    if seconds == 604800:
        return "weekly"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def fmt_local(dt: Optional[datetime], fmt: str = "%m-%d %H:%M") -> str:
    return dt.astimezone().strftime(fmt) if dt else "-"


def fmt_countdown(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not dt:
        return ""
    s = int((dt - (now or now_utc())).total_seconds())
    if s <= 0:
        return "now"
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    return f"{max(m, 1)}m"


def fmt_percent(v: float) -> str:
    return f"{int(v)}%" if float(v).is_integer() else f"{v:.1f}%"


def to_json_value(v: Any) -> Any:
    """datetimes -> ISO strings, recursively, for JSON output."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_json_value(x) for x in v]
    return v
