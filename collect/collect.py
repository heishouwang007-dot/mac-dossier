#!/usr/bin/env python3
"""Read-only fact collector for Mac Dossier (Mac 档案).

Gathers what this machine has and where: Homebrew packages, /Applications
bundles, LaunchAgents, listening ports, PATH-resolved commands, directory
sizes, local-service health probes, and a Brewfile snapshot. Never mutates
anything; every external command is time-boxed.

Output: UTF-8 JSON consumed by render/render.py.

设计约束（照抄 Windows 那套的结论）：
  * 只采集事实，不写说明。「为什么装、怎么跑」猜不出来，那部分在 notes/ 里人工写。
  * 只读：不装、不改、不重启。唯一会 spawn 的是 `--version` 这类查询，且**一律带超时**
    （Windows 那边被 `ssh --version` 挂死过一次，5 分钟才 kill 掉）。
  * 输出 UTF-8 JSON，字段名稳定 —— 渲染器和 SwiftUI 界面都按这个 schema 读。

用法：
  collect/collect.py [--out ~/.cache/mac-inventory/inventory.json]
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
BREW = "/opt/homebrew"

# 单条外部命令的超时：采集卡住比采不全更糟
CMD_TIMEOUT = 8


def run(cmd, timeout=CMD_TIMEOUT, cwd=None):
    """跑一条命令，返回 (rc, stdout)。超时/失败一律返回空串，绝不抛。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, errors="replace")
        return p.returncode, (p.stdout or "").strip()
    except Exception:
        return -1, ""


def run_lines(cmd, **kw):
    rc, out = run(cmd, **kw)
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


def du_mb(path, budget=3.0):
    """目录大小（MB），时间预算内跑不完就返回部分值 + partial 标记。"""
    if not os.path.exists(path):
        return None
    t0 = time.time()
    total = 0
    partial = False
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        if time.time() - t0 > budget:
            partial = True
            break
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return {"mb": round(total / 1048576.0, 1), "partial": partial}


def meta():
    def sysctl(k):
        return run(["sysctl", "-n", k])[1]

    st = os.statvfs("/")
    free_gb = round(st.f_bavail * st.f_frsize / 1073741824.0, 1)
    used_gb = round((st.f_blocks - st.f_bfree) * st.f_frsize / 1073741824.0, 1)
    m = re.search(r"sec\s*=\s*(\d+)", sysctl("kern.boottime"))
    uptime_h = round((time.time() - int(m.group(1))) / 3600.0, 1) if m else None
    return {
        "hostname": os.uname().nodename,
        "user": os.environ.get("USER", ""),
        "os": "macOS " + run(["sw_vers", "-productVersion"])[1],
        "os_build": run(["sw_vers", "-buildVersion"])[1],
        "model": sysctl("hw.model"),
        "arch": os.uname().machine,
        "cpu": sysctl("machdep.cpu.brand_string") or sysctl("hw.model"),
        "cores": int(sysctl("hw.ncpu") or 0) or None,
        "ram_gb": round(int(sysctl("hw.memsize") or 0) / 1073741824.0, 1),
        "disk_free_gb": free_gb,
        "disk_used_gb": used_gb,
        "uptime_hours": uptime_h,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "collector": "collect/collect.py",
    }


