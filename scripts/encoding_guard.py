#!/usr/bin/env python3
"""文本编码的机械统一：BOM / 换行扫描与一键修复（纯 stdlib，无第三方依赖）。

为什么需要：BOM 与 CRLF 都是**外部工具**（Windows 记事本、PowerShell `Set-Content`、
Git autocrlf）写出来的，用户通常不知道 BOM 是什么，也难判断哪个编辑器会写它。
把它们丢给用户手工处理，结果就是工作区随机被判成 `unknown`、diff 里混进整文件改动。
所以策略是四件事（规格 §3.1）：

1. **读时容忍**：`scripts/osnorm.py` 的 `BOM_POLICY = "tolerate"` 统一剥 BOM；
2. **写入永不产生**：osnorm 的 `write_text` 固定 UTF-8 无 BOM；
3. **一键清理**：`python3 scripts/encoding_guard.py --fix`；
4. **门禁只 WARN**：`workspace_preflight.py` 的 `text_encoding` 项不阻断，
   只有模板仓自身的 CI 才把 `--check` 当阻断判据（模板层必须干净）。

扫描面（与规格一致）：
- `TEMPLATE_FILES`（唯一事实源：`scripts/verify_rules.py`）——模板层文件；
- 用户可编辑清单：`.workspace/bootstrap.json`、`WORKSPACE.md`、`TOOLS.md`、
  `notes/**/*.md`、任意 `*.custom.md`（`AGENT_RULES.custom.md` / `AGENTS.custom.md` 等）。

判据只看**字节**（不解码）：`BOM` / 纯 `CRLF` / 纯 `CR` / 混合换行（`MIXED`）。
这样编码违规与「文件里有非 UTF-8 字节」互不干扰，修复也不会因解码失败中途放弃。

用法：
  python3 scripts/encoding_guard.py --check [--json] [--root PATH]
  python3 scripts/encoding_guard.py --fix [--root PATH]

退出码：
  --check：0 干净 / 1 有发现（BOM 或非 LF 换行）/ 2 用法或 IO
  --fix  ：0 全部修好（含无事可做）/ 1 有文件修复失败（备份或写盘）/ 2 用法或 IO

幂等：`--fix` 只重写**有发现**的文件；第二次运行既无输出改动，也不触碰任何文件的 mtime。
改前备份到 `.workspace/backup-<ts>-encoding/`（保留相对路径），失败可原样拷回。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import stat
import sys
from pathlib import Path

# 文本 I/O 单一实现：osnorm。脚本直跑（sys.path[0]=scripts/）与被 importlib
# 按文件加载两种入口都要能解析兄弟模块，所以先把脚本目录钉进 sys.path。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入

osnorm.configure_stdio()

BOM = b"\xef\xbb\xbf"

# 用户可编辑的文件（工作区相对路径）：模板层不管，但最容易带 BOM/CRLF。
USER_EDITABLE_FILES = (".workspace/bootstrap.json", "WORKSPACE.md", "TOOLS.md")
# 用户可编辑的 glob（相对工作区）：notes 下的 note 与任意 *.custom.md。
USER_EDITABLE_GLOBS = ("notes/**/*.md", "*.custom.md")

# 不扫的目录（按路径段匹配）：版本库元数据、虚拟环境、依赖、产物。
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".pytest_cache"}


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def template_files(root: Path) -> list[str]:
    """从 `scripts/verify_rules.py`（唯一事实源）取模板层清单；取不到就只扫用户清单。"""
    path = root / "scripts" / "verify_rules.py"
    if not path.is_file():
        return []
    spec = importlib.util.spec_from_file_location("_encoding_guard_verify_rules", path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:                                   # noqa: BLE001 —— 清单缺失不该让扫描崩掉
        return []
    files = getattr(module, "TEMPLATE_FILES", ())
    return [str(item) for item in files] if isinstance(files, (list, tuple)) else []


def _skipped(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in parts)


def targets(root: Path) -> list[Path]:
    """扫描面：TEMPLATE_FILES + 用户可编辑清单，去重并保持相对路径排序。"""
    seen: list[Path] = []
    known: set[str] = set()

    def add(path: Path) -> None:
        if not path.is_file() or _skipped(path, root):
            return
        key = path.relative_to(root).as_posix()
        if key not in known:
            known.add(key)
            seen.append(path)

    for rel in template_files(root) + list(USER_EDITABLE_FILES):
        add(root / rel)
    for pattern in USER_EDITABLE_GLOBS:
        for path in sorted(root.glob(pattern)):
            add(path)
    return sorted(seen, key=lambda p: p.relative_to(root).as_posix())


def inspect(path: Path) -> list[str]:
    """只按字节判：返回问题码列表（空 = 干净）。"""
    data = osnorm.read_bytes(path)
    problems: list[str] = []
    if data.startswith(BOM):
        problems.append("BOM")
    body = data[len(BOM):] if data.startswith(BOM) else data
    crlf = body.count(b"\r\n")
    lone_cr = body.count(b"\r") - crlf
    if crlf and lone_cr:
        problems.append("MIXED")
    elif crlf:
        problems.append("CRLF")
    elif lone_cr:
        problems.append("CR")
    return problems


def repair_bytes(data: bytes) -> bytes:
    """剥 BOM + 换行归一为 LF（纯字节操作，不解码）。"""
    body = data[len(BOM):] if data.startswith(BOM) else data
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def scan(root: Path) -> list[dict]:
    findings = []
    for path in targets(root):
        problems = inspect(path)
        if problems:
            findings.append({"path": path.relative_to(root).as_posix(), "problems": problems})
    return findings


def backup_dir(root: Path) -> Path:
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return root / ".workspace" / f"backup-{stamp}-encoding"


def fix(root: Path) -> tuple[list[str], list[str], Path | None]:
    """修复有发现的文件；返回 (已修清单, 失败清单, 备份目录)。无发现 → 不写任何文件。"""
    changed: list[str] = []
    failed: list[str] = []
    pending = [(path, inspect(path)) for path in targets(root)]
    pending = [(path, problems) for path, problems in pending if problems]
    if not pending:
        return changed, failed, None

    stamp_dir = backup_dir(root)
    for path, problems in pending:
        rel = path.relative_to(root).as_posix()
        data = osnorm.read_bytes(path)
        fixed = repair_bytes(data)
        try:
            keep = stamp_dir / path.relative_to(root)
            keep.parent.mkdir(parents=True, exist_ok=True)
            if not keep.exists():                       # 同一秒内重复运行不覆盖首份备份
                osnorm.write_bytes(keep, data, atomic=False)
            mode = stat.S_IMODE(path.stat().st_mode)
            osnorm.atomic_write_bytes(path, fixed, mode=mode)
        except OSError as exc:
            failed.append(f"{rel} ({exc})")
            continue
        changed.append(rel)
        print(f"  fixed {rel}: {','.join(problems)} → UTF-8(no BOM) + LF")
    return changed, failed, stamp_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="BOM / 换行机械统一：--check 只报告，--fix 备份后修复（幂等）")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="扫模板层 + 用户可编辑清单；有发现 rc=1")
    group.add_argument("--fix", action="store_true", help="剥 BOM、统一 LF；改前备份，幂等")
    ap.add_argument("--json", action="store_true", help="机器可读输出（仅 --check）")
    ap.add_argument("--root", default=None, help="工作区根（默认脚本所在仓库）")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else workspace_root()
    if not root.is_dir():
        print(f"--root 不是目录：{root}", file=sys.stderr)
        return 2
    if a.fix and a.json:
        print("--json 只用于 --check（--fix 的输出是逐文件改动）", file=sys.stderr)
        return 2

    scanned = [path.relative_to(root).as_posix() for path in targets(root)]

    if a.fix:
        try:
            changed, failed, stamp_dir = fix(root)
        except OSError as exc:
            print(f"修复失败：{exc}", file=sys.stderr)
            return 2
        if failed:
            print(f"{len(failed)} 个文件修复失败：", file=sys.stderr)
            for item in failed:
                print(f"  ! {item}", file=sys.stderr)
        if not changed and not failed:
            print(f"无需修复：{len(scanned)} 个文件已是 UTF-8(no BOM) + LF")
        else:
            print(f"已修复 {len(changed)}/{len(scanned)} 个文件；备份在 {stamp_dir}")
        return 1 if failed else 0

    findings = scan(root)
    if a.json:
        print(json.dumps({"root": str(root), "scanned": len(scanned),
                          "findings": findings}, ensure_ascii=False))
    else:
        for item in findings:
            print(f"{','.join(item['problems']):10s} {item['path']}")
        if findings:
            print(f"{len(findings)}/{len(scanned)} 个文件有 BOM/换行问题 → "
                  f"python3 scripts/encoding_guard.py --fix")
        else:
            print(f"{len(scanned)} 个文件已是 UTF-8(no BOM) + LF")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
