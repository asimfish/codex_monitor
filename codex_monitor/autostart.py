"""Start the dashboard at login: LaunchAgent (macOS), systemd user unit or XDG autostart
(Linux), Task Scheduler (Windows)."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import paths

LABEL = "com.codexmonitor.dashboard"
TASK_NAME = "CodexMonitorDashboard"


def _python() -> str:
    exe = sys.executable
    if paths.IS_WINDOWS:
        # pythonw.exe keeps the console window from popping up at login.
        w = Path(exe).with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return exe


def _entry() -> List[str]:
    """`python -m codex_monitor` when pip-installed; the checkout's bin/codex-monitor otherwise
    (so autostart also works for people who just cloned the repo)."""
    pkg_dir = Path(__file__).resolve().parent
    checkout_script = pkg_dir.parent / "bin" / "codex-monitor"
    if checkout_script.exists() and "site-packages" not in str(pkg_dir) and "dist-packages" not in str(pkg_dir):
        return [_python(), str(checkout_script)]
    return [_python(), "-m", "codex_monitor"]


def _command(extra: Optional[List[str]] = None) -> List[str]:
    return _entry() + ["serve", "--no-browser"] + (extra or [])


def _log_dir() -> Path:
    if paths.IS_MAC:
        d = Path.home() / "Library" / "Logs" / "CodexMonitor"
    elif paths.IS_WINDOWS:
        d = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexMonitor" / "logs"
    else:
        d = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "codex-monitor"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- macOS

def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_install(extra: List[str]) -> str:
    plist = _mac_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    logs = _log_dir()
    data = {
        "Label": LABEL,
        "ProgramArguments": _command(extra),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Standard",  # "Background" gets starved on busy machines and delays startup by 20s+
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(logs / "dashboard.stdout.log"),
        "StandardErrorPath": str(logs / "dashboard.stderr.log"),
        "EnvironmentVariables": {k: v for k, v in os.environ.items() if k in ("CODEX_HOME", "CODEX_ACCOUNTS_DIR", "PATH")},
    }
    plist.write_bytes(plistlib.dumps(data))
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
    if r.returncode != 0:
        return f"wrote {plist} but launchctl bootstrap failed: {r.stderr.strip()}"
    return f"LaunchAgent installed and started: {plist}"


def _mac_remove() -> str:
    plist = _mac_plist()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if plist.exists():
        plist.unlink()
        return f"removed {plist}"
    return "nothing to remove"


def _mac_status() -> str:
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        return "not installed"
    for line in r.stdout.splitlines():
        if "state =" in line:
            return f"installed, {line.strip()}"
    return "installed"


# ---------------------------------------------------------------- Linux

def _systemd_unit() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user" / "codex-monitor.service"


def _xdg_desktop() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart" / "codex-monitor.desktop"


def _has_systemd_user() -> bool:
    if not shutil.which("systemctl"):
        return False
    r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True)
    return r.returncode == 0 or "running" in r.stdout or "degraded" in r.stdout


def _linux_install(extra: List[str]) -> str:
    cmd = " ".join(_quote(c) for c in _command(extra))
    if _has_systemd_user():
        unit = _systemd_unit()
        unit.parent.mkdir(parents=True, exist_ok=True)
        env_lines = "".join(f"Environment={k}={v}\n" for k, v in os.environ.items() if k in ("CODEX_HOME", "CODEX_ACCOUNTS_DIR"))
        unit.write_text(
            "[Unit]\nDescription=Codex Monitor dashboard\nAfter=network-online.target\n\n"
            f"[Service]\nType=simple\nExecStart={cmd}\nRestart=on-failure\nRestartSec=5\n{env_lines}\n"
            "[Install]\nWantedBy=default.target\n", encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        r = subprocess.run(["systemctl", "--user", "enable", "--now", "codex-monitor.service"], capture_output=True, text=True)
        if r.returncode != 0:
            return f"wrote {unit} but systemctl enable failed: {r.stderr.strip()}"
        return f"systemd user service installed and started: {unit}"
    desktop = _xdg_desktop()
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Codex Monitor\n"
        f"Exec={cmd}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n", encoding="utf-8")
    return f"XDG autostart entry written: {desktop} (starts at your next desktop login)"


def _linux_remove() -> str:
    msgs = []
    unit = _systemd_unit()
    if unit.exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", "codex-monitor.service"], capture_output=True)
        unit.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        msgs.append(f"removed {unit}")
    desktop = _xdg_desktop()
    if desktop.exists():
        desktop.unlink()
        msgs.append(f"removed {desktop}")
    return "; ".join(msgs) or "nothing to remove"


def _linux_status() -> str:
    if _systemd_unit().exists():
        r = subprocess.run(["systemctl", "--user", "is-active", "codex-monitor.service"], capture_output=True, text=True)
        return f"systemd user service: {r.stdout.strip() or 'unknown'}"
    if _xdg_desktop().exists():
        return f"XDG autostart entry present: {_xdg_desktop()}"
    return "not installed"


# ---------------------------------------------------------------- Windows

def _quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "\t" in s) else s


def _win_install(extra: List[str]) -> str:
    cmd = " ".join(_quote(c) for c in _command(extra))
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", TASK_NAME, "/TR", cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return f"schtasks failed: {(r.stderr or r.stdout).strip()}"
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True)
    return f"scheduled task '{TASK_NAME}' created (runs at logon) and started"


def _win_remove() -> str:
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True)
    return f"removed task '{TASK_NAME}'" if r.returncode == 0 else "nothing to remove"


def _win_status() -> str:
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True)
    return "installed (Task Scheduler)" if r.returncode == 0 else "not installed"


# ---------------------------------------------------------------- dispatch

def install(extra: Optional[List[str]] = None) -> str:
    extra = extra or []
    if paths.IS_MAC:
        return _mac_install(extra)
    if paths.IS_WINDOWS:
        return _win_install(extra)
    return _linux_install(extra)


def remove() -> str:
    if paths.IS_MAC:
        return _mac_remove()
    if paths.IS_WINDOWS:
        return _win_remove()
    return _linux_remove()


def status() -> str:
    if paths.IS_MAC:
        return _mac_status()
    if paths.IS_WINDOWS:
        return _win_status()
    return _linux_status()
