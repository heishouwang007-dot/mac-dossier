// MacInventoryApp.swift — 「这台 Mac 上有什么」原生窗口（纯 AppKit）
//
// 为什么是 AppKit 不是 SwiftUI：这台机器只装了 CommandLineTools（没有完整 Xcode），
// CLT 里没有 SwiftUI 宏插件（SwiftUIMacros），@State 编不了 —— journal 2026-10-24
// 的菜单栏 App 也是这么选的。纯 AppKit 无外来依赖，swiftc 直编。
//
// 只做显示：内容全部来自 inventory-view.json（render.py 算好标题/列/行/详情/着色），
// 与 Windows 那边 inventory-gui.ps1 分工一致 —— 改内容不用碰这文件，改外观才碰。
//
// view schema 约定：
//   tabs[].columns[].h       列名（数量不定，动态建列）
//   rows[].cells / tag / copy / detail
//   tag: "ok"→绿 "bad"→红 "warn"→橙 "dim"→灰

import AppKit

// MARK: - 模型

struct ViewColumn: Codable { var h: String; var w: Int? }
struct ViewRow: Codable { var cells: [String]; var tag: String?; var copy: String?; var detail: String? }
struct ViewTab: Codable { var name: String; var hint: String?; var columns: [ViewColumn]; var rows: [ViewRow] }
struct DashNum: Codable { var n: String; var label: String }
struct DashService: Codable { var title: String; var state: String; var line: String; var where_: String?

    enum CodingKeys: String, CodingKey { case title, state, line, where_ = "where" }
    var wherePath: String { where_ ?? "" }
}
struct DashCap: Codable { var name: String; var ok: Bool }
struct DashboardC: Codable { var numbers: [DashNum]; var services: [DashService]; var caps: [DashCap]; var warns: [String] }
struct ViewModel: Codable { var title: String; var subtitle: String; var footer: String; var tabs: [ViewTab]; var dashboard: DashboardC? }

func tagColor(_ tag: String?) -> NSColor {
    switch tag {
    case "ok": return .systemGreen
    case "bad": return .systemRed
    case "warn": return .systemOrange
    case "dim": return .secondaryLabelColor
    default: return .labelColor
    }
}

// MARK: - 一个标签页 = 一个 NSTableView + 数据源

