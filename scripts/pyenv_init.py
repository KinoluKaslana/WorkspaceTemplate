#!/usr/bin/env python3
"""Python 运行时初始化器：**uv（默认）** / 手动 venv / MCP 三选一。

对应 `AGENT_RULES.md` §0：`python.mode=unconfigured` 时第一个 Agent 必须先问用户，再执行本脚本。

设计约束：
- 只写 `.workspace/bootstrap.json` 的 `python` 节与（可选）venv 目录；**不改 TOOLS.md**（工作区自有，输出待粘贴片段）；
- 默认 dry-run，`--apply` 才真正创建 venv / 写 bootstrap；
- uv 不存在时**不自动联网安装**：打印官方安装方式并 exit 3，需用户确认后加 `--allow-install`；
- 三种模式互斥，已有 venv 目录时幂等跳过创建但仍做校验。

用法：
  python3 scripts/pyenv_init.py --mode uv --python 3.12            # dry-run，报告将做什么
  python3 scripts/pyenv_init.py --mode uv --python 3.12 --apply
  python3 scripts/pyenv_init.py --mode venv --python 3.12 --apply
  python3 scripts/pyenv_init.py --mode mcp --mcp-server <name> --mcp-tool <tool> --apply
退出码：0 成功 / 1 前置不满足 / 2 参数错误 / 3 需要先安装 uv / 4 校验失败
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_HINT = """uv 未安装。三种安装方式（任选其一，均由用户执行）：
  curl -LsSf https://astral.sh/uv/install.sh | sh      # 官方脚本（默认装到 ~/.local/bin）
  pipx install uv                                       # 已有 pipx
  <包管理器> install uv                                  # apt/dnf/pacman/brew 等
安装后重开 shell（或 export PATH="$HOME/.local/bin:$PATH"），再重跑本命令。"""


def run(cmd: list[str], **kw) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, (p.stdout or p.stderr).strip()


def py_version(exe: str) -> str | None:
    rc, out = run([exe, "-c", "import sys;print(sys.version.split()[0])"])
    return out if rc == 0 else None


def venv_dir_name(version: str) -> str:
    parts = version.split(".")
    return f".venv-{parts[0]}{parts[1]}" if len(parts) >= 2 else ".venv-3"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--mode", required=True, choices=["uv", "venv", "mcp"])
    ap.add_argument("--python", default=None, help="目标版本，如 3.12；默认用检测到的系统版本")
    ap.add_argument("--mcp-server", default=None)
    ap.add_argument("--mcp-tool", default=None)
    ap.add_argument("--allow-install", action="store_true", help="允许执行 uv 官方安装脚本（联网）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_p = ws / ".workspace" / "bootstrap.json"
    if not boot_p.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    boot = json.loads(boot_p.read_text(encoding="utf-8"))
    out: dict = {"mode": a.mode, "apply": a.apply}

    if a.mode == "uv":
        uv = shutil.which("uv")
        if not uv:
            if not a.allow_install:
                print(json.dumps({"error": "uv-missing", "hint": INSTALL_HINT}, ensure_ascii=False))
                return 3
            rc, txt = run(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
            uv = str(Path.home() / ".local" / "bin" / "uv")
            if rc != 0 or not Path(uv).exists():
                print(json.dumps({"error": "uv-install-failed", "detail": txt[-200:]}, ensure_ascii=False))
                return 3
        _, uv_ver = run([uv, "--version"])
        target = a.python or py_version(sys.executable) or "3.12"
        vdir = ws / venv_dir_name(target)
        if vdir.exists():
            out["venv"] = {"path": str(vdir), "created": False}
        elif a.apply:
            rc, txt = run([uv, "venv", str(vdir), "--python", target])
            if rc != 0:
                print(json.dumps({"error": "uv-venv-failed", "detail": txt[-300:]}, ensure_ascii=False))
                return 4
            out["venv"] = {"path": str(vdir), "created": True}
        else:
            out["venv"] = {"path": str(vdir), "created": False, "would_create": True}
        py = vdir / "bin" / "python"
        ver = py_version(str(py)) if py.exists() else None
        out.update({"uv": uv, "uv_version": uv_ver, "python_version": ver,
                    "identifier": vdir.name})
        if a.apply:
            pysec = boot.setdefault("python", {})
            pysec.update({
                "mode": "uv",
                "runtime": {"identifier": vdir.name, "base_interpreter": target,
                            "uv": uv, "mcp_server": None, "mcp_tool": None},
                "version": ver or target,
                "capabilities": {"workspace_file_access": True, "network_access": True,
                                 "dependency_persistence": True},
                "decision_source": "uv（用户选择，模板默认推荐）",
                "configured_at": dt.date.today().isoformat(),
            })
            boot["workspace_status"] = "active"
            boot_p.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out["tools_snippet"] = (f'| Python 运行时 | uv {uv_ver} → {vdir.name} '
                                f'(Python {ver or target}) |')

    elif a.mode == "venv":
        base = a.python or py_version(sys.executable) or "3.12"
        vdir = ws / venv_dir_name(base)
        if vdir.exists():
            out["venv"] = {"path": str(vdir), "created": False}
        elif a.apply:
            rc, txt = run([sys.executable, "-m", "venv", str(vdir)])
            if rc != 0:
                print(json.dumps({"error": "venv-failed", "detail": txt[-300:]}, ensure_ascii=False))
                return 4
            out["venv"] = {"path": str(vdir), "created": True}
        else:
            out["venv"] = {"path": str(vdir), "created": False, "would_create": True}
        py = vdir / "bin" / "python"
        ver = py_version(str(py)) if py.exists() else None
        out.update({"python_version": ver, "identifier": vdir.name})
        if a.apply:
            pysec = boot.setdefault("python", {})
            pysec.update({
                "mode": "local-venv",
                "runtime": {"identifier": vdir.name, "base_interpreter": sys.executable,
                            "mcp_server": None, "mcp_tool": None},
                "version": ver or base,
                "capabilities": {"workspace_file_access": True, "network_access": True,
                                 "dependency_persistence": True},
                "decision_source": "local-venv（用户选择）",
                "configured_at": dt.date.today().isoformat(),
            })
            boot["workspace_status"] = "active"
            boot_p.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out["tools_snippet"] = f"| Python 运行时 | {vdir.name} (Python {ver or base}) |"

    else:  # mcp
        if not a.mcp_server or not a.mcp_tool:
            print(json.dumps({"error": "mcp-args-required", "hint": "--mcp-server/--mcp-tool"},
                             ensure_ascii=False))
            return 2
        out.update({"mcp_server": a.mcp_server, "mcp_tool": a.mcp_tool})
        if a.apply:
            pysec = boot.setdefault("python", {})
            pysec.update({
                "mode": "mcp",
                "runtime": {"identifier": None, "base_interpreter": None,
                            "mcp_server": a.mcp_server, "mcp_tool": a.mcp_tool},
                "version": None,
                "capabilities": {"workspace_file_access": None, "network_access": None,
                                 "dependency_persistence": None},
                "decision_source": "mcp（用户选择）",
                "configured_at": dt.date.today().isoformat(),
            })
            boot["workspace_status"] = "active"
            boot_p.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out["tools_snippet"] = f"| Python 运行时 | MCP {a.mcp_server}:{a.mcp_tool} |"

    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for k, v in out.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
