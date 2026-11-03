// 生成 App 图标（1024×1024 PNG）：Big Sur 风格圆角底板 + 渐变 + 居中白色 SF Symbol
// 用法: makeicon <输出png> <symbol> <顶部色hex> <底部色hex>
import AppKit

func hex(_ s: String) -> NSColor {
    var v = UInt64(0)
    Scanner(string: s.replacingOccurrences(of: "#", with: "")).scanHexInt64(&v)
    return NSColor(srgbRed: CGFloat((v >> 16) & 0xFF) / 255.0,
                   green: CGFloat((v >> 8) & 0xFF) / 255.0,
                   blue: CGFloat(v & 0xFF) / 255.0, alpha: 1.0)
}

let args = CommandLine.arguments
guard args.count >= 5 else {
    FileHandle.standardError.write("usage: makeicon out.png symbol topHex bottomHex\n".data(using: .utf8)!)
    exit(2)
}
let outPath = args[1], symbol = args[2], c1 = hex(args[3]), c2 = hex(args[4])

let px = 1024
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                 isPlanar: false, colorSpaceName: .deviceRGB,
                                 bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let s = CGFloat(px)

// 底板：macOS 图标留边约 9%，圆角半径约 22.5%
let inset = s * 0.09
let rect = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
let radius = rect.width * 0.225
let plate = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)

// 底部投影（让图标在浅色/深色背景上都有立体感）
NSGraphicsContext.current?.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
shadow.shadowBlurRadius = s * 0.02
shadow.shadowOffset = NSSize(width: 0, height: -s * 0.012)
shadow.set()
NSColor.black.setFill()
plate.fill()
NSGraphicsContext.current?.restoreGraphicsState()

// 渐变主体
NSGradient(starting: c1, ending: c2)?.draw(in: plate, angle: -90)

// 顶部高光
NSGraphicsContext.current?.saveGraphicsState()
plate.addClip()
NSGradient(starting: NSColor.white.withAlphaComponent(0.22),
           ending: NSColor.white.withAlphaComponent(0.0))?
    .draw(in: NSRect(x: rect.minX, y: rect.midY, width: rect.width, height: rect.height / 2), angle: -90)
NSGraphicsContext.current?.restoreGraphicsState()

// 居中白色符号
let pointSize = rect.width * 0.52
let cfg = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .semibold)
if let sym = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?
    .withSymbolConfiguration(cfg) {
    let w = sym.size.width, h = sym.size.height
    let target = NSRect(x: (s - w) / 2, y: (s - h) / 2, width: w, height: h)
    let tinted = NSImage(size: NSSize(width: w, height: h))
    tinted.lockFocus()
    sym.draw(in: NSRect(origin: .zero, size: NSSize(width: w, height: h)))
    NSColor.white.set()
    NSRect(origin: .zero, size: NSSize(width: w, height: h)).fill(using: .sourceAtop)
    tinted.unlockFocus()
    tinted.draw(in: target)
}

NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try? png.write(to: URL(fileURLWithPath: outPath))
print("wrote \(outPath)")
