"""Reentrant local and cross-process locks for credential and operation files."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockBusyError(Exception):
    pass


_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def file_lock(path: Path, blocking: bool = True) -> Iterator[None]:
    key = str(path.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(blocking=blocking):
        raise LockBusyError("operation is already in progress")
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = set()
    try:
        if key in held:
            yield
            return
        with path.open("a+b") as handle:
            os.chmod(path, 0o600)
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                acquire = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                release = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            try:
                acquire()
            except OSError as error:
                raise LockBusyError("operation is already in progress") from error
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                if os.name == "nt":
                    handle.seek(0)
                release()
    finally:
        lock.release()
