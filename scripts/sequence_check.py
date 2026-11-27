#!/usr/bin/env python3
"""计划侧 ↔ 事实侧互校：`.workspace/SEQUENCE.md`（计划）↔ dev 账本 + `CHANGELOG_DEV.md`（事实）↔ git 历史（锚）。

为什么要有它：交付顺序、dev 版本号、依赖关系写在计划里，实际落地写在账本里。两边靠人「记得
同步」必然漂移（本轮已实发一次：账本 `summary` 与 `CHANGELOG_DEV.md` 正文差一个措辞）。这里把
`SEQUENCE.md` §4 的 S1–S13 做成机械判据：**只判一致性，不判计划好坏**。

判据（逐条实现 `SEQUENCE.md` §4，编号与那里一致）：

  S1  轮次严格递增、无重复                     解析表格 round 列 + `### RN` 小标题，比较相邻；
                                                **dev 分支缺 SEQUENCE.md = 红**（历史里有计划/_DEV 文件时，
                                                删计划不能把整条判据关掉；SKIP 只留给实例侧/非模板仓）
  S2  `version` 首段 == round                   拆 `.` 取 [0] 与 round 比（非 dev 轮次可不带版本）
  S3  landed 必须在 dev 账本有条目               双向：landed→账本 / 账本→landed；
                                                `landed-no-ledger` 须有提交号且**该提交真在 git 里**；
                                                `land_status` 只接受 planned|landed|landed-no-ledger，
                                                §1 行与 §2 声明必须一致；
                                                **历史锚**：git 历史里的轮次集合必须被当前集合覆盖（只增不减）；
                                                **落地即入账**：某轮首次在历史 SEQUENCE 里标成 landed 的那个提交，
                                                账本必须同时已含该轮条目（planned→landed 的提交必须带账本）
  S4  `依赖` 必须存在且排在其前                  依赖号集合校验 + 文档位置比较（倒置/悬空）
  S5  范围引用的 WORKLIST 条目必须存在           读 `WORKLIST.md` 的 P/C/D 编号集合；
                                                无文件 = **声明过的** SKIP（不是静默放宽）；显式 `--worklist`
                                                指向不存在/不可读的路径 = 红（拼错不许退化成 skip）
  S6  计划 landed 数 == 账本条目数               计数比较；**另加历史下界**（不得低于历史最大值）
  S7  每条账本 `summary` == CHANGELOG_DEV 段落   逐字比较（工作区腿）+ **git 锚腿**（`commit` 的历史 blob）；
                                                无锚（`commit: null` / 浅克隆）→ 状态码 DEGRADED（不算 FAIL，但显式报）
  S8  `version` 严格递增且已落地版本不可变        与 git 历史里的账本版本**全字段严格前缀**比对（summary/files_sha256
                                                也在 append-only 范围内：任何改写/删除 = FAIL，不给 prose_note 放行）；
                                                条目 `commit` 必须 `git cat-file -e` 通过
  S9  `landed` 轮次必须有三席确认                **`landed` 与 `landed-no-ledger` 都要**（后者只是不占账本条目，
                                                不等于免确认）；`verified_by` = [SOL, GPT-SOL-6, PRO] 齐全
                                                （中英文方括号、全角逗号、有无空格都解析）、
                                                `verdict ∈ {pass, pass-with-notes(notes 非空)}`、
                                                `verified_at ≤ 账本条目 date`；`legacy` 轮次豁免但须有 note，
                                                且**只有白名单轮次（LEGACY_ROUNDS = R44/R45）可自称 legacy**；
                                                `verified_by` 里出现白名单外的席位名 → 红
  S10 `_DEV` 文件只属于 dev 分支                 **判 template 身份只用 git 锚**（`git log -- _DEV 文件` 非空），
                                                不看 `.workspace/bootstrap.json` 等可编辑 JSON
                                                （否则把 workspace_status 改成 active 就能关掉本条）；
                                                dev 上必须存在、main 上必须不存在、其它分支只告警；
                                                非模板仓（历史里从未出现 _DEV 文件）只 WARN，不判红；
                                                **没有 SEQUENCE.md 时仍要执行**（main 的合法态正是没有计划文件）
  S11 远端锚（增强层）                          `origin/<当前分支>` 可用 → 本地历史必须是它的超集
                                                （`merge-base --is-ancestor origin/x HEAD`）；
                                                远端不可用（无远端 / 离线 / 未推送）→ WARN 且**明示「仅有本地锚」**
  S12 迭代日志冻结锚                            `.workspace/iterations/R<round>.md`（每轮一个、append-only）：
                                                账本条目带 `iterations_sha256` ⇒ 当前文件 sha256 必须一致
                                                （landed 后回改日志 = 红）；带 `iterations` ⇒ 文件必须存在、
                                                `## it<N>` 节数一致、编号 1..N 连续；
                                                两者都没有 ⇒ **不判**（历史条目 legacy 豁免，不回填）
  S13 §1 landed 行整行冻结（append-only rows）  `git log -- .workspace/SEQUENCE.md` 取各历史版本 blob →
                                                解析 §1 表格**数据行**（不含表头与分隔行）→ 历史全集与
                                                当前 §1 数据行**逐字**比对；历史行缺任一 = 红（贴出缺行
                                                原文与来源提交）。当前**多出新行**（追加）不红。
                                                判据建立前已发生的合法改写由代码内白名单
                                                （`S13_PREEXISTING_EDITS`）豁免，改它必须改代码、diff 留痕

状态取值：`PASS` / `FAIL` / `SKIP`（声明过的豁免）/ `DEGRADED`（锚不可用，已降级但可见）/ `WARN`
（不构成失败）。**只有 FAIL 影响退出码**；DEGRADED 与 WARN 会逐行打印并在汇总里计数。
`DEGRADED` 是**独立状态码**（不是 `PASS` + detail 里的一句提示）：S7 遇到未提交条目
（`commit: null`）就返回它，人读汇总数得到 `N DEGRADED(S7)`，`--json` 里
`checks[].status` 与顶层 `degraded` 都能机器读出——降级可见，但不算 FAIL。

实例侧行为：派生工作区**没有** `.workspace/SEQUENCE.md`（研发路线图是 dev 专用），这时**显式
报告 skip、退出码 0**——不崩、不假红。`check_all.py` 会在实例侧照常调度本阶段。
例外有两处：① S10/S11 只依赖当前分支、git 历史与两份 `_DEV` 文件，**没有计划文件时仍执行**；
② 模板仓的 **dev 分支**缺 `SEQUENCE.md` 是**红**（历史里出现过计划文件或 `_DEV` 文件），
删计划不能把 S1–S9 与 S12/S13 的判据整条关掉——SKIP 只留给实例侧 / 非模板仓。

用法：
  python3 scripts/sequence_check.py                 # 人读
  python3 scripts/sequence_check.py --json          # 机器可读（stdout 只有 JSON）
  python3 scripts/sequence_check.py --root <dir>    # 指定树根（默认 = 本脚本所在仓）
  python3 scripts/sequence_check.py --worklist <p>  # 指定 WORKLIST.md（默认按候选路径找）

退出码：0 全部通过或显式 skip；1 有 findings；2 用法/IO。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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
BOOTSTRAP_REL = ".workspace/bootstrap.json"
# 迭代日志（S12）：dev 专属，**不进** TEMPLATE_FILES。每轮一个文件、append-only，一次尝试一节
# （`## it<N> · YYYY-MM-DD`）。它承载「这一轮每次尝试的设计取舍与遗留项」，不替代 SEQUENCE 的轮次计划。
ITER_DIR_REL = ".workspace/iterations"

# 四段 dev 版本：NN.X.Y.Z，无前导零（与 release.py 的 dev_version_tuple 同形）
VER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# 任何带点号的版本形态（用于判「版本写错段数」——三段版本不能因为解析不出就放过）
VERSIONISH_RE = re.compile(r"\d+(?:\.\d+)+")
# §2 的小标题：`### R47 · `47.3.8.0` · stdio 三件套`
PLANNED_HEAD_RE = re.compile(r"^###\s+R(\d+)\b(.*)$")
# §2 的字段行：`- **范围**：…` / `- **依赖**：R45` / `- **land_status**：landed`
FIELD_RE = re.compile(r"^-?\s*\*\*([^*]+)\*\*\s*[：:]\s*(.*)$")
# 依赖/引用里的轮次号：`R47`、`R47–R51 全绿`、`R49（必须验迁移后的实现）`
ROUND_REF_RE = re.compile(r"R(\d+)\b")
# 范围里的 WORKLIST 条目号：`C11`、`D5`、`P0`
WORKLIST_ID_RE = re.compile(r"\b([PCD]\d{1,3})\b")
# 提交号：7–40 位十六进制
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
H2_RE = re.compile(r"^##\s")
SECTION_RE = re.compile(r"^##\s+(\d+)\.")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# WORKLIST.md 的候选位置（工作区级，SEQUENCE.md §5）：本仓 → 父目录 → 祖父目录。
# 模板仓在 `<workspace>/sandbox-*/<task>` 里时，WORKLIST 在第 2 级父目录。
WORKLIST_CANDIDATES = ("WORKLIST.md", "../WORKLIST.md", "../../WORKLIST.md")

LANDED = "landed"
LANDED_NO_LEDGER = "landed-no-ledger"
PLANNED = "planned"
LEGAL_STATUSES = (PLANNED, LANDED, LANDED_NO_LEDGER)

# 三席（R46 起所有 landed 轮次必须齐全；legacy 轮次豁免但须写明原因）
SEATS = ("SOL", "GPT-SOL-6", "PRO")
LEGAL_VERDICTS = ("pass", "pass-with-notes")

# `legacy` 的**白名单轮次**（S9）。只有这两个轮次可以写 `verified_by: legacy`。
#
# 来由（照抄 SEQUENCE.md §1 的备注，不是随口列的）：R44 = `e3ec3a5`，首个 dev 账本条目，
# 产生于「轮次级三席确认」这条判据建立**之前**，当时只有双修复席产出 + 控制席复验；R45 = `f0ed3fd`，
# 机制轮（dev 四段版本机制本身），不占版本号、也没有轮次级确认。两者都早于 S9 生效。
#
# 为什么写成常量而不是配置文件：`legacy` 是一条豁免口子，必须**改代码才能扩**——
# 扩白名单这件事在 diff 里留痕、要过评审；写成可编辑的名单文件等于给了一条静默旁路，
# 新轮次只要往文件里加一行就绕过了三席确认。R46 起的新轮次自称 `legacy` 一律红。
LEGACY_ROUNDS = frozenset({44, 45})

# S13 的「判据建立前已发生的合法改写」白名单：键 = §1 数据行**原文**的 sha256[:16]。
#
# 来由（真实历史，不是随口列的）：`e895711`（计划首次落盘）的 R45 行确认列写 `` `legacy`（同上） ``，
# `464e0c9`（R46c）把它改成 `` `legacy`（机制建立前；本副本以完整树基线提交为 git 对象锚） ``——
# 这次改写发生在 S13 存在之前，性质是**说明变清楚**，不是结论漂移。S13 若对「历史全集」无条件判，
# 真实树在判据落地当天就红，判据一上线就被迫破例。
# 写成代码常量而不是可编辑名单（与 LEGACY_ROUNDS 同理）：扩白名单必须改代码、在 diff 里留痕。
# 豁免只作用于「这条**旧行原文**不在当前版本里」的缺行判定——当前行自身被改写仍会红
# （新文本不在白名单里），所以它不是一条可用来放行新改写的旁路。
S13_PREEXISTING_EDITS = {
    "00b1277e17991779": "e895711 的 R45 行确认列在 464e0c9 被改写"
                        "（`legacy`（同上）→ `legacy`（机制建立前；…））；S13 建立前的说明澄清",
}

PASS, FAIL, SKIP, DEGRADED, WARN = "PASS", "FAIL", "SKIP", "DEGRADED", "WARN"

# 只在 dev 分支存在的两份文件（SEQUENCE.md 的 §0「边界」）
DEV_FILES = (CHANGELOG_REL, LEDGER_REL)

# 同一轮次里 §1 表格的列名别名（按表头定位，不按位置猜——列序变了不该静默错配）
_COL_ALIASES = {
    "round": ("round", "轮次"),
    "version": ("version", "版本"),
    "commit": ("提交", "commit", "提交号"),
    "scope": ("范围", "scope"),
    "confirm": ("确认", "verified_by", "三席确认"),
    "note": ("备注", "note"),
}


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
        self.status_invalid: str | None = None       # §2 里出现非法 land_status 取值时记原文
        self.deps: list[int] = []
        self.scope = ""
        self.criteria = ""
        self.confirm_raw = ""                        # §1「确认」列的原文
        self.note_cell = ""                          # §1「备注」列的原文
        self.verified_by: list[str] = []             # 三席名单（legacy 轮次为空）
        self.verdict: str | None = None
        self.verified_at: str | None = None
        self.notes = ""                              # pass-with-notes / legacy 的说明

    def __repr__(self) -> str:                      # 调试用
        return f"<Round R{self.round} {self.status} {self.version}>"

    @property
    def legacy(self) -> bool:
        """legacy = 机制建立前的轮次：确认列写 `legacy`（S9 豁免，但必须有 note）。"""
        return bool(re.search(r"\blegacy\b", self.confirm_raw, re.I))

    @property
    def legacy_note(self) -> str:
        """legacy 轮次的豁免说明：§2 notes / §1 备注 / 确认列里的括号补充，取第一个非空的。"""
        if self.notes:
            return self.notes
        if self.note_cell:
            return self.note_cell
        m = re.search(r"[（(]([^）)]*)[）)]", self.confirm_raw)
        return m.group(1).strip() if m else ""


def _table_cells(line: str) -> list[str] | None:
    """把 `| a | b |` 拆成单元格；表头分隔行（`|---|`）返回 None。"""
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return None
    return cells


def _col(cols: dict[str, int], key: str, default: int) -> int:
    """取列号：表头里有就用表头（子串匹配，容忍 `确认（verified_by / verdict）`），否则退回默认。"""
    for alias in _COL_ALIASES[key]:
        for name, idx in cols.items():
            if name == alias or alias in name:
                return idx
    return default


def _cell(cells: list[str], idx: int) -> str:
    return cells[idx] if 0 <= idx < len(cells) else ""


def section1_rows(text: str) -> list[str]:
    """§1「已落地」表格的**数据行原文**（不含表头与分隔行，也不含表外散文）。

    判定域与 `parse_sequence` 的 §1 分支一致：`|` 起头、至少 5 列、首列是纯数字轮次号。
    只 `rstrip()` 掉行尾空白（CR / 尾随空格不参与比较），其余逐字——S13 的「冻结」按字节算：
    改一个字、改一列、整行删除都会让该行不再出现在当前版本里（追加新行不受影响）。
    """
    rows: list[str] = []
    section: int | None = None
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            section = int(m.group(1))
            continue
        if line.startswith("## "):
            section = None
            continue
        if section != 1:
            continue
        cells = _table_cells(line)
        if cells is None or len(cells) < 5 or not cells[0].isdigit():
            continue
        rows.append(line.rstrip())
    return rows


def _parse_confirm(cell: str) -> dict:
    """解析「确认」单元格里的结构化字段。

    合法形态（两种都收）：
      `legacy`（机制建立前…）                                   → legacy 轮次
      verified_by=[SOL, GPT-SOL-6, PRO]；verdict=pass；verified_at=2026-11-23

    方括号中英文都收（`[]` / `【】`）：文档席用全角括号写三席是合法写法，
    解析器不认就等于把合法记录判红。
    """
    out = {"verified_by": [], "verdict": None, "verified_at": None, "notes": ""}
    if not cell:
        return out
    m = re.search(r"verified_by\s*[=:：]\s*[\[【]([^\]】]*)[\]】]", cell)
    if m:
        out["verified_by"] = _parse_seats(m.group(1))
    m = re.search(r"verdict\s*[=:：]\s*([A-Za-z][\w-]*)", cell)
    if m:
        out["verdict"] = m.group(1)
    m = re.search(r"verified_at\s*[=:：]\s*(\d{4}-\d{2}-\d{2})", cell)
    if m:
        out["verified_at"] = m.group(1)
    m = re.search(r"notes\s*[=:：]\s*([^；;]*)", cell)
    if m:
        out["notes"] = m.group(1).strip()
    return out


# 席位分隔符：半角/全角逗号、顿号、分号（文档席混用是常态）
_SEAT_SPLIT_RE = re.compile(r"[,，、;；]+")
# 席位名两侧允许的包装：反引号 + 中英文方括号/圆括号
_SEAT_WRAP = "`[]【】()（）"


def _parse_seats(value: str) -> list[str]:
    """把 `[SOL, GPT-SOL-6, PRO]` / `【SOL、GPT-SOL-6、PRO】` / `SOL,GPT-SOL-6,PRO` 解析成三个席位名。

    R46d 前这里直接对整串 split，`[SOL` / `PRO]` 带着方括号进名单，三席判据把**合法记录**判红。
    方括号、括号、反引号与空格都是包装而不是席位名的一部分，统一在解析时剥掉。
    """
    v = value.strip().strip(_SEAT_WRAP).strip()
    seats: list[str] = []
    for part in _SEAT_SPLIT_RE.split(v):
        name = part.strip().strip(_SEAT_WRAP).strip()
        if name:
            seats.append(name)
    return seats


def parse_sequence(text: str) -> list[Round]:
    """解析 SEQUENCE.md：§1 已落地表 + §2 计划块。其余小节（§3 映射表、§4 判据表、§5 散文）不参与。

    解析不出来就当**格式漂移**处理（S1 红），不静默通过。
    """
    rounds: list[Round] = []
    section: int | None = None
    current: Round | None = None
    field: str | None = None                       # 当前正在累积的字段名（多行续行用）
    cols: dict[str, int] = {}                       # §1 表头列名 → 列号

    for no, line in enumerate(text.splitlines(), 1):
        m = SECTION_RE.match(line)
        if m:
            section, current, field = int(m.group(1)), None, None
            cols = {}
            continue
        if line.startswith("## "):                 # 非数字编号的 H2（如序言外的其它节）
            section, current, field = None, None, None
            continue

        if section == 1:
            cells = _table_cells(line)
            if cells is None or len(cells) < 5:
                continue
            head = cells[0]
            if not head.isdigit():                 # 表头 `round`：记列名映射
                cols = {name.strip(): i for i, name in enumerate(cells) if name.strip()}
                continue
            r = Round(int(head), no, f"§1 第 {no} 行")
            version = _cell(cells, _col(cols, "version", 1)).strip("` ")
            r.version_raw = version
            if VER_RE.match(version):
                r.version, r.status = version, LANDED
            else:
                # 不占版本号的已落地轮次（如 R45「并入 44 条」）：须有提交号
                r.version, r.status = None, LANDED_NO_LEDGER
            commit = _cell(cells, _col(cols, "commit", 2)).strip("` ")
            r.commit = commit if commit else None
            r.scope = _cell(cells, _col(cols, "scope", 3))
            r.confirm_raw = _cell(cells, _col(cols, "confirm", -1))
            r.note_cell = _cell(cells, _col(cols, "note", len(cells) - 1 if len(cells) > 5 else -1))
            conf = _parse_confirm(r.confirm_raw)
            r.verified_by = conf["verified_by"]
            r.verdict, r.verified_at, r.notes = conf["verdict"], conf["verified_at"], conf["notes"]
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

        if line.startswith("#"):                   # 别的标题（### 之外的）结束当前块
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
                if status in LEGAL_STATUSES:
                    current.status = status
                else:
                    # 非法取值**不许**按计划态放过（那会让「landed 却无账本」静默变绿）：S3 判红
                    current.status_invalid = status
            elif field == "verified_by":
                current.verified_by = _parse_seats(value)
                current.confirm_raw = current.confirm_raw or value
            elif field == "verdict":
                current.verdict = value.strip().strip("`") or None
            elif field == "verified_at":
                current.verified_at = value.strip().strip("`") or None
            elif field in ("notes", "note"):
                current.notes = value or current.notes
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


# ── git 锚 ────────────────────────────────────────────────────────────────────
# 只读：全部走 `git` 子进程（osnorm.run_text 恒捕获 + utf-8），不写盘、不联网。
# 为什么必须走 git：账本与 changelog 都是**同轮可改**的工作区文件，两边一起改就能自证一致
# （三席在 R46 复核里给出的端到端伪造路径）。git 历史是第三方留痕：条目一旦提交过，改写/
# 删除就会在「历史版本 → 当前工作区」这条链上露出来。

def _git(root: Path, *args: str) -> tuple[int, str]:
    """跑一条只读 git 命令；缺 git / 非仓库都不抛，按 rc≠0 返回。"""
    try:
        proc = osnorm.run_text(["git", *args], cwd=str(root), errors="replace")
    except OSError:
        return 127, ""
    return proc.returncode, (proc.stdout or "")


def git_branch(root: Path) -> str | None:
    rc, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = out.strip()
    return branch if rc == 0 and branch and branch != "HEAD" else None


def git_show(root: Path, ref: str, rel: str) -> str | None:
    """取 `ref:rel` 的文本；不存在/不是仓库返回 None。"""
    rc, out = _git(root, "show", f"{ref}:{rel}")
    return out if rc == 0 else None


def git_commit_exists(root: Path, sha: str | None) -> bool:
    if not sha or not COMMIT_RE.match(sha):
        return False
    rc, _ = _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    return rc == 0


def git_is_shallow(root: Path) -> bool:
    """浅克隆（CI 默认 checkout）拿不到历史对象：这类「查不到」不等于「是编的」。"""
    rc, out = _git(root, "rev-parse", "--is-shallow-repository")
    return rc == 0 and out.strip() == "true"


def git_log_path(root: Path, rel: str) -> list[str]:
    """该路径的全部提交（新→旧）。"""
    rc, out = _git(root, "log", "--format=%H", "--", rel)
    return out.split() if rc == 0 else []


def _entries_fingerprint(entry: dict) -> str:
    """条目的内容指纹：改一个字符（summary/commit/date…）就变，用来判「历史条目被改写」。"""
    blob = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _ledger_at(root: Path, ref: str) -> tuple[list[dict] | None, str | None]:
    text = git_show(root, ref, LEDGER_REL)
    if text is None:
        return None, f"{ref}:{LEDGER_REL} 取不到"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{ref}:{LEDGER_REL} 不是合法 JSON（{exc}）"
    if not isinstance(data, list):
        return None, f"{ref}:{LEDGER_REL} 不是 JSON 数组"
    return [e for e in data if isinstance(e, dict)], None


def collect_history(root: Path) -> dict:
    """一次性收集 git 历史里的账本/SEQUENCE 版本（新→旧），供 S3/S6/S7/S8/S13 共用。"""
    info: dict = {"is_repo": False, "branch": None, "ledger_versions": [], "seq_versions": [],
                  "seq_rows": [], "errors": [], "ledger_commits": [], "seq_commits": []}
    branch = git_branch(root)
    if branch is None:
        info["errors"].append("不是 git 仓库（或缺 git / 游离 HEAD）")
        return info
    info["is_repo"], info["branch"] = True, branch
    info["ledger_commits"] = git_log_path(root, LEDGER_REL)
    for sha in info["ledger_commits"]:
        entries, err = _ledger_at(root, sha)
        if err is None:
            info["ledger_versions"].append((sha, entries))
        else:
            info["errors"].append(err)
    info["seq_commits"] = git_log_path(root, SEQ_REL)
    for sha in info["seq_commits"]:
        text = git_show(root, sha, SEQ_REL)
        if text is None:
            info["errors"].append(f"{sha}:{SEQ_REL} 取不到")
            continue
        info["seq_versions"].append((sha, parse_sequence(text)))
        # §1 数据行的原文（S13 的锚）：与 rounds 同一份 blob 解析，不额外跑 git
        info["seq_rows"].append((sha, section1_rows(text)))
    return info


def ever_landed_rounds(history: dict) -> dict[int, str]:
    """「曾经存在过的轮次」集合（union）：任一历史版本里入过账、或在 SEQUENCE 里标过落地的轮次。

    S7 用它判「这一轮是不是历史轮次」（决定锚要不要按历史条目从严）；S3 则分两路各自比对
    （账本历史 → 必须在账本里；SEQUENCE 历史 → 必须仍标 landed），两路的集合语义不同，不能混。
    """
    seen = ever_ledger_rounds(history)
    for rnd, sha in ever_seq_landed(history).items():
        seen.setdefault(rnd, sha)
    return seen


def ever_ledger_rounds(history: dict) -> dict[int, str]:
    """历史账本里出现过的轮次（首次出现的提交）。账本 append-only：这些轮次必须仍在账本里。"""
    seen: dict[int, str] = {}
    for sha, entries in history["ledger_versions"]:
        for e in entries:
            rnd = e.get("round")
            if isinstance(rnd, int):
                seen.setdefault(rnd, sha)
    return seen


def ever_seq_landed(history: dict) -> dict[int, str]:
    """历史 SEQUENCE 版本里标过「落地」的轮次（首次出现的提交）。

    落地 = `landed` **或** `landed-no-ledger`：两者都是「这一轮已经交付」的声明，只是后者没占账本条目
    （R45 就是这一类：机制轮不占版本号，`land_status = landed-no-ledger`）。只认 `LANDED` 会让
    「删掉 §1 里那一行 landed-no-ledger」从历史锚下面漏过去——而 R46 恰恰依赖 R45，
    删掉 R45 的行再顺手把 R46 的依赖改成 R44，两边就都「自洽」了。
    """
    seen: dict[int, str] = {}
    for sha, rounds in history["seq_versions"]:
        for r in rounds:
            if r.status in (LANDED, LANDED_NO_LEDGER):
                seen.setdefault(r.round, sha)
    return seen


def first_landed_commits(history: dict) -> dict[int, str]:
    """每轮**首次**在历史 SEQUENCE 里被标成 `landed` 的那个提交（旧→新扫，取最早）。

    H「落地即入账」的输入：landed 的**首次出现**必须与账本条目同批。`landed-no-ledger`
    明确不占账本条目，不在此列（它由 S3 的提交号 + S9 的三席确认各自把关）。
    """
    seen: dict[int, str] = {}
    for sha, rounds in reversed(history["seq_versions"]):
        for r in rounds:
            if r.status == LANDED:
                seen.setdefault(r.round, sha)
    return seen


def history_bounds(history: dict) -> tuple[int, int]:
    """历史下界（账本条目数 / SEQUENCE landed 数）：只增不减。"""
    max_ledger = max((len(e) for _, e in history["ledger_versions"]), default=0)
    max_landed = max((sum(1 for r in rs if r.status == LANDED) for _, rs in history["seq_versions"]),
                     default=0)
    return max_ledger, max_landed


# 账本条目的结构性事实：哪一轮、什么版本、哪一天、对应哪个提交/标签。一旦提交就不许再变。
STRUCT_FIELDS = ("round", "version", "date", "commit", "tag")


def _struct_view(entry: dict) -> str:
    return json.dumps({k: entry.get(k) for k in STRUCT_FIELDS}, sort_keys=True, ensure_ascii=False)


def compare_append_only(older: list[dict], newer: list[dict], older_ref: str,
                        newer_ref: str) -> list[dict]:
    """旧版本必须是新版本的前缀（同序、同内容），新版本只多不少。

    比较是**全字段**的：summary / files_sha256 与 round/version/date/commit/tag 一样属于
    append-only 范围。R46d 曾把两个**已提交**历史版本之间的 summary 差异降级成 prose_note
    （`strict=False`），结果是「改旧条目 summary + 同步改 CHANGELOG + 普通 commit」全绿——
    那正是 append-only 要挡的改写。已提交的历史条目之间没有任何「未提交末条」的豁免理由：
    真要做到同步改写就得重写 git 历史，属于 S11/权限层的事，不由这里放行。
    """
    findings: list[dict] = []
    for i, old in enumerate(older):
        if i >= len(newer):
            findings.append(_fail(f"账本历史条目被删除：{older_ref} 有 {old.get('round')} 轮"
                                  f"（{old.get('version')}），但 {newer_ref} 只到 {len(newer)} 条"))
            continue
        new = newer[i]
        if old.get("round") != new.get("round") or _struct_view(old) != _struct_view(new):
            findings.append(_fail(f"账本历史条目被改写：第 {i + 1} 条 {older_ref} 记 round="
                                  f"{old.get('round')} / {old.get('version')} / date={old.get('date')}"
                                  f" / commit={old.get('commit')}，{newer_ref} 是 round="
                                  f"{new.get('round')} / {new.get('version')} / date={new.get('date')}"
                                  f" / commit={new.get('commit')}——账本 append-only，历史条目不可改"))
        elif _entries_fingerprint(old) != _entries_fingerprint(new):
            findings.append(_fail(f"账本历史条目被改写：{older_ref} 的 round {old.get('round')} 内容"
                                  f"与 {newer_ref} 不同（指纹 {_entries_fingerprint(old)} → "
                                  f"{_entries_fingerprint(new)}）——append-only 覆盖全部字段"
                                  f"（summary / files_sha256 也在内）"))
    return findings


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


def _first_diff_line(want: str, got: str) -> tuple[int, str, str]:
    want_lines, got_lines = want.split("\n"), got.split("\n")
    at = next((i for i, (a, b) in enumerate(zip(want_lines, got_lines)) if a != b),
              min(len(want_lines), len(got_lines)))
    a = want_lines[at] if at < len(want_lines) else "<账本到此结束>"
    b = got_lines[at] if at < len(got_lines) else "<段落到此结束>"
    return at, a, b


def find_worklist(root: Path, explicit: str | None) -> tuple[Path | None, list[str], bool]:
    """返回 (路径, 尝试过的候选, 显式路径是否缺失)。"""
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else (root / p)
        return (p if p.is_file() else None), [str(p)], not p.is_file()
    tried: list[str] = []
    for rel in WORKLIST_CANDIDATES:
        p = (root / rel).resolve()
        tried.append(str(p))
        if p.is_file():
            return p, tried, False
    return None, tried, False


def _fail(msg: str, rnd: Round | None = None, line: int | None = None,
          file: str | None = None) -> dict:
    return {"round": f"R{rnd.round}" if rnd else None, "line": line, "file": file, "message": msg}


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
    return (PASS if not findings else FAIL), detail, findings


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
    return (PASS if not findings else FAIL), detail, findings


def check_s3(rounds: list[Round], ledger: list[dict], ledger_errs: list[str],
             history: dict) -> tuple[str, str, list[dict]]:
    findings: list[dict] = [_fail(e) for e in ledger_errs]
    by_round = {r.round: r for r in rounds if r.status in (LANDED, LANDED_NO_LEDGER)}
    ledger_by_round: dict[int, dict] = {}
    for e in ledger:
        rnd = e.get("round")
        if isinstance(rnd, int):
            ledger_by_round[rnd] = e

    # (a) land_status 取值严格化：非法值不许当成 planned 静默放过
    for r in rounds:
        if r.status_invalid is not None:
            findings.append(_fail(f"R{r.round} 的 land_status 取值非法：{r.status_invalid!r}"
                                  f"（只接受 planned / landed / landed-no-ledger）", r, r.line))

    # (b) §1 landed 表 ↔ §2 声明的 land_status 必须一致（双向）
    for r in rounds:
        if r.source.startswith("§1"):
            # 同一轮既在 §1 又在 §2：§2 的显式声明不得与表的落地态相反
            twin = next((x for x in rounds if x.round == r.round and x.source.startswith("§2")), None)
            if twin is not None and twin.status == PLANNED:
                findings.append(_fail(f"R{r.round} 在 §1 里是落地行（{r.status}），但 §2 声明 "
                                      f"land_status=planned（落地声明不一致）", r, r.line))
        else:
            if r.status in (LANDED, LANDED_NO_LEDGER) and not any(
                    x.round == r.round and x.source.startswith("§1") for x in rounds):
                findings.append(_fail(f"R{r.round} 在 §2 声明 land_status={r.status}，"
                                      f"但 §1 没有对应的落地行", r, r.line))

    # (c) 双向：landed → 账本；账本 → landed
    for r in rounds:
        if r.status == LANDED:
            if r.round not in ledger_by_round:
                findings.append(_fail(f"R{r.round} 标 landed 但 {LEDGER_REL} 没有 round {r.round} 的条目"
                                      f"（缺账本；未入账应标 landed-no-ledger 并给提交号）", r, r.line))
        elif r.status == LANDED_NO_LEDGER:
            if not r.commit or not COMMIT_RE.match(r.commit):
                findings.append(_fail(f"R{r.round} 标 landed-no-ledger 但提交列不是提交号"
                                      f"（实测 {r.commit!r}）", r, r.line))

    # (d) 提交号必须真在 git 里（不是只验正则）
    git_checked = 0
    shallow_skipped = 0
    root = _history_root(history)
    for r in rounds:
        if r.status not in (LANDED, LANDED_NO_LEDGER) or not r.commit or not COMMIT_RE.match(r.commit):
            continue
        if not history["is_repo"]:
            continue                             # 无 git 由 S8 判红，这里不重复
        if git_commit_exists(root, r.commit):
            git_checked += 1
        elif git_is_shallow(root):
            shallow_skipped += 1                 # 浅克隆：对象不在本地，不等于编造
        else:
            git_checked += 1
            findings.append(_fail(f"R{r.round} 的提交 {r.commit} 不在本仓 git 对象库"
                                  f"（浅克隆/副本缺对象，或提交号是编的）", r, r.line))

    for rnd, e in sorted(ledger_by_round.items()):
        r = by_round.get(rnd)
        if r is None:
            findings.append(_fail(f"{LEDGER_REL} 有 round {rnd}（{e.get('version')}），"
                                  f"但 SEQUENCE 没有对应已落地轮次（账本多出）"))
        elif r.status != LANDED:
            findings.append(_fail(f"{LEDGER_REL} 有 round {rnd}，但 SEQUENCE 里 R{rnd} 是 "
                                  f"{r.status}（账本多出/状态不符）", r, r.line))

    # (e) 历史锚：曾经存在过的轮次必须仍然存在（成对删除在这里露出来）
    ever_ledger = ever_ledger_rounds(history) if history["is_repo"] else {}
    ever_landed = ever_seq_landed(history) if history["is_repo"] else {}
    cur_ledger = set(ledger_by_round)
    # 「仍算落地」= 仍是 §1 的落地行（landed 或 landed-no-ledger）。降级成 planned / 整行消失都算撤销。
    cur_landed = {r.round for r in rounds if r.status in (LANDED, LANDED_NO_LEDGER)}
    for rnd in sorted(ever_ledger):
        if rnd not in cur_ledger:
            findings.append(_fail(f"R{rnd} 曾在账本里（首次出现 {ever_ledger[rnd][:7]}），"
                                  f"但当前账本没有该条目——历史锚：账本 append-only，条目不可删除"))
    for rnd in sorted(ever_landed):
        if rnd not in cur_landed:
            findings.append(_fail(f"R{rnd} 曾在 SEQUENCE 标 landed/landed-no-ledger"
                                  f"（{ever_landed[rnd][:7]}），但当前不再有落地行"
                                  f"——历史锚：落地不可撤销/降级（删行、改回 planned 都算）"))

    # (f) 落地即入账：某轮**首次**在历史 SEQUENCE 里被标成 landed 的那个提交，账本必须同时已含该轮。
    #     这条与 (e) 的历史锚互补：(e) 只看「现在还在不在」，(f) 钉「落地那一刻账本是不是已经在了」。
    #     只改计划（planned→landed）并提交、账本不动 → 首次落地提交里的账本缺条目 → 红。
    sync_checked = 0
    if history["is_repo"] and history["seq_versions"]:
        cache: dict[str, tuple[list[dict] | None, str | None]] = {}
        for rnd, sha in sorted(first_landed_commits(history).items()):
            if sha not in cache:
                cache[sha] = _ledger_at(root, sha)
            entries, err = cache[sha]
            has = entries is not None and any(
                isinstance(e.get("round"), int) and e["round"] == rnd for e in entries)
            if not has:
                findings.append(_fail(f"R{rnd} 在 {sha[:7]}:{SEQ_REL} 首次标为 landed，"
                                      f"但该提交的 {LEDGER_REL} 没有该轮条目"
                                      f"（{err or '缺该轮条目'}）——落地必须与入账同批："
                                      f"把 planned 改成 landed 的提交必须同时带上账本条目"))
            else:
                sync_checked += 1

    detail = (f"landed {sum(1 for r in rounds if r.status == LANDED)} ↔ 账本 {len(ledger)} 条"
              f"{'双向一致' if not findings else '（不一致，见下）'}"
              f"；landed-no-ledger {sum(1 for r in rounds if r.status == LANDED_NO_LEDGER)} 条有提交号"
              f"（{git_checked} 个提交经 git 校验）"
              f"；历史锚 账本 {len(ever_ledger)} 轮 / landed {len(ever_landed)} 轮"
              f"{'⊆ 当前' if history['is_repo'] else '（无 git，未校验）'}"
              f"；落地即入账 {sync_checked} 轮同批")
    if shallow_skipped:
        detail += f"；浅克隆：{shallow_skipped} 个提交对象不在本地（未校验，git fetch --unshallow 可补）"
        if not findings:
            return DEGRADED, detail, findings
    return (PASS if not findings else FAIL), detail, findings


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
    return (PASS if not findings else FAIL), detail, findings


def check_s5(rounds: list[Round], worklist: Path | None, tried: list[str],
             explicit_missing: bool) -> tuple[str, str, list[dict]]:
    """返回 (status, detail, findings)。找不到 WORKLIST.md = **声明过的** SKIP（不是红、也不是放宽）。

    两种「找不到」必须分开：
      · 没传 `--worklist`、候选路径也没有 → SKIP（SEQUENCE.md §4/§5 声明过的豁免）；
      · 显式传了 `--worklist` 却不存在/不可读 → FAIL（拼错、路径过期不许退化成 skip，
        否则「写错一个字母」就等于把 S5 关掉了）。
    """
    if explicit_missing:
        return FAIL, "显式指定的 WORKLIST 路径不存在或不可读", [
            _fail("--worklist 指向的路径不存在或不可读：" + "；".join(tried)
                  + "（拼错不许退化成 SKIP）")]
    if worklist is None:
        return (SKIP, "WORKLIST.md 未找到（候选：" + "；".join(tried)
                + "）——S5 按 SEQUENCE.md §4 声明为豁免，范围引用未校验", [])
    try:
        text = osnorm.read_text(worklist)
    except OSError as exc:                       # 存在但读不了（权限/损坏的链接）→ 同样不许 SKIP
        return FAIL, f"WORKLIST 不可读：{worklist}", [
            _fail(f"--worklist 指向的 {worklist} 存在但读取失败：{type(exc).__name__}: {exc}")]
    ids = set(WORKLIST_ID_RE.findall(text))
    findings: list[dict] = []
    refs = 0
    for r in rounds:
        for token in set(WORKLIST_ID_RE.findall(r.scope + "\n" + r.criteria)):
            refs += 1
            if token not in ids:
                findings.append(_fail(f"R{r.round} 引用 {token}（范围/判据），但 {worklist.name} 里没有该编号",
                                      r, r.line))
    detail = f"{refs} 处 P/C/D 引用全部存在于 {worklist.name}"
    return (PASS if not findings else FAIL), detail, findings


def check_s6(rounds: list[Round], ledger: list[dict], history: dict) -> tuple[str, str, list[dict]]:
    landed = [r.round for r in rounds if r.status == LANDED]
    max_ledger, max_landed = history_bounds(history) if history["is_repo"] else (0, 0)
    findings: list[dict] = []
    if len(ledger) < max_ledger:
        findings.append(_fail(f"账本条目数 {len(ledger)} < 历史最大值 {max_ledger}"
                              f"（历史锚：账本只增不减，条目被删）"))
    if len(landed) < max_landed:
        findings.append(_fail(f"SEQUENCE landed 数 {len(landed)} < 历史最大值 {max_landed}"
                              f"（历史锚：落地轮次只增不减）"))
    if len(landed) != len(ledger):
        findings.append(_fail(f"计划 landed {len(landed)} 条（R{', R'.join(str(x) for x in landed) or '—'}）"
                              f"≠ 账本 {len(ledger)} 条，差 {abs(len(landed) - len(ledger))}"))
    detail = (f"landed {len(landed)} 条 == 账本 {len(ledger)} 条"
              f"（历史下界：账本 ≥{max_ledger} / landed ≥{max_landed}）"
              if not findings else "计数不一致")
    return (PASS if not findings else FAIL), detail, findings


def check_s7(ledger: list[dict], root: Path, history: dict) -> tuple[str, str, list[dict]]:
    """账本**每条** `summary` ↔ `CHANGELOG_DEV.md` 对应段落：工作区腿 + git 锚腿，两腿都要逐字一致。

    逐条两腿：
      · 工作区腿：当前 `CHANGELOG_DEV.md` 的 `## <version>` 段落必须等于账本 summary。
        连这一腿都不要只看锚：账本与 CHANGELOG 都是同轮可改的工作区文件，只对锚会放过
        「工作区里把段落删了/改了、锚还是旧的」这种半改状态。
      · git 锚腿：条目带 `commit` 且提交在本地对象库 → 该提交里的段落也必须等于 summary。
        这一腿挡的是「两边一起改、互相自证」：历史 blob 改不了（除非改写历史本身）。
      · 没有锚（`commit: null` 的未提交条目 / 浅克隆对象缺失）→ 整条判据返回 `DEGRADED`：
        独立状态码，不是「PASS + 一句 detail」，人读汇总与 `--json` 都读得出来。
    任一条对不上 → FAIL（降级不能掩盖漂移：先判漂移，再报有没有锚）。
    """
    if not ledger:
        return PASS, "账本为空：无 summary 可比", []
    path = root / CHANGELOG_REL
    work_text = osnorm.read_text(path) if path.is_file() else None
    findings: list[dict] = []
    degraded: list[str] = []
    anchors: list[str] = []
    for entry in ledger:
        version = str(entry.get("version") or "")
        want = str(entry.get("summary") or "").strip()
        sha = entry.get("commit")
        # ── 工作区腿 ──
        if work_text is None:
            findings.append(_fail(f"{CHANGELOG_REL} 不存在（账本 {version} 无从比对）"))
            continue
        start, body = section_body(work_text, version)
        if body is None:
            findings.append(_fail(f"工作区 {CHANGELOG_REL} 没有 `## {version}` 段落"
                                  f"（账本 {version} 无从比对）"))
            continue
        got = body.strip()
        if want != got:
            at, a, b = _first_diff_line(want, got)
            msg = (f"prose 漂移：账本 {version} summary ≠ 工作区 {CHANGELOG_REL} 段落"
                   f"（段内第 {at + 1} 行，账本 {len(want.splitlines())} 行 / "
                   f"段落 {len(got.splitlines())} 行）\n        账本：{a[:160]}\n        段落：{b[:160]}")
            findings.append(_fail(msg, line=(start or 0) + 1 + at, file=CHANGELOG_REL))
            continue
        # ── git 锚腿 ──
        if not sha:
            degraded.append(f"R{entry.get('round')}（commit: null，未提交）")
            continue
        if not history["is_repo"]:
            degraded.append(f"R{entry.get('round')}（无 git，commit {str(sha)[:7]} 无从校验）")
            continue
        if not git_commit_exists(root, str(sha)):
            if git_is_shallow(root):
                degraded.append(f"R{entry.get('round')}（浅克隆：commit {str(sha)[:7]} 对象不在本地）")
            else:
                findings.append(_fail(f"账本 {version} 的 commit {sha} 不在本仓 git 对象库"
                                      f"（浅克隆/副本缺对象，或提交号是编的）"))
            continue
        label = f"{str(sha)[:7]}:{CHANGELOG_REL}"
        blob = git_show(root, str(sha), CHANGELOG_REL)
        if blob is None:
            findings.append(_fail(f"{label} 取不到（账本 {version} 的提交锚）"))
            continue
        _, abody = section_body(blob, version)
        if abody is None:
            findings.append(_fail(f"git 锚 {label} 没有 `## {version}` 段落"
                                  f"（账本 {version} 无从比对）"))
            continue
        if abody.strip() != want:
            at, a, b = _first_diff_line(want, abody.strip())
            msg = (f"git 锚不一致：账本 {version} summary ≠ {label} 段落"
                   f"（段内第 {at + 1} 行）\n        账本：{a[:160]}\n        git 锚：{b[:160]}")
            findings.append(_fail(msg, file=label))
            continue
        anchors.append(label)
    if findings:
        return FAIL, f"账本 {len(ledger)} 条 summary 与 CHANGELOG 段落不一致", findings
    if degraded:
        return DEGRADED, (f"账本 {len(ledger)} 条 summary 与工作区 {CHANGELOG_REL} 段落逐字一致，"
                          f"但 {len(degraded)} 条没有 git 锚（{', '.join(degraded)}）："
                          f"S7 降级为 working-tree 比较（无 git 锚可对，不冒充通过）"), []
    return PASS, (f"账本 {len(ledger)} 条 summary 与工作区段落逐字一致"
                  f"；git 锚 {len(anchors)} 条同值（{', '.join(anchors)}）"), []


def check_s8(rounds: list[Round], ledger: list[dict], history: dict) -> tuple[str, str, list[dict]]:
    findings: list[dict] = []
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

    # (e) git 锚：账本 append-only 由历史证明，不由两个同样可改的当前文件互相证明
    anchor_note = ""
    if not history["is_repo"]:
        findings.append(_fail("git 锚不可用：不是 git 仓库（或缺 git）——"
                              "账本 append-only 无法证明，不接受「当前文件自证」"))
    else:
        versions = history["ledger_versions"]
        if not versions:
            anchor_note = "；git 历史里没有账本版本（首次入账，无历史可锚）"
        else:
            newest_sha, newest = versions[0]
            # 1) 当前工作区必须是**最新历史版本**的逐条前缀扩展（删/改/插/重排全红）
            findings.extend(compare_append_only(
                newest, ledger, f"{newest_sha[:7]}:{LEDGER_REL}", "当前工作区"))
            # 2) 历史自身 append-only：相邻已提交版本之间同样是**全字段**严格前缀比较
            #    （summary / files_sha256 被改写也是 FAIL —— C 判据）
            for older, newer in zip(versions[1:], versions):
                findings.extend(compare_append_only(
                    older[1], newer[1], f"{older[0][:7]}:{LEDGER_REL}",
                    f"{newer[0][:7]}:{LEDGER_REL}"))
            anchor_note = (f"；git 锚 {len(versions)} 个历史版本（{versions[0][0][:7]} 前缀一致，"
                           f"{len(ledger) - len(newest)} 条新增）")
            if len(versions) == 1:
                anchor_note += "（历史仅 1 个版本，跨提交 append-only 未观测）"

    committed = 0
    shallow_commits = 0
    for e in ledger:
        if e.get("commit"):
            if git_is_shallow(_history_root(history)) and not git_commit_exists(
                    _history_root(history), str(e["commit"])):
                shallow_commits += 1
                continue
            committed += 1
            if not git_commit_exists(_history_root(history), str(e["commit"])):
                findings.append(_fail(f"账本条目 {e.get('version')} 的 commit {e['commit']} "
                                      f"不在本仓 git 对象库"))
    anchor_note += f"；条目 commit 锚 {committed}/{committed} 通过"
    if shallow_commits:
        anchor_note += f"；浅克隆：{shallow_commits} 个条目 commit 对象不在本地（未校验）"

    detail = f"{len(versioned)} 个版本严格递增；已落地 {len(ledger)} 条与账本逐轮同值{anchor_note}"
    return (PASS if not findings else FAIL), detail, findings


def check_s9(rounds: list[Round], ledger: list[dict]) -> tuple[str, str, list[dict]]:
    """三席确认：`landed` 与 `landed-no-ledger` 轮次都必须有 SOL / GPT-SOL-6 / PRO 的判定与证据。

    `landed-no-ledger` 只表示「不占账本条目」（机制轮），**不是**三席确认的豁免——
    R46d 前 §1 插一行 landed-no-ledger 就能把 S9 整条跳过，而 S3 只验提交号存在，等于全绿。
    legacy 仍豁免，但只有白名单轮次可自称且必须写原因。
    """
    findings: list[dict] = []
    ledger_by_round = {e.get("round"): e for e in ledger if isinstance(e.get("round"), int)}
    seats_ok = legacy_ok = 0
    for r in rounds:
        if r.status not in (LANDED, LANDED_NO_LEDGER) or r.status_invalid is not None:
            continue
        if r.legacy:
            if r.round not in LEGACY_ROUNDS:
                findings.append(_fail(f"R{r.round} 自称 legacy，但不在 legacy 白名单 "
                                      f"{sorted(LEGACY_ROUNDS)} 里——新轮次必须走三席确认"
                                      f"（verified_by=[SOL, GPT-SOL-6, PRO] + verdict + verified_at）",
                                      r, r.line))
            elif not r.legacy_note:
                findings.append(_fail(f"R{r.round} 标 legacy 豁免，但没有写原因"
                                      f"（SEQUENCE.md §1 备注 / §2 notes 至少一处非空）", r, r.line))
            else:
                legacy_ok += 1
            continue
        got = {s.casefold() for s in r.verified_by}
        missing = [s for s in SEATS if s.casefold() not in got]
        unknown = [s for s in r.verified_by if s.casefold() not in {x.casefold() for x in SEATS}]
        if not r.verified_by:
            findings.append(_fail(f"R{r.round} 标 {r.status} 但没有任何 verified_by 记录"
                                  f"（§1 确认列 / §2 verified_by 为空）", r, r.line))
        elif missing:
            findings.append(_fail(f"R{r.round} 三席不全：缺 {', '.join(missing)}"
                                  f"（实测 verified_by={r.verified_by}）", r, r.line))
        if unknown:
            findings.append(_fail(f"R{r.round} verified_by 含未知席位：{', '.join(unknown)}"
                                  f"（只认 {' / '.join(SEATS)}）", r, r.line))
        verdict = (r.verdict or "").strip().strip("`")
        if not verdict:
            findings.append(_fail(f"R{r.round} 缺 verdict（pass / pass-with-notes）", r, r.line))
        elif verdict not in LEGAL_VERDICTS:
            findings.append(_fail(f"R{r.round} verdict 取值非法：{verdict!r}"
                                  f"（只接受 pass / pass-with-notes）", r, r.line))
        elif verdict == "pass-with-notes" and not r.notes:
            findings.append(_fail(f"R{r.round} verdict=pass-with-notes 但 notes 为空", r, r.line))
        when = (r.verified_at or "").strip()
        if not when or not ISO_DATE_RE.match(when):
            findings.append(_fail(f"R{r.round} 缺 verified_at（YYYY-MM-DD）或形态非法：{when!r}",
                                  r, r.line))
        else:
            e = ledger_by_round.get(r.round)
            date = str((e or {}).get("date") or "")
            if e is not None and ISO_DATE_RE.match(date) and when > date:
                findings.append(_fail(f"R{r.round} 先记账后确认：verified_at {when} 晚于账本条目 date "
                                      f"{date}（②必须在④之前）", r, r.line))
        if not missing and not unknown and verdict in LEGAL_VERDICTS:
            seats_ok += 1
    detail = (f"landed 三席确认 {seats_ok} 轮齐全；legacy 豁免 {legacy_ok} 轮（均有 note）"
              if not findings else "见下")
    return (PASS if not findings else FAIL), detail, findings


def check_s10(root: Path, is_template: bool | None) -> tuple[str, str, list[dict]]:
    """_DEV 文件的分支归属：dev 必须有、main 必须没有、其它分支只告警。

    `is_template` 来自 `template_repo()` 的 **git 锚**（历史里是否出现过 _DEV 文件），
    不读 `.workspace/bootstrap.json`：那份 JSON 是工作区里可编辑的文件，把它当判据输入
    等于允许「改一个 JSON 字段就关掉 S10」（A 判据的三席实跑 rc=0 就是这么来的）。
    `None` = 无法判定（缺 git / 没有历史），按 DEGRADED 报，不冒充 PASS。
    """
    if is_template is None:
        return DEGRADED, "无法判定模板身份（不是 git 仓库 / 缺 git / 无提交历史）：_DEV 文件归属未判", []
    if not is_template:
        return WARN, ("非模板仓（git 历史里从未出现 _DEV 文件）：无 dev/main 边界，"
                      "_DEV 文件归属未判（不判红）"), []
    branch = git_branch(root)
    if branch is None:
        return DEGRADED, "无法判定分支（不是 git 仓库 / 缺 git / 游离 HEAD）：_DEV 文件归属未判", []
    present = [f for f in DEV_FILES if (root / f).is_file()]
    missing = [f for f in DEV_FILES if not (root / f).is_file()]
    if branch == "dev":
        if missing:
            return FAIL, f"dev 分支缺 _DEV 文件：{', '.join(missing)}", [
                _fail(f"当前分支 dev，但缺 {', '.join(missing)}"
                      f"（两份 _DEV 文件只在 dev 分支存在，dev 上必须齐）")]
        return PASS, f"dev 分支：{len(present)}/{len(DEV_FILES)} 个 _DEV 文件齐备", []
    if branch == "main":
        if present:
            return FAIL, f"main 分支上存在 _DEV 文件：{', '.join(present)}", [
                _fail(f"当前分支 main，但存在 {', '.join(present)}"
                      f"（发布到 main 时这些文件必须消失：SEQUENCE.md §0 / AGENT_RULES.custom §14.5）")]
        return PASS, "main 分支：_DEV 文件 0 个（符合发布态）", []
    return WARN, f"分支 {branch} 既非 dev 也非 main：_DEV 文件归属未判（存在 {len(present)}/{len(DEV_FILES)}）", []


def check_s11(root: Path, branch: str | None) -> tuple[str, str, list[dict]]:
    """远端锚（增强层）：`origin/<当前分支>` 可用时，本地历史必须是它的**超集**。

    主防线仍是本地锚（S3/S6/S7/S8）：它能挡误删、误改、三文本同步改写、成对删除；
    挡不住「蓄意重写整条可达历史」。已推送过的历史由远端固定，所以远端可用时必须再对一次账——
    但远端经常不可用（离线 / 无远端 / 新分支尚未推送），因此远端不在时**只 WARN 并明示「仅有本地锚」**，
    不判红：拿不到证据不等于有违规。反过来，一旦 `origin/x` 存在就不是超集 → 红，那不是「不确定」而是
    「本地历史比远端少了东西/被改过」。
    """
    if branch is None:
        return WARN, ("无法判定当前分支（非 git 仓库 / 缺 git / 游离 HEAD）："
                      "仅有本地锚，未做远端对账"), []
    ref = f"origin/{branch}"
    rc, out = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = out.strip()
    if rc != 0 or not sha:
        return WARN, (f"{ref} 不可用（无远端 / 离线 / 尚未推送）：仅有本地锚，未做远端对账"), []
    rc, _ = _git(root, "merge-base", "--is-ancestor", ref, "HEAD")
    if rc == 0:
        return PASS, f"{ref}（{sha[:7]}）是 HEAD 的祖先：本地历史是它的超集", []
    if rc == 1:
        return FAIL, f"本地历史不是 {ref} 的超集", [
            _fail(f"本地历史不是 {ref} 的超集：{ref}（{sha[:7]}）不是 HEAD 的祖先"
                  f"——本地历史被回退/重写而远端未同步"
                  f"（`git merge-base --is-ancestor {ref} HEAD` 退出码 1）")]
    return WARN, f"{ref} 存在但无法比较（git 退出码 {rc}）：仅有本地锚，未做远端对账", []


def iterations_path(root: Path, round_no: int) -> Path:
    """`R<round>` 的迭代日志路径（`.workspace/iterations/R<round>.md`）。"""
    return root / ITER_DIR_REL / f"R{round_no}.md"


# 节标题：`## it3 · 2026-11-24`。只认这一种形态（键名/编号是判据的一部分，不是散文）。
ITER_HEAD_RE = re.compile(r"^##\s+it(\d+)\b")


def count_iterations(text: str) -> tuple[int, list[int]]:
    """数 `## it<N>` 小节：返回 (节数, 按文件顺序的编号列表)。

    与 `release.py` 写账本时的计数**同形**（`tests/test_release.py` 有交叉断言钉住两边一致）：
    两处各数一遍就会分叉，分叉时 S12 会把合法记录判红或把漂移放过。
    """
    nums = []
    for line in text.split("\n"):
        m = ITER_HEAD_RE.match(line)
        if m:
            nums.append(int(m.group(1)))
    return len(nums), nums


def check_s12(ledger: list[dict], root: Path) -> tuple[str, str, list[dict]]:
    """迭代日志冻结锚：账本条目里 `iterations` / `iterations_sha256`（存在才判）。

    为什么要有它：SEQUENCE 每轮只有一行 planned 块，装不下 n 次尝试的设计取舍；
    `.workspace/iterations/R<round>.md` 是 append-only 的尝试日志。日志落盘后如果还能改，
    「事后把当时的结论/证伪改写成通过」就没有任何判据会响——回填历史比编造更难分辨。
    所以 `release.py --dev` 落账本时把**节数**与**文件 sha256** 一起冻结进条目，这里逐条对账：

      · 有 `iterations_sha256` ⇒ 当前文件 sha256 必须相等（landed 后回改一个字节 = 红）；
      · 有 `iterations` ⇒ 文件必须存在、节数相等、`it` 编号必须 1..N 连续（缺节/乱序/跳号都红）；
      · 两者都没有 ⇒ **不判**（历史条目 legacy 豁免：44.3.8.0 早于本判据，不回填）。

    文件被删同样红：账本已经声明了「这一轮的日志长这样」，删掉就没有东西可对账。
    """
    findings: list[dict] = []
    sha_ok = cnt_ok = exempt = 0
    for entry in ledger:
        rnd = entry.get("round")
        if not isinstance(rnd, int) or isinstance(rnd, bool):
            continue
        recorded_sha = entry.get("iterations_sha256")
        recorded_cnt = entry.get("iterations")
        has_sha = recorded_sha is not None and recorded_sha != ""
        has_cnt = recorded_cnt is not None
        if not has_sha and not has_cnt:
            exempt += 1                       # legacy 条目：不判、不回填
            continue
        path = iterations_path(root, rnd)
        rel = f"{ITER_DIR_REL}/R{rnd}.md"
        if not path.is_file():
            findings.append(_fail(f"账本 R{rnd} 记了迭代日志锚"
                                  f"（{'iterations_sha256' if has_sha else 'iterations'}），但 {rel} 不存在"
                                  f"——日志被删或从未提交，冻结锚无从对账", file=rel))
            continue
        data = osnorm.read_bytes(path)
        if has_sha:
            actual = hashlib.sha256(data).hexdigest()
            if not isinstance(recorded_sha, str):
                findings.append(_fail(f"账本 R{rnd} 的 iterations_sha256 不是字符串：{recorded_sha!r}", file=rel))
            elif actual != recorded_sha.strip().lower():
                findings.append(_fail(f"R{rnd} 的迭代日志在落地后被改：账本 iterations_sha256="
                                      f"{str(recorded_sha).strip().lower()}，当前 {rel} 实为 {actual}"
                                      f"——landed 后回改日志 = 红（append-only）", file=rel))
            else:
                sha_ok += 1
        if has_cnt:
            if not isinstance(recorded_cnt, int) or isinstance(recorded_cnt, bool):
                findings.append(_fail(f"账本 R{rnd} 的 iterations 不是整数：{recorded_cnt!r}", file=rel))
            else:
                count, nums = count_iterations(data.decode("utf-8", errors="replace"))
                if count != recorded_cnt:
                    findings.append(_fail(f"账本 R{rnd} 记 iterations={recorded_cnt}，但 {rel} 实有 "
                                          f"{count} 节（`## it<N>`）——节数对不上", file=rel))
                elif nums != list(range(1, count + 1)):
                    findings.append(_fail(f"{rel} 的 it 编号不是 1..{count} 连续：实测 {nums}"
                                          f"（缺节/跳号/乱序都算）", file=rel))
                else:
                    cnt_ok += 1
    detail = (f"迭代日志锚 {sha_ok} 条 sha256 一致 / {cnt_ok} 条节数连续；"
              f"legacy 无锚条目 {exempt} 条（不判）")
    return (PASS if not findings else FAIL), detail, findings


def _history_root(history: dict) -> Path:
    """history 里存的仓库根（避免把 root 串在每个 check 签名里多传一个参数）。"""
    return history["root"]


def _row_fingerprint(row: str) -> str:
    """§1 数据行原文指纹（S13）：改一个字就变。"""
    return hashlib.sha256(row.encode("utf-8")).hexdigest()[:16]


def check_s13(history: dict, current_rows: list[str]) -> tuple[str, str, list[dict]]:
    """§1 landed 数据行整行冻结（append-only rows）。

    为什么要有它：R46f 之前 §1 只有 `version` 列被 S2/S8 钉住、`提交` 列只有「该提交真在 git 里」
    的存在性校验，于是 `范围` / `备注` / `确认` 列的说明文字改写、把提交号换成**另一个真实提交**
    都能 rc=0（v51/v52 三席实跑）。S13 把 §1 数据行当 append-only **行集合**：历史版本里出现过的
    任何一行，必须在当前版本里**逐字**仍在；当前新增行（追加）不算漂移。

    锚必须是 git 历史（`git log -- .workspace/SEQUENCE.md` 的各 blob），不能是当前文件：
    否则「改完当前文件再提交」就自证一致。判据建立前已发生的合法改写见 `S13_PREEXISTING_EDITS`
    （只豁免旧行原文缺失，当前行被改写仍红），豁免条数在 detail 里打印。
    """
    if not history["is_repo"]:
        return DEGRADED, ("无法判定：不是 git 仓库（或缺 git / 游离 HEAD）——"
                          "§1 历史行没有锚可比，S13 不冒充通过"), []
    versions = history.get("seq_rows") or []
    if not versions:
        return PASS, f"git 历史里没有 {SEQ_REL} 版本：无历史 §1 行可锚（0 条）", []
    current = set(current_rows)
    first_seen: dict[str, str] = {}
    for sha, rows in reversed(versions):                # 旧→新：记首次出现的提交
        for row in rows:
            first_seen.setdefault(row, sha)
    findings: list[dict] = []
    exempt = 0
    for row, sha in first_seen.items():
        if row in current:
            continue
        if _row_fingerprint(row) in S13_PREEXISTING_EDITS:
            exempt += 1
            continue
        rnd = row.strip().strip("|").split("|")[0].strip()
        findings.append({
            "round": f"R{rnd}" if rnd else None,
            "line": None,
            "file": SEQ_REL,
            "message": (f"§1 landed 行被改写或删除：历史（首次出现 {sha[:7]}）有该行，"
                        f"当前 §1 没有——append-only rows，历史行必须逐字保留\n"
                        f"        缺行原文：{row}"),
        })
    if findings:
        return FAIL, (f"历史 §1 行 {len(first_seen)} 条中有 {len(findings)} 条在当前缺失"
                      f"（{len(versions)} 个历史版本）"), findings
    detail = f"历史 §1 行 {len(first_seen)} 条全部逐字在位（{len(versions)} 个历史版本）"
    if exempt:
        detail += f"；判据建立前已发生的改写 {exempt} 条（代码白名单，不判）"
    return PASS, detail, findings


def template_repo(root: Path) -> bool | None:
    """模板身份的**唯一**判据：git 历史里是否出现过 `_DEV` 文件（不看工作区 JSON）。

    `git log --oneline -- CHANGELOG_DEV.md`（或 release_dev.json）非空 ⇒ 这是模板仓的开发历史。
    为什么不用 `.workspace/bootstrap.json`：那份 JSON 就在工作区里、谁都能改，把它当判据输入
    等于把 S10 的开关交给被检查方（三席实跑：改成 `workspace_status=active` 后 S10 SKIP、rc=0）。
    返回 None = 没有 git / 没有历史，无法判定（S10 按 DEGRADED 报，不冒充通过）。
    """
    rc, _ = _git(root, "rev-parse", "--git-dir")
    if rc != 0:
        return None
    for rel in DEV_FILES:
        if git_log_path(root, rel):
            return True
    return False


def check_plan_missing(history: dict) -> tuple[str, str, list[dict]]:
    """没有 `SEQUENCE.md` 时的计划侧结论（B）：dev 分支缺计划文件 = FAIL，不是 SKIP。

    实例侧确实没有研发路线图 → 声明式 SKIP；但模板仓的 dev 分支曾经有计划文件（或至少有
    `_DEV` 文件历史），删掉计划再让账本写 `[]` 不能让 S1–S9 读不到东西就整条跳过——
    「当前缺 SEQUENCE.md ⇒ FAIL」正是 R46d 要堵的那条绕过路径。
    """
    root = _history_root(history)
    branch = history["branch"]
    if branch is None:
        return SKIP, (f"未找到 {SEQ_REL}，且无法判定分支（非 git 仓库 / 游离 HEAD）："
                      f"计划侧未校验"), []
    if template_repo(root) is not True:
        return (SKIP, f"未找到 {SEQ_REL}：非模板仓（git 历史里从未出现 _DEV 文件），"
                      f"按实例侧声明跳过", [])
    if branch == "dev":
        return FAIL, f"dev 分支缺 {SEQ_REL}（删计划不能关掉判据）", [
            _fail(f"当前分支 dev 且 git 历史里有计划文件 / _DEV 文件，但工作区没有 {SEQ_REL}"
                  f"——删掉计划文件不能把 S1–S9 变成 SKIP；补回计划侧或走回退落地")]
    return WARN, (f"未找到 {SEQ_REL}（分支 {branch} 不是 dev：计划文件只在 dev 上强制）："
                  f"计划侧未判红，但也没有计划可校验"), []


def run(root: Path, worklist_arg: str | None) -> dict:
    seq_path = root / SEQ_REL
    seq_exists = seq_path.is_file()
    ledger, ledger_errs = load_ledger(root)
    rounds: list[Round] = []
    seq_text: str | None = None
    parse_error: str | None = None
    if seq_exists:
        try:
            seq_text = osnorm.read_text(seq_path)
            rounds = parse_sequence(seq_text)
        except OSError as exc:
            parse_error = f"{SEQ_REL} 读取失败：{type(exc).__name__}: {exc}"
    # S13 的「当前侧」：§1 数据行原文（历史侧在 collect_history 的 seq_rows 里）
    current_rows = section1_rows(seq_text) if seq_text is not None else []

    worklist, tried, explicit_missing = find_worklist(root, worklist_arg)
    history = collect_history(root)
    history["root"] = root
    template = template_repo(root)

    # S10/S11 不依赖计划文件：两条都无条件执行（main 的合法态正是没有 SEQUENCE.md；
    # 删计划不能用来绕过 _DEV 文件归属或远端锚）。
    s10 = check_s10(root, template)
    s11 = check_s11(root, history["branch"])
    checks: list[dict] = []
    reason = ""
    if seq_exists:
        for cid, triple in (
            ("S1", check_s1(rounds)),
            ("S2", check_s2(rounds)),
            ("S3", check_s3(rounds, ledger, ledger_errs, history)),
            ("S4", check_s4(rounds)),
            ("S5", check_s5(rounds, worklist, tried, explicit_missing)),
            ("S6", check_s6(rounds, ledger, history)),
            ("S7", check_s7(ledger, root, history)),
            ("S8", check_s8(rounds, ledger, history)),
            ("S9", check_s9(rounds, ledger)),
            ("S10", s10),
            ("S11", s11),
            ("S12", check_s12(ledger, root)),
            ("S13", check_s13(history, current_rows)),
        ):
            status, detail, findings = triple
            checks.append({"id": cid, "status": status, "detail": detail, "findings": findings})
        if parse_error:
            checks.append({"id": "PARSE", "status": FAIL, "detail": parse_error,
                           "findings": [_fail(parse_error)]})
    else:
        plan_status, plan_detail, plan_findings = check_plan_missing(history)
        checks.append({"id": "S1", "status": plan_status, "detail": plan_detail,
                       "findings": plan_findings})
        for cid in ("S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S12", "S13"):
            checks.append({"id": cid, "status": SKIP, "detail": f"未找到 {SEQ_REL}：计划侧检查不适用",
                           "findings": []})
        checks.append({"id": "S10", "status": s10[0], "detail": s10[1], "findings": s10[2]})
        checks.append({"id": "S11", "status": s11[0], "detail": s11[1], "findings": s11[2]})
        reason = plan_detail

    failed = [c for c in checks if c["status"] == FAIL]
    status_by_id = {c["id"]: c["status"] for c in checks}
    degraded = [c["id"] for c in checks if c["status"] == DEGRADED]
    warned = [c["id"] for c in checks if c["status"] == WARN]
    skipped = [c["id"] for c in checks if c["status"] == SKIP]
    return {
        "ok": not failed,
        "skipped": not seq_exists,
        "reason": reason,
        "sequence": str(seq_path),
        "ledger": str(root / LEDGER_REL),
        "worklist": str(worklist) if worklist else None,
        "worklist_candidates": tried,
        "branch": history["branch"],
        "history_versions": {"ledger": len(history["ledger_versions"]),
                             "sequence": len(history["seq_versions"])},
        # 状态计数机器可读：DEGRADED / WARN / SKIP 都不影响 ok，但必须能被下游逐个读出来
        "counts": {s: sum(1 for c in checks if c["status"] == s)
                   for s in (PASS, FAIL, SKIP, DEGRADED, WARN)},
        "degraded": degraded,
        "warned": warned,
        "skipped_checks": skipped,
        "anchor": {"local": True,
                   "remote_ref": f"origin/{history['branch']}" if history["branch"] else None,
                   "only_local": status_by_id.get("S11") != PASS},
        "rounds": [{"round": r.round, "version": r.version, "status": r.status,
                    "source": r.source} for r in rounds],
        "ledger_entries": len(ledger),
        "checks": checks,
    }


def _print_human(result: dict) -> None:
    """人读输出。所有结局（含 SKIP / FAIL / DEGRADED）都走这一条路径，
    保证 `--json` 与 `--root` 之外的差异只落在渲染层，不会漏掉某项判据。"""
    if result["skipped"]:
        # 计划侧缺失：只有整份结果没有 FAIL 时才是真正的 SKIP（实例侧）；dev 缺计划是 FAIL。
        head = "SKIP" if result["ok"] else "FAIL"
        print(f"sequence_check: {head} —— 未找到 {SEQ_REL}（{result['sequence']}）")
        print(f"  {result['reason']}")
    else:
        counts = {s: sum(1 for r in result["rounds"] if r["status"] == s)
                  for s in (LANDED, LANDED_NO_LEDGER, PLANNED)}
        s11 = next((c for c in result["checks"] if c["id"] == "S11"), None)
        # 锚的来源必须在摘要里明示：远端可用 = 「本地 + 远端锚」；否则明确写「仅有本地锚」，
        # 免得读者把「远端没对过账」误当成「远端也对上了」。
        anchor = ("本地 + 远端锚" if s11 is not None and s11["status"] == PASS else "仅有本地锚")
        print(f"sequence_check: {SEQ_REL} · {len(result['rounds'])} 轮"
              f"（landed {counts[LANDED]} / landed-no-ledger {counts[LANDED_NO_LEDGER]} / "
              f"planned {counts[PLANNED]}）· 账本 {result['ledger_entries']} 条"
              f" · 分支 {result['branch'] or '?'} · 锚 {anchor}")
    for c in result["checks"]:
        print(f"{c['id']} {c['status']:<8} {c['detail']}")
        for f in c["findings"]:
            tag = f"[{f['round']}] " if f.get("round") else ""
            if f.get("line") and f.get("file"):
                where = f"{f['file']}:{f['line']}: "
            elif f.get("line"):
                where = f"line {f['line']}: "
            else:
                where = ""
            print(f"    - {tag}{where}{f['message']}")
    bad = [c["id"] for c in result["checks"] if c["status"] == FAIL]
    extra = ""
    if result["degraded"]:
        extra += f" / {len(result['degraded'])} DEGRADED({', '.join(result['degraded'])})"
    if result["warned"]:
        extra += f" / {len(result['warned'])} WARN({', '.join(result['warned'])})"
    print(f"→ {'OK' if result['ok'] else 'FAIL(' + ', '.join(bad) + ')'}"
          f"（{len(result['checks'])} 项：{result['counts'][PASS]} PASS / "
          f"{len(bad)} FAIL / {result['counts'][SKIP]} SKIP{extra}）")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="SEQUENCE.md（计划）↔ dev 账本/CHANGELOG_DEV/git 历史（事实）互校")
    ap.add_argument("--json", action="store_true", help="stdout 只输出机器可读 JSON（任何结局）")
    ap.add_argument("--root", metavar="DIR", help="树根（默认 = 本脚本所在仓）")
    ap.add_argument("--worklist", metavar="PATH", help="WORKLIST.md 路径（默认按候选路径查找）")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else ROOT
    try:
        result = run(root, a.worklist)
    except OSError as exc:                          # IO 异常不崩：按用法/IO 退出
        if a.json:
            print(json.dumps({"ok": False, "skipped": True, "reason": f"{type(exc).__name__}: {exc}",
                              "sequence": str(root / SEQ_REL), "checks": [], "counts": {},
                              "degraded": [], "warned": [], "skipped_checks": []},
                             ensure_ascii=False))
        else:
            print(f"sequence_check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _print_human(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
