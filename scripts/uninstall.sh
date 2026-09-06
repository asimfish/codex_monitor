#!/bin/bash
# Remove the app, LaunchAgent and CLI link. Keeps ~/.codex-accounts (your stored logins).
set -uo pipefail

APP_NAME="CodexMonitor"
LABEL="com.codexmonitor.app"
APP_DST="$HOME/Applications/$APP_NAME.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
pkill -x "$APP_NAME" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$APP_DST"
rm -f "$HOME/.local/bin/codex-acct"
echo "removed $APP_NAME, its LaunchAgent and the codex-acct link."
echo "stored logins in ~/.codex-accounts were left untouched."
