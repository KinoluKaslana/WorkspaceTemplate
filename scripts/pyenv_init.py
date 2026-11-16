#!/usr/bin/env python3
"""Python 运行时初始化器：**uv（默认）** / 手动 venv / MCP 三选一。

对应 `AGENT_RULES.md` §0：`python.mode=unconfigured` 时第一个 Agent 必须先问用户，再执行本脚本。

设计约束：
- 运行时模式写**本机状态** `.workspace/.local/v1/<context_id>/runtime.json`（`scripts/local_state.py`），
  不再写 `bootstrap.json` 的 `python` 节；`bootstrap` 只翻转 `workspace_status` 与 `workspace_id`；
- **不改 TOOLS.md**（工作区自有，输出待粘贴片段）；
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
import re
import shutil
import subprocess
import sys
from pathlib import Path
try:
    from repo_identity import classify as classify_repo
    from repo_identity import repair as repair_repo
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from repo_identity import classify as classify_repo
    from repo_identity import repair as repair_repo

INSTALL_HINT = """uv 未安装。三种安装方式（任选其一，均由用户执行）：
  curl -LsSf https://astral.sh/uv/install.sh | sh      # 官方脚本（默认装到 ~/.local/bin）
  pipx install uv                                       # 已有 pipx
  <包管理器> install uv                                  # apt/dnf/pacman/brew 等
