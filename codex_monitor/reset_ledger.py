"""Durable, account-scoped reset requests; unknown outcomes retain their UUID."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from .identity import format_iso, now_utc, parse_iso
from .locking import LockBusyError, file_lock
from .store import StoreError, atomic_write


def _request_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


class ResetOperation:
    def __init__(self, path: Path, record: dict, request_id: str):
        self.path, self.record, self.request_id = path, record, request_id
        self.result = record["results"].get(request_id)
        self.retrying = record["pending"] == request_id

    def save(self) -> None:
        atomic_write(self.path, json.dumps(self.record).encode("utf-8"), durable=True)

    def complete(self, result: dict) -> None:
        first_result = self.request_id not in self.record["results"]
        self.record["results"][self.request_id] = dict(result)
        while len(self.record["results"]) > 256:
            del self.record["results"][next(iter(self.record["results"]))]
        self.record["pending"] = None
        if first_result and result["code"] in ("reset", "already_redeemed"):
            self.record["last_reset_at"] = format_iso(now_utc())
        self.save()
        self.result = dict(result)

    def reject(self) -> None:
        self.record["pending"] = None
        self.save()


class ResetLedger:
    def __init__(self, root: Path):
        self.root = root / "_reset_requests"

    def _path(self, account: str) -> Path:
        return self.root / (hashlib.sha256(account.encode("utf-8")).hexdigest() + ".json")

    def _read(self, account: str) -> dict:
        try:
            record = json.loads(self._path(account).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "pending": None, "results": {}, "last_reset_at": None}
        except (OSError, ValueError) as error:
            raise StoreError("Cannot read reset recovery record; existing operations have been preserved") from error
        valid = (isinstance(record, dict) and record.get("version") == 1
                 and "pending" in record and "last_reset_at" in record
                 and (record.get("pending") is None or _request_id(record["pending"]))
                 and isinstance(record.get("results"), dict)
                 and (record.get("last_reset_at") is None or
                      isinstance(record["last_reset_at"], str) and parse_iso(record["last_reset_at"]) is not None))
        if valid:
            for request_id, result in record["results"].items():
                if (not _request_id(request_id) or not isinstance(result, dict)
                        or result.get("code") not in ("reset", "already_redeemed", "nothing_to_reset", "no_credit")
                        or type(result.get("windows_reset")) is not int or result["windows_reset"] < 0):
                    valid = False
                    break
        if not valid:
            raise StoreError("Invalid reset recovery record; existing operations have been preserved")
        return record

    def status(self, account: str) -> dict:
        record = self._read(account)
        return {"pending": record["pending"], "last_reset_at": parse_iso(record.get("last_reset_at"))}

    @contextmanager
    def operation(self, account: str, request_id: str):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(account)
        try:
            with file_lock(path.with_suffix(".lock"), blocking=False):
                record = self._read(account)
                operation = ResetOperation(path, record, request_id)
                if operation.result is None:
                    if record["pending"] is not None and record["pending"] != request_id:
                        raise StoreError("the previous reset outcome is unknown; retry the previous request first")
                    record["pending"] = request_id
                    operation.save()  # Flush the UUID before a request can reach the provider.
                yield operation
        except LockBusyError as error:
            raise StoreError("a reset-card request is already in progress for this account") from error
