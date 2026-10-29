"""Independent API organization costs; grant amounts remain user-supplied."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from . import net, paths, store
from .identity import format_iso, now_utc, parse_iso
from .locking import file_lock


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def amount(value):
    if isinstance(value, bool):
        raise store.StoreError("Invalid USD amount")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise store.StoreError("Invalid USD amount") from None
    if not number.is_finite() or abs(number) > Decimal("1000000000"):
        raise store.StoreError("Invalid USD amount")
    return number


def reporting_period(item, now):
    if item.get("period_mode", "custom") == "calendar_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, (start + timedelta(days=32)).replace(day=1)
    return parse_iso(item["period_start"]), parse_iso(item["period_end"])


def fetch_costs(key, organization, start, end):
    """Fetch the complete bounded interval. Never expose upstream bodies/secrets."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(net.proxies()), NoRedirect())
    query = {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()),
             "bucket_width": "1d", "limit": 180}
    total, days, cursors, buckets = Decimal(0), [], set(), set()
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if organization:
        headers["OpenAI-Organization"] = organization
    observed_org = organization
    for _ in range(4):
        request = urllib.request.Request(
            "https://api.openai.com/v1/organization/costs?" + urllib.parse.urlencode(query),
            headers=headers)
        try:
            with opener.open(request, timeout=20) as response:
                response_org = getattr(response, "headers", {}).get("openai-organization")
                if response_org:
                    if not isinstance(response_org, str) or not re.fullmatch(r"org-[A-Za-z0-9_-]{1,120}", response_org) or (observed_org and observed_org != response_org):
                        raise store.StoreError("Cost response organization does not match; nothing was saved")
                    observed_org = response_org
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise store.StoreError("Cost response exceeds the supported size")
                payload = json.loads(raw, parse_float=Decimal)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise store.StoreError("Admin API key is invalid or expired. Create a new key in official Admin keys settings.") from None
            if error.code == 403:
                raise store.StoreError("Costs access denied (403). Use an organization owner's Admin API key and check the organization ID.") from None
            if error.code == 429:
                raise store.StoreError("Cost query rate limited; wait for the next refresh.") from None
            raise store.StoreError("Cost query failed (HTTP %d)." % error.code) from None
        except (OSError, ValueError) as error:
            raise store.StoreError("Could not read organization costs; previous data is retained.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or type(payload.get("has_more")) is not bool:
            raise store.StoreError("Invalid organization cost response")
        for bucket in payload["data"]:
            if not isinstance(bucket, dict) or type(bucket.get("start_time")) is not int or not isinstance(bucket.get("results"), list):
                raise store.StoreError("Invalid cost bucket")
            stamp = bucket["start_time"]
            if stamp in buckets:
                raise store.StoreError("Repeated cost bucket; incomplete data was discarded")
            buckets.add(stamp)
            daily = Decimal(0)
            for row in bucket["results"]:
                if not isinstance(row, dict) or not isinstance(row.get("amount"), dict):
                    raise store.StoreError("Invalid cost amount")
                value = row["amount"]
                if value.get("currency") != "usd" or "value" not in value:
                    raise store.StoreError("Cost response is not denominated in USD")
                daily += amount(value["value"])
            total += daily
            days.append({"start_time": stamp, "cost_usd": str(daily)})
        if not payload["has_more"]:
            return {"cost_usd": str(total), "days": sorted(days, key=lambda day: day["start_time"]),
                    "queried_until": format_iso(end), "organization": observed_org}
        cursor = payload.get("next_page")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in cursors:
            raise store.StoreError("Invalid cost pagination; incomplete data was discarded")
        cursors.add(cursor)
        query["page"] = cursor
    raise store.StoreError("Cost query exceeded the pagination limit")


class ApiCredits:
    def __init__(self):
        self.root = paths.accounts_dir() / "_api_accounts"
        self.path = self.root / "credentials.json"
        self.lock_path = self.root / ".lock"
        self._refresh_lock = threading.Lock()

    def _load(self):
        if self.root.is_symlink() or self.path.is_symlink():
            raise store.StoreError("API account storage must not be a symbolic link")
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or not isinstance(payload.get("accounts"), dict):
                raise ValueError()
            for identifier, item in payload["accounts"].items():
                required = {"id", "label", "organization", "grant_total", "period_start", "period_end", "refresh_interval", "admin_key", "revision", "last_attempt", "last_success", "error", "costs"}
                if not isinstance(item, dict) or not required.issubset(item) or identifier != item["id"]:
                    raise ValueError()
            return payload["accounts"]
        except (OSError, ValueError, AttributeError):
            raise store.StoreError("API account data cannot be read; the existing file was preserved") from None

    def _save(self, accounts):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        data = json.dumps({"version": 1, "accounts": accounts}, ensure_ascii=False, allow_nan=False).encode()
        store.atomic_write(self.path, data)

    def _prepare(self):
        if self.root.is_symlink() or self.path.is_symlink():
            raise store.StoreError("API account storage must not be a symbolic link")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def save(self, body, verify=False):
        if not isinstance(body, dict):
            raise store.StoreError("Invalid API account")
        label = body.get("label", "")
        if isinstance(label, str) and not label.strip():
            label = "OpenAI API"
        organization = body.get("organization", "")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(ord(c) < 32 for c in label):
            raise store.StoreError("Enter an account label (1-80 characters)")
        key = body.get("admin_key", "")
        if not isinstance(key, str) or (key and not re.fullmatch(r"sk-admin-[A-Za-z0-9_-]{8,512}", key)):
            raise store.StoreError("Costs require an Admin API key (sk-admin-...), not a project/model key. Leave blank for a manual record.")
        if not isinstance(organization, str) or (not organization and not (verify and key)) or (organization and not re.fullmatch(r"org-[A-Za-z0-9_-]{1,120}", organization)):
            raise store.StoreError("Enter the API organization ID (org-...)")
        mode = body.get("period_mode", "custom" if "period_start" in body or "period_end" in body else "calendar_month")
        if mode not in ("calendar_month", "custom"):
            raise store.StoreError("Invalid cost reporting period")
        if mode == "custom" and not all(isinstance(body.get(field), str) for field in ("period_start", "period_end")):
            raise store.StoreError("Enter the grant start and expiry dates")
        now = now_utc()
        start, end = reporting_period({**body, "period_mode": mode}, now)
        if start is None or end is None or not timedelta(0) < end - start <= timedelta(days=370):
            raise store.StoreError("Enter a grant period with start before expiry (up to 370 days)")
        total = None if body.get("grant_total") in (None, "") else amount(body["grant_total"])
        if total is not None and total < 0:
            raise store.StoreError("Grant total cannot be negative")
        if total is not None and mode == "calendar_month":
            raise store.StoreError("Enter the actual grant dates before estimating a balance")
        interval = body.get("refresh_interval", 300)
        if type(interval) is not int or not 60 <= interval <= 3600:
            raise store.StoreError("Refresh interval must be 60-3600 seconds")
        identifier = body.get("id")
        before = self._load()
        if identifier is not None and (not isinstance(identifier, str) or identifier not in before):
            raise store.StoreError("API account changed; reload before editing")
        result = None
        if verify and key:
            if identifier is None and len(before) >= 100:
                raise store.StoreError("At most 100 API organizations may be tracked")
            probe_start = start if start < now else now - timedelta(days=1)
            result = fetch_costs(key, organization, probe_start, min(now, end) if start < now else now)
            organization = result.get("organization") or organization
            if not organization:
                raise store.StoreError("Key verified, but the organization ID was not returned. Enter org-... under Advanced and retry.")
        self._prepare()
        with file_lock(self.lock_path):
            accounts = self._load()
            if identifier is not None and (not isinstance(identifier, str) or identifier not in accounts):
                raise store.StoreError("API account changed; reload before editing")
            if any(item["organization"] == organization and name != identifier for name, item in accounts.items()):
                raise store.StoreError("This API organization is already tracked; edit the existing account")
            if identifier is None and len(accounts) >= 100:
                raise store.StoreError("At most 100 API organizations may be tracked")
            identifier = identifier or uuid.uuid4().hex
            previous = accounts.get(identifier, {})
            if verify and key and previous.get("revision") != before.get(identifier, {}).get("revision"):
                raise store.StoreError("API account changed during verification; reload before editing")
            item = {"id": identifier, "label": label.strip(), "organization": organization,
                    "grant_total": str(total) if total is not None else None,
                    "period_start": format_iso(start), "period_end": format_iso(end), "period_mode": mode,
                    "refresh_interval": interval, "admin_key": key or previous.get("admin_key", ""),
                    "revision": uuid.uuid4().hex, "last_attempt": None, "last_success": None,
                    "error": None, "costs": None}
            if (previous.get("organization"), previous.get("period_start"), previous.get("period_end")) == (item["organization"], item["period_start"], item["period_end"]) and not key:
                for field in ("last_attempt", "last_success", "error", "costs"):
                    item[field] = previous.get(field)
            if result is not None and start < now:
                item.update(costs=result, last_attempt=format_iso(now), last_success=format_iso(now_utc()))
            accounts[identifier] = item
            self._save(accounts)
        return identifier

    def remove(self, identifier):
        self._prepare()
        with file_lock(self.lock_path):
            accounts = self._load()
            if not isinstance(identifier, str) or identifier not in accounts:
                raise store.StoreError("Unknown API account")
            del accounts[identifier]
            self._save(accounts)

    def refresh(self, identifier=None, force=False):
        if not self._refresh_lock.acquire(blocking=False):
            raise store.StoreError("An API cost refresh is already running")
        try:
            accounts = self._load()
            if identifier is not None and (not isinstance(identifier, str) or identifier not in accounts):
                raise store.StoreError("Unknown API account")
            for name, saved in accounts.items():
                if identifier is not None and name != identifier:
                    continue
                if not saved["admin_key"]:
                    continue
                now = now_utc()
                start, expiry = reporting_period(saved, now)
                if start >= now:
                    continue
                last = parse_iso(saved["last_attempt"])
                wait = 15 if force else saved["refresh_interval"]
                if last and (now - last).total_seconds() < wait:
                    continue
                revision = saved["revision"]
                with file_lock(self.lock_path):
                    current = self._load()
                    if current.get(name, {}).get("revision") != revision:
                        continue
                    recent = parse_iso(current[name]["last_attempt"])
                    if recent and (now - recent).total_seconds() < wait:
                        continue
                    current[name]["last_attempt"] = format_iso(now)
                    if current[name]["period_start"] != format_iso(start):
                        current[name].update(period_start=format_iso(start), period_end=format_iso(expiry), costs=None, last_success=None, error=None)
                    self._save(current)
                result, error = None, None
                try:
                    result = fetch_costs(saved["admin_key"], saved["organization"], start, min(now, expiry))
                except store.StoreError as failure:
                    error = str(failure)
                with file_lock(self.lock_path):
                    current = self._load()
                    if current.get(name, {}).get("revision") != revision or current[name]["last_attempt"] != format_iso(now):
                        continue
                    current[name]["error"] = error
                    if result is not None:
                        current[name]["costs"] = result
                        current[name]["last_success"] = format_iso(now_utc())
                    self._save(current)
        finally:
            self._refresh_lock.release()

    def snapshot(self):
        now = now_utc()
        result = []
        for saved in self._load().values():
            item = {field: saved[field] for field in ("id", "label", "organization", "grant_total", "period_start", "period_end", "refresh_interval", "last_attempt", "last_success", "error", "costs")}
            item["period_mode"] = saved.get("period_mode", "custom")
            start, expiry = reporting_period(saved, now)
            if item["period_start"] != format_iso(start):
                item.update(period_start=format_iso(start), period_end=format_iso(expiry), costs=None, last_success=None, error=None)
            item["has_admin_key"] = bool(saved["admin_key"])
            success = parse_iso(item["last_success"])
            item["stale"] = not success or bool(saved["error"]) or (now - success).total_seconds() > saved["refresh_interval"] * 2
            item["expired"] = item["period_mode"] == "custom" and expiry <= now
            item["starts_later"] = start > now
            last = parse_iso(saved["last_attempt"])
            item["next_refresh"] = format_iso(max(now, last + timedelta(seconds=saved["refresh_interval"]))) if last and item["has_admin_key"] else None
            item["estimated_remaining"] = None
            if item["grant_total"] is not None and item["costs"] is not None and not item["expired"]:
                item["estimated_remaining"] = str(max(Decimal(0), amount(item["grant_total"]) - amount(item["costs"]["cost_usd"])))
            result.append(item)
        return {"accounts": sorted(result, key=lambda item: (item["expired"], bool(item["error"]), not item["has_admin_key"], item["last_success"] is None, item["label"].casefold())), "now": format_iso(now)}
