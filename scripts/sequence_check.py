#!/usr/bin/env python3
"""计划侧 ↔ 事实侧互校：`.workspace/SEQUENCE.md`（计划）↔ dev 账本 + `CHANGELOG_DEV.md`（事实）。

为什么要有它：交付顺序、dev 版本号、依赖关系写在计划里，实际落地写在账本里。两边靠人「记得
同步」必然漂移（本轮已实发一次：账本 `summary` 与 `CHANGELOG_DEV.md` 正文差一个措辞）。这里把
`SEQUENCE.md` §4 的 S1–S10 做成机械判据：**只判一致性，不判计划好坏**。

判据（逐条实现 `SEQUENCE.md` §4，编号与那里一致）：

  S1  轮次严格递增、无重复                     解析表格 round 列 + `### RN` 小标题，比较相邻
  S2  `version` 首段 == round                   拆 `.` 取 [0] 与 round 比（非 dev 轮次可不带版本）
  S3  landed 必须在 dev 账本有条目               双向：landed→账本（缺账本）/ 账本→landed（账本多出）；
                                                `landed-no-ledger` 类须有提交号
  S4  `依赖` 必须存在且排在其前                  依赖号集合校验 + 文档位置比较（倒置/悬空）
  S5  范围引用的 WORKLIST 条目必须存在           读 `WORKLIST.md` 的 P/C/D 编号集合（缺文件 = 显式 SKIP）
  S6  计划 landed 数 == 账本条目数               计数比较
  S7  账本 summary == 对应 commit 的 CHANGELOG 段落；无 commit 时显式 DEGRADED
  S8  git 历史账本逐条前缀延续                   历史条目不可改写/删除
  S9  landed 三席确认完整且时序合法               legacy 仅带 note 时豁免
  S10 `_DEV` 文件分支归属                         dev 必有、main 必无、其他分支 WARN

实例侧行为：派生工作区**没有** `.workspace/SEQUENCE.md`（研发路线图是 dev 专用），这时**显式
报告 skip、退出码 0**——不崩、不假红。`check_all.py` 会在实例侧照常调度本阶段。

用法：
  python3 scripts/sequence_check.py                 # 人读
  python3 scripts/sequence_check.py --json          # 机器可读（stdout 只有 JSON）
  python3 scripts/sequence_check.py --root <dir>    # 指定树根（默认 = 本脚本所在仓）
  python3 scripts/sequence_check.py --worklist <p>  # 指定 WORKLIST.md（默认按候选路径找）

退出码：0 全部通过或显式 skip；1 有 findings；2 用法/IO。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# 文本 I/O 单一实现：osnorm。脚本直跑（sys.path[0]=scripts/）与被 importlib
# 按文件加载两种入口都要能解析兄弟模块，所以先把脚本目录钉进 sys.path。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入

# stdio 编码契约的单一实现（TTY / 非 TTY 两个分支在 osnorm 内）
osnorm.configure_stdio()

ROOT = Path(__file__).resolve().parent.parent
SEQ_REL = ".workspace/SEQUENCE.md"
LEDGER_REL = ".workspace/release_dev.json"
CHANGELOG_REL = "CHANGELOG_DEV.md"

# 四段 dev 版本：NN.X.Y.Z，无前导零（与 release.py 的 dev_version_tuple 同形）
VER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# 任何带点号的版本形态（用于判「版本写错段数」——三段版本不能因为解析不出就放过）
VERSIONISH_RE = re.compile(r"\d+(?:\.\d+)+")
# §2 的小标题：`### R47 · `47.3.8.0` · stdio 三件套`
PLANNED_HEAD_RE = re.compile(r"^###\s+R(\d+)\b(.*)$")
# §2 的字段行：`- **范围**：…` / `- **依赖**：R45` / `- **land_status**：landed`
FIELD_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*[：:]\s*(.*)$")
# 依赖/引用里的轮次号：`R47`、`R47–R51 全绿`、`R49（必须验迁移后的实现）`
ROUND_REF_RE = re.compile(r"R(\d+)\b")
# 范围里的 WORKLIST 条目号：`C11`、`D5`、`P0`
WORKLIST_ID_RE = re.compile(r"\b([PCD]\d{1,3})\b")
# 提交号：7–40 位十六进制
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
H2_RE = re.compile(r"^##\s")
SECTION_RE = re.compile(r"^##\s+(\d+)\.")

# WORKLIST.md 的候选位置（工作区级，SEQUENCE.md §5）：本仓 → 父目录 → 祖父目录。
# 模板仓在 `<workspace>/sandbox-*/<task>` 里时，WORKLIST 在第 2 级父目录。
WORKLIST_CANDIDATES = ("WORKLIST.md", "../WORKLIST.md", "../../WORKLIST.md")

LANDED = "landed"
LANDED_NO_LEDGER = "landed-no-ledger"
PLANNED = "planned"
VALID_STATUSES = {PLANNED, LANDED, LANDED_NO_LEDGER}
REQUIRED_REVIEWERS = {"SOL", "GPT-SOL-6", "PRO"}


class Round:
    """SEQUENCE.md 里的一个轮次条目（§1 表格行或 §2 小标题块）。"""

    def __init__(self, round_no: int, line: int, source: str):
        self.round = round_no
        self.line = line
        self.source = source
        self.version: str | None = None
        self.version_raw: str | None = None          # 版本列/小标题里的原始文本（形态非法时用来判红）
        self.commit: str | None = None
        self.status = PLANNED
        self.deps: list[int] = []
        self.scope = ""
        self.criteria = ""
        self.verified_by: str | list[str] | None = None
        self.verdict: str | None = None
        self.verified_at: str | None = None
        self.notes: str | None = None
        self.note: str | None = None
        self.status_error: str | None = None

    def __repr__(self) -> str:                      # 调试用
        return f"<Round R{self.round} {self.status} {self.version}>"


def _table_cells(line: str) -> list[str] | None:
    """把 `| a | b |` 拆成单元格；表头分隔行（`|---|`）返回 None。"""
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return None
    return cells


def parse_sequence(text: str) -> list[Round]:
    """解析 SEQUENCE.md：§1 已落地表 + §2 计划块。其余小节（§3 映射表、§4 判据表、§5 散文）不参与。

    解析不出来就当**格式漂移**处理（S1 红），不静默通过。
    """
    rounds: list[Round] = []
    section: int | None = None
    current: Round | None = None
    field: str | None = None                       # 当前正在累积的字段名（多行续行用）

    for no, line in enumerate(text.splitlines(), 1):
        m = SECTION_RE.match(line)
        if m:
            section, current, field = int(m.group(1)), None, None
            continue
        if line.startswith("## "):                 # 非数字编号的 H2（如序言外的其它节）
            section, current, field = None, None, None
            continue

        if section == 1:
            cells = _table_cells(line)
            if cells is None or len(cells) < 5:
                continue
            head = cells[0]
            if not head.isdigit():                 # 表头 `round`
                continue
            r = Round(int(head), no, f"§1 第 {no} 行")
            version = cells[1].strip("` ")
            r.version_raw = version
            if VER_RE.match(version):
                r.version, r.status = version, LANDED
            else:
                # 不占版本号的已落地轮次（如 R45「并入 44 条」）：须有提交号
                r.version, r.status = None, LANDED_NO_LEDGER
            commit = cells[2].strip("` ")
            r.commit = commit if commit else None
            r.scope = cells[3]
            review = cells[4] if len(cells) > 4 else ""
            if "legacy" in review:
                r.verified_by = "legacy"
                r.note = review
            elif review:
                r.verified_by = [x for x in REQUIRED_REVIEWERS if x in review]
            rounds.append(r)
            continue

        if section != 2:
            continue

        m = PLANNED_HEAD_RE.match(line)
        if m:
            r = Round(int(m.group(1)), no, f"§2 R{m.group(1)}")
            vm = re.search(r"`(\d+\.\d+\.\d+\.\d+)`", m.group(2))
            if vm:
                r.version = vm.group(1)
                r.version_raw = vm.group(1)
            else:
                # 形态不像四段但仍带点号版本（如 `46.3.8`）→ 记原文，S2 判红；非 dev 轮次不带版本
                raw = re.search(r"(\d+(?:\.\d+)+)", m.group(2))
                if raw:
                    r.version_raw = raw.group(1)
            rounds.append(r)
            current, field = r, None
            continue

        if line.startswith("#"):                   # 别的标题（### 之外）结束当前块
            current, field = None, None
            continue
        if current is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            field = fm.group(1).strip()
            value = fm.group(2).strip()
            if field == "范围":
                current.scope = value
            elif field == "判据":
                current.criteria = value
            elif field == "依赖":
                current.deps = [int(x) for x in ROUND_REF_RE.findall(value)]
            elif field in ("land_status", "land-status"):
                status = value.strip().strip("`")
                if status in VALID_STATUSES:
                    current.status = status
                else:
                    current.status_error = status
            elif field == "verified_by":
                raw = value.strip().strip("`")
                current.verified_by = "legacy" if raw == "legacy" else re.findall(r"SOL|GPT-SOL-6|PRO", raw)
            elif field == "verdict":
                current.verdict = value.strip().strip("`")
            elif field == "verified_at":
                current.verified_at = value.strip().strip("`")
            elif field == "notes":
                current.notes = value.strip()
            elif field == "note":
                current.note = value.strip()
            continue
        if not line.strip():                       # 空行结束多行字段
            field = None
            continue
        if field in ("范围", "判据") and line.startswith((" ", "\t")):
            if field == "范围":
                current.scope += "\n" + line.strip()
            else:
                current.criteria += "\n" + line.strip()
    return rounds


def load_ledger(root: Path) -> tuple[list[dict], list[str]]:
    """读 dev 账本；非法形状 fail-closed（返回空账本 + 错误信息，由 S3 判红）。"""
    path = root / LEDGER_REL
    if not path.is_file():
        return [], [f"账本 {LEDGER_REL} 不存在"]
    try:
        data = json.loads(osnorm.read_text(path, bom=True))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"账本 {LEDGER_REL} 读取失败：{type(exc).__name__}: {exc}"]
    if not isinstance(data, list):
        return [], [f"账本 {LEDGER_REL} 必须是 JSON 数组"]
    entries = [e for e in data if isinstance(e, dict)]
    if len(entries) != len(data):
        return entries, [f"账本 {LEDGER_REL} 含非对象条目"]
    return entries, []


def section_body(text: str, version: str) -> tuple[int | None, str | None]:
    """取 `## <version>` 段的正文（标题行之后到下一个 H2 之前）。返回 (起始行号, 正文)。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        head = line[3:].strip()
        if head == version or head.startswith(version + "（") or head.startswith(version + "(") \
                or head.startswith(version + " "):
            j = i + 1
            while j < len(lines) and not H2_RE.match(lines[j]):
                j += 1
            return i + 1, "\n".join(lines[i + 1:j])
    return None, None


