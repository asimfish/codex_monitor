#!/bin/bash
# Build CodexMonitor.app with the system Swift toolchain (no Xcode project needed).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="CodexMonitor"
# Build outside the (possibly iCloud-synced) source tree: file providers attach xattrs
# asynchronously and codesign then rejects the bundle as "detritus".
BUILD_DIR="${BUILD_DIR:-$HOME/Library/Caches/CodexMonitor/build}"
APP="$BUILD_DIR/$APP_NAME.app"
ARCH="$(uname -m)"
SDK="$(xcrun --show-sdk-path)"

mkdir -p "$BUILD_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> compiling ($ARCH, SDK: $SDK)"
swiftc \
  -O \
  -swift-version 5 \
  -parse-as-library \
  -module-name "$APP_NAME" \
  -target "${ARCH}-apple-macos14.0" \
  -sdk "$SDK" \
  -framework AppKit -framework SwiftUI -framework Combine \
  "$ROOT"/Sources/CodexMonitor/*.swift \
  -o "$APP/Contents/MacOS/$APP_NAME"

cp "$ROOT/Resources/Info.plist" "$APP/Contents/Info.plist"
if [ -f "$ROOT/Resources/AppIcon.icns" ]; then
  cp "$ROOT/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$APP/Contents/Info.plist" >/dev/null 2>&1 || true
fi
echo "APPL????" > "$APP/Contents/PkgInfo"

echo "==> signing (ad-hoc)"
xattr -cr "$APP" 2>/dev/null || true
codesign --force --sign - --timestamp=none "$APP" >/dev/null

echo "==> built $APP"
echo "$APP" > "$ROOT/.last-build-path"
