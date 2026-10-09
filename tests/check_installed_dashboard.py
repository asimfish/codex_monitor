"""Start the installed CLI twice and verify HTTP + tag persistence in a temporary home."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main(command: str) -> None:
    with tempfile.TemporaryDirectory(prefix="codex-installed-check-") as directory:
        root = Path(directory)
        environment = dict(os.environ, CODEX_HOME=str(root / "codex"),
                           CODEX_ACCOUNTS_DIR=str(root / "accounts"), CODEX_MONITOR_DEMO="1")
        for attempt in range(2):
            with (root / ("server-%d.log" % attempt)).open("w", encoding="utf-8") as log:
                process = subprocess.Popen([command, "serve", "--no-browser", "--port", "0"],
                                           env=environment, stdout=log, stderr=log)
                try:
                    url = None
                    for _ in range(100):
                        lines = (root / ("server-%d.log" % attempt)).read_text(encoding="utf-8")
                        url = next((line.split(": ", 1)[1] for line in lines.splitlines()
                                    if line.startswith("Codex Monitor dashboard: ")), None)
                        if url:
                            break
                        if process.poll() is not None:
                            raise AssertionError("Installed dashboard exited: " + lines)
                        time.sleep(0.1)
                    assert url, "No dashboard URL within 10 seconds"
                    parsed = urllib.parse.urlsplit(url)
                    base = "%s://%s" % (parsed.scheme, parsed.netloc)
                    token = urllib.parse.parse_qs(parsed.query)["token"][0]

                    def request(path, body=None):
                        data = json.dumps(body).encode() if body is not None else None
                        req = urllib.request.Request(base + path, data=data,
                            headers={"X-Token": token, "Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=5) as response:
                            return response.read()

                    html = request("/").decode()
                    assert 'id="tagDialog"' in html and 'class="account-grid"' in html
                    state = json.loads(request("/api/state"))
                    assert len(state["accounts"]) == 4
                    bob = next(account for account in state["accounts"] if account["name"] == "bob")
                    if attempt == 0:
                        assert state["accounts"][0]["availability"] == "ready"
                        request("/api/annotations", {"name": "bob", "tags": ["Linux 持久化测试"], "unavailable": True})
                    else:
                        assert bob["tags"] == ["Linux 持久化测试"] and bob["manual_unavailable"]
                        assert state["accounts"][-1]["name"] == "bob"
                    try:
                        urllib.request.urlopen(base + "/api/state", timeout=5)
                    except urllib.error.HTTPError as error:
                        assert error.code == 403
                    else:
                        raise AssertionError("Anonymous API access succeeded")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    print("Installed CLI checks passed: dashboard HTTP, usable-first order, authenticated tags and restart persistence")


if __name__ == "__main__":
    main(sys.argv[1])