def find_worklist(root: Path, explicit: str | None) -> tuple[Path | None, list[str]]:
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else (root / p)
        return (p if p.is_file() else None), [str(p)]
    tried: list[str] = []
    for rel in WORKLIST_CANDIDATES:
        p = (root / rel).resolve()
        tried.append(str(p))
        if p.is_file():
            return p, tried
    return None, tried


def _fail(msg: str, rnd: Round | None = None, line: int | None = None,
          file: str | None = None) -> dict:
    return {"round": f"R{rnd.round}" if rnd else None, "line": line, "file": file, "message": msg}


def git(root: Path, *args: str) -> tuple[int, str]:
    p = osnorm.run_text(["git", *args], cwd=root, errors="replace")
    return p.returncode, (p.stdout or "").strip()


def git_json_at(root: Path, commit: str, rel: str) -> tuple[list[dict] | None, str | None]:
    rc, out = git(root, "show", f"{commit}:{rel}")
    if rc:
        return None, f"git show {commit}:{rel} 失败"
    try:
        value = json.loads(out)
    except json.JSONDecodeError as exc:
        return None, f"历史账本 JSON 非法：{exc}"
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        return None, "历史账本必须是对象数组"
    return value, None


def ledger_history(root: Path) -> tuple[list[tuple[str, list[dict]]], list[str]]:
    rc, out = git(root, "log", "--format=%H", "--", LEDGER_REL)
    if rc:
        return [], ["无法读取 git 账本历史"]
    history, errors = [], []
    for sha in out.splitlines():
        entries, err = git_json_at(root, sha, LEDGER_REL)
        if err:
            errors.append(err)
        elif entries is not None:
            history.append((sha, entries))
    return history, errors