def system_caps():
    """系统级能力：不是「装了什么 App」，而是「这台机器能做什么」。"""
    caps = []

    def add(name, ok, detail=""):
        caps.append({"name": name, "ok": bool(ok), "detail": detail})

    rc, sip = run(["csrutil", "status"])
    add("SIP（系统完整性保护）", "enabled" in sip.lower(), sip.replace("System Integrity Protection status: ", ""))

    xp = run(["xcode-select", "-p"])[1]
    add("Xcode 命令行工具", bool(xp), xp)

    rosetta = run(["arch", "-x86_64", "/usr/bin/true"])[0] == 0
    add("Rosetta 2（跑 Intel 程序）", rosetta, "arch -x86_64 /usr/bin/true")

    rc, fv = run(["fdesetup", "status"])
    add("FileVault 磁盘加密", "On" in fv, fv)

    rc, gk = run(["spctl", "--status"])
    add("Gatekeeper", "enabled" in gk.lower(), gk)

    add("Homebrew", os.path.exists(BREW + "/bin/brew"), BREW)

    rc, tm = run(["tmutil", "destinationinfo"])
    add("Time Machine", "No destinations" not in tm and bool(tm), "已配置备份目标" if tm and "No destinations" not in tm else "没有备份目标")

    rc, arch = run(["softwareupdate", "--list"]) if False else (0, "")
    return caps


def brew_packages():
    formulae, casks, taps = [], [], []
    if not os.path.exists(BREW + "/bin/brew"):
        return {"formulae": formulae, "casks": casks, "taps": taps}

    # brew leaves = 我显式装的顶层包；其余 110 条里大多是它们的依赖。
    # 不区分的话「未登记」会被依赖淹没（实测 110 条 formula 里只有 20 多条是 leaves）。
    leaves = set(run_lines([BREW + "/bin/brew", "leaves"], timeout=20))
    for line in run_lines([BREW + "/bin/brew", "list", "--formula", "--versions"], timeout=25):
        parts = line.split()
        if parts:
            formulae.append({"name": parts[0], "version": " ".join(parts[1:]),
                             "leaf": parts[0] in leaves})

    # cask 的安装路径在 Caskroom/<name>/<version>，据此反推 /Applications 里的 .app
    for name in run_lines([BREW + "/bin/brew", "list", "--cask"], timeout=25):
        ver = ""
        caskroom = os.path.join(BREW, "Caskroom", name)
        if os.path.isdir(caskroom):
            vers = [v for v in os.listdir(caskroom) if not v.startswith(".")]
            ver = vers[0] if vers else ""
        casks.append({"name": name, "version": ver})

    taps = run_lines([BREW + "/bin/brew", "tap"], timeout=15)
    return {"formulae": formulae, "casks": casks, "taps": taps}


def read_plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def apps():
    """扫描 .app bundle：/Applications、~/Applications、/System/Applications。

    /System/Applications 是系统自带的（Finder/Mail…），单独标 system=true，
    渲染时折叠，不然一眼全是苹果的 App。
    """
    out = []
    roots = [("/Applications", False), (os.path.join(HOME, "Applications"), False),
             ("/System/Applications", True)]
    for root, system in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if not entry.endswith(".app"):
                continue
            p = os.path.join(root, entry)
            info = read_plist(os.path.join(p, "Contents", "Info.plist")) or {}
            out.append({
                "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or entry[:-4],
                "bundle": entry[:-4],
                "version": info.get("CFBundleShortVersionString") or "",
                "build": info.get("CFBundleVersion") or "",
                "bundle_id": info.get("CFBundleIdentifier") or "",
                "path": p,
                "system": system,
                "executable": info.get("CFBundleExecutable") or "",
            })
    return out


def launch_items():
    """launchd 的 LaunchAgents/Daemons —— 这是 macOS 上的「开机自启」。"""
    items = []
    dirs = [
        (os.path.join(HOME, "Library", "LaunchAgents"), "user-agent", "gui"),
        ("/Library/LaunchAgents", "global-agent", "gui"),
        ("/Library/LaunchDaemons", "daemon", "system"),
    ]
    # launchctl list 里能看到哪些真的加载了
    loaded = set()
    for line in run_lines(["launchctl", "list"], timeout=10):
        parts = line.split("\t")
        if len(parts) >= 3:
            loaded.add(parts[2].strip())
    for d, kind, domain in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".plist"):
                continue
            p = os.path.join(d, f)
            pl = read_plist(p) or {}
            label = pl.get("Label") or f[:-6]
            prog = pl.get("Program") or ""
            if not prog and pl.get("ProgramArguments"):
                prog = " ".join(pl["ProgramArguments"])
            items.append({
                "label": label,
                "file": p,
                "kind": kind,
                "domain": domain,
                "program": prog,
                "run_at_load": bool(pl.get("RunAtLoad")),
                "keep_alive": bool(pl.get("KeepAlive")),
                "loaded": label in loaded,
                "backup": f.endswith(".bak") or ".bak-" in f,
            })
    return items


