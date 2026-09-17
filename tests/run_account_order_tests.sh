#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/codexmonitor-order-tests.XXXXXX")"
trap 'rm -rf "$OUT"' EXIT
swiftc -swift-version 5 "$ROOT/Sources/CodexMonitor/AccountOrder.swift" \
  "$ROOT/tests/account_order/main.swift" -o "$OUT/order-tests"
"$OUT/order-tests"
