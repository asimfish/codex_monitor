#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
task_dir="$(mktemp -d "${TMPDIR:-/tmp}/codex-annotations.XXXXXX")"
trap 'rm -rf "$task_dir"' EXIT
swiftc -swift-version 5 "$ROOT/Sources/CodexMonitor/Strings.swift" \
  "$ROOT/Sources/CodexMonitor/StringsTags.swift" \
  "$ROOT/Sources/CodexMonitor/AccountAnnotations.swift" \
  "$ROOT/tests/annotations/main.swift" -o "$task_dir/annotations-tests"
"$task_dir/annotations-tests"
python3 "$ROOT/tests/test_annotations_bridge.py" "$task_dir/annotations-tests"