def listeners():
    """谁在听端口。lsof 的 NAME 列形如 127.0.0.1:8802 或 *:5000。"""
    out = []
    seen = set()
    for line in run_lines(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=15)[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        cmd, pid, name = parts[0], parts[1], parts[8]
        m = re.match(r"^(\S+):(\d+)$", name)
        if not m:
            continue
        addr, port = m.group(1), int(m.group(2))
        key = (port, pid)
        if key in seen:
            continue
        seen.add(key)
        exe = run(["ps", "-o", "comm=", "-p", pid])[1]
        out.append({"port": port, "addr": addr, "pid": int(pid),
                    "process": cmd, "path": exe})
    return sorted(out, key=lambda x: x["port"])


def path_entries():
    dirs = []
    seen = set()
    for d in os.environ.get("PATH", "").split(":"):
        if not d or d in seen:
            continue
        seen.add(d)
        exists = os.path.isdir(d)
        bins = []
        if exists:
            try:
                bins = [f for f in sorted(os.listdir(d))
                        if not f.startswith(".") and os.access(os.path.join(d, f), os.X_OK)][:12]
            except OSError:
                pass
        owner = "user" if d.startswith(HOME) else ("brew" if d.startswith(BREW) else "system")
        dirs.append({"dir": d, "exists": exists, "owner": owner, "bins": bins})
    return dirs


# 交互式/TUI 工具：不存在 --version 这个概念，实跑探测只会误报黄色
NO_VERSION = {"hermes", "anysearch"}

PROBE = [
    ("brew", ["--version"]), ("git", ["--version"]), ("gh", ["--version"]),
    ("python3", ["-V"]), ("node", ["-v"]), ("npm", ["-v"]), ("pnpm", ["-v"]),
    ("uv", ["--version"]), ("uvx", ["--version"]), ("pipx", ["--version"]),
    ("mise", ["--version"]), ("cargo", ["--version"]), ("go", ["version"]),
    ("rg", ["--version"]), ("fd", ["--version"]), ("fzf", ["--version"]),
    ("jq", ["--version"]), ("yq", ["--version"]), ("ffmpeg", ["-version"]),
    ("soffice", ["--version"]), ("officecli", ["--version"]),
    ("ketch", ["--version"]), ("anysearch", ["--version"]), ("repo-search", ["--help"]),
    ("browser-act", ["--version"]), ("omlx", ["--version"]),
    ("claude", ["--version"]), ("grok", ["--version"]), ("hermes", ["--version"]),
    ("agent", ["--version"]), ("codex", ["--version"]), ("qoderclicn", ["--version"]),
    ("hf", ["version"]), ("docker", ["--version"]), ("colima", ["version"]),
    ("kubectl", ["version", "--client"]), ("swiftc", ["--version"]),
    ("llama-server", ["--version"]), ("tmux", ["-V"]), ("rg", ["--version"]),
]


def commands():
    out = []
    for name, args in PROBE:
        path = shutil.which(name)
        if not path:
            continue
        if name in NO_VERSION:
            # 能解析到、可执行，就说明它是好的；版本列写人话，不跑探测
            out.append({"name": name, "path": path,
                        "version": "交互式工具（不响应 --version）", "ok": True})
            continue
        rc, ver = run([path] + args, timeout=6)
        first = ver.splitlines()[0].strip() if ver else ""
        out.append({"name": name, "path": path, "version": first[:80], "ok": rc == 0})
    # 去重（rg 在 PROBE 里出现两次）
    uniq, seen = [], set()
    for c in out:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        uniq.append(c)
    return uniq


def brewfile():
    """brew bundle dump --file=- ：整机的声明式清单（cask + tap + mas + whalebrew）。

    两个用途：这份文本本身就能 `brew bundle` 一键复装；diff 两次快照能看出
    「上次登记之后新装/卸了什么」——比 brew leaves 的瞬时状态多一个时间轴。
    """
    rc, out = run([BREW + "/bin/brew", "bundle", "dump", "--file=-"],
                  timeout=30)
    lines = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    return {"ok": rc == 0, "lines": lines}


def http_checks():
    """本地通道的真实探活（Homepage 面板的做法）：端口在听 ≠ 服务可用。

    8802/8810 走 /health 的 JSON；7897 是 SOCKS/HTTP 混合口，用 TCP 握手判活。
    """
    import socket
    import urllib.request
    out = []
    for port, path in ((8802, "/health"), (8810, "/health")):
        r = {"port": port, "kind": "http", "ok": False, "detail": ""}
        try:
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
            with urllib.request.urlopen(req, timeout=4) as resp:
                body = resp.read(512).decode("utf-8", "replace")
            r["ok"] = True
            r["detail"] = '"%s"...' % body[:80]
        except Exception as e:
            r["detail"] = repr(e)[:90]
        out.append(r)
    # 9097 是远程桌面 mihomo 的控制口，本机 Clash Verge 不用它
    for port in (7897,):
        r = {"port": port, "kind": "tcp", "ok": False, "detail": ""}
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                r["ok"] = True
                r["detail"] = "TCP 握手成功"
        except Exception as e:
            r["detail"] = repr(e)[:90]
        out.append(r)
    return out


def running_apps():
    """现在真的开着的应用（看板用）。System Events 只列非 background-only 的
    应用进程，再滤掉窗口服务级的系统件。"""
    rc, out = run(["osascript", "-e",
                   "tell application \"System Events\" to get name of every "
                   "application process whose background only is false"],
                  timeout=10)
    names = [x.strip() for x in out.split(",") if x.strip()] if rc == 0 else []
    sys_noise = {"Dock", "Window Server", "SystemUIServer", "Spotlight",
                 "ControlCenter", "Siri", "universalAccessAuthWarn",
                 "universalaccessd", "TextInputMenuAgent", "CoreLocationAgent"}
    return sorted({n for n in names if n not in sys_noise and n[0:1].isprintable()})


def package_managers():
    res = {}
    rc, out = run(["pipx", "list", "--short"], timeout=20)
    res["pipx"] = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    rc, out = run(["npm", "ls", "-g", "--depth=0"], timeout=20)
    res["npm"] = [l.strip() for l in out.splitlines() if "@" in l and "node_modules" not in l] if rc == 0 else []
    rc, out = run(["uv", "python", "list"], timeout=15)
    res["uv_python"] = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
    rc, out = run(["mise", "ls", "--global"], timeout=15)
    res["mise"] = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
    return res


DIR_ROOTS = [
    os.path.join(HOME, "tongji-llm-api"),
    os.path.join(HOME, "tongji-vpn"),
    os.path.join(HOME, "tongji-vdi-ssh"),
    os.path.join(HOME, "tongji-vdi-macos-client-patch"),
    os.path.join(HOME, "clash-stack"),
    os.path.join(HOME, "clash-verge-backup"),
    os.path.join(HOME, "clash-verge-fork"),
    os.path.join(HOME, "mac-定制"),
    os.path.join(HOME, "docker"),
    os.path.join(HOME, "grok_build"),
    os.path.join(HOME, "scripts"),
    os.path.join(HOME, ".local", "bin"),
    os.path.join(HOME, ".config"),
    os.path.join(HOME, ".hermes"),
    os.path.join(HOME, ".agents", "skills"),
    os.path.join(HOME, ".pi", "agent"),
    os.path.join(HOME, "Library", "LaunchAgents"),
    "/opt/homebrew/Caskroom",
    "/Applications",
]


def dirs():
    out = []
    for d in DIR_ROOTS:
        exists = os.path.exists(d)
        info = du_mb(d) if exists else None
        kids = []
        if exists:
            try:
                kids = [{"name": n, "dir": os.path.isdir(os.path.join(d, n))}
                        for n in sorted(os.listdir(d)) if not n.startswith(".")][:25]
            except OSError:
                pass
        out.append({"path": d, "exists": exists,
                    "mb": (info or {}).get("mb", 0), "partial": (info or {}).get("partial", False),
                    "children": kids})
    return out


def ssh_surface():
    """只列位置、类型和备注名 —— 不读私钥内容，不导出任何密钥材料。"""
    keys = []
    sshdir = os.path.join(HOME, ".ssh")
    if os.path.isdir(sshdir):
        for f in sorted(os.listdir(sshdir)):
            p = os.path.join(sshdir, f)
            if not os.path.isfile(p) or f == "known_hosts" or f.endswith(".pub"):
                continue
            if f.startswith("id_"):
                rc, out = run(["ssh-keygen", "-l", "-f", p], timeout=5)
                keys.append({"file": p, "detail": out if rc == 0 else "（需要口令，跳过）"})
    hosts = []
    cfg = os.path.join(sshdir, "config")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"^\s*Host\s+(.+)$", line)
                    if m and "*" not in m.group(1):
                        hosts.append(m.group(1).strip())
        except OSError:
            pass
    return {"keys": keys, "hosts": hosts}