def check_s1(rounds: list[Round]) -> tuple[str, str, list[dict]]:
    if not rounds:
        return "FAIL", "解析不出任何轮次", [_fail("SEQUENCE.md 解析出 0 个轮次（表格/小标题格式漂移？）")]
    findings: list[dict] = []
    seen: dict[int, Round] = {}
    for r in rounds:
        if r.round in seen:
            findings.append(_fail(f"R{r.round} 重复出现（{seen[r.round].source} / {r.source}）", r, r.line))
        else:
            seen[r.round] = r
    for prev, cur in zip(rounds, rounds[1:]):
        if cur.round <= prev.round:
            findings.append(_fail(f"轮次未严格递增：R{prev.round}（{prev.source}）→ "
                                  f"R{cur.round}（{cur.source}）", cur, cur.line))
    detail = (f"轮次严格递增：R{rounds[0].round} → R{rounds[-1].round}"
              f"（{len(rounds)} 轮，无重复）") if not findings else "见下"
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_s2(rounds: list[Round]) -> tuple[str, str, list[dict]]:
    findings: list[dict] = []
    checked = 0
    for r in rounds:
        if r.version is None:
            # 版本形态不合法（如三段）不能被当成「本轮不带版本」放过 —— fail-closed
            if r.version_raw and VERSIONISH_RE.search(r.version_raw):
                findings.append(_fail(f"R{r.round} 版本 {r.version_raw} 不是合法 NN.X.Y.Z"
                                      f"（四段、无前导零）", r, r.line))
            continue
        checked += 1
        tup = VER_RE.match(r.version)
        if not tup:
            findings.append(_fail(f"R{r.round} 版本 {r.version} 不是合法 NN.X.Y.Z（四段、无前导零）", r, r.line))
            continue
        if int(tup.group(1)) != r.round:
            findings.append(_fail(f"R{r.round} 版本 {r.version} 首段 {tup.group(1)} ≠ 轮次 {r.round}", r, r.line))
    detail = f"{checked} 个带版本轮次的首段 == 轮次（非法形态 0 个）"
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_s3(rounds: list[Round], ledger: list[dict], ledger_errs: list[str]) -> tuple[str, str, list[dict]]:
    findings: list[dict] = [_fail(e) for e in ledger_errs]
    by_round = {r.round: r for r in rounds if r.status in (LANDED, LANDED_NO_LEDGER)}
    ledger_by_round: dict[int, dict] = {}
    for e in ledger:
        rnd = e.get("round")
        if isinstance(rnd, int):
            ledger_by_round[rnd] = e

    for r in rounds:
        if r.status_error is not None:
            findings.append(_fail(f"land_status 非法：{r.status_error!r}（只允许 planned|landed|landed-no-ledger）", r, r.line))
        if r.status == LANDED:
            if r.round not in ledger_by_round:
                findings.append(_fail(f"R{r.round} 标 landed 但 {LEDGER_REL} 没有 round {r.round} 的条目"
                                      f"（缺账本；未入账应标 landed-no-ledger 并给提交号）", r, r.line))
        elif r.status == LANDED_NO_LEDGER:
            if not r.commit or not COMMIT_RE.match(r.commit):
                findings.append(_fail(f"R{r.round} 标 landed-no-ledger 但提交列不是提交号"
                                      f"（实测 {r.commit!r}）", r, r.line))
        # planned 不要求账本条目（S6 只比计数）

    for rnd, e in sorted(ledger_by_round.items()):
        r = by_round.get(rnd)
        if r is None:
            findings.append(_fail(f"{LEDGER_REL} 有 round {rnd}（{e.get('version')}），"
                                  f"但 SEQUENCE 没有对应已落地轮次（账本多出）"))
        elif r.status != LANDED:
            findings.append(_fail(f"{LEDGER_REL} 有 round {rnd}，但 SEQUENCE 里 R{rnd} 是 "
                                  f"{r.status}（账本多出/状态不符）", r, r.line))

    detail = (f"landed {sum(1 for r in rounds if r.status == LANDED)} ↔ 账本 {len(ledger)} 条"
              f"{'双向一致' if not findings else '（不一致，见下）'}"
              f"；landed-no-ledger {sum(1 for r in rounds if r.status == LANDED_NO_LEDGER)} 条有提交号")
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_commits(rounds: list[Round], ledger: list[dict], root: Path) -> list[dict]:
    findings: list[dict] = []
    for r in rounds:
        if r.status == LANDED_NO_LEDGER and r.commit and COMMIT_RE.match(r.commit):
            rc, _ = git(root, "cat-file", "-e", f"{r.commit}^{{commit}}")
            if rc:
                findings.append(_fail(f"landed-no-ledger 提交 {r.commit} 不存在于 git 对象库", r, r.line))
    for e in ledger:
        commit = e.get("commit")
        if commit:
            rc, _ = git(root, "cat-file", "-e", f"{commit}^{{commit}}")
            if rc:
                findings.append(_fail(f"账本 R{e.get('round')} commit {commit!r} 不是 git commit"))
    return findings


