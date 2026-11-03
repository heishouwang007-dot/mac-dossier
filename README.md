# mac-inventory — Mac 档案

**一句话**：《Mac 档案》——把「这台机器装了什么、装在哪、为什么装、怎么起」收成一个原生窗口，
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

- **明细**（默认）：8 个标签，三层结构——
  1. **全部 (21)**：归总索引页，全部登记条目按分类分段（服务与自启/工具与运行时/项目与配置/学校授权）
  2. 事实表按"问题的性质"排：**安装的软件**(181，brew自装/cask/应用/依赖合一表) → **命令行**(35) → **运行中**(30，监听端口+进程) → **开机自启**(16, launchd)
  3. 机器底子与维护：**系统能力**(7) → **位置**(19) → **私有凭据及未登记**(2, SSH指纹+漂移报警，垫底)
- **看板**：上方数字条；主体两张卡——**运行的应用**（正在开着的 GUI App）与
  **运行的服务**（常驻通道，● 正常/异常）；底部窄带——**机器底子**（SIP/加密等
  小字三列）+ **要注意**（橙色警告）。

状态语言：● 正常(绿) / ● 异常(红) / ◐ 部分未检查(灰) / — 未检查(黑)；
选中一行底部有「检查：」逐项明细（✓/✗/◐）。

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
