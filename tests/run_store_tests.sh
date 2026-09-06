#!/bin/bash
# Compile the non-UI Swift sources with StoreTests.swift and run them against a temp dir.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/codexmonitor-store-tests"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/codexmonitor-sandbox.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

swiftc -swift-version 5 -module-name StoreTests -target "$(uname -m)-apple-macos14.0" \
  -sdk "$(xcrun --show-sdk-path)" \
  "$ROOT/Sources/CodexMonitor/Models.swift" \
  "$ROOT/Sources/CodexMonitor/ProfileStore.swift" \
  "$ROOT/Sources/CodexMonitor/Strings.swift" \
  "$ROOT/Sources/CodexMonitor/StringsOAuth.swift" \
  "$ROOT/Sources/CodexMonitor/OAuthCore.swift" \
  "$ROOT/Sources/CodexMonitor/LiveRateLimits.swift" \
  "$ROOT/tests/store/main.swift" \
  -o "$OUT"

mkdir -p "$SANDBOX/codex" "$SANDBOX/accounts"
CODEX_HOME="$SANDBOX/codex" CODEX_ACCOUNTS_DIR="$SANDBOX/accounts" "$OUT"
