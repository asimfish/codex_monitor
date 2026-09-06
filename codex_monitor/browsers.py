"""Opening URLs in a *private* browser window on macOS, Linux and Windows. A private window
matters for adding accounts: a normal window silently reuses the ChatGPT session that is
already logged in."""
from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple

from . import paths

# (display name, private-window flag, candidates)
_CHROMIUM_FLAG = "--incognito"
_BROWSERS = [
    ("Google Chrome", _CHROMIUM_FLAG),
    ("Microsoft Edge", "--inprivate"),
    ("Brave Browser", _CHROMIUM_FLAG),
    ("Chromium", _CHROMIUM_FLAG),
    ("Comet", _CHROMIUM_FLAG),
    ("Firefox", "--private-window"),
]

_LINUX_BINARIES = {
    "Google Chrome": ["google-chrome", "google-chrome-stable", "chrome"],
    "Microsoft Edge": ["microsoft-edge", "microsoft-edge-stable"],
    "Brave Browser": ["brave-browser", "brave"],
    "Chromium": ["chromium", "chromium-browser"],
    "Comet": ["comet"],
    "Firefox": ["firefox"],
}

_WINDOWS_PATHS = {
    "Google Chrome": [r"Google\Chrome\Application\chrome.exe"],
    "Microsoft Edge": [r"Microsoft\Edge\Application\msedge.exe"],
    "Brave Browser": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
    "Chromium": [r"Chromium\Application\chrome.exe"],
    "Comet": [r"Perplexity\Comet\Application\comet.exe"],
    "Firefox": [r"Mozilla Firefox\firefox.exe"],
}


def _windows_candidates(rel_paths: List[str]) -> List[Path]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LocalAppData")]
    out = []
    for root in roots:
        if not root:
            continue
        for rel in rel_paths:
            out.append(Path(root).joinpath(*rel.split("\\")))
    return out


def find_private_browser() -> Optional[Tuple[str, List[str]]]:
    """Returns (name, argv-prefix) for the first installed browser that supports a private window;
    append the URL to the argv."""
    for name, flag in _BROWSERS:
        if paths.IS_MAC:
            for root in ("/Applications", str(Path.home() / "Applications")):
                if Path(root, f"{name}.app").exists():
                    return name, ["/usr/bin/open", "-na", name, "--args", flag]
        elif paths.IS_WINDOWS:
            for exe in _windows_candidates(_WINDOWS_PATHS.get(name, [])):
                if exe.exists():
                    return name, [str(exe), flag]
            for b in _LINUX_BINARIES.get(name, []):
                found = shutil.which(b)
                if found:
                    return name, [found, flag]
        else:
            for b in _LINUX_BINARIES.get(name, []):
                found = shutil.which(b)
                if found:
                    return name, [found, flag]
    return None


def open_private(url: str) -> Optional[str]:
    """Open `url` in a private window. Returns the browser name used, or None if none found."""
    found = find_private_browser()
    if not found:
        return None
    name, argv = found
    try:
        subprocess.Popen(argv + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=not paths.IS_WINDOWS)
    except OSError:
        return None
    return name


def open_default(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def open_app_window(url: str) -> Optional[str]:
    """Open the dashboard as a chromeless 'app' window (Chromium browsers) so it looks like a
    widget; falls back to a normal tab."""
    for name, flag in _BROWSERS:
        if flag != _CHROMIUM_FLAG and name != "Microsoft Edge":
            continue
        found = None
        if paths.IS_MAC:
            for root in ("/Applications", str(Path.home() / "Applications")):
                if Path(root, f"{name}.app").exists():
                    found = ["/usr/bin/open", "-na", name, "--args"]
                    break
        elif paths.IS_WINDOWS:
            for exe in _windows_candidates(_WINDOWS_PATHS.get(name, [])):
                if exe.exists():
                    found = [str(exe)]
                    break
        else:
            for b in _LINUX_BINARIES.get(name, []):
                w = shutil.which(b)
                if w:
                    found = [w]
                    break
        if found:
            try:
                subprocess.Popen(found + [f"--app={url}", "--window-size=440,900"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=not paths.IS_WINDOWS)
                return name
            except OSError:
                continue
    return "default browser" if open_default(url) else None
