#!/usr/bin/env python3
"""文档与工作区一致性检查（doc-fact 契约 + 范围表）。

D1 所有相对链接可解析（README.md / README_EN.md / docs/*.md）
D2 docs/skills.md 覆盖磁盘上的每个 skill，且版本号与 SKILL.md frontmatter 一致
D3 docs/tools.md 覆盖 scripts/ 下每个 .py
D5 README.md 提到每个 skill 名
D8 docs/skills.md / README / README_EN 的 skill 表版本 == SKILL.md
D9 `.github/` 层不进模板层
D10 shields 静态徽章 URL 结构合法（`/badge/label-message-color`）

原 D4（政策版本快照）、D6（固定格式计数）、D7（preflight 项数）的**散点正则 + GREEN/RED
语料表已删除**：它们与 G1–G4 描述的是同一批事实，留两套判据只会各自漂移。现在版本与
计数只在冻结清单登记的位置上逐条断言（`_FACT_SITES`），语料不再对着裸字符串空转。

## 范围表（`docs/policy-language.md` §7 的测试侧镜像，行名逐字一致）

机械保证（G，每条都被 `test_scope_g*` 覆盖）：

- G1 政策版本 token == `AGENT_RULES.md:4`
- G2 插件版本 == `plugin.yaml:2`
- G3 每个 doc-fact id 在 manifest 存在且 locator 命中一次
- G4 计数事实 == 实测取数；生成块外的派生事实重复位（README* 的插件版本、`docs/architecture.md` 的 scripts 计数）只许写指针（`plugin.yaml` / `scripts/*.py`），写死值即红
- G5 版本登记与标题/指针扫描（`scripts/policy_facts.py`）：生成块内容 == 权威源渲染；当前政策版本字面量只许在生成块内；历史版本（∈ CHANGELOG `## x.y.z` 账本，标题格式不符即红）只许在 `<!-- history:x.y.z -->` 行上，标记只豁免它声明的版本且不得登记当前版本；历史标记行上的裸版本（不带 `v` 前缀）若既非当前也不在账本 → 红；无结构标记的自然语言版本散文不判（N2）；R-a 标题、R-b 第 N 行指针、R-c §5 无版本快照照旧
- G6 条款范围由 `scripts/policy_facts.py` 渲染（渲染值 == 政策 min..max；max 是发放高水位，不要求无缺号连续）；条款 ID 语义（文件内/跨文件唯一、正文顺序严格递增、起点 R1）；未登记散文的条款范围连接符为闭式集合（`..` / `–` / `-` / `~` / `至` / `……`，全部可识别），起点为 1（或越界、倒序）视为政策范围、必须 == min..max 且形态为两端 R 号 + `..`（缺尾端 R 算形态违规），其余子集（如 `R2–R8`、`R120-R136`）属功能引用不判
- G7 未登记 skill 计数禁写（README* / docs/*.md；形态：中文数词（完整十/百/千组合，如 `十四`/`一百`/`三十五`）、阿拉伯数字或英文数词 + `skills?`，以及 `skills（N 个）`；英文数词为闭式集合：个位/十几/整十/hundred/thousand/dozen）

不保证（N，机械判不了，只能人看；`test_scope_table_ids_match_test_names` 禁止给 N 行挂测试假装覆盖）：

- N1 语义是否正确
- N2 未注册散文（G5/G6 已覆盖项除外）
- N3 日期/sha/外链
- N4 徽章渲染
- N5 本地 skip 的用例

## 判据边界（读代码前先看这里）

- `_FACT_SITES` 只登记**位置与取数方式**（id / path / line / pattern / kind / source），
  **不存期望值**：期望值一律现场从权威源取（政策头、plugin.yaml、`verify_rules.TEMPLATE_FILES`、
  磁盘 glob），所以升版/增删文件时测试不需要改数字，只有位置变了才需要改清单。
- `line` 是**标记剥离后**的行号：文档席在声明行上方插 `<!-- doc-fact:<id> -->`，事实行随之下移；
  一行承载多个事实时（`docs/architecture.md:4` 有 5 个）标记可以叠成连续块，只要**块尾紧贴声明行**。
  标记必须**独立成行**（行内前后只许空白）；行内标记（`正文 <!-- doc-fact:x -->`）一律报错——
  否则任意一行都能靠行尾粘一个标记冒充声明行，紧邻规则就失效了。
- pattern 在**声明行**上必须命中 1 次，在**整篇文件**里也必须命中 1 次（locator 唯一）。
- 集合关系（`_marker_required_ids()` 之后）：需要标记的 id 集合 `⊆ marker_ids ⊆ manifest_ids`，
  且 `manifest_ids == seen_ids`，三者全等——清单里每个事实位都必须靠一个 doc-fact 标记定位，
  没有「免标记」的 kind 了（条款范围在 G6 走另一条路：`policy_facts.py` 渲染 + 散文检查）。
  `seen` = 定位成功并取到值。
- R-a 只扫**标题行**（`#`–`######`，跳过 ``` 围栏内），不扫正文：正文里的历史叙述
  （`政策 2.8.2 起`、`v1.7.0`）是合法沿革，按行扫会误红（`docs/hooks-guide.md:151`、
  `docs/hooks-catalog.md:106`、`hooks-tests/README.md:3` 都是这类句子）。
- R-a / R-b 的扫描范围是本文档集 `_docs()`（README*、CONTRIBUTING、SECURITY、UPGRADE、
  docs/*.md）；`CHANGELOG.md` 是历史账本，其中「第 188 行」这类叙述指向的是当年的事实，
  扫它会假红。
- R-b 只在**同一行能解析出目标 `.md` 文件**时判定（`AGENT_RULES.md` 第 4 行 / 其第 4 行）；
  解析不出目标的「第 N 行」（如 `docs/hooks-guide.md:71` 的「越界到第 188 行」）不判。
- R-c 钉住 `docs/policy-language.md` 的 §5：标题必须是 `## 5. 当前基线`（含 `AGENT_RULES.md`）
  且在第 62 行，正文 0 个 `\\d+\\.\\d+\\.\\d+`（基线小节里不许留版本快照）。
- 登记口径：G5 是「登记」判据，不是「措辞」判据；政策版本的唯一合法落点是**生成块**
  （`<!-- gen:policy-facts:begin -->` … `<!-- gen:policy-facts:end -->`，块内含 doc-fact 标记，
  由 `scripts/policy_facts.py --write` 从权威源整段渲染）；沿革叙述写成
  `<!-- history:x.y.z -->` 行，登记值必须在 `CHANGELOG.md` 的 `## x.y.z` 账本里——
  账本删条目时该行立刻红。
- 词表判据已从 G5 删除（第 13 轮 P0-1 删除 `_POLICY_LABEL_RE`）：自然语言散文断言
  （`本模板现行治理基线为 9.9.9。`、`"当前政策版本"只是字段名`）不由 `policy_facts.py` 判，
  明确归 N2（人工/评审层）。旧实现的开放词表（`当前政策版本|政策版本|policy version`）两头都错：
  词表内的字段名误报、词表外的说法静默放过——再补词表只会扩大误报面。G5 只认**结构**：
  生成块 + `<!-- history:x.y.z -->`（该行上的裸版本必须是当前值或账本值）。围栏里的版本号是
  命令参数，不判；`v` 前缀与插件/工具/skill 版本不判（各有权威源：G2 / D2 / D8）。
- 「登记」不等于「写对」：生成块里写错值 → 块比对红；doc-fact 站点（G1/G2/G4）独立从
  权威源重算一遍，两条路互为对照，生成器读错源也会被抓到。
- 条款范围**不是** doc-fact 站点（第 12 轮定稿）：权威落点是 `docs/architecture.md` 生成块里由
  `scripts/policy_facts.py --write` 渲染的 `条款 **R1..R136**`（改它 = G5 生成块比对红），
  其余出现在散文/架构图里的范围按 G6 判**形态 + 值**：连接符是闭式集合（`..` / `–` / `-` / `~` /
  `至` / `……`），起点为 1（或越界、倒序）才算政策范围（必须 == min..max，形态须为
  两端 R 号 + `..`，缺尾端 R 是形态违规）；其余子集（`R2–R8`、`R120-R136`）是功能引用，
  不判值不报错。这样既不给人手行号清单，也不要求散文行插标记。
- 条款 ID 的语义在 G6 里独立重算（不信注册表）：文件内/跨文件唯一、正文顺序严格递增、
  起点必须是 R1、max 是发放高水位；**不要求无缺号连续**（删掉的 ID 退休、不复用）。
- 计数禁写（G7）扫 README* 与 docs/*.md 的**所有行（含 ``` 围栏里的树注释）**：树注释里的
  「8 个 skill」同样会随磁盘漂移。豁免只有三种：行内指向 `docs/skills.md`、行内指向
  `skill_audit.py --strict`、以及该行本身是已登记 doc-fact 的事实行（那就是权威值）。

运行：python3 -B tests/test_docs_consistency.py
副本模式（接线证明用）：`WS_DOCS_ROOT=<仓库副本> python3 -B tests/test_docs_consistency.py`
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT_ENV = "WS_DOCS_ROOT"
_WIRING_ENV = "WS_DOCS_WIRING_CHILD"
ROOT = Path(os.environ.get(_ROOT_ENV) or Path(__file__).resolve().parent.parent).resolve()
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# ─────────────────────────────────────────────────────────────────────────────
# 冻结清单（测试席 2026-11-13，第 2 段；第 12 轮去掉 clause-range）：9 个事实位
#
#   9 个带 doc-fact 标记的事实位（版本 3 + 计数 6）。条款范围不再是事实位：
#   `R1..R136` 由 `scripts/policy_facts.py` 渲染（G5 块比对），散文里的范围由 G6 判形态+值。
#
# 只登记位置与取数方式，不存期望值。改这里之前先改清单，别在测试里就地解释。
# ─────────────────────────────────────────────────────────────────────────────
_FACT_SITES = (
    {"id": "readme-policy-badge", "path": "README.md", "line": 11,
     "pattern": r"policy-v(\d+\.\d+\.\d+)", "kind": "version:policy",
     "source": "AGENT_RULES.md:4"},
    {"id": "readme-en-policy-badge", "path": "README_EN.md", "line": 11,
     "pattern": r"policy-v(\d+\.\d+\.\d+)", "kind": "version:policy",
     "source": "AGENT_RULES.md:4"},
    {"id": "arch-policy-version", "path": "docs/architecture.md", "line": 4,
     "pattern": r"政策 \*\*(\d+\.\d+\.\d+)\*\*", "kind": "version:policy",
     "source": "AGENT_RULES.md:4"},
    {"id": "arch-plugin-version", "path": "docs/architecture.md", "line": 4,
     "pattern": r"插件 \*\*(\d+\.\d+\.\d+)\*\*", "kind": "version:plugin",
     "source": ".hermes/plugins/workspace-gate/plugin.yaml:2"},
    {"id": "arch-template-files", "path": "docs/architecture.md", "line": 4,
     "pattern": r"`TEMPLATE_FILES` \*\*(\d+)\*\*", "kind": "count-list",
     "source": "verify_rules.TEMPLATE_FILES"},
    {"id": "arch-skills", "path": "docs/architecture.md", "line": 4,
     "pattern": r"skills \*\*(\d+)\*\*", "kind": "count-dir",
     "source": ".workspace/skills/*/SKILL.md"},
    {"id": "arch-scripts", "path": "docs/architecture.md", "line": 4,
     "pattern": r"scripts \*\*(\d+)\*\*", "kind": "count-dir",
     "source": "scripts/*.py"},
    {"id": "skills-md-heading", "path": "docs/skills.md", "line": 1,
     "pattern": r"（(\d+) 个）", "kind": "count-dir",
     "source": ".workspace/skills/*/SKILL.md"},
    {"id": "tools-md-heading", "path": "docs/tools.md", "line": 1,
     "pattern": r"，(\d+) 个）", "kind": "count-dir",
     "source": "scripts/*.py"},
)

# kind 原子到「取数机制」：版本两种源、计数两种源、条款范围一种源（fail-closed：
# manifest 里出现 (kind, source) 之外任何组合都算未知权威源，直接红）。
_KIND_SOURCES = {
    "version:policy": "AGENT_RULES.md:4",
    "version:plugin": ".hermes/plugins/workspace-gate/plugin.yaml:2",
    "count-list": "verify_rules.TEMPLATE_FILES",
    "count-dir": (".workspace/skills/*/SKILL.md", "scripts/*.py"),
}

_MARKER_TOKEN_RE = re.compile(r"<!--\s*doc-fact:\s*([A-Za-z0-9_.\-]+)\s*-->")
_MARKER_ONLY_RE = re.compile(r"^[ \t]*<!--\s*doc-fact:\s*([A-Za-z0-9_.\-]+)\s*-->[ \t]*$")
_H2_RE = re.compile(r"^##[ \t]+\S")
_POINTER_RE = re.compile(r"第\s*(\d+)\s*行")
_FILE_REF_RE = re.compile(r"`([\w./\-]+\.md)`")
# ── 下面这批判据正则**不在这里写**：单一来源是 `scripts/policy_facts.py` ──────────
# 第 32 轮 P1：同一批 pattern 曾两处 `re.compile`、靠手工同步，`_HEADING_RE` 已经漂移
# （生产侧 `\s`、测试侧 `[ \t]`），于是「标题判据」在 CLI 与测试两条入口上根本不是同一条判据。
# 现在按名导入同一对象（别名块在 `_policy_facts_module` 之后，见 `_POLICY_FACTS`）：
#   `_POLICY_HEAD_RE` / `_PLUGIN_VERSION_RE` / `_CLAUSE_ID_RE` / `_SEMVER_RE` /
#   `_HEADING_RE` / `_POLICY_HEADING_WORDS` / `_GEN_MARK_RE`
# `test_regex_single_source_with_policy_facts` 用 `is` + 源码字面量扫描双保险钉死。


def _is_structural(line) -> bool:
    """doc-fact 标记行或生成块标记行：都不是事实内容，行号对账时剥离。"""
    return bool(_MARKER_ONLY_RE.match(line) or _GEN_MARK_RE.match(line))


def _fenced_lines(lines) -> set:
    """``` 围栏内的行号（1-based）。围栏里是**示例**，不是标记/断言。"""
    out, fence = set(), False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            out.add(i)
    return out


_BASELINE_HEAD_RE = re.compile(r"^## 5\. 当前基线")
# 递归 md 的跳过名单**不在这里写**：单一来源是 `policy_facts._transient_dir()`（R39b），
# `_iter_md()` 直接调它；第二份黑名单必然漂移（旧实现已漏掉 `backup-*` / `sandbox-*`）。
# G7：未登记的 skill 计数。三种形状都算「写死了 skill 数量」：
#   ① 中文量词（七个 skill / 8 个 skill）
#   ② 阿拉伯数字或**英文数词**（eight skills / fourteen skills / twenty skills / 101 skills）
#   ③ 后缀量词（`Workspace skills（8 个）`）。
# 英文数词用**闭式集合**（个位/十几/整十/量级词 + 可连写的组合），不枚举到 twelve 为止：
# 旧写法从 thirteen 起全部漏报（第 10 轮 P1）。`8 维契约` 里的「维」不是 skill 计数，不匹配。
_EN_NUM_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve "
                 "thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
                 "twenty thirty forty fifty sixty seventy eighty ninety "
                 "hundred thousand dozen").split()
_EN_NUM_ALT = "|".join(_EN_NUM_WORDS)
_EN_NUM_SEQ = rf"(?:{_EN_NUM_ALT})(?:[\s-]+(?:{_EN_NUM_ALT}))*"
# 中文数词（第 11 轮 P2）：旧写法只认单字 `[一二两三四五六七八九十]`，于是 `一百个 skill`
# 漏报、`十四个 skill` 只命中尾巴「四个」。这里按**单位层级**构造完整数词（十/百/千，
# 允许 `零` 占位，如 十四 / 一百 / 两百 / 三十五 / 一百零五 / 九百九十九 / 一千二百三十五）：
#   千级：[d]?千([零]?[d]百)?([零]?[d]?十)?([零]?[d])?
#   百级：[d]?百([零]?[d]?十)?([零]?[d])?
#   十级：[d]?十[d]?
#   个位：[d]+
# 层级从大到小排列且每层可选，最长的合法数词先匹配，`十四个` 不会被切成 `四个`；
# 前置 `(?<![零一…十百千\d])` 再兜一层词边界，禁止从数词中间起匹配。
_CN_DIGIT = "零一二两三四五六七八九"
_D = f"[{_CN_DIGIT}]"
_CN_NUM = (rf"(?:{_D}?千(?:零?{_D}百)?(?:零?{_D}?十)?(?:零?{_D})?"
           rf"|{_D}?百(?:零?{_D}?十)?(?:零?{_D})?"
           rf"|{_D}?十{_D}?"
           rf"|{_D}+)")
_CN_NUM_BOUND = rf"(?<![{_CN_DIGIT}十百千\d])"
_SKILL_COUNT_RES = (
    # 允许一个 ASCII 限定词：`8 个 workspace skill` 与 `8 个 skill` 是同一类计数
    re.compile(rf"{_CN_NUM_BOUND}(?:{_CN_NUM}|\d+)\s*个\s*"
               r"(?:[A-Za-z][A-Za-z0-9_-]*\s+)?skills?\b", re.I),
    re.compile(rf"(?<![\w.\-])(?:\d[\d,]*|{_EN_NUM_SEQ})\s+skills?\b", re.I),
    re.compile(rf"skills?\s*[（(]\s*(?:{_CN_NUM}|\d+)\s*个", re.I),
)
# 允许的替代写法：指向计数唯一登记处，或指向现场取数的审计器的 strict 模式。
_COUNT_AUTHORITY_RE = re.compile(r"docs/skills\.md|skill_audit(?:\.py)?\s+--strict")
# 值提取：清单里所有 kind 的 pattern 都只有一个捕获组，直接取 group(1)。
# 条款范围不在清单里了（第 12 轮），它的形态判断在 `_clause_prose_offenders`（宽松的
# `_CLAUSE_RANGE_RE`，尾端 R 可缺，以便把缺尾端 R 归为形态违规而不是静默不匹配）。
_FIRST_GROUP = lambda m: m.group(1)                                    # noqa: E731


class _AuthorityError(Exception):
    """权威源取不到值（文件缺失 / 行号漂了 / 格式变了）。"""


# ── 文档集 ──────────────────────────────────────────────────────────────────

def _docs(root) -> list:
    root = Path(root)
    return [root / "README.md", root / "README_EN.md", root / "CONTRIBUTING.md",
            root / "SECURITY.md", root / "UPGRADE.md",
            *sorted((root / "docs").glob("*.md"))]


DOCS = _docs(ROOT)


def _is_template_repo(root=ROOT) -> str:
    """复用生产实现，返回 ``template`` / ``instance`` / ``unknown`` 三态。

    派生实例的 `README.md` / `README_EN.md` 属用户首页（`template_sync` 明确豁免），
    版本与计数由用户自己维护；拿模板正主的标准去要求实例会把刚同步干净的实例判红，
    所以 README 类 doc-fact 只在模板仓参与契约（派生实例见 N5）。
    """
    return _policy_facts_module(root).is_template(root)


def _active_sites(root) -> tuple:
    if _is_template_repo(root) != "instance":
        return _FACT_SITES
    return tuple(s for s in _FACT_SITES if not s["path"].startswith("README"))


def _upgrade_text() -> str:
    return (Path(__file__).resolve().parent.parent / "UPGRADE.md").read_text(encoding="utf-8")


def skills(root=ROOT) -> dict:
    out = {}
    for p in (Path(root) / ".workspace" / "skills").glob("*/SKILL.md"):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^version:\s*(\S+)", text, re.M)
        out[p.parent.name] = m.group(1) if m else ""
    return out


# ── 权威源（现场取数，不存期望值）─────────────────────────────────────────────

def _policy_version(root) -> str:
    lines = (Path(root) / "AGENT_RULES.md").read_text(encoding="utf-8").split("\n")
    if len(lines) < 4:
        raise _AuthorityError("AGENT_RULES.md 不足 4 行")
    m = _POLICY_HEAD_RE.search(lines[3])
    if not m:
        raise _AuthorityError("AGENT_RULES.md:4 没有「模板政策版本：`x.y.z`」")
    return m.group(1)


def _stale_version(root) -> str:
    """一个**必然过期**的政策版本号（给合成负例与副本负例用，不写死当前值）。"""
    cur = _policy_version(root)
    return "3.6.1" if cur != "3.6.1" else "3.6.0"


def _plugin_version(root) -> str:
    path = Path(root) / ".hermes" / "plugins" / "workspace-gate" / "plugin.yaml"
    if not path.is_file():
        raise _AuthorityError("plugin.yaml 不存在")
    lines = path.read_text(encoding="utf-8").split("\n")
    if len(lines) < 2:
        raise _AuthorityError("plugin.yaml 不足 2 行")
    m = _PLUGIN_VERSION_RE.match(lines[1])
    if not m:
        raise _AuthorityError("plugin.yaml:2 不是 `version: \"x.y.z\"`")
    return m.group(1)


