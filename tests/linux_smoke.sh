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
export CODEX_MONITOR_INSTALL_DIR="$task_dir/install"
export CODEX_MONITOR_BIN_DIR="$task_dir/bin"
python --version
python tests/test_python.py
python tests/test_cli.py
python tests/test_dashboard.py
python tests/test_removal.py
bash scripts/install-linux.sh
test -L "$task_dir/bin/codex-monitor"
"$task_dir/bin/codex-monitor" --version
"$task_dir/bin/codex-acct" --version
python tests/check_installed_dashboard.py "$task_dir/bin/codex-monitor"
"$task_dir/bin/codex-monitor" autostart install
test -f "$XDG_CONFIG_HOME/autostart/codex-monitor.desktop"
"$task_dir/bin/codex-monitor" autostart status
"$task_dir/bin/codex-monitor" autostart remove
echo 'Linux acceptance checks passed'
