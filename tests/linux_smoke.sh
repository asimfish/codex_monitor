set -e
export HOME=/tmp/home; mkdir -p $HOME
cd /src
echo "== python: $(python --version)"
python tests/test_python.py 2>&1 | tail -n 3
python tests/test_cli.py
echo "== pip install (isolated copy) =="
cp -r /src /tmp/pkg && cd /tmp/pkg && pip install -q . 2>&1 | tail -n 2 || true
codex-monitor --version
codex-acct --version
echo "== list on empty machine =="
codex-monitor list
echo "== dashboard starts =="
(CODEX_MONITOR_DEMO=1 codex-monitor serve --no-browser --port 7899 > /tmp/serve.log 2>&1 &)
sleep 2; cat /tmp/serve.log | head -2
TOKEN=$(sed -n 's/.*token=\([A-Za-z0-9_-]*\).*/\1/p' /tmp/serve.log | head -1)
python - <<PY
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:7899/api/state", headers={"X-Token": "$TOKEN"})
s = json.load(urllib.request.urlopen(req, timeout=5))
print("dashboard accounts:", [a["name"] for a in s["accounts"]])
PY
echo "== autostart (no systemd here -> XDG autostart file) =="
codex-monitor autostart install
codex-monitor autostart status
cat $HOME/.config/autostart/codex-monitor.desktop
codex-monitor autostart remove
echo "== OK on $(python --version)"