def check_s4(rounds: list[Round]) -> tuple[str, str, list[dict]]:
    findings: list[dict] = []
    pos = {r.round: i for i, r in enumerate(rounds)}
    for i, r in enumerate(rounds):
        for dep in r.deps:
            if dep not in pos:
                findings.append(_fail(f"R{r.round} 依赖 R{dep} 悬空（依赖目标不在 SEQUENCE 中）", r, r.line))
            elif pos[dep] >= i:
                findings.append(_fail(f"R{r.round} 依赖倒置：R{dep}（{rounds[pos[dep]].source}）"
                                      f"排在其后或同一轮", r, r.line))
    pulled = sum(len(r.deps) for r in rounds)
    detail = f"{pulled} 条依赖都指向排在其前的既有轮次"
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_s5(rounds: list[Round], worklist: Path | None, tried: list[str]) -> tuple[str, str, list[dict], bool]:
    """返回 (status, detail, findings, skipped)。找不到 WORKLIST.md = 显式 SKIP（不是红）。"""
    if worklist is None:
        return ("SKIP", "WORKLIST.md 未找到（候选：" + "；".join(tried) + "）——范围引用未校验", [], True)
    ids = set(WORKLIST_ID_RE.findall(osnorm.read_text(worklist)))
    findings: list[dict] = []
    refs = 0
    for r in rounds:
        for token in set(WORKLIST_ID_RE.findall(r.scope + "\n" + r.criteria)):
            refs += 1
            if token not in ids:
                findings.append(_fail(f"R{r.round} 引用 {token}（范围/判据），但 {worklist.name} 里没有该编号",
                                      r, r.line))
    detail = f"{refs} 处 P/C/D 引用全部存在于 {worklist.name}"
    return ("PASS" if not findings else "FAIL"), detail, findings, False


