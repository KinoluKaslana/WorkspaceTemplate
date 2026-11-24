#!/usr/bin/env python3
"""提交前统一检查/修复入口（单一机械入口）。

为什么要有它：格式修复、注册表重建、判据检查、索引校验分散在十几个脚本里，靠人或
Agent「记得逐个跑」必然漏；CI 与本地各跑一套还会漂移。这里把**顺序固定**的阶段表
收敛成一条命令，本地提交前与 CI 治理 job 跑同一条。

阶段顺序（1..17）是判据的一部分，**不得重排**：

  1 encoding          encoding_guard.py --fix            （仅 --fix）
  2 registry          mark_rules.py registry AGENT_RULES.md（仅 --fix）
  3 docs_index_fix    docs_index.py build                （仅 --fix）
  4 notes_fix         notes/rebuild_index.py             （仅 --fix）
  5 encoding_check    encoding_guard.py --check
  6 registry_check    mark_rules.py check AGENT_RULES.md
  7 verify_rules      verify_rules.py --workspace .
  8 clause_lint       clause_lint.py
  9 skill_audit       skill_audit.py --strict
 10 policy_facts      policy_facts.py --check
 11 docs_index_check  docs_index.py check
 12 notes_check       notes_index_check.py
 13 version           version_check.py
 14 sequence          sequence_check.py            （计划侧 SEQUENCE.md ↔ dev 账本；实例侧天然 skip）
 15 osnorm_gate       python -m unittest tests.test_osnorm_single_source
 16 tests             python -m unittest discover -s tests -p 'test_*.py'   （仅 --full）
 17 hooks             python -m unittest discover -s hooks-tests -p 'test_*.py'（仅 --full）

设计约束：
- **只调度，不改判据**：每个阶段就是它自己的脚本，参数固定；本脚本不解释 findings、
  不自动 commit / push、不把失败改判成通过。
- **修复阶段失败不短路**：`--fix` 里某个修复阶段红了，后面的检查照跑（一次跑完看到全貌），
  最终 rc 仍为 1。
- **`--fix` 强制收尾复查**：修复阶段跑完后**再跑一遍全部检查阶段**（1–4 → 5–15），
  并单独打印 `修复后复查` 段与"仍失败的阶段"清单；有任何阶段红（含修复阶段自身红）
  → rc=1。修复是否真的收口只由这次复查回答，不能从"修复阶段 ok"推断。
- **子进程恒捕获 + utf-8**：一律走 `osnorm.run_text`，不自己拼 `subprocess.run` 的编码参数。
- 解释器用**当前解释器**（`sys.executable`）：调用方已经选好 venv，本脚本不再猜 `python3`。
- **放行提示（只提示，不改退出码）**：全部阶段跑完后，若「当前分支有上游且 ahead > 0」且
  「dev 账本非空」，打印一行 `WARN`：三席放行 ≠ 用户放行。`--json` 下这行走 stderr，
  stdout 仍然只有一个 JSON 对象。

用法：
  python3 scripts/check_all.py             # = --check：只跑检查阶段
  python3 scripts/check_all.py --fix       # 先修复（1–4），再全部检查（5–17 中非 full 的）
  python3 scripts/check_all.py --full      # 额外跑 tests + hooks-tests（慢，约 2 分钟）
  python3 scripts/check_all.py --json      # 机器可读汇总（stdout 只有 JSON，进度走 stderr）
  python3 scripts/check_all.py --only a,b  # 只跑指定阶段（按阶段表顺序；调试用）

`--only` 是**调试口**，不是提交门：commit-gate skill 禁止用它绕过必需阶段。

退出码：0 全部通过 / 1 任一阶段失败 / 2 用法错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 文本 I/O 与子进程的单一实现：osnorm。脚本直跑（sys.path[0]=scripts/）与被 importlib
# 按文件加载两种入口都要能解析兄弟模块，所以先把脚本目录钉进 sys.path。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入

# stdio 编码契约的单一实现（TTY / 非 TTY 两个分支在 osnorm 内）
osnorm.configure_stdio()

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TAIL_LINES = 20

KIND_FIX = "fix"
KIND_CHECK = "check"
KIND_FULL = "full"


@dataclass(frozen=True)
class Stage:
    """一个阶段：名字（稳定，`--only` 与 JSON 都认它）+ 归类 + argv。"""

    name: str
    kind: str
    argv: tuple[str, ...]


# 顺序即判据：修复阶段必须在检查阶段之前，osnorm_gate 在 unittest 派发之前。
# 改这张表 = 改门禁行为，必须同步 docs/tools.md 的阶段表与 CHANGELOG。
STAGES: tuple[Stage, ...] = (
    Stage("encoding", KIND_FIX, (PY, "scripts/encoding_guard.py", "--fix")),
    Stage("registry", KIND_FIX, (PY, "scripts/mark_rules.py", "registry", "AGENT_RULES.md")),
    Stage("docs_index_fix", KIND_FIX, (PY, "scripts/docs_index.py", "build")),
    Stage("notes_fix", KIND_FIX, (PY, "notes/rebuild_index.py")),
    Stage("encoding_check", KIND_CHECK, (PY, "scripts/encoding_guard.py", "--check")),
    Stage("registry_check", KIND_CHECK, (PY, "scripts/mark_rules.py", "check", "AGENT_RULES.md")),
    Stage("verify_rules", KIND_CHECK, (PY, "scripts/verify_rules.py", "--workspace", ".")),
    Stage("clause_lint", KIND_CHECK, (PY, "scripts/clause_lint.py")),
    Stage("skill_audit", KIND_CHECK, (PY, "scripts/skill_audit.py", "--strict")),
    Stage("policy_facts", KIND_CHECK, (PY, "scripts/policy_facts.py", "--check")),
    Stage("docs_index_check", KIND_CHECK, (PY, "scripts/docs_index.py", "check")),
    Stage("notes_check", KIND_CHECK, (PY, "scripts/notes_index_check.py")),
    Stage("version", KIND_CHECK, (PY, "scripts/version_check.py")),
    # R46：计划侧（SEQUENCE.md）↔ 事实侧（dev 账本 + CHANGELOG_DEV）互校。排在 version 之后
    # （先确认版本三元组自洽，再问「计划与账本是否互认」），osnorm_gate 之前（仍是判据检查，
    # 不该排到全量 unittest 派发之后）。实例侧没有 SEQUENCE.md → 该阶段显式 skip 且 rc=0。
    Stage("sequence", KIND_CHECK, (PY, "scripts/sequence_check.py")),
    Stage("osnorm_gate", KIND_CHECK, (PY, "-m", "unittest", "tests.test_osnorm_single_source")),
    Stage("tests", KIND_FULL, (PY, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")),
    Stage("hooks", KIND_FULL, (PY, "-m", "unittest", "discover", "-s", "hooks-tests", "-p", "test_*.py")),
)

STAGE_NAMES = tuple(s.name for s in STAGES)
ORDER = {s.name: i for i, s in enumerate(STAGES)}


def select_stages(*, fix: bool, full: bool, only: str | None) -> list[Stage] | None:
    """按模式挑阶段；`--only` 精确覆盖模式过滤（调试口）。返回 None = 用法错误。"""
    if only:
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        unknown = [x for x in wanted if x not in ORDER]
        if unknown or not wanted:
            print(f"--only 里有未知阶段：{', '.join(unknown) or '<空>'}；可用：{', '.join(STAGE_NAMES)}",
                  file=sys.stderr)
            return None
        dupes = sorted({x for x in wanted if wanted.count(x) > 1})
        if dupes:
            print(f"--only 里阶段重复：{', '.join(dupes)}（每阶段只跑一次）", file=sys.stderr)
            return None
        keep = set(wanted)
        return [s for s in STAGES if s.name in keep]
    return [s for s in STAGES
            if s.kind == KIND_CHECK or (s.kind == KIND_FIX and fix) or (s.kind == KIND_FULL and full)]


DEV_LEDGER_REL = ".workspace/release_dev.json"
RELEASE_WARN = "本轮已由三席放行，但尚未由用户放行（未 push）；全绿 ≠ 已放行"


def _git(root: Path, *args: str) -> tuple[int, str]:
    """只读 git 命令；缺 git / 非仓库都不抛，按 rc≠0 返回（与 sequence_check 同口径）。"""
    try:
        proc = osnorm.run_text(["git", *args], cwd=str(root), errors="replace")
    except OSError:
        return 127, ""
    return proc.returncode, (proc.stdout or "")


def dev_ledger_has_entries(root: Path = ROOT) -> bool:
    """dev 账本存在且非空（读不出/不是数组一律按「没有」处理：提示不参与判据）。"""
    path = root / DEV_LEDGER_REL
    if not path.is_file():
        return False
    try:
        data = json.loads(osnorm.read_text(path))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, list) and bool(data)


def release_hint(root: Path = ROOT) -> str | None:
    """三席放行 ≠ 用户放行：有未推送提交且 dev 账本非空 → 一行提示。

    **纯提示**：它不进 `--json` 的 `ok`、不改退出码。理由是这条判据无法替用户表达「放行」：
    push 由用户手动执行，脚本永远不 push（`release.py` 的硬约束），所以「全绿但没 push」
    是正常的中间态，不是失败；但它必须在人读输出里出现一次，免得把三席放行误读成用户放行。
    无上游（新分支从未推送）不提示：那种情况没有可比的远端，S11 已 WARN「仅有本地锚」。
    """
    if not dev_ledger_has_entries(root):
        return None
    rc, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not out.strip() or out.strip() == "HEAD":
        return None
    rc, _ = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc != 0:
        return None
    rc, out = _git(root, "rev-list", "--count", "@{u}..HEAD")
    if rc != 0:
        return None
    try:
        ahead = int(out.strip() or "0")
    except ValueError:
        return None
    return RELEASE_WARN if ahead > 0 else None


def _tail(lines: str) -> list[str]:
    """输出尾部 20 行（去尾部空行；空输出返回 []）。"""
    kept = lines.split("\n")
    while kept and not kept[-1].strip():
        kept.pop()
    return kept[-TAIL_LINES:]


def run_stage(stage: Stage, *, quiet: bool) -> dict:
    """跑一个阶段：恒捕获、恒 utf-8、恒不抛（异常按 rc=127 记失败）。"""
    started = time.monotonic()
    try:
        proc = osnorm.run_text(list(stage.argv), cwd=str(ROOT))
        rc = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except OSError as exc:                      # 解释器/脚本缺失之类：不崩，记失败
        rc = 127
        out = f"{type(exc).__name__}: {exc}"
    seconds = time.monotonic() - started
    tail = _tail(out)
    ok = rc == 0
    stream = sys.stderr if quiet else sys.stdout
    print(f"[{'ok' if ok else 'FAIL'}] {stage.name} {seconds:.2f}s", file=stream, flush=True)
    if not ok:
        for line in tail:
            print(f"    | {line}", file=stream)
        print(file=stream)
    return {"name": stage.name, "ok": ok, "rc": rc, "seconds": round(seconds, 3), "tail": tail}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="提交前统一检查/修复（阶段顺序固定，详见模块 docstring）")
    ap.add_argument("--fix", action="store_true", help="先跑可安全自动修复的阶段，再跑全部检查")
    ap.add_argument("--check", action="store_true", help="只跑检查（默认行为，显式写出也无害）")
    ap.add_argument("--full", action="store_true", help="额外跑 tests + hooks-tests（慢）")
    ap.add_argument("--json", action="store_true", help="stdout 只输出机器可读汇总 JSON")
    ap.add_argument("--only", metavar="a,b", help="只跑指定阶段（逗号分隔；调试用，不是提交门）")
    a = ap.parse_args(argv)

    stages = select_stages(fix=a.fix, full=a.full, only=a.only)
    if stages is None:
        return 2
    if not stages:                              # 理论上到不了（--check 恒有阶段）
        print("没有可跑的阶段（检查阶段表是否被清空）", file=sys.stderr)
        return 2

    quiet = a.json
    if not quiet:
        mode = "fix+check" if a.fix else "check"
        if a.full:
            mode += "+full"
        print(f"check_all: {mode}（{len(stages)} 个阶段，root={ROOT}）", flush=True)

    if a.fix and not a.only:
        # 两段式：先修复，再**强制重跑**全部检查。阶段表顺序本身已保证「修复在前」，
        # 这里显式分段还有一个作用：末尾的复查结果就是最终判据，不掺修复阶段的输出。
        fix_stages = [s for s in stages if s.kind == KIND_FIX]
        check_stages = [s for s in stages if s.kind != KIND_FIX]
        results = [run_stage(s, quiet=quiet) for s in fix_stages]
        if not quiet:
            print(f"check_all: 修复后复查（{len(check_stages)} 个检查阶段强制重跑）", flush=True)
        results += [run_stage(s, quiet=quiet) for s in check_stages]
    else:
        results = [run_stage(s, quiet=quiet) for s in stages]
    ok = all(r["ok"] for r in results)
    failed = [r["name"] for r in results if not r["ok"]]

    hint = release_hint(ROOT)

    if quiet:
        print(json.dumps({"ok": ok, "stages": results}, ensure_ascii=False))
    else:
        if ok:
            summary = "全部通过"
        elif a.fix and not a.only:
            summary = "修复后仍有阶段未通过：" + ", ".join(failed)
        else:
            summary = "失败阶段：" + ", ".join(failed)
        print(f"check_all: {'ok' if ok else 'FAIL'} — {len(results) - len(failed)}/{len(results)} 通过；{summary}")
    # 放行提示：全部阶段跑完后才出现一次；**不改 ok / rc**（三席放行 ≠ 用户放行，见模块 docstring）。
    if hint:
        print(f"WARN: {hint}", file=sys.stderr if quiet else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
