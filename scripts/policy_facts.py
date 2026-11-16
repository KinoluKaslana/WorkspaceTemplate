#!/usr/bin/env python3
"""政策事实生成器 + 「未登记即错」判据。

两件事，一个入口：

1. **生成物**（`--write` / `--check`）：政策版本、插件版本、`TEMPLATE_FILES` 项数、
   skills 个数、scripts 个数只在**权威源**上维护，文档里的对应文字由本脚本整段渲染进
   `<!-- gen:policy-facts:begin -->` … `<!-- gen:policy-facts:end -->` 块。人手改块内容
   = `--check` 红；改权威源不跑 `--write` = `--check` 红。

2. **未登记即错**（`--check` 的第二个半场）：政策版本字面量只许出现在生成块里；
   历史版本字面量只许出现在带 `<!-- history:x.y.z -->` 的行上（并且该值必须在
   `CHANGELOG.md` 的 `## x.y.z` 账本里）；带历史标记的行上出现既非当前、也不在账本里的
   版本号一律红。
   豁免按**版本值**算，不按行算：标记只豁免它声明的那几个版本，同行其它 semver 照判；
   标记也不得登记**当前**版本（那是「把当前版本合法化到生成块外」的通道）。
   `CHANGELOG.md` 的条目标题格式（`## x.y.z`）本身是对账的一部分：不认的标题不进账本，
   会让历史判据静默缩小豁免面。
   **不带任何结构标记的自然语言散文断言不判**（如「本模板现行治理基线为 9.9.9。」）：
   那属于人工/评审层（N2）。旧实现靠开放词表（`政策版本|policy version`）补这块，
   词表外的说法照样漏、词表内的字段名又误报，第 13 轮整段删除。
   其它 semver（插件/工具/skill 版本）不判——它们各自有权威源，不在本脚本的判据面里。

用法：
    python3 scripts/policy_facts.py --check     # 只比对，不写盘（CI/门禁用）
    python3 scripts/policy_facts.py --write     # 按权威源重写生成块
    python3 scripts/policy_facts.py --print     # 打印渲染结果（调试）

退出码：0 一致；1 有 finding（--check）；2 用法/IO/块缺失（--write 不自动插标记）。
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BEGIN = "<!-- gen:policy-facts:begin -->"
END = "<!-- gen:policy-facts:end -->"
# 历史行登记：`<!-- history:3.6.1 -->`（多个用逗号）。标记里**写出**它登记的历史版本，
# 这样「账本删了条目」能被发现：登记值必须在 CHANGELOG 账本里，且**不得是当前版本**
# （当前版本只许在生成块内；登记当前版本 = 块外写当前版本的后门）。
# 不带值的 `<!-- history -->` 不接受——那种标记只是「这行别查」，删条目就没人知道了。
HISTORY_MARK_RE = re.compile(r"<!--\s*history\s*:\s*([0-9][0-9.,\s]*?)\s*-->")

_POLICY_HEAD_RE = re.compile(r"模板政策版本：`(\d+\.\d+\.\d+)`")
_PLUGIN_VERSION_RE = re.compile(r'^version:\s*"?(\d+\.\d+\.\d+)"?\s*$')
_CLAUSE_ID_RE = re.compile(r"<!--\s*id:R(\d+)\s*-->")
# 右边界不能省：`## 3.6.1beta` 曾因 `^##\s+(\d+\.\d+\.\d+)` 无右边界而被收成 `3.6.1`，
# 于是「账本里有 3.6.1」这种假事实被造出来（第 12 轮 P3）。连字符也排除：
# `## 3.6.1-hotfix` 同样会被读成 `3.6.1`，语义上不是同一个账本条目。
_CHANGELOG_ENTRY_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)(?![\dA-Za-z.\-])")
_CHANGELOG_HEADING_RE = re.compile(r"^##\s+(\S.*?)\s*$")
# 只有「看起来是版本号」的标题才算格式候选（`## 1. 概述` 这种编号小节不受约束），
# 但候选比条目宽：`3.6` / `3.6.1beta` / `v3.6.1` 都得被抓住，否则它们静默退出账本。
_CHANGELOG_VERSIONISH_RE = re.compile(r"^v?\d+\.\d")
_SEMVER_RE = re.compile(r"(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])")
# 空白用 `[ \t]`、不用 `\s`（第 32 轮 P1）：`\s` 会吃换行/CR/VT/全角空格/NBSP，跨行匹配是
# bug 温床，也会把 `##　标题`（全角空格分隔）当成标题。测试侧按名导入**同一对象**
# （`tests/test_docs_consistency.py` 的 `_HEADING_RE`），不得再写第二份字面量。
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+\S")
_POLICY_HEADING_WORDS = re.compile(r"政策|policy|基线|baseline", re.I)
_CLAUSE_SEP_RE = r"(?:\.{2,}|…+|⋯+|[–—‒‑‐-]|~|〜|至|到)"
_CLAUSE_RANGE_RE = re.compile(
    rf"(?<![A-Za-z0-9])R(\d+)\s*({_CLAUSE_SEP_RE})\s*(R)?\s*(\d+)"
    rf"(?P<junk>(?:[A-Za-z_][A-Za-z0-9_]*|(?:\s*{_CLAUSE_SEP_RE}\s*R?\s*\d+)+)?)")
_CLAUSE_CANON_SEP = ".."
_GEN_MARK_RE = re.compile(r"^[ \t]*<!--\s*gen:policy-facts:(?:begin|end)\s*-->[ \t]*$")
_DUP_FACT_SITES = (
    (re.compile(r"^README(_EN)?\.md$"),
     re.compile(r"插件\s*v?\d+\.\d+\.\d+|plugin\s+v?\d+\.\d+\.\d+", re.I),
     "插件版本只由 `plugin.yaml:2` 渲染进生成块；这里写「插件版本见 plugin.yaml」"),
    (re.compile(r"^(README(_EN)?|docs/[^/]+)\.md$"),
     re.compile(r"scripts\s*/?[^\n]{0,12}?（\s*\d+\s*个）|(?<![\w.])\d+\s*个\s*(?:scripts?|脚本)|(?:scripts?|脚本)\s+\d+\s*个", re.I),
     "scripts 计数只由生成块渲染（`scripts/*.py` 为准）；这里写「scripts 以 scripts/*.py 为准」"),
)
# G5 **不**判自然语言散文断言（第 13 轮 P0-1）：旧实现的 `_POLICY_LABEL_RE`
# （`当前政策版本|政策版本|policy version`）是开放词表补洞——词表内的字段名会误报
# （`"当前政策版本"只是字段名`），词表外的说法照样漏（`本模板现行治理基线为 9.9.9。`）。
# 现在只认**结构**：生成块 / `<!-- history:x.y.z -->`。纯散文归 N2（人工评审层）。
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".hook-state"}


class FactSourceError(Exception):
    """权威源取不到值（文件缺失 / 行号漂了 / 格式变了）。"""


# ── 权威源 ────────────────────────────────────────────────────────────────────

def _load_verify_rules(root):
    path = Path(root) / "scripts" / "verify_rules.py"
    if not path.is_file():
        raise FactSourceError("scripts/verify_rules.py 不存在")
    spec = importlib.util.spec_from_file_location("_policy_facts_verify_rules", path)
    if spec is None or spec.loader is None:
        raise FactSourceError("无法加载 verify_rules.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                       # noqa: BLE001 —— 任何导入失败都是权威源失效
        raise FactSourceError(f"导入 verify_rules.py 失败：{exc}")
    return mod


def policy_version(root=ROOT):
    """`AGENT_RULES.md:4` 的 `模板政策版本：\\`x.y.z\\``。"""
    path = Path(root) / "AGENT_RULES.md"
    if not path.is_file():
        raise FactSourceError("AGENT_RULES.md 不存在")
    lines = path.read_text(encoding="utf-8").split("\n")
    if len(lines) < 4:
        raise FactSourceError("AGENT_RULES.md 不足 4 行")
    m = _POLICY_HEAD_RE.search(lines[3])
    if not m:
        raise FactSourceError("AGENT_RULES.md:4 没有「模板政策版本：`x.y.z`」")
    return m.group(1)


def plugin_version(root=ROOT):
    """`.hermes/plugins/workspace-gate/plugin.yaml:2` 的 `version:`。"""
    path = Path(root) / ".hermes" / "plugins" / "workspace-gate" / "plugin.yaml"
    if not path.is_file():
        raise FactSourceError("plugin.yaml 不存在")
    lines = path.read_text(encoding="utf-8").split("\n")
    if len(lines) < 2:
        raise FactSourceError("plugin.yaml 不足 2 行")
    m = _PLUGIN_VERSION_RE.match(lines[1])
    if not m:
        raise FactSourceError('plugin.yaml:2 不是 `version: "x.y.z"`')
    return m.group(1)


def template_files_count(root=ROOT):
    files = getattr(_load_verify_rules(root), "TEMPLATE_FILES", None)
    if not isinstance(files, (list, tuple)) or not files:
        raise FactSourceError("verify_rules.TEMPLATE_FILES 不是非空序列")
    return len(files)


def skills_count(root=ROOT):
    return len(sorted((Path(root) / ".workspace" / "skills").glob("*/SKILL.md")))


def scripts_count(root=ROOT):
    return len(sorted((Path(root) / "scripts").glob("*.py")))


def clause_range(root=ROOT):
    """政策层（`AGENT_RULES*.md`）里 `<!-- id:Rn -->` 的 `Rmin..Rmax`。

    渲染用：结果进 `docs/architecture.md` 的生成块（`条款 **R1..R136**`），人手改不到。
    上限是**发放高水位**，不是条目数——删掉的 ID 退休、不复用，所以不要求无缺号连续。
    ID 语义（唯一 / 顺序 / 起点）不在本脚本里判：那是 `tests/test_docs_consistency.py`
    的 G6，两处各算一遍互为对照。
    """
    root = Path(root)
    ids = []
    for path in sorted(root.glob("AGENT_RULES*.md")):
        ids += [int(x) for x in _CLAUSE_ID_RE.findall(path.read_text(encoding="utf-8"))]
    if not ids:
        raise FactSourceError("AGENT_RULES.md 里没有任何 `<!-- id:Rn -->` 标记")
    return f"R{min(ids)}..R{max(ids)}"


def collect(root=ROOT):
    """一次取齐全部派生事实；任何一项取不到都抛 FactSourceError（fail-closed）。"""
    return {
        "policy": policy_version(root),
        "plugin": plugin_version(root),
        "template_files": template_files_count(root),
        "skills": skills_count(root),
        "scripts": scripts_count(root),
        "clause_range": clause_range(root),
    }


def is_template(root=ROOT):
    """返回工作区身份三态：``template`` / ``instance`` / ``unknown``。

    只有可正常解析且 ``workspace_status == active`` 才是合法派生实例；缺文件、不可读、
    JSON 损坏、字段缺失或未知值一律为 ``unknown``，由 ``check`` fail-closed 报错并继续
    按模板契约检查 README。
    """
    try:
        data = json.loads((Path(root) / ".workspace" / "bootstrap.json")
                          .read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001 —— 任一读取/解析失败均未知
        return "unknown"
    status = data.get("workspace_status")
    if status == "template":
        return "template"
    if status == "active":
        return "instance"
    return "unknown"


def _instance(root, status=None):
    """``instance`` 判定：调用方已解析过 bootstrap 就复用那份三态，别再读一遍盘。

    ``status is None`` 是独立调用方（`write_blocks`、测试、单函数直调）的后路：那时
    自己解析一次，语义与 `is_template(root) == "instance"` 逐字相同。不用模块级缓存——
    同进程可能多次 `check()`（测试就反复调），缓存会跨调用残留旧身份。
    """
    return (is_template(root) if status is None else status) == "instance"


def _instance_user_doc(rel, instance):
    """合法实例中由用户维护的首页与一层 ``docs/*.md``。"""
    return instance and (rel.startswith("README") or
                         (rel.startswith("docs/") and rel.count("/") == 1 and
                          rel.endswith(".md")))


# ── 生成块：唯一事实位清单 ────────────────────────────────────────────────────

class Block:
    """一个生成块：`path` 里恰好一个 begin/end 对，块内容 = markers + line。

    `markers` 是**这一块承载的 doc-fact 登记 id**，由生成器一并渲染——登记与取值同源，
    人手改不到中间态。改这里必须同步 `tests/test_docs_consistency.py` 的 `_FACT_SITES`
    与 `docs/policy-language.md` §7。
    """

    __slots__ = ("path", "markers", "line")

    def __init__(self, path, markers, line):
        self.path, self.markers, self.line = path, tuple(markers), line

    def render(self, facts):
        body = [BEGIN]
        body += [f"<!-- doc-fact:{m} -->" for m in self.markers]
        body.append(self.line.format(**facts))
        body.append(END)
        return body


BLOCKS = (
    Block("README.md", ("readme-policy-badge",),
          "[![Policy](https://img.shields.io/badge/policy-v{policy}%20single%20source"
          "%20of%20truth-6f42c1)](AGENT_RULES.md)"),
    Block("README_EN.md", ("readme-en-policy-badge",),
          "[![Policy](https://img.shields.io/badge/policy-v{policy}%20single%20source"
          "%20of%20truth-6f42c1)](AGENT_RULES.md)"),
    Block("docs/architecture.md",
          ("arch-policy-version", "arch-plugin-version", "arch-template-files",
           "arch-skills", "arch-scripts"),
          "> 当前对应：政策 **{policy}** · 插件 **{plugin}** · `TEMPLATE_FILES` **{template_files}** 项"
          " · skills **{skills}** 个 · scripts **{scripts}** 个 · 条款 **{clause_range}**。"),
    Block("docs/skills.md", ("skills-md-heading",), "# Workspace skills（{skills} 个）"),
    Block("docs/tools.md", ("tools-md-heading",), "# 工具（scripts/，{scripts} 个）"),
)


def _span(lines):
    """返回 (begin_idx, end_idx) 或 None；结构非法时抛 ValueError。"""
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        raise ValueError(f"生成块标记应各出现 1 次，实为 begin={len(begins)} end={len(ends)}")
    if ends[0] < begins[0]:
        raise ValueError(f"end 标记在第 {ends[0] + 1} 行，早于 begin 的第 {begins[0] + 1} 行")
    return begins[0], ends[0]


def _iter_md(root):
    root = Path(root)
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts[:-1]
        if _SKIP_DIRS & set(parts) or any(p.startswith(".venv") for p in parts):
            continue
        yield path


def _unknown_blocks(root, instance=False):
    """生成块出现在未登记的 md 文件里 → 红（块里的事实没人对它负责）。"""
    known = {b.path for b in BLOCKS}
    out = []
    for path in _iter_md(root):
        rel = path.relative_to(Path(root)).as_posix()
        if _instance_user_doc(rel, instance):
            continue
        if rel in known:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if BEGIN in text or END in text:
            lines = text.split("\n")
            fence, fenced = False, set()
            for i, line in enumerate(lines, 1):
                if line.lstrip().startswith("```"):
                    fence = not fence
                    continue
                if fence:
                    fenced.add(i)
            real = [line for i, line in enumerate(lines, 1) if i not in fenced]
            if any(line.strip() in (BEGIN, END) for line in real):
                out.append(f"{rel}: 出现生成块标记，但 scripts/policy_facts.py 的 BLOCKS 未登记该文件")
    return out


def check_blocks(root=ROOT, facts=None, status=None):
    """逐块比对渲染结果；返回 findings（空 = 全一致）。"""
    root = Path(root)
    facts = facts or collect(root)
    instance = _instance(root, status)
    findings, seen = [], set()
    for block in BLOCKS:
        if _instance_user_doc(block.path, instance):
            continue                     # 派生实例的 README/docs 是用户文档，不要求生成块
        path = root / block.path
        if not path.is_file():
            findings.append(f"{block.path}: 文件不存在（生成块的落点没了）")
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        try:
            span = _span(lines)
        except ValueError as exc:
            findings.append(f"{block.path}: {exc}")
            continue
        if span is None:
            findings.append(f"{block.path}: 缺生成块（应有一对 {BEGIN} / {END}）")
            continue
        want = block.render(facts)
        got = lines[span[0]:span[1] + 1]
        seen.add(block.path)
        if got != want:
            bad = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                       min(len(got), len(want)))
            findings.append(
                f"{block.path}:{span[0] + bad + 1}: 生成块内容与权威源不一致"
                f"（第 {bad + 1} 行）\n    磁盘: {got[bad] if bad < len(got) else '<缺行>'}"
                f"\n    渲染: {want[bad] if bad < len(want) else '<多余行>'}")
    findings += _unknown_blocks(root, instance)
    return findings


def write_blocks(root=ROOT, facts=None):
    """把每个块换成渲染结果；返回 (changed_paths, findings)。块缺失不自动插入。"""
    root = Path(root)
    facts = facts or collect(root)
    instance = is_template(root) == "instance"
    changed, findings = [], []
    for block in BLOCKS:
        if _instance_user_doc(block.path, instance):
            continue                     # 不重写派生实例的用户首页/docs
        path = root / block.path
        if not path.is_file():
            findings.append(f"{block.path}: 文件不存在")
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        try:
            span = _span(lines)
        except ValueError as exc:
            findings.append(f"{block.path}: {exc}")
            continue
        if span is None:
            findings.append(f"{block.path}: 缺生成块——请先手工插入一对标记（本脚本不替你选位置）")
            continue
        want = block.render(facts)
        got = lines[span[0]:span[1] + 1]
        if got != want:
            lines[span[0]:span[1] + 1] = want
            path.write_text("\n".join(lines), encoding="utf-8")
            changed.append(block.path)
    return changed, findings


# ── 未登记即错：政策版本字面量 ────────────────────────────────────────────────

def changelog_versions(root=ROOT):
    """`CHANGELOG.md` 里所有 `## x.y.z` 条目（按文件顺序）。

    返回 None 表示**账本不可用**（派生工作区通常没有 CHANGELOG）：此时与历史版本相关的
    两条判据自动降级为跳过，其余判据照跑。

    这是历史集合的**唯一权威源**：任何手工清单都不得成为第二真源，否则「账本删条目」
    与「清单还留着」会互相打掩护。标题格式不对的条目由 `changelog_heading_offenders` 另报。
    """
    path = Path(root) / "CHANGELOG.md"
    if not path.is_file():
        return None
    versions = [m.group(1) for m in
                (_CHANGELOG_ENTRY_RE.match(line) for line in
                 path.read_text(encoding="utf-8").split("\n")) if m]
    return versions or None


def releases_versions(root=ROOT):
    """读取 append-only 发版账本；不可读时返回 ``None`` 供 check fail-closed。"""
    path = Path(root) / ".workspace" / "releases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list) or not data:
        return None
    versions = []
    for item in data:
        if not isinstance(item, dict) or not _SEMVER_RE.fullmatch(str(item.get("version", ""))):
            return None
        versions.append(item["version"])
    return versions


def changelog_heading_offenders(root=ROOT):
    """`CHANGELOG.md` 条目标题格式契约：`## x.y.z`，版本号后不得紧跟字母/数字/点。

    `## 3.6.1beta` / `## 3.6.1.1` / `## 3.6` 这类标题拿不到条目，于是**静默退出账本**——
    历史版本判据会以为那个版本不存在，从而把合法的沿革行判红、或让无边界正则把它读成
    `3.6.1`（第 12 轮 P3）。候选只认 `^\\d+\\.\\d`：`## 剩余风险处置：…` 与
    `## 1. 概述` 这类非版本标题不受约束。
    """
    path = Path(root) / "CHANGELOG.md"
    if not path.is_file():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        m = _CHANGELOG_HEADING_RE.match(line)
        if not m or not _CHANGELOG_VERSIONISH_RE.match(m.group(1)):
            continue
        if _CHANGELOG_ENTRY_RE.match(line):
            continue
        out.append(
            f"CHANGELOG.md:{i}: 条目标题格式不符（须 `## x.y.z`）：{line.strip()[:80]}"
            f"\n    → 版本号后必须紧跟空格/全角括号等非 [0-9A-Za-z.-] 字符；"
            f"格式不符的标题不进账本，历史版本判据随之失效")
    return out


def _registered_lines(lines):
    """生成块内的行号（1-based，含块标记行）：块里的事实由生成器负责，不再按字面量判。"""
    try:
        span = _span(lines)
    except ValueError:
        return set()
    if span is None:
        return set()
    return set(range(span[0] + 1, span[1] + 2))


def _doc_files(root):
    root = Path(root)
    names = ("README.md", "README_EN.md", "CONTRIBUTING.md", "SECURITY.md", "UPGRADE.md")
    paths = [root / n for n in names] + sorted((root / "docs").glob("*.md"))
    return [p for p in paths if p.is_file()]


def literal_offenders(root=ROOT, facts=None, ledger=None, status=None):
    """政策版本字面量的「未登记即错」扫描。

    判据（不服从即红）：
      ① 生成块之外的**当前政策版本**字面量 → 红；
      ② 未带 `<!-- history:x.y.z -->` 的行上出现**账本里的历史版本** → 红；
      ③ 历史标记**登记的值**必须 ∈ 账本、**不得等于当前版本**，且必须真的出现在本行
         （账本删条目 / 标记写错 / 拿当前版本冒充历史都会红）；
      ④ 带 `<!-- history:x.y.z -->` 的行上，除标记声明的版本之外出现既不等于当前、也不在
         账本里的版本 → 红（结构判据：该行已声明是沿革引用，同行另一个未登记版本只可能是
         拼错/臆造。**不带任何结构标记的散文断言不判**，归 N2——旧实现在这里用开放词表
         `政策版本|policy version` 补洞，既误报字段名又漏掉词表外说法，已整段删除）。

    ③ 的豁免是**按版本值**、不是按行：标记只豁免它声明的那几个版本，同一行上其余 semver
    继续走 ①②④（否则一行写对一处，别处的错版本就整行免检）。当前版本只许出现在生成块内，
    所以 `<!-- history:<当前版本> -->` 不算豁免通道——它本身就是 ① 的绕过手法。
    代码围栏内的字面量不判：那是命令参数/示例输入，不是断言（同 R-a 的围栏口径）。
    其它 semver（插件/工具/skill 版本）不判。
    """
    root = Path(root)
    facts = facts or collect(root)
    cur = facts["policy"]
    if ledger is None:
        ledger = changelog_versions(root)
    history_set = set(ledger or ())
    instance = _instance(root, status)
    out = []
    for path in _doc_files(root):
        rel = path.relative_to(root).as_posix()
        if _instance_user_doc(rel, instance):
            continue                     # 派生实例的 README/docs 是用户文档
        lines = path.read_text(encoding="utf-8").split("\n")
        registered = _registered_lines(lines)
        fence = False
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if fence or i in registered:
                continue
            declared = [x.strip() for chunk in HISTORY_MARK_RE.findall(line)
                        for x in chunk.split(",") if x.strip()]
            # 标记文本自己就含版本号，先剥掉再取「本行真的写了哪些版本」
            body = HISTORY_MARK_RE.sub("", line)
            present = {m.group(1) for m in _SEMVER_RE.finditer(body)}
            for v in declared:
                if v == cur:
                    out.append(
                        f"{rel}:{i}: `<!-- history:{v} -->` 登记了**当前**政策版本"
                        f"（历史标记不得登记当前版本：当前版本只许出现在生成块内）："
                        f"{line.strip()[:100]}")
                elif v not in history_set:
                    out.append(
                        f"{rel}:{i}: `<!-- history:{v} -->` 登记的版本不在 CHANGELOG 账本里"
                        f"（删过条目？写错了？）：{line.strip()[:100]}")
                elif v not in present:
                    out.append(
                        f"{rel}:{i}: `<!-- history:{v} -->` 登记了 {v}，但本行没有这个版本字面量")
            for m in _SEMVER_RE.finditer(body):
                v = m.group(1)
                if v in declared:
                    continue                 # 只豁免标记声明的那几个版本
                if v == cur:
                    out.append(
                        f"{rel}:{i}: 生成块之外的当前政策版本 {v}（未登记）：{line.strip()[:100]}"
                        f"\n    → 纳入 scripts/policy_facts.py 的 BLOCKS 后跑 --write，"
                        f"或改写成不带版本号的指针")
                elif history_set and v in history_set:
                    out.append(
                        f"{rel}:{i}: 历史政策版本 {v} 未登记：{line.strip()[:100]}"
                        f"\n    → 确属沿革就在行尾加 `<!-- history:{v} -->`（登记值须在 CHANGELOG 账本里）")
                elif declared and body[m.start() - 1:m.start()] not in ("v", "V"):
                    # 结构判据：本行带 history 标记，另一个既非当前/也不在账本的**裸**版本号
                    # 只能是拼错或臆造（M5）。`vX.Y.Z` 是工具/插件命名空间（`plugin v1.4.3`），
                    # 本脚本不判；没有标记的纯散文（「现行治理基线为 9.9.9」）也不判 → N2。
                    out.append(
                        f"{rel}:{i}: 历史标记行上的未登记版本 {v}"
                        f"（当前 {cur}，且不在 CHANGELOG 账本）：{line.strip()[:100]}")
    return out


def heading_text_offenders(rel, text, policy):
    """标题中的政策/基线版本必须等于当前版本；供 CLI 与测试入口共用。"""
    out = []
    fence = False
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or not _HEADING_RE.match(line) or not _POLICY_HEADING_WORDS.search(line):
            continue
        for match in _SEMVER_RE.finditer(line):
            if match.group(1) != policy:
                out.append(f"{rel}:{i}: 标题写旧政策版本 {match.group(1)}（当前 {policy}）：{line.strip()}")
    return out


def heading_offenders(root=ROOT, facts=None, status=None):
    root = Path(root)
    facts = facts or collect(root)
    instance = _instance(root, status)
    out = []
    for path in _doc_files(root):
        rel = path.relative_to(root).as_posix()
        if _instance_user_doc(rel, instance):
            continue
        out += heading_text_offenders(rel, path.read_text(encoding="utf-8"), facts["policy"])
    return out


def dup_fact_offenders(root=ROOT, status=None):
    """生成块外出现插件版本或 scripts 计数等派生事实字面量 → 红。"""
    root = Path(root)
    instance = _instance(root, status)
    out = []
    for path in _doc_files(root):
        rel = path.relative_to(root).as_posix()
        if _instance_user_doc(rel, instance):
            continue
        rules = [(rx, hint) for path_rx, rx, hint in _DUP_FACT_SITES if path_rx.match(rel)]
        if not rules:
            continue
        inside = False
        for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if _GEN_MARK_RE.match(line):
                inside = not inside
                continue
            if inside:
                continue
            for rx, hint in rules:
                if m := rx.search(line):
                    out.append(f"{rel}:{i}: 生成块外的派生事实副本「{m.group(0)}」"
                               f"（{hint}）：{line.strip()[:100]}")
                    break
    return out


def clause_range_text_offenders(rel, text, want):
    """检查政策条款范围；仅放行边界内、正序、两端完整的严格子集。"""
    out = []
    lo_hint, hi_hint = (int(x) for x in want[1:].split("..R"))
    for i, line in enumerate(text.split("\n"), 1):
        for m in _CLAUSE_RANGE_RE.finditer(line):
            lo, sep, tail_r, hi = int(m.group(1)), m.group(2), m.group(3), int(m.group(4))
            got = f"R{lo}..R{hi}"
            if m.group("junk"):
                out.append(f"{rel}:{i}: 未登记正文的条款范围形态不合规「{m.group(0)}」"
                           f"（拒绝粘连尾缀或链式范围；单形态如 {want}）：{line.strip()[:100]}")
            elif not tail_r:
                out.append(f"{rel}:{i}: 未登记正文的条款范围缺尾端 R（形态不合规）「{m.group(0)}」"
                           f"（单形态须两端 R，如 {want}）：{line.strip()[:100]}")
            elif lo < lo_hint or hi > hi_hint:
                out.append(f"{rel}:{i}: 未登记正文的条款范围越界（值不符）「{m.group(0)}」"
                           f"（政策边界 {want}）：{line.strip()[:100]}")
            elif lo > hi:
                out.append(f"{rel}:{i}: 未登记正文的条款范围值不符「{m.group(0)}」"
                           f"（下界不得大于上界；政策 {want}）：{line.strip()[:100]}")
            elif lo > lo_hint:
                continue
            elif sep != _CLAUSE_CANON_SEP:
                out.append(f"{rel}:{i}: 未登记正文的条款范围连接符不合法「{m.group(0)}」"
                           f"（政策范围只认 `R{lo}..R{hi}` 形态，如 {want}）：{line.strip()[:100]}")
            elif got != want:
                out.append(f"{rel}:{i}: 未登记正文写条款范围 {m.group(0)}（政策 {want}）："
                           f"{line.strip()[:100]}")
    return out


def clause_range_offenders(root=ROOT, facts=None, status=None):
    """扫描文档中的政策范围形态和值；派生实例用户文档保持豁免。"""
    root = Path(root)
    facts = facts or collect(root)
    out = []
    instance = _instance(root, status)
    for path in _doc_files(root):
        rel = path.relative_to(root).as_posix()
        if _instance_user_doc(rel, instance):
            continue
        out += clause_range_text_offenders(rel, path.read_text(encoding="utf-8"),
                                           facts["clause_range"])
    return out


def check(root=ROOT):
    """唯一验收口径：生成块、重复事实、条款范围和版本登记。空列表 = 绿。

    身份三态在这里**只解析一次**（`bootstrap.json` 一次 `check()` 只读一遍），再把结果
    显式传给需要它的各检查。故意不用模块级缓存：同进程可能多次 `check()`（测试如此），
    跨调用残留会让身份判定停在第一次的结果上。
    """
    findings = []
    try:
        facts = collect(root)
    except FactSourceError as exc:
        return [f"权威源取值失败：{exc}"]
    status = is_template(root)
    if status == "unknown":
        # 不 early return：身份存疑时仍按模板契约查到底，多报比漏报好。
        findings.append("workspace-status-unknown: bootstrap 缺失、不可读、无法解析，或 "
                        "workspace_status 不是 template/active")
    findings += check_blocks(root, facts, status=status)
    findings += dup_fact_offenders(root, status=status)
    findings += clause_range_offenders(root, facts, status=status)
    findings += literal_offenders(root, facts, status=status)
    findings += heading_offenders(root, facts, status=status)
    findings += changelog_heading_offenders(root)
    if ledger := changelog_versions(root):
        if ledger[0] != facts["policy"]:
            findings.append(
                f"CHANGELOG.md: 顶层条目 {ledger[0]} ≠ 政策头 {facts['policy']}"
                f"（账本不可信，历史版本判据随之失效）")
    releases = releases_versions(root)
    if not releases:
        findings.append(".workspace/releases.json: 发版账本缺失、为空或格式非法（版本升级必须经 release.py）")
    elif releases[-1] != facts["policy"]:
        findings.append(f".workspace/releases.json: 末条版本 {releases[-1]} ≠ 政策头 {facts['policy']}"
                        "（版本升级必须经 release.py）")
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="政策事实生成器（--check / --write）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只比对，不写盘（默认）")
    mode.add_argument("--write", action="store_true", help="按权威源重写生成块")
    mode.add_argument("--print", dest="show", action="store_true", help="打印渲染结果")
    a = ap.parse_args(argv)

    try:
        facts = collect()
    except FactSourceError as exc:
        print(f"FAIL 权威源取值失败：{exc}")
        return 2

    if a.show:
        for block in BLOCKS:
            print(f"# {block.path}")
            print("\n".join(block.render(facts)))
        return 0

    if a.write:
        changed, findings = write_blocks(ROOT, facts)
        for rel in changed:
            print(f"WROTE {rel}")
        for f in findings:
            print(f"FAIL {f}")
        print("policy facts: " + ("ok" if not findings else f"{len(findings)} finding(s)"))
        return 0 if not findings else 2

    findings = check(ROOT)
    for f in findings:
        print(f"FAIL {f}")
    print("policy facts: " + ("ok" if not findings else f"{len(findings)} finding(s)"))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