def _template_file_count(root) -> int:
    """`len(verify_rules.TEMPLATE_FILES)`，按文件路径重新加载而不是 `from verify_rules import`。

    为什么要绕这一下：副本模式下同一进程里可能先后加载「真仓库」和「副本」两份
    verify_rules，`sys.modules` 会缓存第一份，副本里的改动就取不到（T5 的 85/84 正是
    这类比较）。用唯一模块名按路径加载，每个 root 各取各的。
    """
    path = Path(root) / "scripts" / "verify_rules.py"
    if not path.is_file():
        raise _AuthorityError("scripts/verify_rules.py 不存在")
    name = "_verify_rules_under_test_%d_%d" % (os.getpid(), abs(hash(str(path))))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise _AuthorityError("无法加载 verify_rules.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                      # noqa: BLE001 —— 任何导入失败都是权威源失效
        raise _AuthorityError(f"导入 verify_rules.py 失败：{exc}")
    files = getattr(mod, "TEMPLATE_FILES", None)
    if not isinstance(files, (list, tuple)):
        raise _AuthorityError("verify_rules.TEMPLATE_FILES 不是序列")
    return len(files)


def _skills_count(root) -> int:
    return len(sorted((Path(root) / ".workspace" / "skills").glob("*/SKILL.md")))


def _scripts_count(root) -> int:
    return len(sorted((Path(root) / "scripts").glob("*.py")))


def _clause_entries(root) -> list:
    """[(rel, [条款号…])]：政策层文件（`AGENT_RULES*.md`）+ 注册表登记了 clauses 的文件。

    条款 ID 是**单一命名空间**（`scripts/mark_rules.py` 从全局高水位发放），所以判据要看
    跨文件有没有重号；范围与顺序只看带标记的文件。注册表（`.workspace/rule-clauses.json`）
    只用来**扩大扫描面**，不用来当期望值——ID 集合一律从磁盘正文重新数，注册表写错不能
    把测试带绿。
    """
    root = Path(root)
    rels = [p.name for p in sorted(root.glob("AGENT_RULES*.md"))]
    reg = root / ".workspace" / "rule-clauses.json"
    if reg.is_file():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for rel, entry in data.items():
                if isinstance(entry, dict) and entry.get("clauses"):
                    rels.append(rel)
    out, seen = [], set()
    for rel in rels:
        path = root / rel
        if rel in seen or not path.is_file():
            continue
        seen.add(rel)
        ids = [int(x) for x in _CLAUSE_ID_RE.findall(path.read_text(encoding="utf-8"))]
        if ids:
            out.append((rel, ids))
    return out


def _clause_id_offenders(entries) -> list:
    """G6 语义：ID 唯一（文件内 + 跨文件）、正文顺序严格递增、起点 R1。

    **不要求无缺号连续**：条款删掉后 ID 退休、不复用，所以 `R1..Rmax` 允许中间有洞，
    上限永远是发放高水位 `max`，不是条目数。起点必须是 R1（`R2..R136` 这类起点漂移会让
    范围串把 R1 漏在外面）。
    """
    if not entries:
        return ["没有任何文件带 `<!-- id:Rn -->` 标记（条款 ID 权威源缺失）"]
    out, owner = [], {}
    for rel, ids in entries:
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            out.append(f"{rel}: 条款 ID 重复：{[f'R{d}' for d in dupes]}（每个 ID 只能发一次）")
        elif ids != sorted(ids):
            bad = next(i for i in range(1, len(ids)) if ids[i] <= ids[i - 1])
            out.append(f"{rel}: 条款 ID 正文顺序必须严格递增：第 {bad + 1} 个是 R{ids[bad]}，"
                       f"前一个是 R{ids[bad - 1]}")
        for i in ids:
            if i in owner and owner[i] != rel:
                out.append(f"R{i} 跨文件重复：{owner[i]} 与 {rel}（条款 ID 是单一命名空间）")
            owner.setdefault(i, rel)
    lo = min(owner)
    if lo != 1:
        out.append(f"条款 ID 起点必须是 R1，实际最小是 R{lo}")
    return out


def _clause_ids(root) -> tuple:
    """政策里 `<!-- id:Rn -->` 的 (min, max)，不假设连续。

    这是条款范围串的权威值（`min..max`，当前 `1..136`）：min 防「R2..R136」这类起点漂移，
    max 是发放高水位，防新增条款后文档没跟上。取不到任何 id 视为权威源失效（fail-closed）。
    """
    ids = [i for _, xs in _clause_entries(root) for i in xs]
    if not ids:
        raise _AuthorityError("AGENT_RULES.md 里没有任何 `<!-- id:Rn -->` 标记")
    return min(ids), max(ids)


def _clause_range(root) -> str:
    lo, hi = _clause_ids(root)
    return f"{lo}..{hi}"


_AUTHORITY = {
    ("version:policy", "AGENT_RULES.md:4"): _policy_version,
    ("version:plugin", ".hermes/plugins/workspace-gate/plugin.yaml:2"): _plugin_version,
    ("count-list", "verify_rules.TEMPLATE_FILES"): _template_file_count,
    ("count-dir", ".workspace/skills/*/SKILL.md"): _skills_count,
    ("count-dir", "scripts/*.py"): _scripts_count,
}


def _authority_value(site, root) -> str:
    key = (site["kind"], site["source"])
    fn = _AUTHORITY.get(key)
    if fn is None:
        raise _AuthorityError(f"未知权威源 (kind={site['kind']!r}, source={site['source']!r})")
    return str(fn(root))


# ── scan：读盘 → 定位标记 → 提取 → 取权威值 ───────────────────────────────────

def _iter_md(root):
    """递归 md 清单：跳过判据的单一来源是 `policy_facts._transient_dir()`（R39b）。

    测试侧不再自留第三份黑名单——备份副本（`.workspace/backup-*/`）、并行沙箱
    （`sandbox-*/`）在生产扫描器里被跳过，这里也必须跳过，否则「副本基线干净」这类
    前置断言会因备份目录里的副本而假红。
    """
    root = Path(root)
    transient = _policy_facts_module(root)._transient_dir
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts[:-1]
        if any(transient(part, parts[i - 1] if i else "")
               for i, part in enumerate(parts)):
            continue
        yield path


def _collect_markers(root):
    """返回 ({id: [(rel, lineno)]}, findings)。

    只收**独立成行**的标记：行内除标记外只允许空白。行内标记（`正文 <!-- doc-fact:x -->`）
    直接记 finding、不进 marker_map——否则任意一行都能靠行尾粘一个标记冒充声明行，
    「紧贴声明行上方」就退化成「同一行或上一行都行」。
    """
    found, findings = {}, []
    for path in _iter_md(root):
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8").split("\n")
        fenced = _fenced_lines(lines)
        for i, line in enumerate(lines, 1):
            if "doc-fact:" not in line:
                continue
            if i in fenced:
                continue                     # 围栏里是示例标记（如 policy-language §7.1）
            hits = list(_MARKER_TOKEN_RE.finditer(line))
            if not hits:
                findings.append(f"{rel}:{i}: 疑似 doc-fact 标记但语法不匹配：{line.strip()[:80]}")
                continue
            if not _MARKER_ONLY_RE.match(line):
                findings.append(f"{rel}:{i}: doc-fact 标记必须独立成行（标记外只许空白），"
                                f"行内标记不被接受：{line.strip()[:100]}")
                continue
            for m in hits:
                found.setdefault(m.group(1), []).append((rel, i))
    return found, findings


def scan(root=None) -> dict:
    """唯一的取数入口：manifest → 标记 / 行号 → 声明行 → 值。

    返回 `{sites, manifest_ids, marker_ids, seen_ids, findings}`；空结果是**非法状态**，
    由 `check()` 判红（接线证明 T1 就是切断这里）。
    """
    root = Path(root or ROOT)
    findings = []
    marker_map, marker_findings = _collect_markers(root)
    findings += marker_findings
    active = _active_sites(root)
    if _is_template_repo(root) == "instance":
        # 派生实例的 README 是用户首页（N5）：README 里的标记与事实位都不进契约，
        # 否则实例自己写的 `doc-fact` 会被当成「未登记标记」。
        marker_map = {sid: [loc for loc in locs if not loc[0].startswith("README")]
                      for sid, locs in marker_map.items()}
        marker_map = {sid: locs for sid, locs in marker_map.items() if locs}
    marker_ids = set(marker_map)
    sites, seen = {}, set()

    for site in active:
        sid = site["id"]
        locs = marker_map.get(sid, [])
        path = root / site["path"]
        if not path.is_file():
            findings.append(f"[{sid}] 声明文件不存在：{site['path']}")
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        # 第 12 轮起没有免标记 kind：每个事实位都必须靠一个独立成行的 doc-fact 标记定位。
        if len(locs) != 1:
            findings.append(f"[{sid}] 标记出现 {len(locs)} 次（应恰好 1 次）：{locs or '未找到'}"
                            f"；应在 {site['path']}:{site['line']} 紧邻声明行")
            continue
        rel, lineno = locs[0]
        if rel != site["path"]:
            findings.append(f"[{sid}] 标记在 {rel}:{lineno}，清单要求 {site['path']}")
            continue
        fact_idx = lineno                     # 0-based：独立标记的下一行
        if fact_idx >= len(lines):
            findings.append(f"[{sid}] 声明行不存在：{site['path']} 只有 {len(lines)} 行")
            continue
        # 一行可承载多个事实（`docs/architecture.md:4` 有 5 个），标记叠成连续块：
        # 跳过块内后续标记行，声明行 = 块尾的下一行。
        while fact_idx < len(lines) - 1 and _MARKER_ONLY_RE.match(lines[fact_idx]):
            fact_idx += 1
        if not (fact_idx > 0 and _MARKER_ONLY_RE.match(lines[fact_idx - 1])):
            findings.append(f"[{sid}] 声明行 {site['path']}:{fact_idx + 1} 上方不是 doc-fact 标记，"
                            f"标记没有紧邻声明行")
            continue
        fact = lines[fact_idx]
        hits = list(re.finditer(site["pattern"], fact))
        if len(hits) != 1:
            findings.append(f"[{sid}] 声明行 {site['path']}:{fact_idx + 1} 命中 {len(hits)} 次"
                            f"（pattern 应命中 1 次）：{fact.strip()[:100]}")
            continue
        total = len(list(re.finditer(site["pattern"], "\n".join(lines))))
        if total != 1:
            findings.append(f"[{sid}] pattern 在 {site['path']} 全文命中 {total} 次（应恰好 1 次）")
            continue
        marker_only = {j for j, line in enumerate(lines) if _is_structural(line)}
        stripped_idx = fact_idx - len([j for j in marker_only if j < fact_idx])
        if stripped_idx != site["line"] - 1:
            findings.append(f"[{sid}] 声明行位置漂移：剥离标记后是第 {stripped_idx + 1} 行，"
                            f"清单写 {site['line']}")
            continue
        value = _FIRST_GROUP(hits[0])
        seen.add(sid)
        record = {"value": value, "path": site["path"], "line": fact_idx + 1,
                  "kind": site["kind"], "source": site["source"]}
        try:
            record["expected"] = _authority_value(site, root)
        except _AuthorityError as exc:
            findings.append(f"[{sid}] 权威源取值失败：{exc}")
            sites[sid] = record
            continue
        if value != record["expected"]:
            findings.append(f"[{sid}] {site['path']}:{fact_idx + 1} 写 {value}，"
                            f"权威 {site['source']} = {record['expected']}")
        sites[sid] = record

    return {"root": str(root), "sites": sites,
            "manifest_ids": {s["id"] for s in active},
            "marker_ids": marker_ids,
            "seen_ids": seen,
            "findings": findings}


# ── 不加标记但要机械校验的扫描规则（R-a / R-b / R-c）───────────────────────────

def _headings(text):
    """(lineno, line)：真标题行，跳过 ``` 围栏内（围栏里的 `# 注释` 不是标题）。"""
    fence = False
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence and _HEADING_RE.match(line):
            yield i, line


def _r_a_offenders(rel, text, policy, root=ROOT) -> list:
    """标题里出现「政策/policy/基线/baseline + 版本号」→ 版本必须是当前政策版本。

    只扫标题：正文里的历史叙述（谁在哪个版本引入什么）是沿革，不是当前断言。
    """
    return list(_policy_facts_module(root).heading_text_offenders(rel, text, policy))


def _r_b_offenders(rel, text, root) -> list:
    """「第 N 行」式指针必须指向含「模板政策版本」的行。

    只在同一行能解析出目标 `.md` 时判定；解析不出目标的（例如读越界那段里的
    「第 188 行」）属于行内举例，不判。
    """
    out = []
    doc_dir = (Path(root) / rel).parent
    for i, line in enumerate(text.split("\n"), 1):
        m = _POINTER_RE.search(line)
        if not m:
            continue
        refs = _FILE_REF_RE.findall(line)
        if not refs:
            continue
        wanted = int(m.group(1))
        for ref in refs:
            target = doc_dir / ref
            if not target.is_file():
                target = Path(root) / ref
            if not target.is_file():
                out.append(f"{rel}:{i}: 「第 {wanted} 行」指向不存在的文件 {ref}")
                continue
            tlines = target.read_text(encoding="utf-8").split("\n")
            if not 1 <= wanted <= len(tlines) or "模板政策版本" not in tlines[wanted - 1]:
                out.append(f"{rel}:{i}: 「{ref} 第 {wanted} 行」不是政策版本行"
                           f"（该行不含「模板政策版本」）")
    return out


def _r_c_offenders(root) -> list:
    """`docs/policy-language.md` §5 必须是纯指针基线，正文不留版本快照。"""
    rel = "docs/policy-language.md"
    path = Path(root) / rel
    if not path.is_file():
        return [f"{rel} 不存在（R-c 无法判定）"]
    lines = path.read_text(encoding="utf-8").split("\n")
    target = [(i, line) for i, line in enumerate(lines) if _BASELINE_HEAD_RE.match(line)]
    if len(target) != 1:
        return [f"{rel}: 未找到唯一的 `## 5. 当前基线` 标题（命中 {len(target)}）"]
    idx, line = target[0]
    out = []
    if idx + 1 != 62:
        out.append(f"{rel}: §5 标题在第 {idx + 1} 行，冻结清单写 62 行")
    if "AGENT_RULES.md" not in line:
        out.append(f"{rel}:{idx + 1}: §5 标题必须指向 `AGENT_RULES.md`")
    body_end = len(lines)
    for j in range(idx + 1, len(lines)):
        if _H2_RE.match(lines[j]):
            body_end = j
            break
    tokens = re.findall(r"\d+\.\d+\.\d+", "\n".join(lines[idx + 1:body_end]))
    if tokens:
        out.append(f"{rel}: §5 正文出现版本快照 {tokens}（应为 0 个：基线只写指针）")
    return out


def _policy_facts_module(root):
    """按路径加载 `scripts/policy_facts.py`（副本模式各 root 各取各的，不吃 sys.modules 缓存）。"""
    path = Path(root) / "scripts" / "policy_facts.py"
    if not path.is_file():
        raise _AuthorityError("scripts/policy_facts.py 不存在")
    name = "_policy_facts_under_test_%d_%d" % (os.getpid(), abs(hash(str(path))))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise _AuthorityError("无法加载 policy_facts.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                      # noqa: BLE001 —— 导入失败就是判据失效
        raise _AuthorityError(f"导入 policy_facts.py 失败：{exc}")
    return mod


# ── 判据正则的单一来源（第 32 轮 P1）──────────────────────────────────────────
# 这 7 条 pattern 的实现方是 `scripts/policy_facts.py`；测试侧只持有**同一个对象**，
# 不再保存第二份字面量。历史教训：两处 `re.compile` + 手工同步，`_HEADING_RE` 已漂移成
# 生产 `\s` / 测试 `[ \t]` 两种写法，R-a 的标题判据在两个入口上不是同一条判据。
# 名字与 policy_facts 逐字一致，`test_regex_single_source_with_policy_facts` 用 `is` 断言。
_POLICY_FACTS = _policy_facts_module(ROOT)
_POLICY_HEAD_RE = _POLICY_FACTS._POLICY_HEAD_RE
_PLUGIN_VERSION_RE = _POLICY_FACTS._PLUGIN_VERSION_RE
_CLAUSE_ID_RE = _POLICY_FACTS._CLAUSE_ID_RE
_SEMVER_RE = _POLICY_FACTS._SEMVER_RE
_HEADING_RE = _POLICY_FACTS._HEADING_RE
_POLICY_HEADING_WORDS = _POLICY_FACTS._POLICY_HEADING_WORDS
_GEN_MARK_RE = _POLICY_FACTS._GEN_MARK_RE


def _facts_offenders(root) -> list:
    """G5：生成块与权威源一致 + 政策版本字面量「未登记即错」。

    委托 `scripts/policy_facts.py`（CI 直接跑 `--check`，是同一个实现）：生成块比对、
    未登记当前版本、未登记历史版本、政策标签 + 臆造版本、历史行登记值不在账本。
    """
    try:
        mod = _policy_facts_module(root)
        facts = mod.collect(root)
    except _AuthorityError as exc:
        return [f"G5 无法判定：{exc}"]
    return list(mod.check_blocks(root, facts)) + list(mod.literal_offenders(root, facts))


def _clause_prose_offenders(rel, text, want) -> list:
    """G6 复用 policy_facts 的唯一条款范围实现。"""
    return list(_policy_facts_module(ROOT).clause_range_text_offenders(rel, text, want))


def _rule_offenders(root) -> list:
    """R-a/R-b/R-c：条款 ID 语义；G6 统一委托 policy_facts.check(root)。

    条款范围没有登记站点（第 12 轮）：权威落点是 `policy_facts.py` 渲染进
    `docs/architecture.md` 生成块的那一行（手改 = G5 块比对红）；这里判 ID 语义（唯一、
    顺序、起点）与**所有**散文/架构图里出现的范围（含生成块那一行的渲染结果——它写对了才绿）。
    """
    try:
        policy = _policy_version(root)
        _clause_ids(root)  # 权威源存在性仍需 fail-closed 核验
    except _AuthorityError as exc:
        return [f"R-a/R-b/R-c/G6 无法判定：{exc}"]
    out = list(_clause_id_offenders(_clause_entries(root)))
    for path in _docs(root):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        out += _r_a_offenders(rel, text, policy, root)
        out += _r_b_offenders(rel, text, root)
    out += _r_c_offenders(root)
    return out


# ── G7：未登记的 skill 计数禁写（README* / docs/*.md）─────────────────────────

def _skill_count_offenders(rel, text, allowed_lines=()) -> list:
    """写死了 skill 数量的行 → 违规。允许两种替代写法，外加已登记事实行本身。

    `docs/skills.md:38` 曾写「七个 skill」而磁盘是 8 个，没有任何测试会因此变红：计数是
    跟着目录走的**派生事实**，散落在散文里就必然漂移。所以这里不看数字对不对（对的也会漂），
    只看它有没有被登记：要么指向 `docs/skills.md`，要么指向 `skill_audit.py --strict`。
    """
    out = []
    for i, line in enumerate(text.split("\n"), 1):
        if (rel, i) in allowed_lines:
            continue                     # 已登记 doc-fact 的事实行 = 权威值本身
        if _COUNT_AUTHORITY_RE.search(line):
            continue                     # 指向唯一登记处 / 现场取数，不复制计数
        for rx in _SKILL_COUNT_RES:
            m = rx.search(line)
            if m:
                out.append(f"{rel}:{i}: 未登记的 skill 计数「{m.group(0)}」（删掉，或指向 "
                           f"`docs/skills.md` / `skill_audit.py --strict`）：{line.strip()[:100]}")
                break
    return out


def _count_offenders(root, sites) -> list:
    """真文档上的 G7 扫描；派生实例的 README 是用户首页（N5），不参与。"""
    root = Path(root)
    allowed = {(rec.get("path"), rec.get("line")) for rec in sites.values()}
    instance = _is_template_repo(root) == "instance"
    out = []
    for path in _docs(root):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if instance and rel.startswith("README"):
            continue
        out += _skill_count_offenders(rel, path.read_text(encoding="utf-8"), allowed)
    return out


# ── G4 续：派生事实的块外重复位只许写指针（第 13 轮 P0-2）───────────────────────
#
# 根因：生成块只独占**部分**派生事实，块外还留着同一事实的手写副本，而没有任何判据看它：
#   · `README.md:78` 手写「插件 1.9.0」（权威源是 `plugin.yaml:2`，生成块在 architecture.md）
#   · `docs/architecture.md:33` 架构图手写「scripts/（16 个）」（实测 17，生成块已渲染 17）
# 于是「块外副本静默改错」是真空：改坏没人红。修法不是再加一条「值比对」（那需要第二真源），
# 而是**判形状**：这些槽位只许写指针，出现值就红——写对的冗余值也红（冗余本身就是漂移源）。
def _dup_fact_offenders(root) -> list:
    """G4 复用 policy_facts 的唯一块外重复事实实现。"""
    return list(_policy_facts_module(root).dup_fact_offenders(root))


def _marker_required_ids(root) -> set:
    """必须有 doc-fact 标记的站点 id。

    第 12 轮起清单里没有免标记 kind（条款范围退出清单），所以这里等于全部站点：
    `manifest_ids == marker_ids == seen_ids` 是硬等式。
    """
    return {s["id"] for s in _active_sites(root)}


# ── check：唯一验收口径（CLI 与测试共用）──────────────────────────────────────

def check(root=None) -> list:
    """返回违规清单；空列表 = 绿。scan 返回空/未定位任何事实位都算红（fail-closed）。"""
    root = Path(root or ROOT)
    res = scan(root)
    findings = list(res.get("findings") or ())
    manifest_ids = set(res.get("manifest_ids") or ())
    marker_ids = set(res.get("marker_ids") or ())
    seen_ids = set(res.get("seen_ids") or ())
    sites = res.get("sites") or {}
    required = _marker_required_ids(root)
    if not manifest_ids:
        findings.append("scan 返回空 manifest：清单被清空或生产扫描被切断")
    if not sites:
        findings.append("scan 未定位任何事实位：生产扫描被切断")
    for label, bad in (("清单有、标记缺失", required - marker_ids),
                       ("标记有、清单未登记", marker_ids - manifest_ids),
                       ("有事实位但 locator 未命中", manifest_ids - seen_ids)):
        if bad:
            findings.append(f"{label}：{sorted(bad)}")
    findings += _rule_offenders(root)
    findings += _count_offenders(root, sites)
    try:
        findings += list(_policy_facts_module(root).check(root))
    except _AuthorityError as exc:
        findings.append(f"G4/G5/G6 无法判定：{exc}")
    return findings


def main() -> int:
    findings = check()
    for line in findings:
        print(f"FAIL {line}")
    print("docs consistency: " + ("ok" if not findings else f"{len(findings)} finding(s)"))
    return 0 if not findings else 1


# ── 副本工具（接线证明 T2–T5 用；不污染真仓库）──────────────────────────────────

_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo", ".venv*",
                                     "venv", "node_modules", ".local", "*.log")
_COPY_SEQ = iter(range(1, 1000))


def _tmpdir() -> Path:
    """临时目录跟随 TMPDIR；显式给 `WS_DOCS_TMP` 时以它为准（本机约定写 ~/.cache）。"""
    base = os.environ.get("WS_DOCS_TMP")
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="docs-fact-", dir=base))
    return Path(tempfile.mkdtemp(prefix="docs-fact-"))


