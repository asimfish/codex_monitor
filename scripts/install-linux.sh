#!/usr/bin/env bash
# Install the web dashboard in an isolated Python environment (no sudo/pip changes).
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
  echo 'This installer is for Linux. On macOS use python3 bin/codex-monitor serve.' >&2
  exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${CODEX_MONITOR_INSTALL_DIR:-$HOME/.local/share/codex-monitor}"
BIN_DIR="${CODEX_MONITOR_BIN_DIR:-$HOME/.local/bin}"
command -v python3 >/dev/null || { echo 'Install Python 3.8+ and python3-venv first.' >&2; exit 1; }
python3 -m venv "$INSTALL_DIR/venv" || {
  echo 'Cannot create a virtual environment. On Debian/Ubuntu: sudo apt install python3-venv' >&2
  exit 1
}
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade "$ROOT"
mkdir -p "$BIN_DIR"
for name in codex-monitor codex-acct; do
  destination="$BIN_DIR/$name"
  if [[ -e "$destination" && ! -L "$destination" ]]; then
    backup="$destination.previous-$(date +%Y%m%d-%H%M%S)"
    mv "$destination" "$backup"
    echo "Moved existing $destination to $backup"
  fi
  ln -sfn "$INSTALL_DIR/venv/bin/$name" "$destination"
done
printf '\nInstalled. Start the dashboard:\n  "%s/venv/bin/codex-monitor" serve\n' "$INSTALL_DIR"
printf 'Optional start at login:\n  "%s/venv/bin/codex-monitor" autostart install\n' "$INSTALL_DIR"
echo 'If ~/.local/bin is on PATH, you can simply run: codex-monitor serve'
