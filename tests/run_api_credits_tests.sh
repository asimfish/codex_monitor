#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/cm-api-native.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
SOURCES=()
for SOURCE in "$ROOT"/Sources/CodexMonitor/*.swift; do
  [[ "$SOURCE" == */App.swift ]] || SOURCES+=("$SOURCE")
done
swiftc -swift-version 5 -parse-as-library -module-name ApiCreditTests \
  -target "$(uname -m)-apple-macos14.0" -sdk "$(xcrun --show-sdk-path)" \
  -framework AppKit -framework SwiftUI -framework Combine \
  "${SOURCES[@]}" "$ROOT/tests/api_credits/main.swift" -o "$SANDBOX/api-tests"
CODEX_HOME="$SANDBOX/main" CODEX_ACCOUNTS_DIR="$SANDBOX/accounts" CM_TEST_CLI="$ROOT/bin/codex-monitor" "$SANDBOX/api-tests"
