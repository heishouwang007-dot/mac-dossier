# 两套 inventory 的对照（Mac / 远程桌面）

同一份设计跑在两个平台，schema 完全一致，只是采集器和查看器的技术栈不同：

| | 远程桌面（tongji-vdi-ssh） | 本机 Mac（本仓库） |
|---|---|---|
| 采集器 | `inventory-collect.ps1`（注册表/计划任务/服务/PATH/端口） | `collect/collect.py`（brew/LaunchAgents/lsof/plist） |
| 说明层 | `remote/inventory-notes.json` | `notes/inventory-notes.json` |
| 渲染器 | `inventory-render.py`（--view） | `render/render.py` |
| 查看器 | WinForms（DPI 感知，PS 5.1） | AppKit（swiftc 直编） |
| 启动 | 桌面快捷方式 → wscript + vbs | `open` 一个 .app bundle |
| 平台特有坑 | UTF-8 BOM、200% 缩放、ConvertTo-Json 单元素数组 | CLT 无 SwiftUI 宏、NSTabView 需显式给 item.view frame |

## 平台差异备忘（写代码前先看）

- **macOS 的「开机自启」是 launchd**：plist 注册 ≠ 加载，`launchctl list` 才是现况；
  Windows 的对应物是计划任务，同样注册 ≠ Running。
- **brew 的权威口径**：`brew list --formula/--cask` 是清单，`brew leaves` 是「我装的」，
  其余是依赖 —— 不区分的话未登记会被依赖淹掉。
- **命令版本采集**：外部命令全部带超时；`soffice --version` 这类个别命令要 3-5 秒，
  30+ 条串行就是采集慢的大头。
- **AppKit 布局**：NSTabView 的 item.view 需要显式 `frame = tabView.contentRect`，
  否则内容区几何不定（实测表格只出一两行）；NSTableColumn 的自动调整在
  `.noColumnAutoresizing` 下由自己按比例铺。

## 无头验证 AppKit 交互的三个坑（2026-11-03，修交互 bug 时实测）

1. **System Events 的 `click at {x,y}` 不是真点击**：它只做 AXPress 命中测试，
   返回被点中的元素描述，但驱动不了 NSTableView 的选择/双击。
2. **`CGEvent.postToPid` 对非 key 状态进程直接丢事件**（keyDown、mouse 都一样）。
   真要用合成事件：`e.post(tap: .cghidEventTap)` 会话级注入 = 等同真实硬件事件；
   或 `System Events keystroke`（前提：App 已 frontmost + 辅助功能已授权）。
3. **验证快捷键**看 `pbpaste`（清空→发 ⌘C→读回，端到端）；**验证双击**看
   Finder 窗口数（close every window → 双击 → count）。