安装后重开 shell（或 export PATH="$HOME/.local/bin:$PATH"），再重跑本命令。"""


def run(cmd: list[str], **kw) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, (p.stdout or p.stderr).strip()


# 布局口径只有一处：workspace_preflight.py 里的 VENV_INTERPRETERS / venv_interpreter。
# 这里 import 而不是复制——曾经两份复制粘贴过，改一处忘一处就会让"init 能建、preflight 说没有"。
try:
    from workspace_preflight import VENV_INTERPRETERS, venv_interpreter  # noqa: F401
except ImportError:                       # 以 `scripts.pyenv_init` 形式被 import 时 sys.path[0] 不是 scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from workspace_preflight import VENV_INTERPRETERS, venv_interpreter  # noqa: F401
    except ImportError:
        print("pyenv_init 需要同目录下的 workspace_preflight.py（venv 布局口径的唯一来源）；"
              "两者随 TEMPLATE_FILES 一起分发，请勿单独拷贝其中一个。", file=sys.stderr)
        raise SystemExit(2)


def _local_state():
    try:
        import local_state  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import local_state  # noqa: F401
    import local_state as ls
    return ls


def read_mode(ws: Path) -> str | None:
    """本机已配置的运行时模式；读不到返回 None（旧工作区回退 bootstrap.python.mode 由调用方决定）。"""
    try:
        ls = _local_state()
        wid = ls.workspace_id_of(ws)          # 纯读：dry-run 不得写盘
        if not wid:
            return None
        ident = ls.resolve_identity({}, ls.detect_platform_info())
        if ident is None:
            return None
        res = ls.read_state(ws, wid, ident, ephemeral=False)
        if res.get("status") == "BOUND":
            return (res.get("data") or {}).get("mode")
    except Exception:
        return None
    return None


def persist_mode(ws: Path, mode: str, venv_name: str | None, version: str | None,
                 runtime: dict | None, capabilities: dict | None, source: str) -> dict:
    """把运行时模式写进本机状态；返回 {"status": "BOUND", ...} 或 {"error": ...}。"""
    ls = _local_state()
    wid = ls.ensure_workspace_id(ws)
    if not wid:
        return {"error": "no-workspace-id", "hint": "工作区缺 .workspace/bootstrap.json 或不可写"}
    ident = ls.resolve_identity({}, ls.detect_platform_info())
    if ident is None:
        return {"error": "identity-unavailable",
                "hint": "取不到本机身份（machine-id / MachineGuid / 本机种子）；不静默降级，先修环境"}
    try:
        res = ls.bind(ws, wid, ident, mode, venv_name, version or "", ephemeral=False,
                      runtime=runtime, capabilities=capabilities, decision_source=source)
    except (ValueError, TypeError, OSError) as exc:
        return {"error": "bind-failed", "detail": str(exc)}
    if res.get("status") != "BOUND":
        return {"error": "bind-failed", "detail": res}
    return {"status": "BOUND", "context_id": res.get("context_id")}


def _repo_identity(value: str, base: str | os.PathLike[str] | None = None) -> str | None:
    """兼容旧调用点；实现唯一位于 repo_identity.repair。"""
    return repair_repo(value, base=base)


def _may_activate_workspace(ws: Path, boot: dict) -> tuple[bool | None, str | None]:
    """只在可归一化的 origin 明确不同于模板源时允许翻转。"""
    source_value = str((boot.get("template") or {}).get("source_repo") or "")
    source = _repo_identity(source_value, base=ws)
    if not source:
        return None, "缺 template.source_repo，无法排除模板正主；已 fail-closed 保留状态"
    rc, inside = run(["git", "-C", str(ws), "rev-parse", "--is-inside-work-tree"])
    if rc != 0 or inside.strip() != "true":
        return None, ("非 git 工作树或 git 判定不可得；已 fail-closed 保留状态。"
                       "请执行 `git init`，再执行 `git remote add origin <你的派生仓地址>`，"
                       "然后原样重跑当前 `pyenv_init.py ... --apply` 命令；"
                       "必须提供派生 origin，才能证明当前目录不是模板正主")
    rc, origin = run(["git", "-C", str(ws), "config", "--get", "remote.origin.url"])
    if rc == 1 and not origin:
        return None, ("git 工作树没有 remote.origin.url，无法证明已脱离模板正主；"
                       "已 fail-closed 保留状态。请执行 "
                       "`git remote add origin <你的派生仓地址>`，然后原样重跑当前 "
                       "`pyenv_init.py ... --apply` 命令")
    if rc != 0:
        return None, "无法读取 remote.origin.url；已 fail-closed 保留状态"
    kind = classify_repo(origin, source_value, base=ws)
    if kind is None:
        return None, ("remote.origin.url 无法归一化；已 fail-closed 保留状态。"
                       "合法本地路径会作为派生身份；否则请执行 "
                       "`git remote set-url origin <你的派生仓地址>`，然后原样重跑当前 "
                       "`pyenv_init.py ... --apply` 命令")
    if kind == "template":
        return False, ("检测到模板正主（origin == template.source_repo）；"
                       "已保留 workspace_status=template。请执行 "
                       "`git remote set-url origin <你的派生仓地址>`，然后原样重跑当前 "
                       "`pyenv_init.py ... --apply` 命令；"
                       "必须把 origin 改为派生仓，才能证明不会直接接管模板正主")
    return True, None


def _mark_active(boot_p: Path, activate: bool, hint: str | None) -> dict:
    """bootstrap 只删旧 python，且仅在可证明为派生工作区时翻转状态。

    必须**重新读盘**再改：`persist_mode` 刚写入了 `workspace_id`，用函数开头那份旧内容
    覆盖回去会把它抹掉。
    """
    boot = json.loads(boot_p.read_text(encoding="utf-8-sig"))
    if activate:
        boot["workspace_status"] = "active"
    boot.pop("python", None)
    tmp = boot_p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, boot_p)
    result = {"workspace_status": boot.get("workspace_status")}
    if hint:
        result.update({"template_protected": True, "hint": hint})
    return result


def _resolve_base_python(spec: str | None) -> str | None:
    """把 `--python 3.12` 解析成真实解释器路径；不指定则用当前解释器。

    **不得**在指定了版本却找不到时静默退回 `sys.executable`——那会让 `--python` 变成摆设。
    """
    if not spec:
        return sys.executable
    text = str(spec).strip()
    cands: list[str] = []
    if os.path.isabs(text) or os.sep in text:
        cands.append(text)
    cands += [f"python{text}", f"python{text.replace('.', '')}"]
    for cand in cands:
        path = shutil.which(cand) if not os.path.isabs(cand) else (cand if Path(cand).is_file() else None)
        if path and py_version(path):
            return path
    return None


def py_version(exe: str) -> str | None:
    rc, out = run([exe, "-c", "import sys;print(sys.version.split()[0])"])
    return out if rc == 0 else None


def _norm_python_version(value: str) -> str:
    """Normalize common Python version spellings to ``major.minor[.patch]``.

    Interpreter names are deliberately not retained: identifiers and human-readable
    snippets must be derived from the semantic version, never from ``python3.12``.
    """
    text = str(value).strip()
    match = re.fullmatch(r"(?i:python)\s*(\d+\.\d+(?:\.\d+)?)", text)
    if match is None:
        match = re.fullmatch(r"(\d+\.\d+(?:\.\d+)?)", text)
    if match is None:
        raise ValueError(
            f"非法 Python 版本 {value!r}；请使用 3.12、python3.12、3.12.4 或 Python 3.12"
        )
    return ".".join(str(int(part)) for part in match.group(1).split("."))


def venv_dir_name(version: str) -> str:
    return f".venv-{_norm_python_version(version).replace('.', '')}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--mode", required=True, choices=["uv", "venv", "mcp"])
    ap.add_argument("--python", default=None, help="目标版本，如 3.12；默认用检测到的系统版本")
    ap.add_argument("--mcp-server", default=None)
    ap.add_argument("--mcp-tool", default=None)
    ap.add_argument("--allow-install", action="store_true", help="允许执行 uv 官方安装脚本（联网）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="已配置过 Python 时仍强制切换（默认拒绝，保护既有工作区）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_p = ws / ".workspace" / "bootstrap.json"
    if not boot_p.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    boot = json.loads(boot_p.read_text(encoding="utf-8-sig"))
    local_mode = read_mode(ws)                      # 只认本机 BOUND 状态，绝不回退 bootstrap
    cur_mode = local_mode or ((boot.get("python") or {}).get("mode")) or "unconfigured"
    out: dict = {"mode": a.mode, "apply": a.apply, "existing_mode": cur_mode}
    target_mode = "local-venv" if a.mode == "venv" else a.mode
    activation: bool | None = False
    activation_hint: str | None = None
    if a.apply:
        activation, activation_hint = _may_activate_workspace(ws, boot)
        if activation is None:
            print(json.dumps({"error": "repo-identity-unavailable", "hint": activation_hint},
                             ensure_ascii=False))
            return 4
    if (a.apply and local_mode == target_mode and not a.force
            and boot.get("workspace_status") != "active"):
        # 半提交恢复：**本机确实 BOUND 且模式一致**、只是共享状态没翻（上次 _mark_active 失败）。
        # 只补翻转，不重建。判据必须用 local_mode，不能用手回退过的 cur_mode——
        # 否则「只有旧 bootstrap.python.mode」的老工作区会被误当成半提交，被删掉唯一记录并假激活。
        try:
            mark = _mark_active(boot_p, activation, activation_hint)
        except OSError as exc:
            print(json.dumps({"error": "mark-active-failed", "detail": str(exc)}, ensure_ascii=False))
            return 4
        out.update(mark)
        if mark.get("workspace_status") == "active":
            out["repaired"] = "workspace_status"
        out["mode"] = cur_mode
        if a.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            for k, v in out.items():
                print(f"{k}: {v}")
        return 0
    if a.apply and cur_mode not in ("unconfigured", None) and not a.force:
        print(json.dumps({
            "error": "already-configured", "existing_mode": cur_mode,
            "hint": ("既有工作区保持原模式（模板默认只作用于 unconfigured）。"
                     "旧工作区（模式只在 bootstrap.python 里）迁移到本机状态需要 --force 并取得用户同意；"
                     "确需切换同理。"),
        }, ensure_ascii=False))
        return 5

    if a.apply:
        # 身份不可得就不要开始建 venv：否则会留下「建了却没登记」的半套副作用
        _ls = _local_state()
        if _ls.resolve_identity({}, _ls.detect_platform_info()) is None:
            print(json.dumps({"error": "identity-unavailable",
                              "hint": "取不到本机身份（machine-id / MachineGuid / 本机种子）；不静默降级，先修环境"},
                             ensure_ascii=False))
            return 4

    if a.mode in ("uv", "venv"):
        requested = a.python or py_version(sys.executable) or "3.12"
        try:
            normalized_python = _norm_python_version(requested)
        except ValueError as exc:
            print(json.dumps({"error": "invalid-python-version", "detail": str(exc)},
                             ensure_ascii=False))
            return 2

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
        target = normalized_python
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
        py = venv_interpreter(vdir)
        ver = py_version(str(py)) if py else None
        out.update({"uv": uv, "uv_version": uv_ver, "python_version": ver,
                    "identifier": vdir.name})
        if a.apply:
            res = persist_mode(ws, "uv", vdir.name, ver or target,
                               {"identifier": vdir.name, "uv_version": uv_ver},
                               {"workspace_file_access": True, "network_access": None,
                                "dependency_persistence": True, "basis": "structural"},
                               "uv（用户选择，模板默认推荐）")
            if "error" in res:
                print(json.dumps(res, ensure_ascii=False))
                return 4
            out["local_state"] = res
            try:
                out.update(_mark_active(boot_p, activation, activation_hint))
            except OSError as exc:
                print(json.dumps({"error": "mark-active-failed", "detail": str(exc),
                                  "note": "本机状态已写入；修复 bootstrap 权限后重跑同一命令即可补翻状态"},
                                 ensure_ascii=False))
                return 4
        out["tools_snippet"] = (f'| Python 运行时 | uv {uv_ver} → {vdir.name} '
                                f'(Python {ver or target}) |')

    elif a.mode == "venv":
        base = normalized_python
        base_exe = _resolve_base_python(normalized_python if a.python else None)
        if base_exe is None:
            print(json.dumps({
                "error": "python-not-found", "requested": a.python,
                "hint": "指定的解释器不在 PATH 上；装好对应 python<ver> 或改 --python。不会静默退回当前进程解释器。",
            }, ensure_ascii=False))
            return 2
        vdir = ws / venv_dir_name(base)
        if vdir.exists():
            out["venv"] = {"path": str(vdir), "created": False}
        elif a.apply:
            rc, txt = run([base_exe, "-m", "venv", str(vdir)])
            if rc != 0:
                print(json.dumps({"error": "venv-failed", "detail": txt[-300:]}, ensure_ascii=False))
                return 4
            out["venv"] = {"path": str(vdir), "created": True}
        else:
            out["venv"] = {"path": str(vdir), "created": False, "would_create": True}
        py = venv_interpreter(vdir)
        ver = py_version(str(py)) if py else None
        out.update({"python_version": ver, "identifier": vdir.name})
        if a.apply:
            res = persist_mode(ws, "local-venv", vdir.name, ver or base,
                               {"identifier": vdir.name},
                               {"workspace_file_access": True, "network_access": None,
                                "dependency_persistence": True, "basis": "structural"},
                               "local-venv（用户选择）")
            if "error" in res:
                print(json.dumps(res, ensure_ascii=False))
                return 4
            out["local_state"] = res
            try:
                out.update(_mark_active(boot_p, activation, activation_hint))
            except OSError as exc:
                print(json.dumps({"error": "mark-active-failed", "detail": str(exc),
                                  "note": "本机状态已写入；修复 bootstrap 权限后重跑同一命令即可补翻状态"},
                                 ensure_ascii=False))
                return 4
        out["tools_snippet"] = f"| Python 运行时 | {vdir.name} (Python {ver or base}) |"

    else:  # mcp
        if not a.mcp_server or not a.mcp_tool:
            print(json.dumps({"error": "mcp-args-required", "hint": "--mcp-server/--mcp-tool"},
                             ensure_ascii=False))
            return 2
        out.update({"mcp_server": a.mcp_server, "mcp_tool": a.mcp_tool})
        if a.apply:
            res = persist_mode(ws, "mcp", None, None,
                               {"mcp_server": a.mcp_server, "mcp_tool": a.mcp_tool},
                               {"workspace_file_access": None, "network_access": None,
                                "dependency_persistence": None, "basis": "unverified"},
                               "mcp（用户选择）")
            if "error" in res:
                print(json.dumps(res, ensure_ascii=False))
                return 4
            out["local_state"] = res
            try:
                out.update(_mark_active(boot_p, activation, activation_hint))
            except OSError as exc:
                print(json.dumps({"error": "mark-active-failed", "detail": str(exc),
                                  "note": "本机状态已写入；修复 bootstrap 权限后重跑同一命令即可补翻状态"},
                                 ensure_ascii=False))
                return 4
        out["tools_snippet"] = f"| Python 运行时 | MCP {a.mcp_server}:{a.mcp_tool} |"

    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for k, v in out.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
