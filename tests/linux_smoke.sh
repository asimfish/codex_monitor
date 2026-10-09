#!/usr/bin/env bash
# Container acceptance checks. All account/config data stays in a temporary directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
task_dir="$(mktemp -d)"
trap 'rm -rf "$task_dir"' EXIT
export CODEX_HOME="$task_dir/codex"
export CODEX_ACCOUNTS_DIR="$task_dir/accounts"
export XDG_CONFIG_HOME="$task_dir/config"
export XDG_STATE_HOME="$task_dir/state"
export XDG_DATA_HOME="$task_dir/data"
export CODEX_MONITOR_TOKEN_FILE="$task_dir/data/dashboard.token"
export CODEX_MONITOR_INSTALL_DIR="$task_dir/install"
export CODEX_MONITOR_BIN_DIR="$task_dir/bin"
# Force the XDG fallback without contacting the host user's service manager.
mkdir -p "$task_dir/stubs"
cat > "$task_dir/stubs/systemctl" <<'SYSTEMCTL'
#!/bin/sh
exit 1
SYSTEMCTL
chmod +x "$task_dir/stubs/systemctl"
export PATH="$task_dir/stubs:$PATH"
python_bin="$(command -v "${PYTHON:-python3}")"
# The installer invokes python3; use the same selected interpreter throughout.
ln -s "$python_bin" "$task_dir/stubs/python3"
"$python_bin" --version
"$python_bin" tests/test_python.py
"$python_bin" tests/test_cli.py
"$python_bin" tests/test_reset_credits.py
"$python_bin" tests/test_web_regressions.py
"$python_bin" tests/test_recovery.py
"$python_bin" tests/test_dashboard.py
"$python_bin" tests/test_removal.py
"$python_bin" tests/test_rename.py
bash scripts/install-linux.sh
test -L "$task_dir/bin/codex-monitor"
"$task_dir/bin/codex-monitor" --version
"$task_dir/bin/codex-acct" --version
"$python_bin" tests/check_installed_dashboard.py "$task_dir/bin/codex-monitor"
"$task_dir/bin/codex-monitor" autostart install
test -f "$XDG_CONFIG_HOME/autostart/codex-monitor.desktop"
"$task_dir/bin/codex-monitor" autostart status
"$task_dir/bin/codex-monitor" autostart remove
echo 'Linux acceptance checks passed'
