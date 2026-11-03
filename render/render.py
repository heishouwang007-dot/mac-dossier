#!/usr/bin/env python3
"""Merge facts + hand-written notes into a view JSON (and markdown).

The viewer (`app/`) is a dumb renderer of inventory-view.json; all semantic
work — status words, badges, tab taxonomy, drift detection — happens here so
that content changes never touch UI code.
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
    "tasks", "covers", "pipx", "npm", "uv_python", "mise", "http", "lines", "keys", "running_apps",
"numbers", "services", "caps", "warns",
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
        self.http = {int(x["port"]): x for x in (d.get("http") or [])}

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
                           "detail": ("%s（pid %s）" % (st.get("process"), st.get("pid"))) if st["up"] else "没有程序在监听"})
        elif kind == "launchd":
            x = facts.launchd.get(arg.lower())
            if not x:
                badges.append({"ok": False, "label": arg, "detail": "没有这个 plist"})
            else:
                badges.append({"ok": x["loaded"], "label": "开机自启",
                               "detail": "已加载" if x["loaded"] else "没在运行（plist 在，进程没跑）"})
        elif kind == "dir":
            d = facts.dir_lookup(arg)
            if d is None:
                badges.append({"ok": None, "label": short(arg, 2), "detail": "未采集"})
            elif not d["exists"]:
                badges.append({"ok": False, "label": "文件位置", "detail": "目录不存在（东西被删/移走？）"})
            else:
                badges.append({"ok": True, "label": "文件存在",
                               "detail": "%s%s" % (mb(d.get("mb")), "（统计不全）" if d.get("partial") else "")})
        elif kind == "cmd":
            c = facts.cmds.get(arg.lower())
            badges.append({"ok": bool(c), "label": "命令可用", "detail": c["path"] if c else "PATH 里找不到"})
        elif kind == "brew":
            f = facts.formulae.get(arg.lower())
            ck = facts.casks.get(arg.lower())
            hit = f or ck
            badges.append({"ok": bool(hit), "label": "brew 已装",
                           "detail": (hit or {}).get("version", "") or ("未安装" if not hit else "")})
        elif kind == "app":
            hit = next((a for a in facts.apps if a["bundle"].lower() == arg.lower()), None)
            badges.append({"ok": bool(hit), "label": "应用已装",
                           "detail": hit["path"] if hit else "不在 /Applications 里"})
        elif kind == "probe":
            h = facts.http.get(int(arg))
            if h is None:
                badges.append({"ok": None, "label": "探活", "detail": "未采集"})
            else:
                badges.append({
                    "ok": h["ok"],
                    "label": "%s 服务正常" % arg,
                    "detail": (h.get("detail") or ""),
                })
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


def item_detail(it, facts, badges=None, broken=None):
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
    if badges:
        L.append("检查：")
        for b in badges:
            mark = "\u2713" if b["ok"] else ("\u2717" if b["ok"] is False else "\u25d0")
            extra = b.get("detail") or ""
            L.append("  %s %s%s" % (mark, b["label"], ("\uff0c%s" % extra) if extra else ""))
    if broken:
        L.append("注意：以上 \u2717 的项就是「异常」的原因。")
    if it.get("confirmed") is False:
        L.append("（这条是按痕迹推断的，待确认）")
    return "\n".join(L)


def state_word(badges):
    """把一堆探针结果折叠成用户语言：● 正常 / ● 异常 / ◐ 部分未检查。
    探针的含义用户不需要知道；词和点的颜色已经说完了。"""
    if not badges:
        return "\u2014 未检查", "", []
    if any(b["ok"] is False for b in badges):
        return "\u25cf 异常", "bad", [b for b in badges if b["ok"] is False]
    unknown = [b for b in badges if b["ok"] is None]
    if unknown:
        return "\u25d0 部分未检查", "dim", unknown
    return "\u25cf 正常", "ok", []


def build_dashboard(data, notes, facts, missing, drift):
    """看板层：不做新采集，只把已有事实折叠成一屏能看懂的形状。

    rules：
      numbers  头部数字 chips
      services 说明层里带 port/probe 探针的条目 → 卡片（状态点 + 一句话）
      caps     系统能力点名
      warns    要注意的事：未登记、能力缺失、探活失败
    """
    d = data
    loaded = [x for x in d.get("launchd") or [] if x["loaded"] and not x["backup"]]
    m = d["meta"]

    numbers = [
        {"n": "%d" % len([f for f in d["brew"]["formulae"] if f.get("leaf")]),
         "label": "brew 自装"},
        {"n": "%d" % len(d["brew"]["casks"]), "label": "brew cask"},
        {"n": "%d" % len(d["apps"]), "label": "应用"},
        {"n": "%d" % len(loaded), "label": "自启已加载"},
        {"n": "%d" % len(d["listeners"]), "label": "监听端口"},
        {"n": "%s GB" % m["disk_free_gb"], "label": "磁盘空闲"},
    ]

    services = []
    for it in notes["items"]:
        # 看板右卡只回答一件事：这个服务现在跑着吗、健康吗。
        # 开机自启的罗列在明细「开机自启」标签里，这里不重复整段
        # launchd 清单，只在每个服务下面括号标注它是自启还是手动。
        if it.get("group") != "服务与自启" or it.get("id") == "launchd-local":
            continue
        probes = [p for p in (it.get("status") or []) if p.startswith(("port:", "probe:", "launchd:", "app:"))]
        if not probes:
            continue
        badges = resolve_status(it, facts)
        word, tag, _ = state_word(badges)
        services.append({
            "title": it.get("title", ""),
            "state": tag,
            "line": word.replace("\u25cf ", "").replace("\u25d0 ", "").replace("\u2014 ", ""),
            "note": it.get("startmode") or "",
            "where": it.get("where") or "",
        })

    caps = [{"name": c["name"], "ok": c["ok"]} for c in d.get("caps") or []]

    warns = []
    if drift or missing:
        warns.append("未登记 %d 条（明细里看，改 notes/inventory-notes.json）" % (len(drift) + len(missing)))
    for c in d.get("caps") or []:
        if not c["ok"]:
            warns.append("%s：%s" % (c["name"], c.get("detail") or "未开启"))
    for h in d.get("http") or []:
        if not h["ok"]:
            warns.append("通道 %d 探活失败：%s" % (h["port"], (h.get("detail") or "")[:60]))
    for x in d.get("launchd") or []:
        if x["backup"] and x["loaded"]:
            warns.append("备份文件 %s 处于加载态" % x["label"])

    return {"numbers": numbers, "services": services, "caps": caps, "warns": warns,
            "apps": d.get("running_apps") or []}


def build_view(data, notes):
    facts = Facts(data)
    meta = data["meta"]
    items = notes["items"]
    missing, drift = coverage(notes, facts)

    # 「全部」= 归总索引页：21 条说明条目都在这，首列是分类。
    # 后面的标签全是事实表；分组细节页取消（搜索按分类名过滤即可）。
    order = ["服务与自启", "工具与运行时", "项目与配置", "学校授权"]
    all_rows = []
    for g in order:
        for it in items:
            if it.get("group", "项目与配置") != g:
                continue
            badges = resolve_status(it, facts)
            word, tag, broken = state_word(badges)
            all_rows.append({
                "cells": [g, it.get("title", ""), word, it.get("where") or ""],
                "tag": tag,
                "copy": it.get("where") or (it.get("run") or [""])[0],
                "detail": item_detail(it, facts, badges, broken),
            })
    tabs = [{
        "name": "全部 (%d)" % len(all_rows),
        "hint": "这台 Mac 的全部登记条目，按分类分段 · 状态：\u25cf 绿=正常 \u25cf 红=异常 "
                "\u25d0 灰=部分未检查 \u2014 没=没查 · 搜索可按分类名过滤 · 点行看底部完整说明",
        "columns": [{"h": "分类", "w": 130}, {"h": "条目", "w": 310},
                    {"h": "状态", "w": 100}, {"h": "位置", "w": 380}],
        "rows": all_rows,
    }]

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
    tabs.append({"name": "命令行 (%d)" % len(cmd_rows),
                 "hint": "敲一个命令名，实际执行的是哪个文件（版本实时试跑出来的）· "
                         "橙色 = 探测异常 · 交互式工具不参与版本探测",
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
    tabs.append({"name": "开机自启 (%d)" % len(ld_rows),
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
    tabs.append({"name": "运行中 (%d)" % len(port_rows),
                 "hint": "现在正在监听的端口，即当前真的在跑的服务顺便看进程 · "
                         "「仅本机」= 外面连不进来，只能本机或经 SSH 隧道访问",
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
    tabs.append({"name": "目录占用 (%d)" % len(dir_rows),
                 "hint": "我建的东西放在哪、各占多少磁盘（比如你最大的那个项目仓库）· "
                         "橙色 = 大小统计不全（3 秒预算没走完，实际只会更大）",
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
    # 「安装的软件」= brew 自装 + brew App + 应用 + brew 依赖 合一张表，
    # 按来源分段（依赖垫底、灰色——它们不是我装的，是上面这些东西拖进来的）
    soft_rows = []
    for f in (data.get("brew") or {}).get("formulae") or []:
        if not f.get("leaf"):
            continue
        soft_rows.append({"cells": ["brew 自装", f["name"], f.get("version") or "", ""],
                          "tag": "", "copy": f["name"],
                          "detail": "brew formula\n卸载：brew uninstall %s" % f["name"]})
    for c in (data.get("brew") or {}).get("casks") or []:
        soft_rows.append({"cells": ["brew App", c["name"], c.get("version") or "", "→ /Applications"],
                          "tag": "", "copy": c["name"],
                          "detail": "brew cask\n卸载：brew uninstall --cask %s" % c["name"]})
    for a in data.get("apps") or []:
        if a["system"]:
            continue
        soft_rows.append({
            "cells": ["应用", a["name"], a.get("version") or "-", a.get("bundle_id") or ""],
            "tag": "dim" if not a.get("version") else "",
            "copy": a["path"],
            "detail": "%s\n%s\n%s" % (a["name"], a.get("bundle_id") or "", a["path"]),
        })
    dep_rows = []
    for f in (data.get("brew") or {}).get("formulae") or []:
        if f.get("leaf"):
            continue
        dep_rows.append({"cells": ["brew 依赖", f["name"], f.get("version") or "", ""],
                         "tag": "dim", "copy": f["name"],
                         "detail": "%s 是上面某个包的依赖，不是单独装的\n卸载：brew uninstall %s"
                                   % (f["name"], f["name"])})

    # 其他包管理器（与 brew 平行的真·安装来源，brew list / /Applications 都看不见它们）
    pkgs = data.get("packages") or {}
    for line in pkgs.get("mise") or []:
        parts = line.split()
        if len(parts) >= 2:
            soft_rows.append({"cells": ["mise", parts[0], parts[1], "mise 管理"],
                              "tag": "", "copy": parts[0],
                              "detail": "mise 管理的工具\n升级：mise upgrade %s\n卸载：mise rm %s"
                                        % (parts[0], parts[0])})
    for line in pkgs.get("npm") or []:
        name = line.strip("\u251c\u2500\u2500 \u2514\u2500\u2500 ").strip()
        if "@" in name[1:]:
            pkg, ver = name.rsplit("@", 1)
            soft_rows.append({"cells": ["npm 全局", pkg, ver, ""],
                              "tag": "", "copy": pkg,
                              "detail": "npm 全局包\n升级：npm i -g %s@latest\n卸载：npm rm -g %s" % (pkg, pkg)})
    for line in pkgs.get("pipx") or []:
        parts = line.split()
        if parts:
            soft_rows.append({"cells": ["pipx", parts[0], parts[1] if len(parts) > 1 else "", ""],
                              "tag": "", "copy": parts[0],
                              "detail": "pipx 独立 Python 环境\n卸载：pipx uninstall %s" % parts[0]})
    # uv python list 列的是「uv 能看到的所有 Python」——brew/系统装的也在内，
    # 重复计入会是假账。只保留 uv 自己装的（路径在 ~/.local/share/uv 下）。
    seen_uv = set()
    for line in pkgs.get("uv_python") or []:
        if "<download available>" in line or "/.local/share/uv/" not in line:
            continue
        parts = line.split()
        if not parts or parts[0] in seen_uv:
            continue
        seen_uv.add(parts[0])
        soft_rows.append({"cells": ["uv 运行时", parts[0], "", line[line.find("/"):] if "/" in line else ""],
                          "tag": "", "copy": parts[0],
                          "detail": "uv 自己装的 Python 运行时（brew/系统的那份不算）\n"
                                  "卸载：uv python uninstall %s" % parts[0]})
    soft_rows.extend(dep_rows)   # 依赖垫底：不是用户装的，放最后不抢视线
    tabs.append({"name": "安装的软件 (%d)" % len(soft_rows),
                 "hint": "这台机器装过的全部软件，按来源分段 · 灰色的 brew 依赖不是你装的，"
                         "是其他包拖进来的 · 学校授权的 Office/Xmind 不在 brew 里，"
                         "见「全部」页学校授权分类",
                 "columns": [{"h": "来源", "w": 110}, {"h": "名称", "w": 380},
                             {"h": "版本", "w": 180}, {"h": "说明", "w": 300}],
                 "rows": soft_rows})

    # 凭据
    cred_rows = []
    for k in (data.get("ssh") or {}).get("keys") or []:
        cred_rows.append({"cells": [k["file"], "SSH 私钥", k.get("detail") or ""], "tag": "",
                          "copy": k["file"],
                          "detail": "%s\n%s\n\n（只列指纹和备注名，不读内容）" % (k["file"], k.get("detail") or "")})
    for h in (data.get("ssh") or {}).get("hosts") or []:
        cred_rows.append({"cells": ["~/.ssh/config", "Host", h], "tag": "", "copy": h,
                          "detail": "ssh config 里的 Host 条目：%s" % h})
    # 最后一个标签 = 私有凭据 + 未登记（用户拍板合并；敏感和"账实对不上"都属"要盯着的"）
    priv_rows = []
    for k in (data.get("ssh") or {}).get("keys") or []:
        priv_rows.append({"cells": ["SSH 私钥", k["file"], k.get("detail") or ""],
                          "tag": "", "copy": k["file"],
                          "detail": "%s\n%s\n\n（只列指纹和备注名，不读内容）" % (k["file"], k.get("detail") or "")})
    for h in (data.get("ssh") or {}).get("hosts") or []:
        priv_rows.append({"cells": ["SSH Host", "~/.ssh/config", h], "tag": "", "copy": h,
                          "detail": "ssh config 里的 Host 条目：%s" % h})
    for it in missing:
        priv_rows.append({"cells": ["未登记 · 应该没有", it.get("title", ""), it.get("where", "")],
                          "tag": "bad", "copy": it.get("where", ""),
                          "detail": "清单里说有这东西，但机器上找不到 —— 清单过时了，或者东西被删了"})
    for kind, name, extra, path in drift:
        priv_rows.append({"cells": ["未登记", name, str(extra)], "tag": "warn",
                          "copy": path or "",
                          "detail": "机器上有、说明里没有（%s）\n%s\n\n要登记它：改 notes/inventory-notes.json "
                                    "再跑 mac-inventory --render-only" % (kind, path or "")})
    tabs.append({"name": "私有凭据及未登记 (%d)" % len(priv_rows),
                 "hint": "私钥只显示指纹，不含密钥内容 · 「未登记」= 机器上有但说明里没写的，"
                         "红色是说明过时，橙色是漏登记 —— 都是 0 最好",
                 "columns": [{"h": "类型", "w": 200}, {"h": "名称 / 位置", "w": 440},
                             {"h": "说明", "w": 420}],
                 "rows": priv_rows})

    return {
        "title": "Mac 档案",
        # 副标题只留五个有人看的字段，统一「值 · 值」节奏；磁盘过 1 TB 就用 TB
        "subtitle": "%s · %s 核 / %s GB · %s · 磁盘空闲 %s · 采集于 %s" % (
            meta["cpu"], meta["cores"], int(meta["ram_gb"]), meta["os"],
            ("%.2f TB" % (meta["disk_free_gb"] / 1024.0)) if meta["disk_free_gb"] >= 1024
                else "%s GB" % meta["disk_free_gb"],
            meta["collected_at"]),
        "footer": "%d 条说明 · 未登记 %d 条 · 采集只读" % (len(items), len(drift) + len(missing)),
        "dashboard": build_dashboard(data, notes, facts, missing, drift),
        # 叙事顺序：是什么 → 装了什么 → 能用哪些命令 → 正在跑什么 → 什么会自启
        #           → 机器底子 → 东西在哪 → 要盯着的（凭据与漂移）
        "tabs": [t for k in ("全部", "安装的软件", "命令行", "运行中",
                             "开机自启", "系统能力", "目录占用", "私有凭据")
                 for t in tabs if t["name"].startswith(k)]
                + [t for t in tabs if not t["name"].startswith(
                    ("全部", "安装的软件", "命令行", "运行中", "开机自启",
                     "系统能力", "目录占用", "私有凭据"))],
    }


def build_md(data, notes):
    facts = Facts(data)
    meta = data["meta"]
    L = ["# %s · 这台 Mac 上有什么" % meta["hostname"], "",
         "> %s · %s · %s · %s 核 / %s GB · 采集于 %s" % (
             meta["user"], meta["os"], meta["cpu"], meta["cores"], meta["ram_gb"], meta["collected_at"]), ""]
    for g in ("服务与自启", "工具与运行时", "项目与配置", "学校授权"):
        sel = [i for i in notes["items"] if i.get("group") == g]
        if not sel:
            continue
        L += ["## " + g, ""]
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
        [t["name"] for t in view["tabs"] if "未登记" in t["name"]][0].split("(")[1].rstrip(")")))
    if args.view:
        print("view: %s (%d KB)" % (args.view, os.path.getsize(args.view) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
