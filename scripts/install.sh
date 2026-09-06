#!/bin/bash
# Build, install to ~/Applications, register a LaunchAgent (start at login) and link the CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="CodexMonitor"
LABEL="com.codexmonitor.app"
APP_DST="$HOME/Applications/$APP_NAME.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
UID_NUM="$(id -u)"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  "$ROOT/scripts/build.sh"
fi
BUILT_APP="$(cat "$ROOT/.last-build-path")"
[ -d "$BUILT_APP" ] || { echo "build output missing: $BUILT_APP" >&2; exit 1; }

echo "==> stopping any running instance"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
pkill -x "$APP_NAME" 2>/dev/null || true
sleep 0.5

echo "==> installing to $APP_DST"
mkdir -p "$HOME/Applications"
rm -rf "$APP_DST"
cp -R "$BUILT_APP" "$APP_DST"
xattr -cr "$APP_DST" 2>/dev/null || true

echo "==> linking CLI: $BIN_DIR/codex-acct"
mkdir -p "$BIN_DIR"
chmod +x "$ROOT/bin/codex-acct"
ln -sf "$ROOT/bin/codex-acct" "$BIN_DIR/codex-acct"

echo "==> writing LaunchAgent $PLIST"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$APP_DST/Contents/MacOS/$APP_NAME</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
	</dict>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>LimitLoadToSessionType</key>
	<string>Aqua</string>
	<key>StandardOutPath</key>
	<string>$LOG_DIR/stdout.log</string>
	<key>StandardErrorPath</key>
	<string>$LOG_DIR/stderr.log</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

echo "==> loading LaunchAgent"
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>/dev/null || true
sleep 2

if pgrep -x "$APP_NAME" >/dev/null; then
  echo "==> $APP_NAME is running (pid $(pgrep -x "$APP_NAME" | head -1)); it will start automatically at login."
else
  echo "!! $APP_NAME did not start; check $LOG_DIR/stderr.log" >&2
  exit 1
fi

if ! command -v codex-acct >/dev/null 2>&1; then
  echo "note: $BIN_DIR is not on PATH in this shell; add it or call $BIN_DIR/codex-acct directly."
fi
echo "done."