def check_s6(rounds: list[Round], ledger: list[dict], history: list[tuple[str, list[dict]]]) -> tuple[str, str, list[dict]]:
    landed = [r.round for r in rounds if r.status == LANDED]
    findings = []
    if len(landed) != len(ledger):
        findings.append(_fail(f"计划 landed {len(landed)} 条（R{', R'.join(str(x) for x in landed) or '—'}）"
                              f"≠ 账本 {len(ledger)} 条，差 {abs(len(landed) - len(ledger))}"))
    current = {r.round for r in rounds}
    historical = {e.get("round") for _, entries in history for e in entries if isinstance(e.get("round"), int)}
    missing = sorted(historical - current)
    if missing:
        findings.append(_fail("SEQUENCE 删除了 git 历史账本曾存在的轮次：" + ", ".join(f"R{x}" for x in missing)))
    if not findings:
        return "PASS", f"landed {len(landed)} 条 == 账本 {len(ledger)} 条；历史轮次集合只增不减", []
    return "FAIL", "计数不一致", findings


def check_s7(ledger: list[dict], root: Path) -> tuple[str, str, list[dict]]:
    if not ledger:
        return "PASS", "账本为空：无 summary 可比", []
    findings, degraded = [], 0
    for entry in ledger:
        version, summary, commit = str(entry.get("version")), str(entry.get("summary") or ""), entry.get("commit")
        if commit:
            rc, text = git(root, "show", f"{commit}:{CHANGELOG_REL}")
            if rc:
                findings.append(_fail(f"git show {commit}:{CHANGELOG_REL} 失败（R{entry.get('round')}）")); continue
        else:
            degraded += 1
            path = root / CHANGELOG_REL
            if not path.is_file():
                findings.append(_fail(f"{CHANGELOG_REL} 不存在（账本 {version} 无从比对）")); continue
            text = osnorm.read_text(path)
        start, body = section_body(text, version)
        if body is None:
            findings.append(_fail(f"{CHANGELOG_REL} 没有 `## {version}` 段落")); continue
        if summary.strip() != body.strip():
            want_lines, got_lines = summary.strip().splitlines(), body.strip().splitlines()
            at = next((i for i, pair in enumerate(zip(want_lines, got_lines)) if pair[0] != pair[1]),
                      min(len(want_lines), len(got_lines)))
            want = want_lines[at] if at < len(want_lines) else "<账本到此结束>"
            got = got_lines[at] if at < len(got_lines) else "<段落到此结束>"
            findings.append(_fail(f"prose 漂移：账本 {version} summary ≠ {'git '+str(commit) if commit else 'working-tree'} 的 {CHANGELOG_REL} 段落\n        账本：{want[:160]}\n        段落：{got[:160]}", line=(start or 0)+1+at, file=CHANGELOG_REL))
    detail = f"{len(ledger)} 条 summary 均与各自 commit 的 CHANGELOG 段落一致"
    if degraded:
        detail += f"；DEGRADED：{degraded} 条未提交，S7 降级为 working-tree 比较"
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_s8(rounds: list[Round], ledger: list[dict], history: list[tuple[str, list[dict]]], history_errs: list[str]) -> tuple[str, str, list[dict]]:
    findings: list[dict] = []
    findings.extend(_fail(e) for e in history_errs)
    versioned = [r for r in rounds if r.version]
    prev: Round | None = None
    for r in versioned:
        cur_t = VER_RE.match(r.version or "")
        if prev is not None and prev.version:
            prev_t = VER_RE.match(prev.version)
            if cur_t and prev_t and tuple(int(x) for x in cur_t.groups()) <= \
                    tuple(int(x) for x in prev_t.groups()):
                findings.append(_fail(f"版本未严格递增：{prev.version}（R{prev.round}）→ "
                                      f"{r.version}（R{r.round}）", r, r.line))
        prev = r

    ledger_by_round = {e.get("round"): e for e in ledger if isinstance(e.get("round"), int)}
    for r in rounds:
        if r.status != LANDED or not r.version:
            continue
        e = ledger_by_round.get(r.round)
        if e is None:
            continue                                # 缺条目由 S3 判红，这里不重复
        recorded = str(e.get("version"))
        if recorded != r.version:
            findings.append(_fail(f"已落地版本被改：R{r.round} SEQUENCE 写 {r.version}，"
                                  f"账本记 {recorded}（已落地版本不可变）", r, r.line))

    prev_tup: tuple[int, ...] | None = None
    for e in ledger:
        v = str(e.get("version"))
        m = VER_RE.match(v)
        if not m:
            findings.append(_fail(f"账本里有非法 dev 版本 {v!r}（须 NN.X.Y.Z）"))
            continue
        tup = tuple(int(x) for x in m.groups())
        if prev_tup is not None and tup <= prev_tup:
            findings.append(_fail(f"账本版本未严格递增：{prev_tup} → {v}"))
        if e.get("round") != tup[0]:
            findings.append(_fail(f"账本 round 字段 {e.get('round')!r} ≠ 版本首段 {tup[0]}（{v}）"))
        prev_tup = tup

    # git 是 append-only 的信任锚：每个较旧快照必须是较新快照的逐条前缀，当前也必须延续最近快照。
    chain = list(reversed(history)) + [("WORKTREE", ledger)]
    for (old_sha, old), (new_sha, new) in zip(chain, chain[1:]):
        if len(new) < len(old) or new[:len(old)] != old:
            findings.append(_fail(f"git 历史锚失败：{new_sha[:12]} 不是 {old_sha[:12]} 账本的 append-only 前缀延续（历史条目被改写/删除）"))

    detail = f"{len(versioned)} 个版本严格递增；已落地 {len(ledger)} 条与账本逐轮同值"
    return ("PASS" if not findings else "FAIL"), detail, findings


