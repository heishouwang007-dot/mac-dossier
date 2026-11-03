#!/usr/bin/env python3
"""render.py -- 把 inventory.json（事实）+ notes/inventory-notes.json（说明）
合并成 inventory-view.json（界面数据）和 Markdown。

和 Windows 那套用**同一个 view schema**：
  {title, subtitle, footer,
   tabs: [{name, hint, columns: [{h, w}], rows: [{cells, tag, copy, detail}]}]}
所以 app/ 里的 SwiftUI 查看器只认这个 schema，不认机器。

tag 决定行颜色：ok / bad / warn / dim / ""。
"""

import argparse
import json
import os
import re
import sys

LIST_FIELDS = {
    "caps", "apps", "launchd", "listeners", "path_entries", "commands", "dirs",
    "formulae", "casks", "taps", "keys", "hosts", "env", "items", "children",
    "bins", "run", "stop", "config", "docs", "status", "programs", "commands_note",
    "tasks", "covers", "pipx", "npm", "uv_python", "mise",
}


def normalize(node):
    """单元素数组在 JSON 里可能被压成对象，统一充气。"""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in LIST_FIELDS:
                if v is None:
                    out[k] = []
                elif isinstance(v, list):
                    out[k] = [normalize(x) for x in v]
                elif isinstance(v, str):
                    out[k] = [v] if v else []
                else:
                    out[k] = [normalize(v)]
            else:
                out[k] = normalize(v)
        return out
    if isinstance(node, list):
        return [normalize(x) for x in node]
    return node


def mb(v):
    v = v or 0
    return "%.1f GB" % (v / 1024.0) if v >= 1024 else "%.0f MB" % v


def short(path, keep=3):
    if not path:
        return ""
    parts = [p for p in path.strip("/").split("/") if p]
    return "…/" + "/".join(parts[-keep:]) if len(parts) > keep else path


def norm(p):
    return (p or "").rstrip("/").lower()


class Facts:
    def __init__(self, d):
        self.d = d
        self.ports = {}
        for l in d.get("listeners") or []:
            self.ports.setdefault(int(l["port"]), []).append(l)
        self.launchd = {x["label"].lower(): x for x in d.get("launchd") or []}
        self.dirs = {norm(x["path"]): x for x in d.get("dirs") or []}
        self.cmds = {c["name"].lower(): c for c in d.get("commands") or []}
        self.casks = {c["name"].lower(): c for c in (d.get("brew") or {}).get("casks") or []}
        self.formulae = {f["name"].lower(): f for f in (d.get("brew") or {}).get("formulae") or []}
        self.apps = d.get("apps") or []

    def dir_lookup(self, path):
        hit = self.dirs.get(norm(path))
        if hit:
            return hit
        n = norm(path)
        best = None
        for root, info in self.dirs.items():
            if n.startswith(root + "/") and (best is None or len(root) > len(norm(best["path"]))):
                best = info
        return best

    def port_state(self, port):
        hits = self.ports.get(int(port))
        if not hits:
            return {"up": False}
        h = hits[0]
        return {"up": True, "process": h.get("process"), "pid": h.get("pid"),
                "path": h.get("path"), "addr": h.get("addr")}


def resolve_status(item, facts):
    badges = []
    for probe in item.get("status") or []:
        kind, _, arg = probe.partition(":")
        if kind == "port":
            st = facts.port_state(arg)
            badges.append({"ok": st["up"], "label": "端口 %s" % arg,
                           "detail": ("%s · pid %s" % (st.get("process"), st.get("pid"))) if st["up"] else "没在监听"})
        elif kind == "launchd":
            x = facts.launchd.get(arg.lower())
            if not x:
                badges.append({"ok": False, "label": arg, "detail": "没有这个 plist"})
            else:
                badges.append({"ok": x["loaded"], "label": arg,
                               "detail": "已加载" if x["loaded"] else "已注册但未加载"})
        elif kind == "dir":
            d = facts.dir_lookup(arg)
            if d is None:
                badges.append({"ok": None, "label": short(arg, 2), "detail": "未采集"})
            elif not d["exists"]:
                badges.append({"ok": False, "label": short(arg, 2), "detail": "目录不存在"})
            else:
                badges.append({"ok": True, "label": "目录",
                               "detail": "%s%s" % (mb(d.get("mb")), "（统计不全）" if d.get("partial") else "")})
        elif kind == "cmd":
            c = facts.cmds.get(arg.lower())
            badges.append({"ok": bool(c), "label": arg, "detail": c["path"] if c else "解析不到"})
        elif kind == "brew":
            f = facts.formulae.get(arg.lower())
            ck = facts.casks.get(arg.lower())
            hit = f or ck
            badges.append({"ok": bool(hit), "label": "brew %s" % arg,
                           "detail": (hit or {}).get("version", "") or ("未安装" if not hit else "")})
        elif kind == "app":
            hit = next((a for a in facts.apps if a["bundle"].lower() == arg.lower()), None)
            badges.append({"ok": bool(hit), "label": "App %s" % arg,
                           "detail": hit["path"] if hit else "不在 /Applications"})
        else:
            badges.append({"ok": None, "label": probe, "detail": ""})
    return badges


