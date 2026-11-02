#!/usr/bin/env python3
"""跨设备预检：核对「登记的事实」与「这台机器上的实际状态」。

为什么需要：工作区在多台设备上各自克隆，**路径、模板克隆、venv、hostname 都可能不同**。
按另一台机器的路径写死的事实，会让所有辅助工具（verify_rules / docs_index / hook / skill）
在这台机器上静默失效。本脚本把差异摊开，交给用户裁决，**不自动改写记录**。

检查项（每项给 recorded / actual / status / action）：
  host            当前主机名
  workspace_root  TOOLS §1 是否登记了本机路径、是否与当前 cwd 一致
  python          bootstrap 记录的 venv 是否存在、版本是否一致
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
    p = subprocess.run(list(args), cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    return p.returncode, (p.stdout or p.stderr).strip()


def item(id_: str, recorded, actual, status: str, action: str = "") -> dict:
    return {"id": id_, "recorded": recorded, "actual": actual, "status": status, "action": action}


def norm_url(u: str) -> str:
    u = (u or "").rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_p = ws / ".workspace" / "bootstrap.json"
    if not boot_p.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    boot = json.loads(boot_p.read_text(encoding="utf-8"))
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
        venv_py = ws / ident / "bin" / "python"
        want = py.get("version") or (py.get("runtime") or {}).get("version")
        if not ident:
            rows.append(item("python", "未记录 identifier", "", BAD, "回写 bootstrap.python.runtime.identifier"))
        elif not venv_py.exists():
            rows.append(item("python", str(venv_py), "missing", BAD,
                             "本机按 §0.1 重建 venv（venv 目录不跨设备共享）"))
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
        rows.append(item("python", "mcp", "mcp", NA, "MCP 模式：核验 provider/tool 仍可用"))
    else:
        rows.append(item("python", "unconfigured", mode, BAD, "按 §0 询问用户后配置"))

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
        missing = [f for f in files if not (ws / f).is_file()]
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
