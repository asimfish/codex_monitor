"""Local account labels, kept separately from login credentials."""
from __future__ import annotations

import json
import threading
import unicodedata
from pathlib import Path

from .store import StoreError, atomic_write


class Annotations:
    def __init__(self, root: Path):
        self.path = root / "_annotations.json"
        self.lock = threading.RLock()

    def read(self) -> dict:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError) as error:
                raise StoreError("Cannot read account tags: " + str(error)) from error
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
                raise StoreError("Invalid account tags file; existing labels have been preserved")
            for value in data["accounts"].values():
                if not isinstance(value, dict):
                    raise StoreError("Invalid account tags record")
                self.validate(value.get("tags"), value.get("unavailable"))
            return data["accounts"]

    @staticmethod
    def validate(tags: object, unavailable: object) -> dict:
        if not isinstance(tags, list) or len(tags) > 8 or type(unavailable) is not bool:
            raise StoreError("Use up to 8 tags and a boolean unavailable flag")
        cleaned = []
        for tag in tags:
            if not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 30:
                raise StoreError("Each tag must contain 1–30 characters")
            text = tag.strip()
            if any(unicodedata.category(char) in ("Cc", "Cs") for char in text):
                raise StoreError("Tags cannot contain control characters")
            if text not in cleaned:
                cleaned.append(text)
        return {"tags": cleaned, "unavailable": unavailable}

    def save(self, key: str, tags: object, unavailable: object) -> dict:
        value = self.validate(tags, unavailable)
        with self.lock:
            accounts = self.read()
            accounts[key] = value
            payload = {"version": 1, "accounts": accounts}
            atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        return value


def availability(account: dict, has_tokens: bool) -> str:
    if account["manual_unavailable"]:
        return "manual"
    if not has_tokens:
        return "signed_out"
    if account["access_expired"]:
        return "expired"
    if account["error"]:
        return "error"
    if account["limit_reached"] or account["tightest_remaining"] == 0:
        return "limited"
    if account["tightest_remaining"] is not None and account["tightest_remaining"] > 0:
        return "ready"
    return "checking"


def account_order(account: dict) -> tuple:
    priorities = {"ready": 0, "checking": 1, "limited": 2, "expired": 3, "error": 3, "signed_out": 3, "manual": 4}
    remaining = account["tightest_remaining"]
    return (priorities[account["availability"]], not account["active"],
            -(remaining if remaining is not None else -1), account["name"].casefold())