def check_s9(rounds: list[Round], ledger: list[dict]) -> tuple[str, str, list[dict]]:
    findings, by_round = [], {e.get("round"): e for e in ledger}
    for r in rounds:
        if r.status != LANDED:
            continue
        if r.verified_by == "legacy":
            if not (r.note or "").strip():
                findings.append(_fail("legacy 豁免必须有 note", r, r.line))
            continue
        reviewers = set(r.verified_by or [])
        if reviewers != REQUIRED_REVIEWERS:
            findings.append(_fail(f"verified_by 必须三席齐全，当前缺 {sorted(REQUIRED_REVIEWERS-reviewers)}", r, r.line))
        if r.verdict not in {"pass", "pass-with-notes"}:
            findings.append(_fail(f"verdict 非法：{r.verdict!r}", r, r.line))
        if r.verdict == "pass-with-notes" and not (r.notes or "").strip():
            findings.append(_fail("pass-with-notes 的 notes 不得为空", r, r.line))
        entry = by_round.get(r.round)
        if entry and r.verified_at:
            try:
                if date.fromisoformat(r.verified_at) > date.fromisoformat(str(entry.get("date"))):
                    findings.append(_fail("verified_at 晚于账本 date", r, r.line))
            except ValueError:
                findings.append(_fail("verified_at/date 必须是 ISO 日期", r, r.line))
        elif entry:
            findings.append(_fail("landed 缺 verified_at", r, r.line))
    return ("PASS" if not findings else "FAIL"), "landed 三席确认/legacy 豁免均合规", findings


