#!/bin/bash
# make-icon.sh —— 重新生成 AppIcon.icns（Big Sur 底板 + 渐变 + SF Symbol）
# 配方来自 ~/.local/opt/makeicon.swift（journal 2026-10-24 menubar-apps）。
# 用法: ./make-icon.sh [symbol] [topHex] [bottomHex]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SYMBOL="${1:-list.bullet.rectangle}"
TOP="${2:-0A6CFF}"
BOTTOM="${3:-00C7BE}"

swiftc -O "$HERE/makeicon.swift" -o /tmp/_makeicon -framework Cocoa
/tmp/_makeicon /tmp/_appicon.png "$SYMBOL" "$TOP" "$BOTTOM"
rm -rf /tmp/_AppIcon.iconset && mkdir -p /tmp/_AppIcon.iconset
for sz in 16 32 128 256 512; do
  sips -z $sz $sz /tmp/_appicon.png --out /tmp/_AppIcon.iconset/icon_${sz}x${sz}.png >/dev/null
  sips -z $((sz*2)) $((sz*2)) /tmp/_appicon.png --out /tmp/_AppIcon.iconset/icon_${sz}x${sz}@2x.png >/dev/null
done
iconutil -c icns /tmp/_AppIcon.iconset -o "$HERE/AppIcon.icns"
echo "✓ $HERE/AppIcon.icns  (symbol=$SYMBOL $TOP→$BOTTOM)"
echo "  下一步: ./build.sh && touch build/这台Mac上有什么.app"