def coverage(notes, facts):
    annotated = {norm(i["where"]) for i in notes["items"] if i.get("where")}
    for i in notes["items"]:
        for c in i.get("covers") or []:
            annotated.add(norm(c))
    annotated_cmds = {c.lower() for i in notes["items"] for c in (i.get("commands") or [])}
    annotated_brew = {b.lower() for i in notes["items"] for b in (i.get("brew") or [])}
    annotated_apps = {a.lower() for i in notes["items"] for a in (i.get("apps") or [])}
    annotated_launchd = {l.lower() for i in notes["items"] for l in (i.get("launchd") or [])}
    paths = [a for a in annotated if a]

    def covered(path):
        p = norm(path)
        return any(a == p or a.startswith(p + "/") for a in paths)

    missing = [i for i in notes["items"] if i.get("where") and facts.dir_lookup(i["where"])
               and not facts.dir_lookup(i["where"])["exists"]]

    drift = []
    for a in facts.apps:
        if a["system"]:
            continue
        if any(a["bundle"].lower() == x or x in a["name"].lower() for x in annotated_apps):
            continue
        if any(norm(a["path"]).startswith(p) for p in paths):
            continue
        drift.append(("应用", a["name"], a.get("version") or "", a["path"]))

    for f in (facts.d.get("brew") or {}).get("formulae") or []:
        if not f.get("leaf"):
            continue          # 依赖项不算「我装的」，不然 110 条全变未登记
        if f["name"].lower() in annotated_brew:
            continue
        drift.append(("brew formula", f["name"], f.get("version") or "", ""))

    for c in (facts.d.get("brew") or {}).get("casks") or []:
        if c["name"].lower() in annotated_brew:
            continue
        drift.append(("brew cask", c["name"], c.get("version") or "", ""))

    for x in facts.d.get("launchd") or []:
        if x["label"].lower() in annotated_launchd:
            continue
        if re.match(r"^com\.(google|microsoft|docker|apple)", x["label"]):
            continue
        drift.append(("launchd", x["label"], "loaded" if x["loaded"] else "未加载", x["program"]))

    for d in facts.d.get("dirs") or []:
        if not d["exists"] or covered(d["path"]):
            continue
        drift.append(("目录", d["path"], mb(d.get("mb")), ""))

    for c in facts.d.get("commands") or []:
        if c["name"].lower() in annotated_cmds:
            continue
        if c["name"].lower() in ("brew", "git", "gh", "python3", "node", "npm", "pnpm", "uv",
                                 "uvx", "pipx", "mise", "cargo", "go", "rg", "fd", "fzf", "jq",
                                 "yq", "ffmpeg", "swiftc", "tmux", "docker", "kubectl", "colima"):
            continue
        drift.append(("命令", c["name"], c.get("version") or "", c["path"]))
    return missing, drift


def item_detail(it, facts):
    L = []
    for key, label in (("what", "是什么"), ("why", "为什么")):
        if it.get(key):
            L.append("%s： %s" % (label, it[key]))
    if it.get("where"):
        d = facts.dir_lookup(it["where"])
        if d and d["exists"]:
            L.append("位置：   %s（%s）" % (it["where"], mb(d.get("mb"))))
        else:
            L.append("位置：   " + it["where"])
    for c in it.get("run") or []:
        L.append("怎么跑： " + c)
    for c in it.get("stop") or []:
        L.append("怎么停： " + c)
    for c in it.get("config") or []:
        L.append("配置：   " + c)
    if it.get("note"):
        L.append("注意：   " + it["note"])
    if it.get("docs"):
        L.append("文档：   " + "、".join(it["docs"]))
    if it.get("confirmed") is False:
        L.append("（这条是按痕迹推断的，待确认）")
    return "\n".join(L)