def shell_env():
    """用户自己设的环境变量（只从 rc 文件里抓 export，不执行任何东西）。"""
    out = []
    for rc in (".zshrc", ".zprofile", ".bash_profile", ".profile"):
        p = os.path.join(HOME, rc)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
                    if m:
                        out.append({"name": m.group(1), "value": m.group(2)[:160], "file": rc})
        except OSError:
            pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HOME, ".cache", "mac-inventory", "inventory.json"))
    args = ap.parse_args(argv)

    data = {
        "meta": meta(),
        "caps": system_caps(),
        "brew": brew_packages(),
        "apps": apps(),
        "launchd": launch_items(),
        "listeners": listeners(),
        "path_entries": path_entries(),
        "commands": commands(),
        "packages": package_managers(),
        "brewfile": brewfile(),
        "http": http_checks(),
        "running_apps": running_apps(),
        "dirs": dirs(),
        "ssh": ssh_surface(),
        "env": shell_env(),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # Brewfile 快照：跟上次比对，给入口脚本打印「新装了什么」
    snap_dir = os.path.dirname(args.out)
    bf = os.path.join(snap_dir, "Brewfile")
    cur = set((data.get("brewfile") or {}).get("lines") or [])
    if cur:
        prev = set()
        if os.path.exists(bf):
            with open(bf, encoding="utf-8") as f:
                prev = {l.strip() for l in f if l.strip()}
        added = sorted(cur - prev)
        removed = sorted(prev - cur)
        if added or removed or not os.path.exists(bf):
            with open(bf, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(cur)) + "\n")
            if added:
                print("Brewfile 新增 %d 条:" % len(added))
                for a in added[:8]:
                    print("  + " + a)
            if removed:
                print("Brewfile 减少 %d 条:" % len(removed))
                for r in removed[:8]:
                    print("  - " + r)
        else:
            print("Brewfile 无变化（%d 条）" % len(cur))

    m = data["meta"]
    print("OK %s (%d bytes) · %s · brew %d formula / %d cask · %d app · %d launchd · %d 端口 · %d 命令"
           % (
        args.out, os.path.getsize(args.out), m["hostname"],
        len(data["brew"]["formulae"]), len(data["brew"]["casks"]),
        len(data["apps"]), len(data["launchd"]), len(data["listeners"]),
        len(data["commands"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