final class TabPane: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    let tab: ViewTab
    let tableView: NSTableView
    var shown: [Int] = []       // 过滤后要显示的行索引
    var onSelection: () -> Void = {}

    init(tab: ViewTab, width: CGFloat) {
        self.tab = tab
        let cols = tab.columns
        let total = CGFloat(cols.reduce(60) { $0 + CGFloat($1.w ?? 120) })
        self.tableView = NSTableView()

        super.init()
        for (i, c) in cols.enumerated() {
            let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(String(i)))
            col.headerCell = NSTableHeaderCell(textCell: c.h)
            // 宽度按声明的 w 等比铺满
            col.width = max(50, (width - 24) * CGFloat(c.w ?? 120) / max(total, 1))
            col.resizingMask = [.userResizingMask, .autoresizingMask]
            col.minWidth = 50
            tableView.addTableColumn(col)
        }
        tableView.dataSource = self
        tableView.delegate = self
        tableView.target = self                        // doubleAction 的应答者
        tableView.action = #selector(rowClicked(_:))
        tableView.usesAlternatingRowBackgroundColors = true
        tableView.rowHeight = 26
        tableView.columnAutoresizingStyle = .noColumnAutoresizing
        tableView.doubleAction = #selector(doubleClickRow(_:))
        filter("")
    }

    /// 窗口尺寸变化后重铺列宽（按声明的 w 等比，铺满给定宽度）
    func resize(_ width: CGFloat) {
        let cols = tab.columns
        let total = CGFloat(cols.reduce(60) { $0 + CGFloat($1.w ?? 120) })
        for (i, c) in cols.enumerated() {
            let col = tableView.tableColumns[i]
            col.width = max(48, (width - 24) * CGFloat(c.w ?? 120) / max(total, 1))
        }
        tableView.tile()
    }

    func filter(_ q: String) {
        let query = q.trimmingCharacters(in: .whitespaces).lowercased()
        if query.isEmpty {
            shown = Array(tab.rows.indices)
        } else {
            shown = tab.rows.indices.filter { i in
                let r = tab.rows[i]
                return ((r.cells as [String?]) + [r.detail ?? ""])
                    .compactMap { $0?.lowercased() }
                    .joined(separator: " ").contains(query)
            }
        }
        tableView.reloadData()
    }

    var selectedRow: ViewRow? {
        let r = tableView.selectedRow
        guard shown.indices.contains(r) else { return nil }
        return tab.rows[shown[r]]
    }

    // MARK: datasource

    func numberOfRows(in tableView: NSTableView) -> Int { shown.count }

    // MARK: delegate（view-based，控制颜色和字体）

    func tableView(_ tableView: NSTableView, rowViewForRow row: Int) -> NSTableRowView? { TableRowView() }

    // 选中变化（点击、↑↓键盘、selectRowIndexes 全都会走这里，比 action 可靠）
    func tableViewSelectionDidChange(_ notification: Notification) {
        onSelection()
    }

    @objc func rowClicked(_ sender: Any?) { onSelection() }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?,
                   row: Int) -> NSView? {
        guard let col = tableColumn, let ci = Int(col.identifier.rawValue),
              shown.indices.contains(row) else { return nil }
        let r = tab.rows[shown[row]]
        let text = r.cells.indices.contains(ci) ? r.cells[ci] : ""

        let cell = (col.identifier == ColumnID.mono) ? makeMonoCell() : makeTextCell()
        cell.identifier = col.identifier
        cell.textField?.stringValue = text
        cell.textField?.textColor = tagColor(r.tag)
        cell.textField?.toolTip = text
        return cell
    }

    private func makeTextCell() -> NSTableCellView {
        let v = NSTableCellView()
        let t = NSTextField(labelWithString: "")
        t.font = .systemFont(ofSize: 11)
        t.lineBreakMode = .byTruncatingMiddle
        v.textField = t
        v.addSubview(t)
        t.translatesAutoresizingMaskIntoConstraints = false
        t.centerYAnchor.constraint(equalTo: v.centerYAnchor).isActive = true
        t.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 4).isActive = true
        t.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -2).isActive = true
        return v
    }

    private func makeMonoCell() -> NSTableCellView {
        let v = makeTextCell()
        v.textField?.font = .monospacedSystemFont(ofSize: 10.5, weight: .regular)
        return v
    }

    @objc func doubleClickRow(_ sender: Any?) {
        guard let r = selectedRow, let p = r.copy, !p.isEmpty else { return }
        if FileManager.default.fileExists(atPath: p) {
            NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: p)
        } else {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(p, forType: .string)
        }
    }
}

/// 选中行自己画底色——用系统强调色、足够不透明，和斑马纹拉开对比；
/// 文字色不动（tag 色保留），只压浅一层底
final class TableRowView: NSTableRowView {
    override func drawSelection(in dirtyRect: NSRect) {
        NSColor.controlAccentColor.withAlphaComponent(0.55).setFill()
        bounds.fill()
    }
}

enum ColumnID {
    static let mono = NSUserInterfaceItemIdentifier("mono")
}

// MARK: - 窗口组装（全部手工布局，Auto Layout 管缩放）

final class InventoryWindow: NSObject, NSWindowDelegate {
    var model: ViewModel
    let viewPath: String
    var window: NSWindow!
    var panes: [TabPane] = []
    var tabView: NSTabView!
    var searchField: NSSearchField!
    var detailView: NSTextView!
    var statusLabel: NSTextField!
    var hintLabel: NSTextField!
    var titleLabel: NSTextField!
    var subLabel: NSTextField!
    var btnCopy: NSButton!
    var btnReload: NSButton!
    var detailBox: NSBox!
    var footerLabel: NSTextField!
    var sepTop: NSBox!
    var segCtrl: NSSegmentedControl!
    var dashView: NSView!
    var inDashboard = false  // 默认明细表格（用户对比后定稿）；看板是第二入口

    var dashNumBoxes: [NSBox] = []
    var dashServiceRows: NSView! = NSView()
    var dashCapView: NSView! = NSView()
    var dashWarnView: NSView! = NSView()
    var svcCardBox: NSBox?
    var svcCardTitle: NSTextField?
    var capCardBox: NSBox?
    var capCardTitle: NSTextField?
    var warnCardBox: NSBox?
    var warnCardTitle: NSTextField?

