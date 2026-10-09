"""Reentrant local and cross-process locks for credential and operation files."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(Exception):
    pass


_guard = threading.Lock()
_locks = {}
_local = threading.local()


def _open_lock(path: Path):
    if os.name != "nt":
        return path.open("a+b")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    # FILE_SHARE_DELETE lets an account move to its backup while this lock is held.
    # The byte-range lock below still serializes competing writers.
    read_write, share_read_write_delete = 0xC0000000, 0x7
    open_always, normal = 4, 0x80
    handle = create_file(str(path), read_write, share_read_write_delete, None, open_always, normal, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_APPEND | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes, close_handle.restype = [wintypes.HANDLE], wintypes.BOOL
        close_handle(handle)
        raise
    try:
        return os.fdopen(fd, "a+b")
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def file_lock(path: Path, blocking: bool = True):
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
        with _open_lock(path) as handle:
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
