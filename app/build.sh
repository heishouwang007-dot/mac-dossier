#!/bin/bash
# build.sh —— swiftc 直编（本机只有 CommandLineTools，无 Xcode 工程、无 SwiftUI 宏插件）
# 产物：app/build/Mac档案.app（ad-hoc 签名，双击或 open 运行）
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
APP="$BUILD/Mac档案.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

# ⚠ 不再 rm -rf 整个 bundle：整个目录重建会换 inode，LaunchServices 的图标
# 绑定就旧了（Dock 会自己重读，桌面链接不会 → 图标显示成空）。
# 改成原位覆盖二进制 + plist；目录保持稳定。
mkdir -p "$MACOS" "$RESOURCES"
swiftc -O -o "$MACOS/Mac档案" "$HERE/MacInventoryApp.swift"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.mac.inventory</string>
  <key>CFBundleName</key><string>Mac档案</string>
  <key>CFBundleExecutable</key><string>Mac档案</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict>
</plist>
PLIST

# 图标（app/AppIcon.icns 由 makeicon.swift 生成，缺了只是没图标，不挡构建）
[ -f "$HERE/AppIcon.icns" ] && cp "$HERE/AppIcon.icns" "$RESOURCES/AppIcon.icns"

codesign --force --sign - "$APP" >/dev/null 2>&1
# 图标缓存刷新（桌面/Dock 链接跟着新图标走）
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1
killall Dock >/dev/null 2>&1 || true
echo "✓ $APP"