def _copy_repo(root=ROOT) -> Path:
    dst = _tmpdir() / f"repo-{next(_COPY_SEQ)}"
    shutil.copytree(Path(root), dst, ignore=_COPY_IGNORE, symlinks=True)
    return dst


def _child_env(root) -> dict:
    env = dict(os.environ)
    env[_ROOT_ENV] = str(root)
    env[_WIRING_ENV] = "1"          # 子进程只跑生产检查，不再递归复制仓库
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_suite(root, *args) -> subprocess.CompletedProcess:
    """在副本里跑测试文件（=生产 `check()` 入口）。`args` 透传给 unittest（可只跑单个测试）。"""
    return subprocess.run([sys.executable, "-B",
                           str(Path(root) / "tests" / "test_docs_consistency.py"), *args],
                          cwd=str(root), env=_child_env(root),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)


# ── 范围表镜像（docstring ↔ docs/policy-language.md §7，逐行同文）───────────────

def _section7(root) -> str:
    doc = (Path(root) / "docs" / "policy-language.md").read_text(encoding="utf-8")
    m = re.search(r"^## 7\..*?(?=^## |\Z)", doc, re.S | re.M)
    return m.group(0) if m else ""


def _norm_row(text) -> str:
    """行文本规范化：只去格式（反引号 / 加粗）并折叠空白，一个字都不改。"""
    return re.sub(r"\s+", " ", text.replace("`", "").replace("**", "")).strip()