def check_s10(root: Path) -> tuple[str, str, list[dict]]:
    rc, branch = git(root, "branch", "--show-current")
    if rc:
        return "WARN", "非 git 工作树：无法判分支归属", []
    present = [rel for rel in (CHANGELOG_REL, LEDGER_REL) if (root / rel).is_file()]
    if branch == "dev":
        missing = [rel for rel in (CHANGELOG_REL, LEDGER_REL) if rel not in present]
        return ("FAIL", "dev 缺 _DEV 文件", [_fail("dev 分支缺：" + ", ".join(missing))]) if missing else ("PASS", "dev 分支两个 _DEV 文件均存在", [])
    if branch == "main":
        return ("FAIL", "main 不得含 _DEV 文件", [_fail("main 分支仍有：" + ", ".join(present))]) if present else ("PASS", "main 分支不存在 _DEV 文件", [])
    return "WARN", f"分支 {branch or '<detached>'} 非 dev/main：只告警，不判红", []


def run(root: Path, worklist_arg: str | None) -> dict:
    seq_path = root / SEQ_REL
    ledger, ledger_errs = load_ledger(root)
    rounds: list[Round] = []
    parse_error: str | None = None
    if seq_path.is_file():
        try:
            rounds = parse_sequence(osnorm.read_text(seq_path))
        except OSError as exc:
            parse_error = f"{SEQ_REL} 读取失败：{type(exc).__name__}: {exc}"

    worklist, tried = find_worklist(root, worklist_arg)
    history, history_errs = ledger_history(root)
    s5_status, s5_detail, s5_findings, _skipped = check_s5(rounds, worklist, tried)

    checks: list[dict] = []
    for cid, triple in (
        ("S1", check_s1(rounds)),
        ("S2", check_s2(rounds)),
        ("S3", check_s3(rounds, ledger, ledger_errs)),
        ("S4", check_s4(rounds)),
        ("S6", check_s6(rounds, ledger, history)),
        ("S7", check_s7(ledger, root)),
        ("S8", check_s8(rounds, ledger, history, history_errs)),
        ("S9", check_s9(rounds, ledger)),
        ("S10", check_s10(root)),
    ):
        status, detail, findings = triple
        checks.append({"id": cid, "status": status, "detail": detail, "findings": findings})
    checks.insert(4, {"id": "S5", "status": s5_status, "detail": s5_detail, "findings": s5_findings})
    commit_findings = check_commits(rounds, ledger, root)
    if commit_findings:
        checks[2]["status"] = "FAIL"
        checks[2]["findings"].extend(commit_findings)
    if parse_error:
        checks.append({"id": "PARSE", "status": "FAIL", "detail": parse_error,
                       "findings": [_fail(parse_error)]})

    failed = [c for c in checks if c["status"] == "FAIL"]
    return {
        "ok": not failed,
        "skipped": False,
        "sequence": str(seq_path),
        "ledger": str(root / LEDGER_REL),
        "worklist": str(worklist) if worklist else None,
        "worklist_candidates": tried,
        "rounds": [{"round": r.round, "version": r.version, "status": r.status,
                    "source": r.source} for r in rounds],
        "ledger_entries": len(ledger),
        "checks": checks,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="SEQUENCE.md（计划）↔ dev 账本/CHANGELOG_DEV（事实）互校")
    ap.add_argument("--json", action="store_true", help="stdout 只输出机器可读 JSON")
    ap.add_argument("--root", metavar="DIR", help="树根（默认 = 本脚本所在仓）")
    ap.add_argument("--worklist", metavar="PATH", help="WORKLIST.md 路径（默认按候选路径查找）")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else ROOT
    seq_path = root / SEQ_REL

    # 实例侧：没有研发路线图。显式 skip，rc=0（不是失败，也不静默假装通过）。
    if not seq_path.is_file():
        if a.json:
            print(json.dumps({"ok": True, "skipped": True,
                              "reason": f"{SEQ_REL} 不存在：实例侧无计划侧文件，全部判据跳过",
                              "sequence": str(seq_path)}, ensure_ascii=False))
        else:
            print(f"sequence_check: SKIP —— 未找到 {SEQ_REL}（{seq_path}）")
            print("  实例侧不继承研发路线图：S1–S10 全部跳过，rc=0")
        return 0

    try:
        result = run(root, a.worklist)
    except OSError as exc:                          # IO 异常不崩：按用法/IO 退出
        print(f"sequence_check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        counts = {s: sum(1 for r in result["rounds"] if r["status"] == s)
                  for s in (LANDED, LANDED_NO_LEDGER, PLANNED)}
        print(f"sequence_check: {SEQ_REL} · {len(result['rounds'])} 轮"
              f"（landed {counts[LANDED]} / landed-no-ledger {counts[LANDED_NO_LEDGER]} / "
              f"planned {counts[PLANNED]}）· 账本 {result['ledger_entries']} 条")
        for c in result["checks"]:
            print(f"{c['id']} {c['status']:<4} {c['detail']}")
            for f in c["findings"]:
                tag = f"[{f['round']}] " if f.get("round") else ""
                if f.get("line") and f.get("file"):
                    where = f"{f['file']}:{f['line']}: "
                elif f.get("line"):
                    where = f"line {f['line']}: "
                else:
                    where = ""
                print(f"    - {tag}{where}{f['message']}")
        ok_n = sum(1 for c in result["checks"] if c["status"] == "PASS")
        skip_n = sum(1 for c in result["checks"] if c["status"] == "SKIP")
        bad = [c["id"] for c in result["checks"] if c["status"] == "FAIL"]
        print(f"→ {'OK' if result['ok'] else 'FAIL(' + ', '.join(bad) + ')'}"
              f"（{len(result['checks'])} 项：{ok_n} PASS / {len(bad)} FAIL / {skip_n} SKIP）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