def build_view(data, notes):
    facts = Facts(data)
    meta = data["meta"]
    items = notes["items"]
    missing, drift = coverage(notes, facts)

    def item_tab(title, sel):
        rows = []
        for it in sel:
            badges = resolve_status(it, facts)
            state = " ".join("%s%s" % ("[OK] " if b["ok"] else ("[!!] " if b["ok"] is False else "[--] "),
                                    b["label"]) for b in badges)
            bad = any(b["ok"] is False for b in badges)
            rows.append({
                "cells": [it.get("title", ""), state, it.get("where") or "",
                          (it.get("run") or [""])[0]],
                "tag": "bad" if bad else ("ok" if badges and all(b["ok"] for b in badges) else ""),
                "copy": it.get("where") or (it.get("run") or [""])[0],
                "detail": item_detail(it, facts),
            })
        return {"name": "%s (%d)" % (title, len(sel)),
                "hint": "选中一行看详情；Ctrl+C 复制路径；双击有路径的行在 Finder 里打开",
                "columns": [{"h": "条目", "w": 300}, {"h": "状态", "w": 320},
                            {"h": "位置", "w": 320}, {"h": "怎么跑", "w": 280}],
                "rows": rows}

    tabs = [
        item_tab("我装的", [i for i in items if i.get("installed_by", "me") == "me"]),
        item_tab("系统自带", [i for i in items if i.get("installed_by") == "system"]),
        item_tab("第三方", [i for i in items if i.get("installed_by") == "vendor"]),
    ]

    # 系统能力
    cap_rows = []
    for c in data.get("caps") or []:
        cap_rows.append({
            "cells": [c["name"], "OK" if c["ok"] else "—", c.get("detail") or ""],
            "tag": "ok" if c["ok"] else "warn",
            "copy": "",
            "detail": "%s\n%s" % (c["name"], c.get("detail") or "（无输出）"),
        })
    tabs.append({"name": "系统能力 (%d)" % len(cap_rows),
                 "hint": "这台机器「能做什么」：SIP、命令行工具、Rosetta、加密、Gatekeeper、备份",
                 "columns": [{"h": "能力", "w": 260}, {"h": "状态", "w": 80}, {"h": "详情", "w": 760}],
                 "rows": cap_rows})

    # 命令
    cmd_rows = []
    for c in data.get("commands") or []:
        cmd_rows.append({
            "cells": [c["name"], c.get("version") or "-", short(c["path"], 4)],
            "tag": "" if c.get("ok") else "warn",
            "copy": c["path"],
            "detail": "%s\n%s\n%s" % (c["name"], c.get("version") or "", c["path"]),
        })
    tabs.append({"name": "命令 (%d)" % len(cmd_rows),
                 "hint": "从 PATH 里解析出来的，版本是现跑的（每条都有超时，卡住不影响采集）",
                 "columns": [{"h": "命令", "w": 140}, {"h": "版本", "w": 380}, {"h": "路径", "w": 500}],
                 "rows": cmd_rows})

    # launchd
    ld_rows = []
    for x in data.get("launchd") or []:
        tag = "ok" if x["loaded"] else ("dim" if not x["backup"] else "warn")
        ld_rows.append({
            "cells": [x["label"], x["kind"],
                      "已加载" if x["loaded"] else "未加载",
                      ("开机" if x["run_at_load"] else "") + (" 常驻" if x["keep_alive"] else ""),
                      short(x.get("program") or "", 4)],
            "tag": tag,
            "copy": x["file"],
            "detail": "Label：%s\n类型：%s（%s）\nplist：%s\n程序：%s\nRunAtLoad：%s  KeepAlive：%s\n当前：%s%s" % (
                x["label"], x["kind"], x["domain"], x["file"], x.get("program") or "-",
                x["run_at_load"], x["keep_alive"], "已加载" if x["loaded"] else "未加载",
                "\n（这是个 .bak 备份文件，不会生效）" if x["backup"] else ""),
        })
    tabs.append({"name": "自启 (%d)" % len(ld_rows),
                 "hint": "launchd = macOS 的开机自启。橙色 = 只剩备份文件，或注册了没加载",
                 "columns": [{"h": "Label", "w": 330}, {"h": "类型", "w": 120},
                             {"h": "状态", "w": 90}, {"h": "触发", "w": 120},
                             {"h": "程序", "w": 360}],
                 "rows": ld_rows})

    # 端口
    port_rows = []
    for l in data.get("listeners") or []:
        scope = "仅本机" if l["addr"].startswith("127.") else ("所有网卡" if l["addr"] in ("*", "0.0.0.0") else l["addr"])
        port_rows.append({
            "cells": [str(l["port"]), scope, l["process"], short(l.get("path") or "", 4)],
            "tag": "ok" if scope == "仅本机" else "",
            "copy": l.get("path") or "",
            "detail": "端口 %s\n范围：%s\n进程：%s (pid %s)\n程序：%s" % (
                l["port"], scope, l["process"], l["pid"], l.get("path") or "-"),
        })
    tabs.append({"name": "端口 (%d)" % len(port_rows),
                 "hint": "「仅本机」= 外面连不进来，只能本机或经 SSH 隧道访问",
                 "columns": [{"h": "端口", "w": 90}, {"h": "范围", "w": 120},
                             {"h": "进程", "w": 200}, {"h": "程序", "w": 600}],
                 "rows": port_rows})

    # 目录
    dir_rows = []
    for d in data.get("dirs") or []:
        if not d["exists"]:
            continue
        kids = "、".join(c["name"] + ("/" if c["dir"] else "") for c in (d.get("children") or [])[:8])
        dir_rows.append({
            "cells": [mb(d.get("mb")), d["path"], kids + ("…" if len(d.get("children") or []) > 8 else "")],
            "tag": "warn" if d.get("partial") else "",
            "copy": d["path"],
            "detail": "%s\n%s%s\n里面：%s" % (d["path"], mb(d.get("mb")),
                                            "（3 秒预算内没走完，偏小）" if d.get("partial") else "", kids),
        })
    tabs.append({"name": "目录 (%d)" % len(dir_rows),
                 "hint": "我的仓库和工具目录；橙色 = 大小统计不全",
                 "columns": [{"h": "大小", "w": 100}, {"h": "路径", "w": 460},
                             {"h": "里面有什么", "w": 480}],
                 "rows": dir_rows})

    # brew
    brew_rows = []
    for f in (data.get("brew") or {}).get("formulae") or []:
        brew_rows.append({"cells": ["formula" if f.get("leaf") else "依赖", f["name"], f.get("version") or ""], "tag": "",
                          "copy": f["name"],
                          "detail": "brew formula: %s %s\n卸载：brew uninstall %s" % (f["name"], f.get("version") or "", f["name"])})
    for c in (data.get("brew") or {}).get("casks") or []:
        brew_rows.append({"cells": ["cask", c["name"], c.get("version") or ""], "tag": "",
                          "copy": c["name"],
                          "detail": "brew cask: %s %s\n卸载：brew uninstall --cask %s" % (c["name"], c.get("version") or "", c["name"])})
    brew_rows.sort(key=lambda r: (r["cells"][0], r["cells"][1]))
    tabs.append({"name": "Homebrew (%d)" % len(brew_rows),
                 "hint": "brew list 是这台机器上「装了什么」的权威来源；cask 对应 /Applications 里的 App",
                 "columns": [{"h": "类型", "w": 90}, {"h": "包名", "w": 380}, {"h": "版本", "w": 450}],
                 "rows": brew_rows})

    # 应用
    app_rows = []
    for a in data.get("apps") or []:
        if a["system"]:
            continue
        app_rows.append({
            "cells": [a["name"], a.get("version") or "", a.get("bundle_id") or "", a["path"]],
            "tag": "dim" if not a.get("version") else "",
            "copy": a["path"],
            "detail": "%s %s\n%s\n%s" % (a["name"], a.get("version") or "", a.get("bundle_id") or "", a["path"]),
        })
    tabs.append({"name": "应用 (%d)" % len(app_rows),
                 "hint": "只列非系统的 .app；灰色的没有版本号（多见于脚本封装的包）",
                 "columns": [{"h": "名称", "w": 280}, {"h": "版本", "w": 140},
                             {"h": "Bundle ID", "w": 340}, {"h": "路径", "w": 280}],
                 "rows": app_rows})

    # 凭据
    cred_rows = []
    for k in (data.get("ssh") or {}).get("keys") or []:
        cred_rows.append({"cells": [k["file"], "SSH 私钥", k.get("detail") or ""], "tag": "",
                          "copy": k["file"],
                          "detail": "%s\n%s\n\n（只列指纹和备注名，不读内容）" % (k["file"], k.get("detail") or "")})
    for h in (data.get("ssh") or {}).get("hosts") or []:
        cred_rows.append({"cells": ["~/.ssh/config", "Host", h], "tag": "", "copy": h,
                          "detail": "ssh config 里的 Host 条目：%s" % h})
    tabs.append({"name": "凭据 (%d)" % len(cred_rows),
                 "hint": "只列位置、类型、指纹备注，不含任何密钥内容",
                 "columns": [{"h": "位置", "w": 340}, {"h": "类型", "w": 130}, {"h": "说明", "w": 570}],
                 "rows": cred_rows})

    # 未登记
    drift_rows = []
    for it in missing:
        drift_rows.append({"cells": ["说明里有、机器上没有", it.get("title", ""), it.get("where", "")],
                           "tag": "bad", "copy": it.get("where", ""),
                           "detail": "清单过时了，或者东西被删了"})
    for kind, name, extra, path in drift:
        drift_rows.append({"cells": [kind, name, str(extra), short(path, 4)], "tag": "warn",
                           "copy": path or "",
                           "detail": "%s：%s\n%s\n\n要登记它：改 notes/inventory-notes.json 再跑 mac-inventory --render-only" % (
                               kind, name, path or "")})
    tabs.append({"name": "未登记 (%d)" % len(drift_rows),
                 "hint": "机器上有、说明里没有 —— 这一段是给清单做维护用的，为空说明清单跟得上",
                 "columns": [{"h": "类型", "w": 180}, {"h": "名称", "w": 380},
                             {"h": "补充", "w": 150}, {"h": "位置", "w": 330}],
                 "rows": drift_rows})

    return {
        "title": "%s · 这台 Mac 上有什么" % meta["hostname"].split(".")[0],
        "subtitle": "%s · %s (%s) · %s · %s 核 / %s GB · 磁盘空闲 %s GB · 采集于 %s" % (
            meta["user"], meta["os"], meta["os_build"], meta["cpu"], meta["cores"],
            meta["ram_gb"], meta["disk_free_gb"], meta["collected_at"]),
        "footer": "%d 条说明 · 未登记 %d 条 · 采集只读" % (len(items), len(drift) + len(missing)),
        "tabs": tabs,
    }