def _docstring_cells(docstring) -> list:
    """模块 docstring 的 `- G1 <正文>` / `- N2 <正文>` 行 → [(id, 正文)]，保序保重复。"""
    return re.findall(r"^- ((?:G|N)\d) (.+)$", docstring or "", re.M)


def _section7_cells(section) -> list:
    """§7 表格里每个 G/N 单元格 → [(id, 正文)]；`—` 占位与表头不产单元格。"""
    out = []
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        for cell in line.strip("|").split("|"):
            m = re.match(r"^\s*([GN]\d)\s+(.+)$", cell)
            if m:
                out.append((m.group(1), m.group(2)))
    return out


def _mirror_mismatches(docstring, section) -> list:
    """G/N 各列保序逐行比较规范化文本；改字、删行、换序都报错。"""
    doc_rows = _docstring_cells(docstring)
    section_rows = _section7_cells(section)
    got = {rid: _norm_row(txt) for rid, txt in doc_rows}
    want = {rid: _norm_row(txt) for rid, txt in section_rows}
    out = []
    # §7 是 G/N 两列表格，模块 docstring 是先 G 后 N，分别校验两列的行序。
    for prefix in ("G", "N"):
        doc_ids = [rid for rid, _ in doc_rows if rid.startswith(prefix)]
        section_ids = [rid for rid, _ in section_rows if rid.startswith(prefix)]
        if doc_ids != section_ids:
            out.append(f"{prefix} 行序不一致：docstring={doc_ids}，§7={section_ids}")
    for rid in sorted(set(got) | set(want), key=lambda r: (r[0], int(r[1:]))):
        if rid not in want:
            out.append(f"{rid}: docstring 有、§7 没有")
        elif rid not in got:
            out.append(f"{rid}: §7 有、docstring 没有")
        elif got[rid] != want[rid]:
            out.append(f"{rid}: 文本不一致\ndocstring = {got[rid]}\n§7        = {want[rid]}")
    return out


# ── 契约测试 ────────────────────────────────────────────────────────────────

