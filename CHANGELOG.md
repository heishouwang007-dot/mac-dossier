# Changelog

## 1.0.0 (2026-11-03)

First public release.

- Three-layer design: read-only fact collector + hand-written notes layer
  + drift detection between the two
- 8-tab native AppKit viewer (built with plain `swiftc`, no Xcode project):
  全部 / 安装的软件 / 命令行 / 运行中 / 开机自启 / 系统能力 / 目录占用 /
  私有凭据及未登记
- Dashboard mode with running apps, running services (auto-start notes
  in-line), and a compact system-check strip
- Health probes (real `GET /health` + TCP handshakes), Brewfile snapshot
  with per-run diff, fingerprint-only credential display
- User-language status system: ● 正常 / ● 异常 / ◐ 部分未检查 (no probe
  jargon in the UI)
