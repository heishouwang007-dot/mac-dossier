#!/bin/bash
# build.sh —— swiftc 直编（本机只有 CommandLineTools，无 Xcode 工程、无 SwiftUI 宏插件）
# 产物：app/build/这台Mac上有什么.app（ad-hoc 签名，双击或 open 运行）
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
APP="$BUILD/这台Mac上有什么.app"
MACOS="$APP/Contents/MacOS"

rm -rf "$APP"
mkdir -p "$MACOS/Resources"
swiftc -O -o "$MACOS/这台Mac上有什么" "$HERE/MacInventoryApp.swift"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.mac.inventory</string>
  <key>CFBundleName</key><string>这台Mac上有什么</string>
  <key>CFBundleExecutable</key><string>这台Mac上有什么</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1
echo "✓ $APP"