def build_md(data, notes):
    facts = Facts(data)
    meta = data["meta"]
    L = ["# %s · 这台 Mac 上有什么" % meta["hostname"], "",
         "> %s · %s · %s · %s 核 / %s GB · 采集于 %s" % (
             meta["user"], meta["os"], meta["cpu"], meta["cores"], meta["ram_gb"], meta["collected_at"]), ""]
    for group, title in (("me", "## 我装的"), ("system", "## 系统自带"), ("vendor", "## 第三方")):
        sel = [i for i in notes["items"] if i.get("installed_by", "me") == group]
        if not sel:
            continue
        L += [title, ""]
        for it in sel:
            badges = resolve_status(it, facts)
            state = " ".join(("OK" if b["ok"] else ("!!" if b["ok"] is False else "--")) + " " + b["label"]
                             for b in badges)
            L.append("### %s" % it.get("title", "?"))
            for k, lab in (("where", "位置"), ("what", "是什么"), ("why", "为什么")):
                if it.get(k):
                    L.append("- %s：%s" % (lab, it[k]))
            for c in it.get("run") or []:
                L.append("- 怎么跑：`%s`" % c)
            for c in it.get("stop") or []:
                L.append("- 怎么停：`%s`" % c)
            if it.get("note"):
                L.append("- 注意：%s" % it["note"])
            if state:
                L.append("- 现况：%s" % state)
            L.append("")
    missing, drift = coverage(notes, facts)
    if missing or drift:
        L += ["## 未登记 / 不一致", ""]
        for it in missing:
            L.append("- 说明里有、机器上没有：%s" % it.get("title"))
        for kind, name, extra, path in drift:
            L.append("- %s：%s %s" % (kind, name, extra or ""))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--view", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args(argv)
    with open(args.json, encoding="utf-8") as f:
        data = normalize(json.load(f))
    with open(args.notes, encoding="utf-8") as f:
        notes = normalize(json.load(f))
    view = build_view(data, notes)
    if args.view:
        with open(args.view, "w", encoding="utf-8") as f:
            json.dump(view, f, ensure_ascii=False, indent=1)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(build_md(data, notes) + "\n")
    print("%s · %d 个标签页 · %d 条说明 · 未登记 %s 条" % (
        view["title"], len(view["tabs"]), len(notes["items"]),
        [t["name"] for t in view["tabs"] if t["name"].startswith("未登记")][0].split("(")[1].rstrip(")")))
    if args.view:
        print("view: %s (%d KB)" % (args.view, os.path.getsize(args.view) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
