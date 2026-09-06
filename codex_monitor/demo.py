"""`CODEX_MONITOR_DEMO=1`: fabricated accounts for screenshots and UI work. No network, no files."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .identity import format_iso, identity
from .usage import AccountState, LiveEvent


def enabled() -> bool:
    return os.environ.get("CODEX_MONITOR_DEMO") == "1"


def _jwt(claims: dict) -> str:
    def b64(o: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'RS256'})}.{b64(claims)}.demo"


def _auth(email: str, plan: str, account_id: str, sub_until, token_exp: datetime, last_refresh: datetime) -> dict:
    auth_claims = {"chatgpt_account_id": account_id, "chatgpt_plan_type": plan}
    if sub_until:
        auth_claims["chatgpt_subscription_active_until"] = format_iso(sub_until)
    return {
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": _jwt({"email": email, "exp": int(token_exp.timestamp()), "https://api.openai.com/auth": auth_claims}),
            "access_token": _jwt({"exp": int(token_exp.timestamp()), "iat": int(last_refresh.timestamp()),
                                  "https://api.openai.com/auth": auth_claims, "https://api.openai.com/profile": {"email": email}}),
            "refresh_token": "rt.demo", "account_id": account_id,
        },
        "last_refresh": format_iso(last_refresh),
    }


def _window(used: float, seconds: int, reset_in: float, now: datetime) -> dict:
    return {"used_percent": used, "limit_window_seconds": seconds, "reset_after_seconds": int(reset_in),
            "reset_at": (now + timedelta(seconds=reset_in)).timestamp()}


def states(now: datetime = None) -> List[AccountState]:  # type: ignore[assignment]
    now = now or datetime.now(timezone.utc)
    d, h = 86400, 3600

    def make(name, email, plan, acct, sub_until, token_exp, last_refresh, active, usage, credits, fetched, error=None, live=None):
        a = _auth(email, plan, acct, sub_until, token_exp, last_refresh)
        st = AccountState(name=name, display_name=email, active=active, is_main=False,
                          auth_path=f"~/.codex-accounts/{name}/auth.json", ident=identity(a),
                          usage=usage, credits=credits, fetched_at=fetched, error=error, live=live)
        return st

    alice = make("alice", "alice@example.com", "pro", "acct-alice", now + timedelta(days=21), now + timedelta(days=8), now - timedelta(days=2), True,
                 {"plan_type": "pro", "rate_limit": {"allowed": True, "limit_reached": False, "primary_window": _window(38, 604800, 3 * d + 5 * h, now), "secondary_window": None},
                  "additional_rate_limits": [{"limit_name": "GPT-5.3-Codex-Spark", "metered_feature": "codex_spark",
                                              "rate_limit": {"primary_window": _window(12, 18000, 2 * h, now), "secondary_window": _window(4, 604800, 5 * d, now)}}],
                  "credits": {"has_credits": False}, "rate_limit_reached_type": None, "rate_limit_reset_credits": {"available_count": 2}},
                 {"available_count": 2, "credits": [{"status": "available", "expires_at": format_iso(now + timedelta(days=25))}]},
                 now - timedelta(seconds=70),
                 live=LiveEvent(timestamp=now - timedelta(seconds=40), limit_id="codex", limit_name=None,
                                primary={"used_percent": 39.0, "limit_window_seconds": 604800, "reset_at": (now + timedelta(seconds=3 * d + 5 * h)).timestamp()},
                                secondary=None, plan_type="pro", limit_reached=False))
    bob = make("bob", "bob@example.com", "plus", "acct-bob", now + timedelta(days=12), now + timedelta(days=6), now - timedelta(days=4), False,
               {"plan_type": "plus", "rate_limit": {"allowed": True, "limit_reached": False, "primary_window": _window(12, 18000, 3 * h + 20 * 60, now), "secondary_window": _window(65, 604800, 2 * d + 9 * h, now)},
                "additional_rate_limits": [], "rate_limit_reset_credits": {"available_count": 1}}, None, now - timedelta(seconds=20))
    carol = make("carol", "carol@example.com", "free", "acct-carol", None, now + timedelta(days=9), now - timedelta(days=1), False,
                 {"plan_type": "free", "rate_limit": {"allowed": False, "limit_reached": True, "primary_window": _window(100, 2592000, 26 * d, now), "secondary_window": None},
                  "additional_rate_limits": [], "rate_limit_reached_type": {"type": "rate_limit_reached", "details": "default"}, "rate_limit_reset_credits": {"available_count": 1}},
                 None, now - timedelta(seconds=20))
    dave = make("dave", "dave@example.com", "pro", "acct-dave", now + timedelta(days=3), now + timedelta(days=5), now - timedelta(days=5), False,
                {"plan_type": "pro", "rate_limit": {"allowed": True, "limit_reached": False, "primary_window": _window(71, 604800, d + 2 * h, now), "secondary_window": None},
                 "additional_rate_limits": [], "rate_limit_reset_credits": {"available_count": 0}}, None, now - timedelta(hours=6),
                error="token rejected (401): token_expired")
    return [alice, bob, carol, dave]