class DocFactContract(unittest.TestCase):
    """G1–G7：冻结清单的机械保证部分。"""

    def _literal(self, text, *, policy=None, ledger=("3.6.1", "3.5.9")):
        """在临时根上跑真实的 `policy_facts.literal_offenders` 语料。

        只造 `docs/probe.md` 一个文件（`_doc_files` 扫 README*/docs/*.md），事实与账本
        显式传入——语料不依赖真 CHANGELOG，也不会因为仓库升版而失效。
        """
        tmp = Path(tempfile.mkdtemp(prefix="policy-facts-"))
        try:
            (tmp / "docs").mkdir()
            (tmp / "docs" / "probe.md").write_text(text, encoding="utf-8")
            mod = _policy_facts_module(ROOT)
            return list(mod.literal_offenders(
                tmp, {"policy": policy or _policy_version(ROOT)}, list(ledger)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 守卫：清单本身坏了要独立报错，不能靠下游断言「碰巧」红。
    def test_manifest_shape_is_sound(self):
        self.assertEqual(len(_FACT_SITES), 9, "冻结清单应是 9 个事实位（版本 3 + 计数 6）")
        ids = [s["id"] for s in _FACT_SITES]
        self.assertEqual(len(ids), len(set(ids)), f"id 重复：{ids}")
        for site in _FACT_SITES:
            with self.subTest(id=site["id"]):
                self.assertEqual(set(site), {"id", "path", "line", "pattern", "kind", "source"},
                                 "manifest 条目只许这 6 个字段（不存期望值）")
                self.assertIsInstance(site["line"], int)
                self.assertGreaterEqual(site["line"], 1)
                self.assertEqual(re.compile(site["pattern"]).groups, 1,
                                 "pattern 应恰好 1 个捕获组（值提取只取 group(1)）")
                kind = site["kind"]
                self.assertIn(kind, _KIND_SOURCES, f"未知 kind {kind!r}（fail-closed）")
                allowed = _KIND_SOURCES[kind]
                allowed = (allowed,) if isinstance(allowed, str) else allowed
                self.assertIn(site["source"], allowed,
                              f"kind {kind!r} 的合法权威源是 {allowed}，写了 {site['source']!r}")
                self.assertIn((kind, site["source"]), _AUTHORITY, "每个 (kind, source) 必须有取数器")
                self.assertTrue((ROOT / site["path"]).is_file(), f"声明文件不存在：{site['path']}")
        for path in {s["path"] for s in _FACT_SITES}:
            self.assertIn(path, {p.relative_to(ROOT).as_posix() for p in _docs(ROOT)},
                          f"{path} 不在文档集内，R-a/R-b 扫不到它")
        # 条款范围不再是事实位（第 12 轮）：清单里出现它 = 有人把「渲染 + G6」退回人手行号对账。
        self.assertEqual([s["id"] for s in _FACT_SITES if s["kind"] == "clause-range"], [],
                         "条款范围不该回到 _FACT_SITES：它由 policy_facts 渲染 + G6 语义校验")
        self.assertNotIn("clause-range", _KIND_SOURCES, "clause-range 不该再有取数机制登记")

    # 7 条判据正则的单一来源对账（第 32 轮 P1）。左=测试侧属性名，右=policy_facts 属性名。
    _SHARED_RES = (
        ("_POLICY_HEAD_RE", "_POLICY_HEAD_RE"),
        ("_PLUGIN_VERSION_RE", "_PLUGIN_VERSION_RE"),
        ("_CLAUSE_ID_RE", "_CLAUSE_ID_RE"),
        ("_SEMVER_RE", "_SEMVER_RE"),
        ("_HEADING_RE", "_HEADING_RE"),
        ("_POLICY_HEADING_WORDS", "_POLICY_HEADING_WORDS"),
        ("_GEN_MARK_RE", "_GEN_MARK_RE"),
    )

    def test_regex_single_source_with_policy_facts(self):
        """测试侧 7 条判据正则必须是 `scripts/policy_facts.py` 的**同一对象**，不是复制品。

        第 32 轮 P1：同一批 pattern 曾两处 `re.compile`、靠手工同步，`_HEADING_RE` 已经漂移
        （生产 `\\s` vs 测试 `[ \\t]`），于是 R-a 的标题判据在两个入口上不是同一条判据——而
        没有任何测试会发现这种漂移，因为它只在「注释/代码里写全角空格、CR、NBSP 分隔的标题」
        上才表现不同。

        两道判据都用，因为单独一道都能被绕过：
          ① `is` 盯对象身份（`==` 不行：`re.compile` 的 pattern 对象没有有意义的 `==`，
             而等值文案能轻松复制）；
          ② 但 `re.compile` 自带进程内缓存——同一个 pattern 字面量再次 compile 会拿回**同一对象**，
             等值副本因此能伪装成别名。所以再扫一遍本文件源码：任何
             `re.compile(<字面量>)` 都不许写出这 7 条 pattern。
        """
        mod = sys.modules[__name__]
        fresh = _policy_facts_module(ROOT)
        for test_name, prod_name in self._SHARED_RES:
            with self.subTest(name=test_name):
                mine = getattr(mod, test_name, None)
                self.assertIsNotNone(mine, f"测试侧缺少 {test_name}（别名块被删了？）")
                self.assertIs(mine, getattr(_POLICY_FACTS, prod_name),
                              f"{test_name} 不是 policy_facts 的同名对象（被复制了一份？）")
                self.assertIs(mine, getattr(fresh, prod_name),
                              f"{test_name} 与现场重新加载的 policy_facts 不是同一对象")
        patterns = {getattr(_POLICY_FACTS, prod).pattern for _, prod in self._SHARED_RES}
        self.assertEqual(len(patterns), len(self._SHARED_RES),
                         "7 条 pattern 有重复：单一来源清单与实现不对齐")
        literals = set()
        for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
            func = getattr(node, "func", None)
            if (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name) and func.value.id == "re"
                    and func.attr == "compile" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                literals.add(node.args[0].value)
        dupes = sorted(patterns.intersection(literals))
        self.assertEqual(dupes, [],
                         f"测试侧又写了第二份 pattern 字面量（改回从 policy_facts 取名）：{dupes}")

    def test_scope_g1_policy_version_matches_policy_head(self):
        res = scan(ROOT)
        want = _policy_version(ROOT)
        expected = sorted(s["id"] for s in _active_sites(ROOT) if s["kind"] == "version:policy")
        self.assertTrue(expected, "清单里没有 version:policy 事实位")
        got = {sid: rec for sid, rec in res["sites"].items() if rec["kind"] == "version:policy"}
        self.assertEqual(sorted(got), expected,
                         f"version:policy 站点缺失（标记未落盘？findings={res['findings']}）")
        for sid, rec in got.items():
            with self.subTest(id=sid):
                self.assertEqual(rec["value"], want, f"{sid} 写 {rec['value']}，政策头 {want}")

    def test_scope_g2_plugin_version_matches_plugin_yaml(self):
        res = scan(ROOT)
        want = _plugin_version(ROOT)
        expected = sorted(s["id"] for s in _active_sites(ROOT) if s["kind"] == "version:plugin")
        got = {sid: rec for sid, rec in res["sites"].items() if rec["kind"] == "version:plugin"}
        self.assertEqual(sorted(got), expected, f"version:plugin 站点缺失（findings={res['findings']}）")
        for sid, rec in got.items():
            self.assertEqual(rec["value"], want, f"{sid} 写 {rec['value']}，plugin.yaml:2 {want}")

    def test_scope_g3_doc_facts_are_located_once(self):
        res = scan(ROOT)
        required = _marker_required_ids(ROOT)
        self.assertTrue(required, "清单里没有任何需要标记的事实位")
        self.assertEqual(sorted(required), sorted(res["manifest_ids"] & required),
                         "有需要标记的事实位丢了（manifest 被改窄？）")
        self.assertEqual(sorted(required - res["marker_ids"]), [], "清单有、磁盘标记缺失")
        self.assertEqual(sorted(res["marker_ids"] - res["manifest_ids"]), [], "磁盘标记有、清单未登记")
        self.assertEqual(sorted(res["manifest_ids"]), sorted(res["seen_ids"]),
                         "有事实位没能定位/取值")
        self.assertEqual(check(ROOT), [], "doc-fact 契约违规：\n" + "\n".join(check(ROOT)))

    def test_scope_g4_count_facts_match_disk(self):
        res = scan(ROOT)
        expected = sorted(s["id"] for s in _active_sites(ROOT)
                          if s["kind"] in ("count-dir", "count-list"))
        got = {sid: rec for sid, rec in res["sites"].items()
               if rec["kind"] in ("count-dir", "count-list")}
        self.assertEqual(sorted(got), expected, f"计数站点缺失（findings={res['findings']}）")
        for sid, rec in got.items():
            with self.subTest(id=sid):
                self.assertEqual(rec["value"], rec["expected"],
                                 f"{sid} 写 {rec['value']}，实测 {rec['source']} = {rec['expected']}")
        self.assertEqual(_skills_count(ROOT), len(skills(ROOT)),
                         "skills 目录数与 SKILL.md 数不一致（count-dir 口径应为 */SKILL.md）")
        # G4 续（第 13 轮 P0-2）：块外的派生事实副本只许是指针。
        # 真文档必须无副本；判据是形状（出现字面值即红），不是「值对不对」——
        # 比对值需要第二真源，而冗余副本本身就是漂移源（architecture 写 16、实测 17 就是这么来的）。
        dup = _dup_fact_offenders(ROOT)
        self.assertEqual(dup, [], "生成块外仍有派生事实副本（改成权威指针）：\n" + "\n".join(dup))
        dup_sites = _policy_facts_module(ROOT)._DUP_FACT_SITES
        self.assertTrue(dup_sites, "policy_facts 的副本判据清单为空：判据被清空了")
        samples = ("插件 8.8.8", "工具层 scripts/（999 个）")
        self.assertEqual(len(samples), len(dup_sites), "样例与判据条目数不一致")
        for (_rel_rx, rx, _hint), sample in zip(dup_sites, samples):
            with self.subTest(sample=sample):
                self.assertIsNotNone(rx.search(sample), f"正则抓不到错值样例「{sample}」（判据空转）")

    def test_scope_g5_heading_and_pointer_scans(self):
        policy = _policy_version(ROOT)
        stale = _stale_version(ROOT)                           # 一个必然过期的版本号
        self.assertEqual(_rule_offenders(ROOT), [], "R-a/R-b/R-c 在真实文档上有违规")
        # 合成负例：规则本身不能空转（真实文档干净 ≠ 规则会抓）
        self.assertTrue(any(stale in x for x in
                            _r_a_offenders("docs/x.md", f"## 政策 {stale} 生效\n", policy)),
                        "R-a 漏报标题里的旧版本")
        self.assertTrue(any(stale in x for x in
                            _r_a_offenders("docs/x.md", f"## 历史沿革：政策 {stale} 起\n", policy)),
                        "R-a 不能因为标题里有历史词就豁免旧版本")
        self.assertTrue(_r_b_offenders("docs/x.md", "> 以 `AGENT_RULES.md` 第 3 行为准\n", ROOT),
                        "R-b 漏报指向非政策版本行的指针")
        self.assertEqual(_r_b_offenders("docs/x.md", "> 以 `AGENT_RULES.md` 第 4 行为准\n", ROOT),
                         [], "R-b 误报正确的第 4 行指针")
        self.assertEqual(_r_b_offenders("docs/x.md", "越界到第 188 行会被拒记\n", ROOT),
                         [], "R-b 不该判没有目标文件的「第 N 行」")
        # G5：政策版本字面量「未登记即错」。判据实现在 `scripts/policy_facts.py`（CI 同一份），
        # 这里给 GREEN/RED 语料证明它不空转；语料不依赖真 CHANGELOG（账本显式传入）。
        for line in ("当前政策版本以 `AGENT_RULES.md` 第 4 行为准。",     # 指针不带号
                     "政策 3.6.1 起引入 R-a 扫描。<!-- history:3.6.1 -->",  # 已登记的沿革
                     "插件版本 1.9.0（`plugin.yaml:2`），skill 1.2.0。",      # 其它 semver 不判
                     "| skill | 1.2.0 |",                                    # 表格版本列
                     "```\npython3 x.py --ref v3.6.1\n```",                  # 围栏里是命令参数
                     # 第 13 轮 P0-1：词表删除后，纯散文断言一律不由本脚本判（归 N2）。
                     # 下面两句是**三席实测的误报/漏判反例**，必须保持绿（不再由 policy_facts 判）。
                     "工具版本 9.9.9；\"当前政策版本\"只是字段名，不表示该版本。",
                     "本模板现行治理基线为 9.9.9。"):
            with self.subTest(green=line):
                self.assertEqual(self._literal(line + "\n"), [], f"GREEN 误报：{line}")
        # 但结构标记的判据不能因此失守：带 history 标记的行上，裸版本仍须是当前值/账本值。
        for line, why in {
            f"政策 3.6.1 起，另见现行基线 9.9.9。<!-- history:3.6.1 -->": "历史标记行上的未登记版本",
            f"<!-- history:{_stale_version(ROOT)} --> 现行基线 9.9.9。": "历史标记行上的未登记版本",
        }.items():
            with self.subTest(marked=line):
                out = self._literal(line + "\n")
                self.assertTrue(any(why in x for x in out), f"标记行判据失守：{line} → {out}")
        # 生成块内的当前版本号不判：块内容由「块 == 渲染」逐字负责，不重复判字面量
        block = ("<!-- gen:policy-facts:begin -->\n"
                 "> 当前对应：政策 **3.7.0**\n"
                 "<!-- gen:policy-facts:end -->\n")
        self.assertEqual(self._literal(block), [], "生成块内的当前版本号不该被判成未登记")
        red_forms = {
            f"当前政策版本：{stale}": "历史政策版本",              # 未登记的历史版本
            f"当前政策版本：{policy}": "生成块之外的当前政策版本",   # 当前版本在块外
            f"政策 {stale} 生效。": "历史政策版本",                # 正文里的旧版本
            f"政策 {stale} <!-- history -->": "历史政策版本",         # 不带值的标记不认
            "政策 9.9.9 <!-- history:9.9.9 -->": "不在 CHANGELOG 账本",
        }
        for line, why in red_forms.items():
            with self.subTest(red=line):
                out = self._literal(line + "\n")
                self.assertTrue(out, f"RED 漏报：{line}")
                self.assertIn(why, out[0], f"报错没说明错因：{out}")
        # 标记登记了 A、本行却写 B → 红（标记写错/行被改写都跑不掉）
        out = self._literal(f"政策 {stale} 起。<!-- history:3.5.9 -->\n",
                            ledger=(stale, "3.5.9"))
        self.assertTrue(any("本行没有这个版本字面量" in x for x in out), out)
        # 账本删条目 → 历史行立刻红（Q2：CHANGELOG 删条目不会静默缩小豁免面）
        out = self._literal(f"政策 {stale} 起。<!-- history:{stale} -->\n", ledger=("3.5.9",))
        self.assertTrue(any("不在 CHANGELOG 账本" in x for x in out), out)
        copy = _copy_repo()
        try:
            target = copy / "docs" / "policy-language.md"
            lines = target.read_text(encoding="utf-8").split("\n")
            idx = next(i for i, line in enumerate(lines) if _BASELINE_HEAD_RE.match(line))
            end = next((j for j in range(idx + 1, len(lines)) if _H2_RE.match(lines[j])), len(lines))
            lines.insert(end, f"政策 {stale} 生效。")
            target.write_text("\n".join(lines), encoding="utf-8")
            self.assertTrue(any("§5 正文" in x for x in _r_c_offenders(copy)),
                            "R-c 漏报 §5 正文里的版本快照")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_scope_g5_history_mark_scope_and_changelog_format(self):
        """第 12 轮的三条漏洞：标记按**值**豁免、不得登记当前版本、CHANGELOG 标题格式。

        ① 旧实现只要行上有 `<!-- history:… -->` 就整行 `continue`，于是
           `政策 3.6.1 起，另见当前政策版本 9.9.9。<!-- history:3.6.1 -->` 里 9.9.9 没过判据；
        ② `<!-- history:<当前版本> -->` 能把当前版本合法化到生成块**之外**（登记即红）；
        ③ `^##\\s+(\\d+\\.\\d+\\.\\d+)` 没有右边界，`## 3.6.1beta` 被读成 `3.6.1`——账本里
           因此凭空多出一个条目，历史判据的豁免面随之失真。
        """
        cur = _policy_version(ROOT)
        stale = _stale_version(ROOT)          # 必然过期，且在 `_literal` 的默认账本里
        # ① 标记只豁免它声明的版本：同一行的其它错版本照红
        line = f"政策 {stale} 起，另见现行基线 9.9.9。<!-- history:{stale} -->"
        out = self._literal(line + "\n")
        self.assertTrue(any("历史标记行上的未登记版本 9.9.9" in x for x in out),
                        f"整行豁免回归：标记旁边的臆造版本又漏了：{out}")
        self.assertFalse(any("历史政策版本" in x for x in out),
                         f"标记声明的版本不该被误报：{out}")
        # ①b 同一行上另一个**账本内**的历史版本也必须红（旧写法整行免检）
        out = self._literal(f"政策 3.5.9 起。<!-- history:{stale} -->\n",
                            ledger=(stale, "3.5.9"))
        self.assertTrue(any("历史政策版本 3.5.9 未登记" in x for x in out),
                        f"同行另一个账本版本被一起豁免：{out}")
        # ② history 标记不得登记当前版本（那正是「块外写当前版本」的后门）
        out = self._literal(f"政策 {cur} 起。<!-- history:{cur} -->\n", ledger=(cur, stale))
        self.assertTrue(any("登记了**当前**政策版本" in x for x in out),
                        f"当前版本被 history 标记洗白：{out}")
        self.assertFalse(any("生成块之外的当前政策版本" in x for x in out),
                         f"同一处只报一条即可（不要和 ① 重复计数）：{out}")
        # ③ CHANGELOG 标题格式契约（无右边界正则会把 3.6.1beta 读成 3.6.1）
        mod = _policy_facts_module(ROOT)
        tmp = Path(tempfile.mkdtemp(prefix="policy-facts-ledger-"))
        try:
            (tmp / "CHANGELOG.md").write_text(
                f"## {cur}（基线）\n\n## 3.6.1beta 坏标题\n\n## 3.6.1.1 也坏\n\n"
                f"## 剩余风险处置：非版本条目\n", encoding="utf-8")
            self.assertEqual(mod.changelog_versions(tmp), [cur],
                             "无右边界时 3.6.1beta/3.6.1.1 会被误收成账本条目")
            bad = mod.changelog_heading_offenders(tmp)
            self.assertEqual(len(bad), 2, f"两个坏标题应各报一条：{bad}")
            for lineno, token in ((3, "3.6.1beta"), (5, "3.6.1.1")):
                self.assertTrue(any(f"CHANGELOG.md:{lineno}:" in x and token in x for x in bad),
                                f"第 {lineno} 行的坏标题没被点名：{bad}")
            self.assertFalse(any("剩余风险处置" in x for x in bad),
                             f"非版本条目不该被格式契约误伤：{bad}")
            self.assertEqual(mod.changelog_heading_offenders(ROOT), [],
                             "真 CHANGELOG 里有格式不符的条目标题")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_scope_g6_clause_range_matches_max_policy_id(self):
        """条款范围：`policy_facts.py` 渲染值 == 政策 min..max；ID 语义在测试侧独立重算。

        第 12 轮定稿：范围不再靠「人手行号清单 + 登记片段」对账（那套站点落在散文与架构图
        正中，行号一漂就红，豁免按行/按片段又各自漏一种形态）。现在：
          ① 权威落点 = `docs/architecture.md` 生成块里渲染出来的 `条款 **R1..R136**`
             （手改 = G5 块比对红，升版漏跑 `--write` 同样红）；
          ② 散文/架构图里其余范围按 G6 判**形态 + 值**（两端 R；值 == min..max）；
          ③ 条款 ID 语义在这里从正文重新数：文件内/跨文件唯一、正文顺序严格递增、
             起点 R1、上限是发放高水位；**不要求无缺号连续**（删掉的 ID 退休、不复用）。
        """
        lo, hi = _clause_ids(ROOT)
        want = f"R{lo}..R{hi}"
        entries = _clause_entries(ROOT)
        self.assertEqual([rel for rel, _ in entries], ["AGENT_RULES.md"],
                         f"带条款标记的文件应只有政策正主：{[rel for rel, _ in entries]}")
        self.assertEqual(_clause_id_offenders(entries), [],
                         "真政策的条款 ID 语义有违规：\n" + "\n".join(_clause_id_offenders(entries)))
        self.assertEqual(lo, 1, "条款 ID 起点必须是 R1")
        self.assertEqual(hi, max(i for _, ids in entries for i in ids), "max 必须是发放高水位")
        # 渲染值 == min..max：渲染走 `policy_facts.py`，期望值在上面独立重算。
        mod = _policy_facts_module(ROOT)
        facts = mod.collect(ROOT)
        self.assertEqual(facts["clause_range"], want,
                         f"policy_facts 渲染 {facts['clause_range']}，独立重算是 {want}")
        arch = next(b for b in mod.BLOCKS if b.path == "docs/architecture.md")
        self.assertIn(want, arch.line.format(**facts), "架构生成块的渲染行里没有条款范围")
        self.assertEqual(_rule_offenders(ROOT), [],
                         "R-a/R-b/R-c/G6 在真实文档上有违规：\n" + "\n".join(_rule_offenders(ROOT)))

        # ── ID 语义：合成语料证明规则不空转（不依赖真仓库的 136 个条款）──
        self.assertEqual(_clause_id_offenders([("AGENT_RULES.md", [1, 2, 4])]), [],
                         "缺号（退休不复用）不该红：不要求无缺号连续")
        for bad_entries, why in (
            ([("AGENT_RULES.md", [1, 2, 2, 3])], "重复"),
            ([("AGENT_RULES.md", [1, 3, 2])], "顺序"),
            ([("AGENT_RULES.md", [2, 3])], "起点"),
            ([("AGENT_RULES.md", [1, 2]), ("AGENT_RULES.custom.md", [2, 3])], "跨文件"),
            ([], "权威源缺失"),
        ):
            with self.subTest(why=why):
                out = _clause_id_offenders(bad_entries)
                self.assertTrue(out, f"{why} 没报：{bad_entries}")
                self.assertTrue(any(why in x for x in out), f"错因不是「{why}」：{out}")
        # 高水位不是条目数：R1,R2,R4 → 范围 R1..R4（按条目数写会漏掉 R4）
        self.assertEqual(f"R{min([1, 2, 4])}..R{max([1, 2, 4])}", "R1..R4")

        # ── 散文形态/值（负例实跑在 T6/T13 的副本上）──
        # GREEN：唯一承认的形态就是两端都带 R；判值不判词。
        for line in (f"条款集见 {want}。", f"条款集见 **{want}**。",
                     f"| `{want}` | 全量条款 |"):
            with self.subTest(green=line):
                self.assertEqual(_clause_prose_offenders("docs/x.md", line + "\n", want), [],
                                 f"GREEN 误报：{line}")
        # RED ①：缺尾端 R 属**形态违规**（第 10 轮 P1：`(?:R)?` 让 `R1..136` 判绿）。
        for bad in (f"R{lo}..{hi}", f"R{lo}..999"):
            with self.subTest(bad=bad):
                out = _clause_prose_offenders("docs/x.md", f"条款集见 {bad}。\n", want)
                self.assertTrue(out, f"缺尾端 R 的 {bad} 漏报")
                self.assertTrue(any("缺尾端 R" in x for x in out),
                                f"{bad} 报错没说明形态违规：{out}")
        # RED ②：起点为 1 但值不等于 min..max；超过高水位则单独报越界。
        for bad, reason in ((f"R{lo}..R999", "越界"),
                            (f"R{lo}..R{hi - 1}", "写条款范围")):
            with self.subTest(bad=bad):
                out = _clause_prose_offenders("docs/x.md", f"条款集见 {bad}。\n", want)
                self.assertTrue(out, f"坏条款范围 {bad} 漏报")
                self.assertTrue(any(reason in x for x in out),
                                f"{bad} 报错没走「{reason}」分支：{out}")
        # 一行两个范围：第一个写对不能给第二个当挡箭牌（第 11 轮 P3 的回归）。
        mixed = f"条款集见 {want}，旧写法 R{lo}..R{hi + 5}。\n"
        out = _clause_prose_offenders("docs/x.md", mixed, want)
        self.assertEqual(len(out), 1, f"同一行的第二个坏范围被一起放过：{out}")
        self.assertIn(f"R{lo}..R{hi + 5}", out[0])
        mixed_shape = f"条款集见 {want}，旧写法 R{lo}..{hi}。\n"
        out = _clause_prose_offenders("docs/x.md", mixed_shape, want)
        self.assertTrue(out and any("缺尾端 R" in x for x in out),
                        f"同一行的缺尾端 R 写法漏报：{out}")
        self.assertRegex(want, r"^R\d+\.\.R\d+$")
        # ── 连接符闭式集合（第 13 轮 P0，G6 形态封闭）──
        # 六种连接符**全部必须被识别**：写对值时非 `..` 连接符是形态违规（红），
        # 写错值时同样红——旧实现只认 `..`，这六种写法全部静默变绿（D4 式失效）。
        seps = ("..", "\u2013", "-", "~", "至", "……")
        for sep in seps:
            with self.subTest(sep=sep, form="policy-range"):
                line = f"条款集见 R{lo}{sep}R{hi}。\n"
                out = _clause_prose_offenders("docs/x.md", line, want)
                if sep == "..":
                    self.assertEqual(out, [], f"规范形态 {sep!r} 被误判：{out}")
                else:
                    self.assertTrue(out, f"连接符 {sep!r} 的 R{lo}{sep}R{hi} 静默变绿")
                    self.assertTrue(any("连接符不合法" in x for x in out),
                                    f"{sep!r} 没走形态分支：{out}")
            with self.subTest(sep=sep, form="wrong-value"):
                bad = f"R{lo}{sep}R{hi + 1}"
                out = _clause_prose_offenders("docs/x.md", f"条款集见 {bad}。\n", want)
                self.assertTrue(out, f"连接符 {sep!r} + 错值 {bad} 静默变绿")
                self.assertTrue(any(bad in x for x in out), f"{sep!r} 的红没点名 {bad}：{out}")
        # 子集引用属功能引用：任何连接符都不判值、不报错（README.md:80 的真实形态）。
        for line in (f"Python 三选一 | R2\u2013R8 + `pyenv_init.py`",
                     f"条款 R{lo + 1}-R{hi - 1} 是功能引用",
                     f"条款 R120\u2013R136 子集"):
            with self.subTest(subset=line):
                self.assertEqual(_clause_prose_offenders("docs/x.md", line + "\n", want), [],
                                 f"子集引用被误判：{line}")
        # 只有边界内正序严格子集可放行；两侧越界与倒序必须红，且错因可区分。
        for bad in (f"R{lo - 1}..R{hi}", f"R{lo}..R{hi + 1}"):
            with self.subTest(out_of_bounds=bad):
                out = _clause_prose_offenders("docs/x.md", f"条款集见 {bad}。\n", want)
                self.assertTrue(out, f"越界 {bad} 静默变绿")
                self.assertTrue(any("越界" in x for x in out), f"{bad} 错因不是越界：{out}")
        bad = "R9..R3"
        out = _clause_prose_offenders("docs/x.md", f"条款集见 {bad}。\n", want)
        self.assertTrue(out, f"倒序 {bad} 静默变绿")
        self.assertTrue(any("值不符" in x for x in out), f"{bad} 错因不是值不符：{out}")
        self.assertEqual(_clause_prose_offenders("docs/x.md", "条款 R2–R8。\n", want), [],
                         "边界内正序严格子集 R2–R8 应放行")
        for bad in ("R2..R8foo", "R2..R8..R999"):
            out = _clause_prose_offenders("docs/x.md", f"条款 {bad}\n", want)
            self.assertTrue(out and any("形态不合规" in x for x in out),
                            f"粘连/链式范围应走形态分支：{bad}: {out}")

    def test_scope_g7_unregistered_skill_count_is_banned(self):
        """skill 计数是派生事实：只许留在登记位，或指向登记位/现场取数。"""
        sites = scan(ROOT)["sites"]
        allowed = {(rec["path"], rec["line"]) for rec in sites.values()}
        bad = []
        for path in _docs(ROOT):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            bad += _skill_count_offenders(rel, path.read_text(encoding="utf-8"), allowed)
        self.assertEqual(bad, [], "真文档里仍有未登记的 skill 计数：\n" + "\n".join(bad))
        # GREEN：允许的写法（指针 / 现场取数 / 事实行本身）
        for line in ("以 `skill_audit.py --strict` 为准",
                     "见 `docs/skills.md`",
                     "8 个 skill，以 `skill_audit.py --strict` 为准",
                     "skill 计数见 `docs/skills.md`",
                     "8 维契约（触发/前置/契约/边界/执行/失败回滚/验收/维护）"):
            self.assertEqual(_skill_count_offenders("README.md", line + "\n"), [],
                             f"GREEN 误报：{line}")
        rec = sites["skills-md-heading"]
        self.assertEqual(
            _skill_count_offenders("docs/skills.md", "# Workspace skills（8 个）\n", {("docs/skills.md", 1)}),
            [], "已登记 doc-fact 的事实行不该被判红")
        self.assertTrue(_skill_count_offenders("docs/skills.md", "# Workspace skills（8 个）\n"),
                        "未登记的行必须判红（豁免不能变成默认放行）")
        self.assertEqual((rec["path"], rec["line"]), ("docs/skills.md", 3),
                         "skills-md-heading 的事实行应是第 3 行（生成块标记 1、doc-fact 标记 2）")
        # RED：中文量词 / 阿拉伯数字 / 英文数词 + 后缀量词，一个都不许漏
        for line in ("七个 skill", "8 个 skill", "六个 skill", "8 skills", "eight skills",
                     "## 2. 七个 skill 的细节", "Skills（8 个）",
                     # 第 10 轮 P1：英文数词旧集合只到 twelve，以下三种曾全部漏报
                     "Fourteen skills", "twenty skills", "101 skills",
                     "Twenty skills", "two hundred skills", "a dozen skills",
                     # 第 11 轮 P2：完整中文数词（旧写法 `一百` 漏报、`十四` 只命中「四个」）
                     "一百个 skill", "十四个 skill", "两百个 skill",
                     "三十五个 skill", "一百零五个 skill", "一千二百三十五个 skill",
                     "九百九十九个 skill", "Skills（一百个）"):
            with self.subTest(line=line):
                self.assertTrue(_skill_count_offenders("README.md", line + "\n"),
                                f"RED 漏报：{line}")
        # 词边界：`十四个` 必须整体命中，不能被切回单字尾巴「四个」
        cut = _skill_count_offenders("README.md", "十四个 skill 在这里。\n")
        self.assertTrue(cut and "「十四个 skill」" in cut[0],
                        f"中文数词被切成尾巴：{cut}")
        # GREEN：不是计数的英文词、以及两种权威指针写法
        for line in ("skill_audit.py --strict", "以 `docs/skills.md` 为准",
                     "skill 计数见 `docs/skills.md`",
                     "the skill loader is documented elsewhere"):
            with self.subTest(line=line):
                self.assertEqual(_skill_count_offenders("README.md", line + "\n"), [],
                                 f"GREEN 误报：{line}")
        # 豁免只认「指向登记处」，不认别的文件
        self.assertTrue(_skill_count_offenders("README.md", "七个 skill，见 `docs/tools.md`\n"),
                        "指向非登记文件不该被豁免")

    # 范围表 ↔ §7 逐行镜像（不只比 id：改字不改号也必须红）+ 测试名双向对账
    def test_scope_table_row_order_mutation_is_red(self):
        """即使 G/N 编号与文本各自不变，行次序漂移也不能算逐行镜像。"""
        doc = __doc__ or ""
        first = "- G1 政策版本 token == `AGENT_RULES.md:4`\n"
        second = "- G2 插件版本 == `plugin.yaml:2`\n"
        self.assertIn(first + second, doc, "变异锚点不在相邻两行")
        swapped = doc.replace(first + second, second + first, 1)
        self.assertTrue(_mirror_mismatches(swapped, _section7(ROOT)), "只交换 G1/G2 行顺序必须红")

    def test_scope_g1_text_mutation_in_copy_is_red(self):
        """仅改副本模块范围表 G1 的正文、保留编号，镜像测试应在子进程红。"""
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程不递归跑变异回归")
        copy = _copy_repo()
        try:
            suite = copy / "tests" / "test_docs_consistency.py"
            original = suite.read_text(encoding="utf-8")
            before = "- G1 政策版本 token == `AGENT_RULES.md:4`"
            after = "- G1 政策版本 口径 == `AGENT_RULES.md:4`"
            self.assertEqual(original.count(before + "\n"), 1, "G1 变异锚点不存在或重复")
            baseline = _run_suite(copy, "DocFactContract.test_scope_table_ids_match_test_names")
            self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
            suite.write_text(original.replace(before + "\n", after + "\n", 1), encoding="utf-8")
            mutated = _run_suite(copy, "DocFactContract.test_scope_table_ids_match_test_names")
            self.assertNotEqual(mutated.returncode, 0, "只改 G1 文本但保留编号的变异漏检")
            self.assertIn("G1: 文本不一致", mutated.stdout + mutated.stderr)
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_scope_table_ids_match_test_names(self):
        """docstring 里的 G/N 范围表必须与 `docs/policy-language.md` §7 **逐行同文**。

        第 6 轮发现：只比 id 集合时，把 `G1 政策版本 token == AGENT_RULES.md:4` 改成
        `G1 版本号的松散说明` 也不红——保证什么变了而范围表的行名没变，等于 §7 说了假话。
        所以按 id 逐条比规范化文本（只去反引号/加粗、折叠空白，不改字），并且用变异自证
        「只改正文不改编号」会被抓到。
        """
        section = _section7(ROOT)
        self.assertTrue(section.strip(), "docs/policy-language.md 缺 §7")
        doc_rows = _docstring_cells(__doc__ or "")
        self.assertTrue(doc_rows, "模块 docstring 里没有 G/N 范围表（应原样镜像 §7）")
        self.assertEqual(len(doc_rows), len({rid for rid, _ in doc_rows}),
                         f"docstring 范围表 id 重复：{[rid for rid, _ in doc_rows]}")
        self.assertEqual(len(_section7_cells(section)),
                         len({rid for rid, _ in _section7_cells(section)}),
                         "§7 表格里同一个 G/N id 出现了多次")
        self.assertEqual(_mirror_mismatches(__doc__ or "", section), [],
                         "docstring 与 §7 不是逐行同文（改行文必须两边一起改）")
        ids = {rid for rid, _ in doc_rows}
        g_ids = {rid for rid in ids if rid.startswith("G")}
        n_ids = {rid for rid in ids if rid.startswith("N")}
        self.assertEqual(g_ids, {f"G{i}" for i in range(1, 8)}, "G 行必须是 G1–G7")
        self.assertEqual(n_ids, {f"N{i}" for i in range(1, 6)}, "N 行必须是 N1–N5")
        self.assertEqual({rid for rid, _ in _section7_cells(section)}, ids,
                         "§7 与 docstring 的 G/N 行集合不一致")
        names = [n for n in dir(self) if n.startswith("test_scope_g")]
        self.assertTrue(names, "没有任何 test_scope_g* 测试")
        covered = set()
        for name in names:
            m = re.match(r"test_scope_g(\d+)_", name)
            self.assertIsNotNone(m, f"{name} 不符合 test_scope_g<N>_<slug> 命名")
            covered.add(f"G{m.group(1)}")
        self.assertEqual(covered, g_ids, f"G 行与 test_scope_g* 不是双向对应：缺 {g_ids - covered}")
        self.assertEqual([n for n in dir(self) if n.startswith("test_scope_n")], [],
                         "N 行是「不保证」，不许挂测试假装覆盖")
        # 非空转自证：只动正文、不动编号，镜像对账必须报出来
        mutated_doc = (__doc__ or "").replace("G1 政策版本 token", "G1 政策版本 字面量", 1)
        self.assertNotEqual(mutated_doc, __doc__, "自证夹具失效：docstring 里没有「G1 政策版本 token」")
        self.assertTrue(_mirror_mismatches(mutated_doc, section),
                        "只改 G1 的正文（编号不变）时镜像对账没红")
        mutated_sec = section.replace("N2 未注册散文", "N2 未注册散文。", 1)
        self.assertNotEqual(mutated_sec, section, "自证夹具失效：§7 里没有「N2 未注册散文」")
        self.assertTrue(_mirror_mismatches(__doc__ or "", mutated_sec),
                        "只改 §7 的 N2 正文时镜像对账没红")

    # ── 接线证明 T1–T13：切断生产扫描 / 改坏真实槽位 / 删掉接线都必须红 ──
    def test_derived_instance_keeps_readme_out_of_contract(self):
        """合法派生实例（workspace_status == active）的 README 属用户首页（N5）。

        副本里把状态改成实例后：README 的 doc-fact 既不能要求存在，也不能被报成
        「未登记标记」——否则刚同步干净的实例会因为用户改写首页而红。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            boot = copy / ".workspace" / "bootstrap.json"
            data = json.loads(boot.read_text(encoding="utf-8"))
            data["workspace_status"] = "active"
            boot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # 实例会改写首页：删掉生成块、徽章写成别的样子。G5 的生成块与字面量判据都
            # 必须跟着放手，否则「刚同步干净的实例」会因为用户首页而红。
            readme = copy / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8")
                              .replace("<!-- gen:policy-facts:begin -->\n", "")
                              .replace("<!-- gen:policy-facts:end -->\n", "")
                              .replace("<!-- doc-fact:readme-policy-badge -->\n", "")
                              .replace("policy-v", "my-own-badge-v"), encoding="utf-8")
            res = scan(copy)
            self.assertFalse([i for i in res["manifest_ids"] if "readme" in i],
                             "实例模式仍把 README 事实位算进契约")
            self.assertFalse([i for i in res["marker_ids"] if "readme" in i],
                             "实例模式把 README 的标记报成未登记")
            self.assertEqual(check(copy), [], "实例模式本不该红")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_workspace_status_unknown_is_fail_closed_in_both_entries(self):
        """坏 JSON、未知值和缺字段都必须由生产/测试两入口同判 unknown 且非零。"""
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明")
        for mutation in ("bad-json", "weird", "missing"):
            with self.subTest(mutation=mutation):
                copy = _copy_repo()
                try:
                    boot = copy / ".workspace" / "bootstrap.json"
                    if mutation == "bad-json":
                        boot.write_text("{not-json\n", encoding="utf-8")
                    else:
                        data = json.loads(boot.read_text(encoding="utf-8"))
                        if mutation == "weird":
                            data["workspace_status"] = "weird"
                        else:
                            data.pop("workspace_status", None)
                        boot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
                    prod = _policy_facts_module(copy)
                    self.assertEqual(prod.is_template(copy), "unknown")
                    self.assertEqual(_is_template_repo(copy), "unknown")
                    prod_findings = prod.check(copy)
                    test_findings = check(copy)
                    self.assertTrue(any("workspace-status-unknown" in x for x in prod_findings))
                    self.assertTrue(any("workspace-status-unknown" in x for x in test_findings))
                    proc = subprocess.run(
                        [sys.executable, "-B", str(copy / "scripts" / "policy_facts.py"), "--check"],
                        cwd=str(copy), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                finally:
                    shutil.rmtree(copy, ignore_errors=True)

    def test_template_status_keeps_readme_contract_enabled(self):
        """模板状态下改坏 README 徽章，生产与测试两入口都必须报错。"""
        copy = _copy_repo()
        try:
            self.assertEqual(_is_template_repo(copy), "template")
            readme = copy / "README.md"
            # 徽章版本从文件派生：写死 policy-v3.7.0 会让发版后的仓库永远测不到坏徽章
            text, n = re.subn(r"policy-v\d+\.\d+\.\d+", "policy-v0.0.1",
                              readme.read_text(encoding="utf-8"), count=1)
            self.assertEqual(n, 1, "README.md 政策徽章定位失败")
            readme.write_text(text, encoding="utf-8")
            prod_findings = _policy_facts_module(copy).check(copy)
            test_findings = check(copy)
            self.assertTrue(any("README.md" in x for x in prod_findings), prod_findings)
            self.assertTrue(any("README.md" in x for x in test_findings), test_findings)
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t1_scan_cut_makes_check_nonzero(self):
        empty = {"root": str(ROOT), "sites": {}, "manifest_ids": set(),
                 "marker_ids": set(), "seen_ids": set(), "findings": []}
        with mock.patch.object(sys.modules[__name__], "scan", lambda root=None: empty):
            self.assertNotEqual(check(ROOT), [], "scan 返回空时 check 仍绿：生产扫描可被切断")

    def test_t2_broken_badge_in_copy_makes_subprocess_nonzero(self):
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            base = _run_suite(copy)
            self.assertEqual(base.returncode, 0,
                             "副本基线不干净，T2 无效：\n" + (base.stdout + base.stderr)[-2000:])
            readme = copy / "README.md"
            old = _policy_version(copy)
            text = readme.read_text(encoding="utf-8")
            self.assertIn(f"policy-v{old}", text, "README 里没有政策徽章")
            readme.write_text(text.replace(f"policy-v{old}", "policy-v3.6.1", 1), encoding="utf-8")
            bad = _run_suite(copy)
            out = bad.stdout + bad.stderr
            self.assertNotEqual(bad.returncode, 0, "改坏真实徽章后子进程仍绿：生产检查没接线")
            self.assertIn("readme-policy-badge", out, f"子进程红了但没报该 id：\n{out[-2000:]}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t3_version_token_in_baseline_section_is_red(self):
        """副本在 §5 正文追加 `政策 3.6.1 生效。` → 必须红。

        说明（与清单对账）：冻结规则里唯一会对「正文里的版本 token」判红的是 R-c
        （§5 基线 0 个 `x.y.z`），R-a 只管标题；未注册散文按 N2 明确**不在机械保证内**。
        所以 T3 的副本目标是 `docs/policy-language.md` §5，命中的是 R-c，而不是把旧的
        七条断言正则再请回来（那会让 N2 变成假话）。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T3 无效")
            target = copy / "docs" / "policy-language.md"
            lines = target.read_text(encoding="utf-8").split("\n")
            idx = next(i for i, line in enumerate(lines) if _BASELINE_HEAD_RE.match(line))
            end = next((j for j in range(idx + 1, len(lines)) if _H2_RE.match(lines[j])), len(lines))
            lines.insert(end, "政策 3.6.1 生效。")
            target.write_text("\n".join(lines), encoding="utf-8")
            findings = check(copy)
            self.assertTrue(any("§5 正文" in f for f in findings),
                            f"§5 追加「政策 3.6.1 生效。」后没红：{findings}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t4_unregistered_marker_is_red(self):
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T4 无效")
            target = copy / "docs" / "tools.md"
            text = target.read_text(encoding="utf-8")
            target.write_text("<!-- doc-fact:not-registered -->\n" + text, encoding="utf-8")
            findings = check(copy)
            self.assertTrue(any("not-registered" in f for f in findings),
                            f"未登记标记没红：{findings}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t5_count_drift_is_red(self):
        """副本把 `TEMPLATE_FILES` 的实测项数改成 −1（当前就是 85→84）→ 必须红。"""
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T5 无效")
            res = scan(copy)
            rec = res["sites"]["arch-template-files"]
            target = copy / "docs" / "architecture.md"
            text = target.read_text(encoding="utf-8")
            wrong = int(rec["value"]) - 1
            target.write_text(text.replace(f"`TEMPLATE_FILES` **{rec['value']}**",
                                           f"`TEMPLATE_FILES` **{wrong}**", 1), encoding="utf-8")
            findings = check(copy)
            self.assertTrue(any("arch-template-files" in f for f in findings),
                            f"计数漂移没红：{findings}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t6_clause_range_drift_is_red(self):
        """条款范围的三条漂移路：手改渲染值 / 散文写错 / 升条款不跑 `--write` → 全红。

        第 12 轮后条款范围没有「站点 id」可点名，红必须来自两条真实判据：
        G5 的生成块比对（渲染值被谁改了、升了条款没重渲染）与 G6 的散文形态/值检查。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T6 无效")
            lo, hi = _clause_ids(copy)
            good = f"R{lo}..R{hi}"
            target = copy / "docs" / "architecture.md"
            text = target.read_text(encoding="utf-8")
            self.assertEqual(text.count(good), 2,
                             f"architecture.md 应有两处 {good}（生成块渲染行 + 架构图），"
                             f"实际 {text.count(good)} 处")
            lines = text.split("\n")
            gen = next(i for i, ln in enumerate(lines) if "当前对应" in ln)
            fig = next(i for i, ln in enumerate(lines) if good in ln and "当前对应" not in ln)
            # ① 手改生成块里的渲染值 → G5 块比对必须红（人手改不到中间态）
            mutated = list(lines)
            mutated[gen] = mutated[gen].replace(good, f"R{lo}..R{hi + 1}")
            target.write_text("\n".join(mutated), encoding="utf-8")
            out = check(copy)
            self.assertTrue(any("docs/architecture.md" in f and "生成块" in f for f in out),
                            f"手改渲染值后没红：{out}")
            target.write_text(text, encoding="utf-8")
            # ② 架构图（散文）里的值/形态/连接符漂移 → G6 必须红
            for bad, why in ((f"R{lo}..R{hi - 1}", "写条款范围"),
                             (f"R{lo}..R{hi + 40}", "越界"),
                             (f"R{lo}..{hi}", "缺尾端 R"),
                             (f"R{lo}\u2013R{hi}", "连接符不合法")):     # en dash（M8 同形）
                with self.subTest(bad=bad):
                    mutated = list(lines)
                    mutated[fig] = mutated[fig].replace(good, bad)
                    target.write_text("\n".join(mutated), encoding="utf-8")
                    findings = check(copy)
                    self.assertTrue(any(why in f and bad in f for f in findings),
                                    f"架构图里的 {bad} 没红（应报「{why}」）：{findings}")
            target.write_text(text, encoding="utf-8")
            # ③ 新增条款把高水位抬到 R{hi+1}，却没跑 --write → 渲染值落后，块比对必须红
            rules = copy / "AGENT_RULES.md"
            rules.write_text(rules.read_text(encoding="utf-8")
                             + f"\n<!-- id:R{hi + 1} --> 变异条款（T6）\n", encoding="utf-8")
            out = check(copy)
            self.assertTrue(any(f"R{lo}..R{hi + 1}" in f for f in out),
                            f"新增条款后渲染值没跟上（渲染值 == min..max 失效）：{out}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t7_unregistered_skill_count_in_copy_is_red(self):
        """副本在真实文档里写回未登记的计数 → 必须红（G7 不能只在语料上绿）。

        覆盖中文数词（含十/百/千组合）、英文数词与阿拉伯数字三种写法；一次注入多行，
        子进程仍须逐条报出（`assertIn` 对每条注入行点名），避免「只抓到一个就算过」。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T7 无效")
            forms = ("八个 skill 全在这里。", "Fourteen skills 全在这里。",
                     "twenty skills are documented here.", "101 skills in total.",
                     # 第 11 轮 P2：完整中文数词（旧写法只认单字）
                     "一百个 skill 全在这里。", "十四个 skill 全在这里。")
            target = copy / "docs" / "skills.md"
            target.write_text(target.read_text(encoding="utf-8") + "\n" + "\n".join(forms) + "\n",
                              encoding="utf-8")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, "散文里的 skill 计数没红：G7 没接线")
            for form in forms:
                self.assertIn(form, out, f"注入 {form!r} 后没有逐条报出：\n{out[-2000:]}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)


    def test_t8_rule_offenders_cut_keeps_entry_red(self):
        """切断 `check()` 对 `_rule_offenders` 的调用 → 必须红（第 6 轮发现 2）。

        两步都是真跑，不靠 `mock` 自证：① 副本里注入一个 R-a 违例（标题写旧版本），
        **生产入口**（跑测试文件本身就是跑 `check()`）必须红；② 恢复文档、删掉
        `check()` 里的 `findings += _rule_offenders(root)`，再单独跑断线测试 T10，
        必须红并且点名被切断的函数。删掉调用仍全绿 ⇒ 这条会挂。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T8 无效")
            stale = _stale_version(copy)
            target = copy / "docs" / "tools.md"
            original = target.read_text(encoding="utf-8")
            target.write_text(original + f"\n## 政策 {stale} 的旧说明\n", encoding="utf-8")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, "注入 R-a 违例后生产入口仍绿：规则没接线")
            self.assertIn("标题写旧政策版本", out, f"子进程红了但没报 R-a：\n{out[-1500:]}")

            target.write_text(original, encoding="utf-8")
            suite = copy / "tests" / "test_docs_consistency.py"
            src = suite.read_text(encoding="utf-8")
            wired = "    findings += _rule_offenders(root)\n"
            self.assertIn(wired, src, "副本里找不到 `findings += _rule_offenders(root)` 接线点")
            suite.write_text(src.replace(wired, "", 1), encoding="utf-8")
            cut = _run_suite(copy, "DocFactContract.test_t10_every_offender_function_is_wired_into_check")
            out = cut.stdout + cut.stderr
            self.assertNotEqual(cut.returncode, 0,
                                "删掉 check() 对 _rule_offenders 的调用后仍全绿：接线证明是假的")
            self.assertIn("_rule_offenders", out, f"红了但没点名被切断的函数：\n{out[-1500:]}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t9_count_offenders_cut_keeps_entry_red(self):
        """切断 `check()` 对 `_count_offenders` 的调用 → 必须红（第 6 轮发现 2）。"""
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T9 无效")
            target = copy / "docs" / "skills.md"
            original = target.read_text(encoding="utf-8")
            target.write_text(original + "\n八个 skill 全在这里。\n", encoding="utf-8")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, "注入未登记 skill 计数后生产入口仍绿：规则没接线")
            self.assertIn("skill 计数", out, f"子进程红了但没报 G7：\n{out[-1500:]}")

            target.write_text(original, encoding="utf-8")
            suite = copy / "tests" / "test_docs_consistency.py"
            src = suite.read_text(encoding="utf-8")
            wired = "    findings += _count_offenders(root, sites)\n"
            self.assertIn(wired, src, "副本里找不到 `findings += _count_offenders(root, sites)` 接线点")
            suite.write_text(src.replace(wired, "", 1), encoding="utf-8")
            cut = _run_suite(copy, "DocFactContract.test_t10_every_offender_function_is_wired_into_check")
            out = cut.stdout + cut.stderr
            self.assertNotEqual(cut.returncode, 0,
                                "删掉 check() 对 _count_offenders 的调用后仍全绿：接线证明是假的")
            self.assertIn("_count_offenders", out, f"红了但没点名被切断的函数：\n{out[-1500:]}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t10_every_offender_function_is_wired_into_check(self):
        """每个 offender 函数都必须真的接进 `check()`（不是只有两个入口）。

        做法：把某一个 offender 函数换成「返回一条哨兵 finding」的桩，`check()` 的结果里
        必须出现这条哨兵——`check()` 里少了 `findings += <fn>(...)` 那一行，哨兵就不见了。
        T8/T9 用真删行证明两个入口，这条把规则层（R-a/R-b/R-c/G7）也逐一钉住。
        """
        # G4/G5/G6 已统一由 policy_facts.check(root) 接入；不再把测试侧的单元级代理
        # `_facts/_clause_prose/_dup_fact` 当作生产接线点，否则会恢复平行判据。
        wired = ("_rule_offenders", "_count_offenders", "_r_a_offenders", "_r_b_offenders",
                 "_r_c_offenders", "_skill_count_offenders")
        mod = sys.modules[__name__]
        for name in wired:
            with self.subTest(fn=name):
                self.assertTrue(hasattr(mod, name), f"{name} 不存在（改名了？清单要同步）")
                sentinel = f"WIRED-SENTINEL::{name}"
                with mock.patch.object(mod, name, lambda *a, **k: [sentinel]):
                    self.assertIn(sentinel, "\n".join(check(ROOT)),
                                  f"{name} 没接进 check()：删掉它的调用也不会红")

    def test_t11_inline_marker_is_red(self):
        """行内标记必须报错：只认「独立成行 + 紧贴声明行上方」（第 6 轮发现 3）。"""
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T11 无效")
            readme = copy / "README.md"
            lines = readme.read_text(encoding="utf-8").split("\n")
            hits = [i for i, line in enumerate(lines) if "doc-fact:readme-policy-badge" in line]
            self.assertEqual(len(hits), 1, f"README 里 readme-policy-badge 标记应恰好 1 个：{hits}")
            idx = hits[0]
            marker = lines.pop(idx).strip()
            self.assertIsNotNone(_MARKER_ONLY_RE.match(marker), "夹具里的标记不是独立成行")
            lines[idx] = lines[idx] + " " + marker       # 挪到声明行行尾（inline）
            readme.write_text("\n".join(lines), encoding="utf-8")
            tools = copy / "docs" / "tools.md"
            tools.write_text(tools.read_text(encoding="utf-8") +
                             "\n正文 <!-- doc-fact:x -->\n", encoding="utf-8")
            scanned = scan(copy)
            self.assertNotIn("readme-policy-badge", scanned["marker_ids"],
                             "行内标记不能充当已登记事实位的 locator")
            self.assertNotIn("readme-policy-badge", scanned["seen_ids"])
            self.assertNotIn("x", scanned["marker_ids"], "正文中的行内标记不能进入 marker_map")
            self.assertTrue(any("doc-fact:x" in f and "独立成行" in f
                                for f in scanned["findings"]), scanned["findings"])
            findings = check(copy)
            self.assertTrue(any("独立成行" in f for f in findings),
                            f"行内标记没报错：{findings}")
            self.assertTrue(any("readme-policy-badge" in f for f in findings),
                            f"行内标记没让该事实位判红（应报标记缺失）：{findings}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_n2_semantic_prose_is_not_mechanically_rejected(self):
        """§7 N2 只留「G5/G6 已覆盖项除外」的散文（语义、日期、sha），不得变成全篇规则。

        版本断言与条款范围已按选项 2 纳入 G5/G6（见 T12/T13）；仍然机械判不了的是
        「这句话说得对不对」，这类散文必须保持绿，不能因为「像是在讲事实」就红。
        """
        copy = _copy_repo()
        try:
            target = copy / "docs" / "tools.md"
            original = target.read_text(encoding="utf-8")
            examples = ("这段政策的语义写得含糊，建议重写。",
                        "评审日期 2026-11-13，措辞待定。",
                        "对应实现见提交 3f2a1b9。")
            target.write_text(original + "\n" + "\n".join(examples) + "\n", encoding="utf-8")
            self.assertEqual(check(copy), [], "N2 的纯语义/日期/sha 散文不应被机械扫描判红")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t12_unregistered_version_prose_is_red(self):
        """§7 G5：未登记的政策版本散文，生产入口必须红（判据是登记，不是形态）。

        旧方案（`_VERSION_ASSERT_RE` 散文正则）与政策标签词表都已删除：词表只认固定说法，
        换个句式就漏、字段名又误报。现在三种情况走子进程验证：
        ① 当前版本写在生成块之外；② 历史版本没有 `<!-- history:x.y.z -->`；
        ③ 历史标记行上再写一个裸的未登记版本（结构判据，M5）。
        反向绿样本：登记过的沿革句、以及**不带结构标记的自然语言断言**（`工具版本 9.9.9；
        "当前政策版本"只是字段名`、`本模板现行治理基线为 9.9.9。`）必须仍绿——那归 N2。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T12 无效")
            target = copy / "docs" / "tools.md"
            original = target.read_text(encoding="utf-8")
            stale = _stale_version(copy)
            policy = _policy_version(copy)
            for bad in (f"当前政策版本：{policy}", f"当前对应：政策 {stale}",
                        f"## 政策 {stale} 的旧说明",
                        f"现行基线 9.9.9。<!-- history:{stale} -->"):
                with self.subTest(bad=bad):
                    target.write_text(original + "\n" + bad + "\n", encoding="utf-8")
                    live = _run_suite(copy)
                    out = live.stdout + live.stderr
                    self.assertNotEqual(live.returncode, 0,
                                        f"注入 {bad!r} 后生产入口仍绿：G5 没接线")
                    self.assertIn(bad, out, f"子进程红了但没点名该行：\n{out[-2000:]}")
            target.write_text(original + f"\n政策 {stale} 起引入本节。<!-- history:{stale} -->\n",
                              encoding="utf-8")
            self.assertEqual(check(copy), [], "已登记的沿革句被误判红（不能见版本号就红）")
            thin = ('工具版本 9.9.9；"当前政策版本"只是字段名，不表示该版本。\n'
                    "本模板现行治理基线为 9.9.9。\n")
            target.write_text(original + "\n" + thin, encoding="utf-8")
            self.assertEqual(check(copy), [],
                             "纯散文断言（无结构标记）不该由 policy_facts 判：那是 N2")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t13_unregistered_clause_range_prose_is_red(self):
        """§7 G6 增补：未登记正文里的条款范围写错，生产入口必须红。

        两类都走 `python3 -B tests/test_docs_consistency.py` 子进程：
        ① 值不符（`R1..R999`）；② **缺尾端 R 的形态违规**（`R1..136`，第 10 轮 P1）。
        反向绿样本：值等于政策 `min..max` 的两端 R 写法仍绿（判值不判词）。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T13 无效")
            lo, hi = _clause_ids(copy)
            target = copy / "docs" / "tools.md"
            original = target.read_text(encoding="utf-8")
            missing_tail = f"R{lo}..{hi}"
            en_dash = f"R{lo}\u2013R{hi}"
            forms = (f"条款范围见 R{lo}..R{hi + 863}。", f"条款范围见 {missing_tail}。",
                     f"条款范围见 {en_dash}。")
            target.write_text(original + "\n" + "\n".join(forms) + "\n", encoding="utf-8")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, "注入未登记条款范围后生产入口仍绿：G6 没接线")
            self.assertIn("未登记正文的条款范围越界", out,
                          f"子进程红了但没报越界：\n{out[-2000:]}")
            self.assertIn(f"R{lo}..R{hi + 863}", out, f"报错没点名实际写坏的串：\n{out[-2000:]}")
            self.assertIn("未登记正文的条款范围缺尾端 R", out,
                          f"子进程红了但没报形态违规：\n{out[-2000:]}")
            self.assertIn(missing_tail, out, f"报错没点名缺尾端 R 的串：\n{out[-2000:]}")
            self.assertIn("连接符不合法", out, f"子进程红了但没报连接符：\n{out[-2000:]}")
            self.assertIn(en_dash, out, f"报错没点名 en dash 的串：\n{out[-2000:]}")

            target.write_text(original + f"\n条款范围见 R{lo}..R{hi}（未登记散文）。\n",
                              encoding="utf-8")
            self.assertEqual(check(copy), [], "值等于政策 min..max 的条款范围不该红")
            # 子集引用（README.md:80 的真实形态）任何连接符都不判：M10 必须绿。
            target.write_text(original + "\nPython 三选一：R2\u2013R8。\nR120-R136 是子集引用。\n",
                              encoding="utf-8")
            self.assertEqual(check(copy), [], "子集条款范围被误判红（功能引用不判值）")

            # 同一行两个范围：写对的那个不能给写错的当挡箭牌（第 11 轮 P3 的回归；
            # 第 12 轮起没有「登记行」这个概念，全部散文行一视同仁）。
            good, bad = f"R{lo}..R{hi}", f"R{lo}..{hi - 1}"
            target.write_text(original + f"\n条款集见 {good}，旧写法 {bad}。\n", encoding="utf-8")
            findings = [f for f in check(copy) if bad in f]
            self.assertEqual(len(findings), 1,
                             f"同一行写对的 {good} 把写错的 {bad} 挡掉了：{check(copy)}")
            self.assertIn("缺尾端 R", findings[0], f"错因不是形态违规：{findings[0]}")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, f"同行坏范围没进生产入口：\n{out[-2000:]}")
            self.assertIn(bad, out, f"子进程没点名同行坏范围：\n{out[-2000:]}")
            self.assertIn("缺尾端 R", out, f"错因不是形态违规：\n{out[-2000:]}")
            target.write_text(original, encoding="utf-8")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t16_duplicated_fact_slots_are_red(self):
        """P0-2 接线：块外派生事实位被改成错值必须红（修复前这两条实测 rc=0）。

        根因（三席一致）：生成块只独占**部分**派生事实——
        `README.md` 手写插件版本（权威源 `plugin.yaml:2`）、`docs/architecture.md` 的架构图
        手写 `scripts/（16 个）`（实测 17，生成块已渲染 17），而没有任何判据看这两格，于是
        「块外副本静默改错」是真空。修法是把这两格改成**权威指针**，判据是形状而非值：
        块外出现字面值就红（写对的冗余值也红——冗余本身就是漂移源）。

        这里在副本上注入错值（M6 同形的 `插件 8.8.8`、M7 同形的 `scripts/（999 个）`），
        生产入口（子进程跑整份测试）必须 rc≠0，且 findings 点名两个文件。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T16 无效")
            readme = copy / "README.md"
            lines = readme.read_text(encoding="utf-8").split("\n")
            idx = next(i for i, ln in enumerate(lines) if "机械守门补强" in ln)
            lines[idx] = lines[idx] + " 插件 8.8.8"          # M6 同形：块外插件版本副本
            readme.write_text("\n".join(lines), encoding="utf-8")
            arch = copy / "docs" / "architecture.md"
            lines = arch.read_text(encoding="utf-8").split("\n")
            idx = next(i for i, ln in enumerate(lines) if "工具层" in ln)
            lines.insert(idx + 1, "│    变异副本：scripts/（999 个）        │")   # M7 同形
            arch.write_text("\n".join(lines), encoding="utf-8")
            single = [f for f in check(copy) if "派生事实副本" in f]
            self.assertTrue(any(f.startswith("README.md:") and "8.8.8" in f for f in single),
                            f"README 的插件版本副本没红（M6）：{single}")
            self.assertTrue(any(f.startswith("docs/architecture.md:") and "999" in f for f in single),
                            f"architecture 的 scripts 计数副本没红（M7）：{single}")
            live = _run_suite(copy)
            out = live.stdout + live.stderr
            self.assertNotEqual(live.returncode, 0, "块外派生事实副本改坏后生产入口仍绿：G4 没接线")
            self.assertIn("派生事实副本", out, f"子进程红了但没报副本：\n{out[-2000:]}")
            self.assertIn("8.8.8", out, f"子进程没点名 README 的错值：\n{out[-2000:]}")
            self.assertIn("999", out, f"子进程没点名 architecture 的错值：\n{out[-2000:]}")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_t15_policy_bump_without_regeneration_is_red(self):
        """升政策版本却不跑生成器 → `--check` 红；`--write` 之后才绿（变异实证）。

        变异走真实升级路径：政策头 / CHANGELOG 顶层 / bootstrap 记录三处一起升到 9.9.8，
        于是 `version_check.py` 的三元组仍然自洽——红必须**只能**由生成块比对报出来。

        用 9.9.8 而不是 9.9.9：文档里把 `9.9.9` 当作**示例臆造版本**引用（`docs/policy-language.md`
        的「不覆盖自然语言散文断言」一段就写着它）。升到 9.9.9 会让那句合法示例变成
        「生成块外的当前版本」，红的原因就不是「没跑生成器」了——变异必须单因。
        """
        if os.environ.get(_WIRING_ENV) == "1":
            self.skipTest("副本子进程内不递归跑接线证明（N5）")
        copy = _copy_repo()
        try:
            self.assertEqual(check(copy), [], "副本基线不干净，T15 无效")
            old = _policy_version(copy)
            bump = "9.9.8"
            rules = copy / "AGENT_RULES.md"
            rules.write_text(rules.read_text(encoding="utf-8").replace(
                f"模板政策版本：`{old}`", f"模板政策版本：`{bump}`", 1), encoding="utf-8")
            log = copy / "CHANGELOG.md"
            log.write_text(f"## {bump}（变异，无发布）\n\n测试用。\n\n"
                           + log.read_text(encoding="utf-8"), encoding="utf-8")
            boot = copy / ".workspace" / "bootstrap.json"
            data = json.loads(boot.read_text(encoding="utf-8"))
            data["template"]["current_policy_version"] = bump
            boot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            releases = copy / ".workspace" / "releases.json"
            release_data = json.loads(releases.read_text(encoding="utf-8"))
            release_data.append({"version": bump, "date": "2099-01-01", "summary": "变异",
                                 "commit": None, "tag": None, "files_sha256": {}})
            releases.write_text(json.dumps(release_data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

            def run(*args):
                return subprocess.run(
                    [sys.executable, "-B", str(copy / "scripts" / "policy_facts.py"), *args],
                    cwd=str(copy), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)

            stale = run("--check")
            out = stale.stdout + stale.stderr
            self.assertNotEqual(stale.returncode, 0, f"升版未跑生成器却绿：{out[-1500:]}")
            for rel in ("README.md", "README_EN.md", "docs/architecture.md"):
                self.assertIn(rel, out, f"红了但没点名 {rel} 的生成块：{out[-1500:]}")
            self.assertIn(bump, out, "报错没给出渲染后的新值")

            wrote = run("--write")
            self.assertEqual(wrote.returncode, 0, wrote.stdout + wrote.stderr)
            self.assertEqual(run("--check").returncode, 0, "重新生成后 --check 仍红")
            self.assertIn(f"policy-v{bump}", (copy / "README.md").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(copy, ignore_errors=True)


# ── 既有文档检查（与 doc-fact 契约无交集，保留）──────────────────────────────────
class DocsConsistency(unittest.TestCase):
    def test_d1_links_resolve(self):
        bad = []
        exempt_missing = {"README.md", "README_EN.md"}   # 用户首页可删（R127）
        for md in DOCS:
            if not md.is_file():
                continue                     # README_EN 被用户删除时不该让文档检查崩
            base = md.parent
            for target in LINK.findall(md.read_text(encoding="utf-8")):
                t = target.split("#")[0].strip()
                if not t or t.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (base / t).exists() and Path(t).name not in exempt_missing:
                    bad.append(f"{md.relative_to(ROOT)} -> {target}")
        self.assertEqual(bad, [], "断链：\n" + "\n".join(bad))

    def test_d2_skills_doc_covers_all(self):
        doc = (ROOT / "docs" / "skills.md").read_text(encoding="utf-8")
        missing = [n for n in skills() if n not in doc]
        self.assertEqual(missing, [], f"docs/skills.md 未覆盖：{missing}")
        # 版本号一致（允许 3.2.1 这类点号版本直接出现）
        wrong = [f"{n}: doc 未写 {v}" for n, v in skills().items()
                 if v and f"{n}" in doc and v not in doc.split(n, 1)[-1][:400]]
        self.assertEqual(wrong, [], "; ".join(wrong))

    def test_d3_tools_doc_covers_all_scripts(self):
        doc = (ROOT / "docs" / "tools.md").read_text(encoding="utf-8")
        missing = [p.name for p in (ROOT / "scripts").glob("*.py") if p.name not in doc]
        self.assertEqual(missing, [], f"docs/tools.md 未覆盖脚本：{missing}")

    def test_d5_readme_lists_all_skills(self):
        if _is_template_repo() == "instance":
            self.skipTest("派生实例的 README 是用户首页，不参与模板 skill 清单检查")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        missing = [n for n in skills() if n not in readme]
        self.assertEqual(missing, [], f"README 未提到：{missing}")

    def test_d9_github_layer_is_not_template_layer(self):
        """.github/ 是模板仓自己的 CI 基础设施，不下发到实例：不得出现在 TEMPLATE_FILES。"""
        path = ROOT / "scripts" / "verify_rules.py"
        spec = importlib.util.spec_from_file_location("_verify_rules_d9", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        bad = [f for f in mod.TEMPLATE_FILES if f.startswith(".github/")]
        self.assertEqual(bad, [], f".github/ 不应进模板层清单：{bad}")
        if _is_template_repo() == "template":
            self.assertTrue((ROOT / ".github" / "scripts" / "release_anchor.py").is_file(),
                            "模板仓仍需要该脚本（CI 用），只是不下发")

    def test_d10_shields_badges_are_well_formed(self):
        """shields 静态徽章是 `/badge/<label>-<message>-<color>`。

        message 里出现**裸 `-`** 会被当成字段分隔：`policy-v3.5.4-single%20source…` 里的
        `v3.5.4` 被当成 message、`single source of truth` 被当成颜色段，shields 于是返回
        `404: badge not found`——徽章坏掉但页面照常渲染，肉眼与「链接能不能打开」都看不出来。
        字面量连字符要写 `--`，空格写 `%20`。（N4：本测试只判 URL 结构，不判渲染结果。）
        """
        if _is_template_repo() == "instance":
            self.skipTest("派生实例的 README 属用户首页（用户自维护）")
        colors = {"brightgreen", "green", "yellowgreen", "yellow", "orange", "red", "blue",
                  "lightgrey", "grey", "gray", "success", "important", "critical",
                  "informational", "inactive", "blueviolet"}
        url_re = re.compile(r"https://img\.shields\.io/badge/([^\s)\"']+)")
        bad = []
        for name in ("README.md", "README_EN.md"):
            path = ROOT / name
            if not path.exists():
                continue
            for raw in url_re.findall(path.read_text(encoding="utf-8")):
                body = raw.split("?")[0].replace("--", "\x00")   # `--` = 合法的字面量连字符
                parts = body.split("-")
                if len(parts) != 3:
                    bad.append(f"{name}: {raw} → 被切成 {len(parts)} 段（message 里的裸 `-` 要写成 `--` 或 `%20`）")
                    continue
                color = parts[2].removesuffix(".svg")
                if not (re.fullmatch(r"[0-9a-fA-F]{3,8}", color) or color.lower() in colors):
                    bad.append(f"{name}: {raw} → 颜色段 `{parts[2]}` 既不是 hex 也不是已知色名")
        self.assertEqual(bad, [], "徽章 URL 结构错误：\n" + "\n".join(bad))

    def test_d8_skill_table_versions_match_skill_md(self):
        """README / README_EN / docs/skills.md 的 skill 表版本列必须等于 SKILL.md 的 version。

        d2 只查 docs/skills.md，两份 README 的版本列无人管——实测漂移过
        （`docs-retrieval-maintenance` 写 1.2.0，SKILL.md 是 1.3.0），发布前才被审计席抓到。
        """
        bad = []
        user_home = _is_template_repo() == "instance"
        for name, ver in skills().items():
            if not ver:
                continue
            row = re.compile(r"\|\s*\[" + re.escape(name) + r"\]\([^)]*\)\s*\|\s*([^|]+?)\s*\|")
            for md in DOCS:
                if user_home and md.name.startswith("README"):
                    continue      # 实例的 README 是用户首页（同步豁免），版本列不归模板管
                text = md.read_text(encoding="utf-8")
                for m in row.finditer(text):
                    got = m.group(1).strip()
                    if got != ver:
                        bad.append(f"{md.relative_to(ROOT)}: {name} 写 {got}（SKILL.md {ver}）")
        self.assertEqual(bad, [], "skill 表版本漂移：\n" + "\n".join(bad))


class UpgradeSemantics(unittest.TestCase):
    """UPGRADE 的迁移口径必须与当前模型一致（否则会教用户写回旧位置/绝对路径）。"""

    def test_upgrade_says_mode_lives_in_local_state(self):
        text = _upgrade_text()
        self.assertIn("runtime.json", text, "没提本机状态")
        self.assertIn("目录名", text, "没说明 identifier 是 venv 目录名")
        self.assertIn("<scope>/gate.json", text, "没说明 gate 结论的位置")
        self.assertNotIn("identifier` 写**venv 目录的绝对路径**", text,
                         "仍在教用户写明文绝对路径")
        self.assertNotIn("回写 `bootstrap.python`", text, "仍在教用户写旧位置")


if __name__ == "__main__":
    if check():
        sys.exit(main())          # 直接跑文件 = 门禁：先打印 findings 再退出 1
    unittest.main(verbosity=2)