    init(model: ViewModel, viewPath: String) {
        self.model = model
        self.viewPath = viewPath
        super.init()
        buildUI()
        installKeyMonitors()
    }

    /// 无主菜单的 App：按钮的 keyEquivalent 不保证全局生效，用事件监视器兜底
    private func installKeyMonitors() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] ev in
            guard let self = self, ev.modifierFlags.contains(.command) else { return ev }
            if ev.charactersIgnoringModifiers == "c" {
                self.copyCurrent()
                return nil
            }
            if ev.charactersIgnoringModifiers == "r" {
                self.reload()
                return nil
            }
            return ev
        }
    }

    private func buildUI() {
        // 刷新（reload）会带着新数据重进这里：只能复用现有窗口，
        // 再建一个 NSWindow 会把内容偷走、留个空黑窗在最前面（实测黑屏就是这么来的）
        let w: NSWindow
        if let existing = window {
            w = existing
        } else {
            w = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 1320, height: 820),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false)
            // 大标题在窗口内容区里画了，titlebar 只留红绿灯，免得双标题
            w.title = ""
            w.titleVisibility = .hidden
            w.titlebarAppearsTransparent = true
            w.standardWindowButton(.documentIconButton)?.isHidden = true
            w.isMovableByWindowBackground = false
            w.backgroundColor = .windowBackgroundColor
            w.delegate = self
            window = w
        }

        // 旧内容整体丢弃（刷新=重建，不做局部 diff）
        let oldFrame = window?.contentView?.frame ?? NSRect(x: 0, y: 0, width: 1320, height: 820)
        let content = NSView(frame: oldFrame)
        w.contentView = content
        w.contentView = content

        // ── 顶栏：标题 / 副标题 / 搜索 / 复制 / 刷新 ──
        let title = NSTextField(labelWithAttributedString:
            NSAttributedString(string: model.title, attributes: [
                .font: NSFont.boldSystemFont(ofSize: 15),
            ]))
        content.addSubview(title)
        titleLabel = title

        let sub = NSTextField(labelWithString: model.subtitle)
        sub.font = .systemFont(ofSize: 10.5)
        sub.textColor = .secondaryLabelColor
        content.addSubview(sub)
        subLabel = sub

        segCtrl = NSSegmentedControl(labels: ["明细", "看板"], trackingMode: .selectOne,
                                      target: self, action: #selector(modeChanged(_:)))
        segCtrl.selectedSegment = 0
        content.addSubview(segCtrl)

        sepTop = NSBox()
        sepTop.boxType = .separator
        content.addSubview(sepTop)

        searchField = NSSearchField()
        searchField.placeholderString = "搜索：名称 / 路径 / 端口 / 命令"
        searchField.target = self
        searchField.action = #selector(searchChanged)
        content.addSubview(searchField)

        let btnCopy = NSButton(title: "复制", target: self, action: #selector(copyCurrent))
        btnCopy.bezelStyle = .rounded
        btnCopy.keyEquivalent = "c"
        btnCopy.keyEquivalentModifierMask = .command
        content.addSubview(btnCopy)
        self.btnCopy = btnCopy

        let btnReload = NSButton(title: "刷新", target: self, action: #selector(reload))
        btnReload.bezelStyle = .rounded
        content.addSubview(btnReload)
        self.btnReload = btnReload

        // ── 标签区 ──
        tabView = NSTabView()
        // ⚠ delegate 延后挂：addTabViewItem 中途就会走 didSelect 回调，
        // 那时下面的 hintLabel/detailView 还没建出来（实测就崩在这里）。
        content.addSubview(tabView)

        for (i, t) in model.tabs.enumerated() {
            let item = NSTabViewItem(identifier: String(i))
            item.label = t.name

            let pane = TabPane(tab: t, width: content.bounds.width - 40)
            let scroll = NSScrollView()
            scroll.documentView = pane.tableView
            scroll.hasVerticalScroller = true
            scroll.autoresizesSubviews = true
            scroll.autoresizingMask = [.width, .height]
            item.view = scroll
            tabView.addTabViewItem(item)
            pane.onSelection = { [weak self] in self?.selectionChanged() }
            panes.append(pane)
        }
        tabView.delegate = self   // 现在才安全

        // ── 提示行 ──
        hintLabel = NSTextField(labelWithString: "")
        hintLabel.font = .systemFont(ofSize: 9.5)
        hintLabel.textColor = .secondaryLabelColor
        content.addSubview(hintLabel)

        // ── 详情框 ──
        let detailBox = NSBox()
        detailBox.title = "详情"
        detailBox.titleFont = .boldSystemFont(ofSize: 10)
        content.addSubview(detailBox)
        self.detailBox = detailBox

        detailView = NSTextView()
        detailView.isEditable = false
        detailView.isRichText = false
        detailView.font = .systemFont(ofSize: 11.5)
        detailView.backgroundColor = .textBackgroundColor
        detailView.autoresizingMask = [.width]
        let dscroll = NSScrollView()
        dscroll.documentView = detailView
        dscroll.hasVerticalScroller = true
        detailBox.contentView = dscroll

        // ── 状态行 ──
        statusLabel = NSTextField(labelWithString: "")
        statusLabel.font = .systemFont(ofSize: 9.5)
        statusLabel.textColor = .secondaryLabelColor
        content.addSubview(statusLabel)

        let footer = NSTextField(labelWithString: model.footer)
        footer.font = .systemFont(ofSize: 9.5)
        footer.textColor = .secondaryLabelColor
        content.addSubview(footer)
        footerLabel = footer

        // ── 手动布局（定高顶栏 + 弹性中区 + 定高详情/状态，frame 一遍算清）──
        buildDashboardView(in: content)

        layoutSubviews(of: content)

        w.delegate = self
        if w.isVisible == false { w.makeKeyAndOrderFront(nil) }
        selectPane(0)
        // 开窗默认选中第一行 + 把键盘焦点交给表格：
        // 不做 makeFirstResponder 的话 ↑↓ 不进表格（实测），必须先点一下才响应
        if let g = panes.first?.tableView {
            if g.numberOfRows > 0 { g.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false) }
            w.makeFirstResponder(g)
        }
        selectionChanged()
        // 窗口尺寸变化时整套重算（NSTabView/表格的响应都依赖同一份 frame）
        NotificationCenter.default.addObserver(
            forName: NSWindow.didResizeNotification, object: w, queue: .main
        ) { [weak self] note in
            guard let self = self else { return }
            self.layoutSubviews(of: self.window.contentView!)
        }
    }


    // MARK: 看板视图

    // MARK: 看板：数字卡 + 左「常驻服务」卡 + 右「机器底子/要注意」两卡
    //
    // 教训：NSBox(.custom) 的 title 即使设了 titlePosition 也常常不渲染
    // （实测 .aboveTop 无效）——卡片标题一律用独立 NSTextField 画，不赌。

    private func makeCard(_ title: String) -> (NSBox, NSTextField) {
        let box = NSBox()
        box.boxType = .custom
        box.borderType = .lineBorder
        box.cornerRadius = 8
        box.borderWidth = 1
        box.borderColor = .separatorColor
        let lab = NSTextField(labelWithString: title)
        lab.font = .boldSystemFont(ofSize: 11)
        lab.textColor = .secondaryLabelColor
        dashView.addSubview(lab)
        dashView.addSubview(box)
        return (box, lab)
    }

    private func buildDashboardView(in content: NSView) {
        dashView = NSView()
        content.addSubview(dashView)

        // 数字 chips
        dashNumBoxes.removeAll()
        for num in model.dashboard?.numbers ?? [] {
            let box = NSBox()
            box.boxType = .custom
            box.borderType = .lineBorder
            box.cornerRadius = 8
            box.borderWidth = 1
            box.borderColor = .separatorColor
            let big = NSTextField(labelWithString: num.n)
            big.font = .monospacedDigitSystemFont(ofSize: 22, weight: .semibold)
            let small = NSTextField(labelWithString: num.label)
            small.font = .systemFont(ofSize: 10)
            small.textColor = .secondaryLabelColor
            box.addSubview(big)
            box.addSubview(small)
            big.frame = NSRect(x: 10, y: 24, width: 140, height: 26)
            small.frame = NSRect(x: 12, y: 8, width: 150, height: 12)
            dashView.addSubview(box)
            dashNumBoxes.append(box)
        }

        // 左卡：常驻服务（点 + 标题 + 状态词）
        let (svcBox, svcTitle) = makeCard("常驻服务 —— 现在在跑的")
        self.svcCardBox = svcBox; self.svcCardTitle = svcTitle
        for sv in model.dashboard?.services ?? [] {
            let dot = NSButton(title: "●", target: nil, action: nil)
            dot.isBordered = false
            dot.font = .systemFont(ofSize: 13)
            dot.contentTintColor = sv.state == "ok" ? .systemGreen : (sv.state == "bad" ? .systemRed : .secondaryLabelColor)
            let title = NSTextField(labelWithString: sv.title)
            title.font = .systemFont(ofSize: 12, weight: .medium)
            let line = NSTextField(labelWithString: sv.line)
            line.font = .systemFont(ofSize: 10.5, weight: .medium)
            line.textColor = sv.state == "ok" ? .systemGreen : (sv.state == "bad" ? .systemRed : .secondaryLabelColor)
            line.lineBreakMode = .byTruncatingMiddle
            line.cell?.truncatesLastVisibleLine = true
            line.cell?.wraps = false
            dashServiceRows.addSubview(dot)
            dashServiceRows.addSubview(title)
            dashServiceRows.addSubview(line)
        }
        svcBox.addSubview(dashServiceRows)

        // 右上卡：机器底子（能力清单 ✓/✗）
        let (capBox, capTitle) = makeCard("机器底子 —— 系统检查")
        self.capCardBox = capBox; self.capCardTitle = capTitle
        for c in model.dashboard?.caps ?? [] {
            let l = NSTextField(labelWithString: (c.ok ? "✓ " : "✗ ") + c.name)
            l.font = .systemFont(ofSize: 10.5)
            if !c.ok { l.textColor = .systemRed }
            dashCapView.addSubview(l)
        }
        capBox.addSubview(dashCapView)

        // 右下卡：要注意（⚠ 橙色）
        let (warnBox, warnTitle) = makeCard("要注意 —— 没检查过的/有问题的")
        self.warnCardBox = warnBox; self.warnCardTitle = warnTitle
        for wn in model.dashboard?.warns ?? [] {
            let l = NSTextField(labelWithString: "⚠ " + wn)
            l.font = .systemFont(ofSize: 11)
            l.textColor = .systemOrange
            l.lineBreakMode = .byTruncatingMiddle
            l.cell?.truncatesLastVisibleLine = true
            l.cell?.wraps = false
            dashWarnView.addSubview(l)
        }
        warnBox.addSubview(dashWarnView)
        let emptyWarns = (model.dashboard?.warns ?? []).isEmpty
        warnBox.isHidden = emptyWarns
        warnTitle.isHidden = emptyWarns
    }

    private func layoutDashboard(W: CGFloat, H: CGFloat) {
        dashView.frame = NSRect(x: 0, y: 0, width: W, height: H - 140)

        // 数字条
        let nums = dashNumBoxes.count
        let cw: CGFloat = 176, ch: CGFloat = 58, gap: CGFloat = 14
        let totalW = CGFloat(nums) * cw + CGFloat(nums - 1) * gap
        let startX = max(16, (W - totalW) / 2)
        for (i, b) in dashNumBoxes.enumerated() {
            b.frame = NSRect(x: startX + CGFloat(i) * (cw + gap),
                             y: dashView.bounds.height - ch - 8, width: cw, height: ch)
        }

        let warnCount = CGFloat(model.dashboard?.warns.count ?? 0)
        let boxesBottom: CGFloat = 12
        let boxesTopY = dashView.bounds.height - ch - 24   // 数字条下 4pt
        let boxesH = boxesTopY - boxesBottom
        let svcW = (W - 44) * 0.60
        let rightX = 16 + svcW + 12
        let rightW = W - rightX - 16
        let titleH: CGFloat = 18

        // 左：常驻服务
        svcCardBox?.frame = NSRect(x: 16, y: boxesBottom, width: svcW, height: boxesH)
        svcCardTitle?.frame = NSRect(x: 20, y: boxesBottom + boxesH + 2, width: svcW - 8, height: titleH)
        let nSv = max(1, CGFloat(model.dashboard?.services.count ?? 1))
        let rowGap = min(34, max(20, (boxesH - 16) / nSv))
        for (i, v) in dashServiceRows.subviews.enumerated() {
            let row = i / 3, kind = i % 3
            let y = boxesH - 12 - CGFloat(row + 1) * rowGap
            let w0 = svcW - 24
            switch kind {
            case 0: v.frame = NSRect(x: 10, y: y, width: 18, height: 16)
            case 1: v.frame = NSRect(x: 28, y: y, width: w0 * 0.46, height: 16)
            default: v.frame = NSRect(x: 28 + w0 * 0.46 + 6, y: y, width: w0 * 0.40, height: 16)
            }
        }
        dashServiceRows.frame = NSRect(x: 0, y: 0, width: svcW - 8, height: boxesH)

        // 右列：机器底子（上）+ 要注意（下）
        let warnH = warnCount == 0 ? 0 : min(150, 30 + warnCount * 26)
        let capsH = boxesH - warnH - (warnCount == 0 ? 0 : 10) - titleH
        capCardBox?.frame = NSRect(x: rightX, y: boxesBottom + warnH + (warnCount == 0 ? 0 : 10),
                                   width: rightW, height: capsH)
        capCardTitle?.frame = NSRect(x: rightX + 4, y: boxesBottom + warnH + (warnCount == 0 ? 0 : 10) + capsH + 2,
                                     width: rightW, height: titleH)
        if warnCount > 0 {
            warnCardBox?.frame = NSRect(x: rightX, y: boxesBottom, width: rightW, height: warnH - titleH)
            warnCardTitle?.frame = NSRect(x: rightX + 4, y: boxesBottom + warnH - titleH + 2,
                                          width: rightW, height: titleH)
            for (i, v) in dashWarnView.subviews.enumerated() {
                v.frame = NSRect(x: 10, y: (warnH - titleH) - 12 - CGFloat(i + 1) * 24,
                                 width: rightW - 26, height: 16)
            }
            dashWarnView.frame = NSRect(x: 0, y: 0, width: rightW - 8, height: warnH - titleH)
        }

        let capN = max(1, CGFloat(model.dashboard?.caps.count ?? 1))
        let capGap = min(30, max(16, (capsH - 12) / capN))
        for (i, v) in dashCapView.subviews.enumerated() {
            v.frame = NSRect(x: 10, y: capsH - 12 - CGFloat(i + 1) * capGap,
                             width: rightW - 26, height: 16)
        }
        dashCapView.frame = NSRect(x: 0, y: 0, width: rightW - 8, height: capsH)
    }


    @objc func modeChanged(_ sender: NSSegmentedControl) {
        inDashboard = sender.selectedSegment == 1
        layoutSubviews(of: window.contentView!)
        if !inDashboard { selectionChanged() }
    }

    // MARK: 布局（frame 手排，Auto Layout 和 NSTabView 打架）

    private func layoutSubviews(of content: NSView) {
        let W = content.bounds.width, H = content.bounds.height
        let top: CGFloat = 140
        let detailH: CGFloat = 132
        let statusH: CGFloat = 24
        let margin: CGFloat = 12

        // fullSizeContentView：顶部 ~28pt 是红绿灯区。标题整体下压 + 留白，
        // 底下一根发丝线把「页头」和内容干净分开
        titleLabel.frame = NSRect(x: 84, y: H - 88, width: 520, height: 24)
        subLabel.frame = NSRect(x: 86, y: H - 110, width: 980, height: 14)

        btnReload.frame = NSRect(x: W - 84, y: H - 92, width: 70, height: 24)
        btnCopy.frame = NSRect(x: W - 160, y: H - 92, width: 70, height: 24)
        searchField.frame = NSRect(x: W - 436, y: H - 92, width: 268, height: 22)
        segCtrl.frame = NSRect(x: W - 560, y: H - 90, width: 108, height: 22)
        sepTop.frame = NSRect(x: margin, y: H - 126, width: W - 2 * margin, height: 1)

        // 模式切换：看板占满 / 明细表 + 详情
        dashView.isHidden = !inDashboard
        searchField.isHidden = inDashboard
        tabView.isHidden = inDashboard
        hintLabel.isHidden = inDashboard
        detailBox.isHidden = inDashboard
        if inDashboard {
            layoutDashboard(W: W, H: H)
            statusLabel.stringValue = "看板 · 切到「明细」看全部数据 · ⌘R 刷新"
            statusLabel.textColor = .secondaryLabelColor
        } else {
            let tabsH = H - top - detailH - statusH - 44
            tabView.frame = NSRect(x: margin, y: statusH + detailH + 34,
                                   width: W - 2 * margin, height: tabsH)
            let cr = tabView.contentRect
            for item in tabView.tabViewItems { item.view?.frame = cr }
            panes.forEach { $0.resize(cr.width) }
        }


        hintLabel.frame = NSRect(x: margin + 8, y: statusH + detailH + 12,
                                 width: W - 2 * margin - 16, height: 14)

        detailBox.frame = NSRect(x: margin, y: statusH + 4, width: W - 2 * margin, height: detailH)
        if let dscroll = detailBox.contentView as? NSScrollView {
            let b = detailBox.bounds
            dscroll.frame = NSRect(x: 10, y: 8, width: b.width - 22, height: b.height - 24)
            detailView.frame = NSRect(x: 0, y: 0, width: dscroll.bounds.width, height: max(dscroll.bounds.height, 100))
        }

        statusLabel.frame = NSRect(x: margin + 6, y: 4, width: W / 2, height: 16)
        footerLabel.frame = NSRect(x: W - 460, y: 4, width: 448, height: 16)
    }

    var currentPane: TabPane? {
        let i = tabView.indexOfTabViewItem(tabView.selectedTabViewItem ?? tabView.tabViewItems.first!)
        return panes.indices.contains(i) ? panes[i] : nil
    }

    func selectPane(_ idx: Int) {
        tabView.selectTabViewItem(at: idx)
        selectionChanged()
    }

    @objc func searchChanged() {
        for p in panes { p.filter(searchField.stringValue) }
        selectionChanged()
    }

    func selectionChanged() {
        guard let pane = currentPane,
              let hint = hintLabel, let dv = detailView, let st = statusLabel else { return }
        hint.stringValue = pane.tab.hint ?? ""
        let row = pane.selectedRow ?? pane.tab.rows.first
        dv.string = row?.detail ?? "（选中一行查看详情）"

        let query = (searchField?.stringValue ?? "").trimmingCharacters(in: .whitespaces)
        st.stringValue = "\(pane.shown.count) 行\(query.isEmpty ? "" : "（已过滤）") · " + model.footer
    }

    @objc func copyCurrent() {
        guard let pane = currentPane, let row = pane.selectedRow,
              let text = row.copy, !text.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        statusLabel.stringValue = "已复制：" + text
        statusLabel.textColor = .systemGreen
        // ⌘C 复制完成后字变灰色的活儿交给下一次 selectionChanged / 搜索，不用定时器
    }

    @objc func reload() {
        // 数据不热更新：重读 view 文件 → 同一个窗口里整套重建。
        // ⚠ 不许新建 NSWindow：旧实现把新窗的内容偷给旧窗，
        //   留下一个空黑窗在最前面（用户实测"点刷新黑屏"）。
        guard let d = FileManager.default.contents(atPath: viewPath),
              let m = try? JSONDecoder().decode(ViewModel.self, from: d) else {
            NSSound.beep()
            statusLabel?.stringValue = "读不到 \(viewPath)，先跑一次 mac-inventory"
            return
        }
        model = m
        panes = []
        dashNumBoxes.removeAll()
        dashServiceRows = NSView()
        dashCapView = NSView()
        dashWarnView = NSView()
        buildUI()
    }
}

// MARK: -NSTabView delegate

extension InventoryWindow: NSTabViewDelegate {
    func tabView(_ tabView: NSTabView, didSelect tabViewItem: NSTabViewItem?) {
        selectionChanged()
        statusLabel.textColor = .secondaryLabelColor
        // 焦点跟到新标签的表格，↑↓ 连续可用
        if let g = currentPane?.tableView { window.makeFirstResponder(g) }
    }
}

// MARK: - 入口

enum Launch {
    static func main() {
        let args = CommandLine.arguments
        let viewPath = args.count > 1 ? args[1]
            : NSHomeDirectory() + "/.cache/mac-inventory/inventory-view.json"

        guard let data = FileManager.default.contents(atPath: viewPath),
              let model = try? JSONDecoder().decode(ViewModel.self, from: data) else {
            let alert = NSAlert()
            alert.messageText = "读不到 " + viewPath
            alert.informativeText = "先跑一次：mac-inventory"
            alert.runModal()
            return
        }

        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let holder = InventoryWindow(model: model, viewPath: viewPath)
        holder.window.center()
        holder.window.makeKeyAndOrderFront(nil)
        app.activate(ignoringOtherApps: true)
        app.run()
    }
}

Launch.main()
