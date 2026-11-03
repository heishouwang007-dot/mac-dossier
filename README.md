# mac-inventory — 这台 Mac 上有什么

**一句话**：把「这台机器装了什么、装在哪、为什么装、怎么起」收成一个原生窗口，
和 [`tongji-vdi-ssh` 的 `vdi-inventory`](../tongji-vdi-ssh)（远程桌面版）是同一套设计的两个实现。

```
bin/mac-inventory        ← 入口（~/.local/bin 有符号链接）
   │
   ├─ collect/collect.py          只读采集 → inventory.json（事实层）
   ├─ notes/inventory-notes.json   人写的说明（为什么装 / 怎么跑 / 怎么停）
   ├─ render/render.py            两者合并 → inventory-view.json + Markdown
   └─ app/MacInventoryApp.swift   纯 AppKit 窗口（只负责显示 view.json）
```

## 用法

```bash
mac-inventory              # 采集(约40s) → 渲染 → 打开窗口
mac-inventory --render-only # 改完 notes 重渲染（几秒）
mac-inventory --md          # 终端看 Markdown 版
mac-inventory --app-only    # 不采集直接开窗口
```

## 窗口：明细 / 看板 两个模式

- **明细**（默认，2026-11-03 用户对比 Windows 版后定稿）：12 标签页全量表格，
  条目 / 状态 / 位置 / 怎么跑四列，选中一行底部联动详情 —— 与 Windows 版
  `vdi-inventory` 同一形态。
  - 交互（全部实测过）：点击或 ↑↓ 选行（开窗即选中第一行，焦点在表格上）；
    **⌘C** 复制当前行的路径/命令；**双击**路径行直接在 Finder 里打开；
    **⌘R** 重读数据文件。搜索框实时过滤当前标签。
- **看板**（第二入口）：一行数字卡 + 服务卡 + 能力/警告卡，给一眼式的概览。

## 吸收自社区的做法

- **Brewfile 导出**（Homebrew `bundle dump`，参考无数 dotfiles 仓库）：
  每次采集把整机清单快照到 `~/.cache/mac-inventory/Brewfile`，与上次 diff——
  这份文件本身可以 `brew bundle` 一键复装整机；diff 输出「新装了什么」。
- **HTTP 探活**（Homepage/Dashy 面板的做法）：端口在听 ≠ 服务可用，
  采集时对 8802/8810 打真实 `/health`，对 7897 做 TCP 握手，结果进徽章和看板警告。
  Windows 那份 `vdi-inventory` 同步吸收（远端本机探 `8802 /health`）。

## 窗口里有什么

| 标签 | 内容 |
|---|---|
| 我装的 / 系统自带 / 第三方 | 说明层条目，带实时状态徽章（端口/目录/命令/brew/App） |
| 系统能力 | SIP、命令行工具、Rosetta、FileVault、Gatekeeper、Time Machine |
| 命令 | PATH 解析出的 35 个 CLI + 现跑版本（每条带超时） |
| 自启 | LaunchAgents/Daemons + 是否真的加载了 |
| 端口 | lsof 监听表，仅本机 / 所有网卡 |
| 目录 | 我的仓库和工具目录 + 大小 |
| Homebrew | 110 formula（区分 leaf / 依赖）+ 15 cask |
| 应用 | 非系统 .app（bundle id / 版本 / 路径） |
| 凭据 | SSH 私钥指纹、config Host —— 只列位置，不读内容 |
| 未登记 | 机器上有、说明里没有 → 提示补 notes；为空 = 清单跟得上 |

## 设计约束（为什么长这样）

1. **事实和说明分两层**。扫描器查不出「为什么装 clash-stack」；那部分人写在
   `notes/inventory-notes.json` 里、进版本库。两边对不上会显示在「未登记」。
2. **采集只读**。外部命令一律带超时（Windows 那边被 `ssh --version` 挂死过 5 分钟）；
   SSH 凭据只取指纹，不读私钥内容。
3. **界面只做显示**。`render.py` 输出 `inventory-view.json`
   （`{title, tabs:[{columns, rows[{cells, tag, copy, detail}]}]}`），
   和 Windows 版**同一个 schema**——两个平台共用一份心智模型，改说明不碰界面代码。
4. **查看器是纯 AppKit**。这台机器只有 CommandLineTools（无完整 Xcode），
   SwiftUI 的宏插件（SwiftUIMacros）不在 CLT 里，`@State` 编不过 ——
   journal 2026-10-24 的菜单栏 App 同理。`app/build.sh` 一条 swiftc 直编 + ad-hoc 签名。

## 已知取舍

- 表格视图是手写 frame 布局（不跑 Auto Layout），窗口尺寸变化靠
  `NSWindow.didResizeNotification` 重铺；拉得极窄时列会挤，不影响数据。
- `brew leaves` 之外的依赖不进「未登记」，否则 110 条 formula 会淹没真正要登记的。
- 采集里 `du` 目录大小有 3 秒预算，超时标 partial（/Applications 这类只会偏小）。
