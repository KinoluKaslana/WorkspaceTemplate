#!/usr/bin/env python3
"""跨设备预检：核对「登记的事实」与「这台机器上的实际状态」。

为什么需要：工作区在多台设备上各自克隆，**路径、模板克隆、venv、hostname 都可能不同**。
按另一台机器的路径写死的事实，会让所有辅助工具（verify_rules / docs_index / hook / skill）
在这台机器上静默失效。本脚本把差异摊开，交给用户裁决，**不自动改写记录**。

检查项（每项给 recorded / actual / status / action）：
  host            当前主机名
  workspace_root  TOOLS §1 是否登记了本机路径、是否与当前 cwd 一致
  python          bootstrap 记录的 venv 是否存在、版本是否一致
  gate            bootstrap.gate 结论 vs 运行期证据（§15；configured 但 TTL 内无证据 = mismatch）
  template_clone  template.local_clone 是否存在、remote 是否与 source_repo 一致、HEAD 分支
  skills          目录 ↔ REGISTRY.md ↔ TOOLS.md 三方一致
  template_files  TEMPLATE_FILES 是否齐全
  notes_index     notes/INDEX.md 是否存在
  docs_index      docs/ 存在时索引是否新鲜（不存在则 n/a）
  git             remote / 当前分支 / 上游 / ahead-behind / 脏文件数 / .hook-state 是否被忽略

用法：python3 scripts/workspace_preflight.py [--workspace .] [--json] [--strict]
退出码：0 无 mismatch；1 有 mismatch（--strict 时 warning 也算）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shutil
import re
import socket
import subprocess
import sys
from pathlib import Path

OK, WARN, BAD, NA = "ok", "warn", "mismatch", "n/a"


def sh(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    """跑一个外部命令并取输出。

    - 统一 UTF-8：Windows 默认 cp1252，解释器输出非 ASCII 时会 UnicodeDecodeError；
    - 兜住 OSError：目标文件不是本平台可执行格式（例如 Windows 上的 `bin/python` 脚本、
      或没有执行权限的普通文件）时，只应**降级**（版本未知），不该让整个预检崩掉。
    """
    try:
        p = subprocess.run(list(args), cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"{type(e).__name__}: {e}"
    return p.returncode, (p.stdout or p.stderr or "").strip()


VENV_INTERPRETERS = (("bin", "python"), ("bin", "python3"),
                     ("Scripts", "python.exe"), ("Scripts", "python3.exe"))


def venv_interpreter(venv_dir: Path) -> Path | None:
    """venv 里的解释器：POSIX 是 `bin/python`，Windows 是 `Scripts/python.exe`。

    只认 `bin/python` 会把所有 Windows/MINGW 工作区判成 mismatch——路径布局是平台事实，
    不是用户配错。按顺序取第一个真实存在的候选。
    """
    for parts in VENV_INTERPRETERS:
        cand = venv_dir.joinpath(*parts)
        if cand.is_file():
            return cand
    return None


def venv_layout(venv_dir: Path) -> str:
    """返回解释器所在布局：`posix` / `windows` / `none`。"""
    for parts in VENV_INTERPRETERS:
        if venv_dir.joinpath(*parts).is_file():
            return "windows" if parts[0] == "Scripts" else "posix"
    return "none"


def resolve_venv_dir(ws: Path, ident: str) -> Path:
    r"""把 bootstrap 里的 `runtime.identifier` 解析成 venv 目录。

    支持三种写法：venv 目录名（相对工作区）、POSIX 绝对路径、Windows 盘符路径（`D:\ws\.venv-314`）。
    解析口径只此一处——preflight 的 python 项与 `--layout-only` 都走它。
    """
    ident = (ident or "").strip()
    if re.match(r"^[A-Za-z]:[\\/]", ident):
        return Path(ident.replace("\\", "/"))
    path = Path(ident) if ident else ws
    if ident and not path.is_absolute():
        path = ws / ident
    return path


def describe_layout(venv_dir: Path) -> dict:
    """只读诊断：这个 venv 目录里解释器在哪、是什么版本。

    **布局是本机派生事实，永远现算**——不接受外部传入的布局/解释器路径
    （那会把"现场核验"降级成"信任一份可能过期或被伪造的快照"）。
    """
    exe = venv_interpreter(venv_dir)
    out = {
        "venv": str(venv_dir),
        "layout": venv_layout(venv_dir),
        "interpreter": str(exe) if exe else None,
        "version": None,
        "candidates": [f"{a}/{b}" for a, b in VENV_INTERPRETERS],
        "source": "probed-on-host",
    }
    if exe:
        rc, ver = sh(str(exe), "-c", "import sys;print(sys.version.split()[0])")
        # 只有真正跑成功才认这个版本；跑不起来（不是本平台可执行格式 / 没权限）就留 None，
        # 不把错误文本冒充成版本号。
        out["version"] = (ver or None) if rc == 0 else None
    return out


def item(id_: str, recorded, actual, status: str, action: str = "") -> dict:
    return {"id": id_, "recorded": recorded, "actual": actual, "status": status, "action": action}


def norm_url(u: str) -> str:
    u = (u or "").rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record-readme", default="", metavar="MODE",
                    help="记录 README 决策：auto=按工作区内容自动生成 / provided=用户提供说明后结合生成 / declined=不重构")
    ap.add_argument("--layout-only", action="store_true",
                    help="只读诊断：打印本机 venv 布局 JSON（exit 0=找到解释器，3=没有）")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--harness", default="",
                    help="当前在用的 harness（cc,codex,hermes,dsh，逗号分隔）：给了就要求它已在 gate 结论里")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_p = ws / ".workspace" / "bootstrap.json"
    if not boot_p.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    boot = json.loads(boot_p.read_text(encoding="utf-8-sig"))
    if a.record_readme:
        mode = a.record_readme.strip().lower()
        allowed = {"auto": "generated-auto", "provided": "generated-provided", "declined": "declined"}
        if mode not in allowed:
            print(json.dumps({"error": "bad-mode", "allowed": sorted(allowed)}, ensure_ascii=False))
            return 2
        rd = boot.setdefault("readme", {})
        rd.update({"status": allowed[mode], "mode": mode,
                   "decided_at": dt.datetime.now().isoformat(timespec="seconds")})
        boot_p.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"readme": rd}, ensure_ascii=False))
        return 0
    if a.layout_only:
        pysec = boot.get("python") or {}
        mode = pysec.get("mode") or "unconfigured"
        ident = ((pysec.get("runtime") or {}).get("identifier") or "").strip()
        # 先按 mode 分流，再决定要不要探测：mcp/unconfigured 根本不碰文件系统。
        # （曾把 describe_layout 放在分流之前：mcp 模式下会去执行工作区根目录里恰好存在的 bin/python。）
        info = {"mode": mode, "venv": None, "layout": "n/a", "interpreter": None, "version": None,
                "candidates": [f"{a}/{b}" for a, b in VENV_INTERPRETERS], "source": "probed-on-host"}
        if mode == "mcp":
            info.update({"applicable": False,
                         "note": "MCP 模式没有本地 venv；要核验的是 MCP provider/tool 是否可用"})
            print(json.dumps(info, ensure_ascii=False))
            return 0
        if mode == "unconfigured":
            info.update({"layout": "none", "applicable": False,
                         "note": "python.mode 未配置：先按 §0 选择运行时（uv / venv / mcp）"})
            print(json.dumps(info, ensure_ascii=False))
            return 3
        if not ident:
            # 空 identifier 绝不能回退到工作区根：根目录里的 bin/python 与"这个工作区登记的 venv"无关，
            # 探测它会给出假阳性 exit 0。
            info.update({"layout": "none", "applicable": True,
                         "note": "bootstrap 未记录 python.runtime.identifier：回写 venv 目录后再诊断"})
            print(json.dumps(info, ensure_ascii=False))
            return 3
        info = describe_layout(resolve_venv_dir(ws, ident))
        info["mode"] = mode
        info["applicable"] = True          # uv 与 local-venv 都建本地 venv，布局规则相同
        if mode == "uv":
            uv = ((pysec.get("runtime") or {}).get("uv")) or shutil.which("uv") or ""
            info["uv"] = {"path": uv or None, "available": bool(uv and shutil.which(uv))}
        print(json.dumps(info, ensure_ascii=False))
        return 0 if info["interpreter"] else 3
    tools_p = ws / "TOOLS.md"
    tools = tools_p.read_text(encoding="utf-8") if tools_p.is_file() else ""
    rows: list[dict] = []

    host = socket.gethostname() or platform.node()
    rows.append(item("host", "—", host, OK, "本机标识，仅用于对照 TOOLS §1"))

    # 1) 工作区根路径是否按主机登记
    host_lines = [ln for ln in tools.splitlines() if host in ln]
    ws_paths = {p.rstrip("/") for p in re.findall(r"/(?:home|Users)/[^\s`，。、）)]+",
                                                          "\n".join(host_lines))}
    if not host_lines:
        rows.append(item("workspace_root", "TOOLS §1 无本机条目", str(ws), WARN,
                         "在 TOOLS §1 增加本机行（主机名 + 实际根路径），不要改写他机行"))
    elif ws_paths and str(ws) not in ws_paths:
        rows.append(item("workspace_root", sorted(ws_paths), str(ws), BAD,
                         "本机路径与登记不符：确认后改本机行，保留他机行"))
    else:
        rows.append(item("workspace_root", sorted(ws_paths) or host, str(ws), OK))

    # 2) Python 运行时
    py = boot.get("python") or {}
    mode = py.get("mode")
    if mode in ("local-venv", "uv"):
        ident = ((py.get("runtime") or {}).get("identifier") or "").strip()
        venv_dir = resolve_venv_dir(ws, ident)
        venv_py = venv_interpreter(venv_dir) if ident else None
        want = py.get("version") or (py.get("runtime") or {}).get("version")
        if not ident:
            rows.append(item("python", "未记录 identifier", "", BAD, "回写 bootstrap.python.runtime.identifier"))
        elif venv_py is None:
            rows.append(item("python", {"venv": str(venv_dir),
                                        "searched": [f"{a}/{b}" for a, b in VENV_INTERPRETERS]},
                             "missing", BAD,
                             "本机按 §0.1 重建 venv（venv 目录不跨设备共享）；"
                             "POSIX 是 bin/python，Windows 是 Scripts/python.exe"))
        else:
            _, out = sh(str(venv_py), "-c", "import sys;print(sys.version.split()[0])")
            ok = (not want) or out.startswith(str(want))
            extra = {}
            if mode == "uv":
                uv = ((py.get("runtime") or {}).get("uv")) or shutil.which("uv") or ""
                _, uvv = sh(uv, "--version") if uv else (1, "")
                extra = {"uv": uv, "uv_version": uvv.split()[1] if uvv else "missing"}
                if not uv or not uvv:
                    ok = False
            rows.append(item("python", {"path": str(venv_py), "version": want, "mode": mode},
                             {"version": out, **extra}, OK if ok else WARN,
                             "" if ok else ("uv 不可用：安装 uv 或改用 venv/mcp 模式" if mode == "uv"
                                            else "版本不同：确认后更新 bootstrap/TOOLS")))
    elif mode == "mcp":
        venvs = sorted(p.name for p in ws.glob(".venv-*") if p.is_dir())
        if venvs:                                  # R11：MCP 模式不得同时建本地 venv
            rows.append(item("python", "mcp（不得建本地 venv）", ", ".join(venvs), BAD,
                             "MCP 模式下不得创建本地 venv：删掉或改回 local-venv 模式"))
        else:
            rows.append(item("python", "mcp", "mcp", NA, "MCP 模式：核验 provider/tool 仍可用"))
    else:
        rows.append(item("python", "unconfigured", mode, BAD, "按 §0 询问用户后配置"))

    # 2b) Gate 接线（§15）：登记状态 vs 运行期证据
    gate = boot.get("gate") or {}
    g_status = gate.get("status")
    g_harness = [h for h in (gate.get("harness") or []) if h]
    want = [h.strip() for h in (getattr(a, "harness", "") or "").split(",") if h.strip()]
    if not g_status:
        rows.append(item("gate", "未记录", "—", WARN,
                         "按 §15 核验后记录：python3 scripts/gate_status.py --require <harness> --record"))
    elif g_status not in ("configured", "unconfigured"):
        rows.append(item("gate", g_status, g_status, BAD,
                         "非法 status（只允许 configured / unconfigured）：按 §15 重新 --record"))
    elif g_status != "configured":
        rows.append(item("gate", g_status, g_status, WARN,
                         "已记录「无 gate」：接线后按 §15 重新核验并翻转状态"))
    elif want and (set(want) - set(g_harness)):
        rows.append(item("gate", {"harness": g_harness}, {"required": want}, BAD,
                         "当前 harness 不在已登记的接线结论里：先按 §15 核验该端并 --record"))
    elif not g_harness:
        rows.append(item("gate", "configured（缺 harness）", "", BAD,
                         "bootstrap.gate.harness 为空：重新用 --record 写入"))
    else:
        ttl = min(float(gate.get("ttl_minutes") or 30), 30.0)
        rc, out = sh(sys.executable, str(Path(__file__).resolve().parent / "gate_status.py"),
                     "--workspace", str(ws), "--require", ",".join(g_harness),
                     "--ttl-minutes", str(ttl), "--json")
        try:
            probe = json.loads(out)
            fresh = probe.get("missing") == []
        except ValueError:
            probe, fresh = {}, False
        # 区分「过期」与「没有证据」：证据过期常见于工作区闲置（插件只在其事件发生时写日志），
        # 判 BAD 会天天误报；完全没有证据才是「声明与事实矛盾」。
        ages = [((probe.get("clients") or {}).get(h) or {}).get("last_seen_minutes") for h in g_harness]
        ever = any(a is not None for a in ages)
        stale_but_known = (not fresh) and ever and all(a is not None for a in ages)
        rows.append(item("gate", {"harness": g_harness, "ttl": ttl,
                                  "last_seen_minutes": ages},
                         {"missing": (probe or {}).get("missing", ["?"])},
                         OK if fresh else (WARN if stale_but_known else BAD),
                         "" if fresh else ("证据已过期：写入前重跑 gate_status --require <harness>"
                                           if stale_but_known
                                           else "声明已接线但没有任何证据：确认 harness 启动方式（dsh 需 --patch）")))


    # 3) 模板克隆
    tpl = boot.get("template") or {}
    tc = tpl.get("local_clone")
    if tc:
        tcp = Path(tc).expanduser()
        if not (tcp / ".git").exists():
            rows.append(item("template_clone", tc, "missing", BAD,
                             "模板克隆不存在：用 template_sync.py --clone-url 重克隆，或改 local_clone"))
        else:
            _, url = sh("git", "-C", str(tcp), "remote", "get-url", "origin")
            _, br = sh("git", "-C", str(tcp), "rev-parse", "--abbrev-ref", "HEAD")
            _, dirty = sh("git", "-C", str(tcp), "status", "--porcelain")
            same = (not tpl.get("source_repo")) or norm_url(url) == norm_url(tpl["source_repo"])
            rows.append(item("template_clone", {"path": tc, "remote": tpl.get("source_repo")},
                             {"remote": url, "branch": br, "dirty_files": len(dirty.splitlines())},
                             OK if same else BAD,
                             "" if same else "remote 与登记不一致：确认后改 source_repo/local_clone"))
    else:
        rows.append(item("template_clone", "未登记", "", WARN, "如需模板同步，在 bootstrap.template 登记"))

    # 4) skills 三方一致
    disk = {p.parent.name for p in (ws / ".workspace" / "skills").glob("*/SKILL.md")}
    reg_p = ws / "skills" / "REGISTRY.md"
    reg = reg_p.read_text(encoding="utf-8") if reg_p.is_file() else ""
    reg_entries = set(re.findall(r"\.workspace/skills/([A-Za-z0-9_-]+)/SKILL\.md", reg))
    if disk != reg_entries:
        rows.append(item("skills", sorted(reg_entries), sorted(disk), BAD,
                         "目录与 REGISTRY 不一致：以磁盘为准补登记，或恢复缺失 skill"))
    else:
        unlisted = sorted(s for s in disk if s not in tools)
        rows.append(item("skills", sorted(disk), {"count": len(disk), "not_in_TOOLS": unlisted},
                         OK if not unlisted else WARN,
                         "" if not unlisted else "在 TOOLS.md 补登记"))

    # 5) TEMPLATE_FILES 齐全
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ws_verify_rules", ws / "scripts" / "verify_rules.py")
        mod = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
        spec.loader.exec_module(mod)                          # type: ignore[union-attr]
        files = list(mod.TEMPLATE_FILES)
        exempt = {"README.md", "README_EN.md"}      # 用户首页：可替换、可删除（R127）
        missing = [f for f in files if not (ws / f).is_file() and f not in exempt]
        rows.append(item("template_files", len(files),
                         {"present": len(files) - len(missing), "missing": missing[:6]},
                         OK if not missing else BAD,
                         "" if not missing else "缺模板层文件：template_sync.py --apply"))
    except Exception as e:                                    # noqa: BLE001
        rows.append(item("template_files", "verify_rules 不可导入", repr(e)[:120], BAD,
                         "先同步模板层（scripts/verify_rules.py 缺失或损坏）"))

    # 6) notes 索引
    idx = ws / "notes" / "INDEX.md"
    rows.append(item("notes_index", "notes/INDEX.md", "exists" if idx.is_file() else "missing",
                     OK if idx.is_file() else WARN,
                     "" if idx.is_file() else "跑 notes/rebuild_index.py"))

    # 7) docs 索引
    if (ws / "docs").is_dir() and (ws / "scripts" / "docs_index.py").is_file():
        rc, out = sh(sys.executable, "-B", str(ws / "scripts" / "docs_index.py"), "check", cwd=ws)
        rows.append(item("docs_index", "ok:true", out[:120], OK if rc == 0 else WARN,
                         "" if rc == 0 else "跑 scripts/docs_index.py build"))
    else:
        rows.append(item("docs_index", "—", "no docs/", NA))

    # 8) git（工作区自身仓库）
    if (ws / ".git").exists():
        _, url = sh("git", "-C", str(ws), "remote", "get-url", "origin")
        _, br = sh("git", "-C", str(ws), "rev-parse", "--abbrev-ref", "HEAD")
        rc, cnt = sh("git", "-C", str(ws), "rev-list", "--left-right", "--count", "@{u}...HEAD")
        ahead = behind = None
        if rc == 0 and len(cnt.split()) == 2:
            behind, ahead = (int(x) for x in cnt.split())
        _, dirty = sh("git", "-C", str(ws), "status", "--porcelain")
        gi_rc, _ = sh("git", "-C", str(ws), "check-ignore", "-q", ".workspace/.hook-state")
        want = norm_url((boot.get("git") or {}).get("remote") or "")
        same = (not want) or norm_url(url) == want
        rows.append(item("git", {"remote": want, "default_branch": (boot.get("git") or {}).get("default_branch")},
                         {"remote": url, "branch": br, "ahead": ahead, "behind": behind,
                          "dirty_files": len(dirty.splitlines()), "hook_state_ignored": gi_rc == 0},
                         OK if same else BAD,
                         "" if same else "remote 与 bootstrap 不一致：确认后更新记录"))
    else:
        rows.append(item("git", "initialized?", "no .git", WARN, "本机无仓库：clone 或按 git-init 建仓"))

    # 10) README 决策（§0.4）：建仓后必须问过用户"要不要重构 README"，并记录用户选的方式
    README_STATES = {"undecided", "generated-auto", "generated-provided", "declined"}
    rd = boot.get("readme") or {}
    rd_status = (rd.get("status") or "undecided").strip()
    git_ready = ((boot.get("git") or {}).get("status") == "initialized")
    if rd_status not in README_STATES:
        rows.append(item("readme", rd_status, sorted(README_STATES), BAD,
                         "bootstrap.readme.status 非法：只允许 " + "/".join(sorted(README_STATES))))
    elif git_ready and rd_status == "undecided":
        rows.append(item("readme", "undecided", "git=initialized", WARN,
                         "建仓后未记录 README 决策：先问用户是否需要重构，再记 --record-readme auto|provided|declined"))
    elif rd_status != "undecided" and not rd.get("decided_at"):
        rows.append(item("readme", {"status": rd_status}, "缺 decided_at", BAD,
                         "记录决策时间（--record-readme 会写入）"))
    else:
        rows.append(item("readme", rd_status, rd.get("mode") or "—", OK))

    bad = [r for r in rows if r["status"] == BAD]
    warn = [r for r in rows if r["status"] == WARN]
    out = {"workspace": str(ws), "host": host, "ok": not bad,
           "mismatch": len(bad), "warn": len(warn), "items": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in rows:
            print(f'{r["status"].upper():8s} {r["id"]:15s} {json.dumps(r["recorded"], ensure_ascii=False)[:70]}')
            if r["action"]:
                print(f'         → {r["action"]}')
        print(f'结论: {"OK" if out["ok"] else "需裁决"}（mismatch={len(bad)} warn={len(warn)}）')
    return 0 if (not bad and (not a.strict or not warn)) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
