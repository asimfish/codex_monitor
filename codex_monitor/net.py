"""HTTP plumbing shared by the usage client and the OAuth flow.

urllib normally discovers proxies on every request; on macOS that goes through the `_scproxy`
C extension, which has been seen to stall for a long time when the process is started by
launchd. We resolve proxies once (environment first, then `scutil --proxy` on macOS, the
registry on Windows) and reuse one opener."""
from __future__ import annotations

import subprocess
import threading
import urllib.request
from typing import Dict, Optional

from . import paths

_lock = threading.RLock()  # opener() calls proxies() while holding it
_opener: Optional[urllib.request.OpenerDirector] = None
_proxies: Optional[Dict[str, str]] = None


def _mac_system_proxies() -> Dict[str, str]:
    try:
        out = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    kv: Dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    proxies: Dict[str, str] = {}
    if kv.get("HTTPEnable") == "1" and kv.get("HTTPProxy"):
        proxies["http"] = f"http://{kv['HTTPProxy']}:{kv.get('HTTPPort', '80')}"
    if kv.get("HTTPSEnable") == "1" and kv.get("HTTPSProxy"):
        proxies["https"] = f"http://{kv['HTTPSProxy']}:{kv.get('HTTPSPort', '443')}"
    elif "http" in proxies:
        proxies["https"] = proxies["http"]
    return proxies


def proxies() -> Dict[str, str]:
    global _proxies
    with _lock:
        if _proxies is None:
            env = urllib.request.getproxies_environment()
            if env:
                _proxies = env
            elif paths.IS_MAC:
                _proxies = _mac_system_proxies()
            else:
                try:
                    _proxies = urllib.request.getproxies()
                except Exception:
                    _proxies = {}
        return dict(_proxies)


def opener() -> urllib.request.OpenerDirector:
    global _opener
    with _lock:
        if _opener is None:
            _opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies()))
        return _opener


def urlopen(req, timeout: float = 20):
    return opener().open(req, timeout=timeout)
