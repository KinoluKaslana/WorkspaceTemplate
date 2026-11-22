#!/usr/bin/env python3
"""osnorm 防复发门禁（规格 §5）：R1–R9 + adapter oracle（§5.1）+ repo_identity 矩阵（§5.2）。

**这是 blocking 门禁**（由 `tests/` discovery 执行，`ci.yml` 已跑；`docs/tools.md` 有登记）：
`scripts/*.py`、`.workspace/hooks/*.py`、`tests/*.py` 里出现自写归一化 / 自带编码 / 自写原子发布
就直接红，除非该处**结构性豁免**（osnorm 自身、`local_state` 的 no-follow 路径、
两个 hooks adapter）或已在 `tests/osnorm_allowlist.json` 登记。

关键约定：
- 匹配粒度是 `(file, rule, target, line_kind)`，**不按行号**（行号会漂移）。
- allowlist 的 `count` 必须精确等于实测；`baseline` 另钉住每个 `(file, rule)` 的历史最低值。
- 每条规则都有正例/反例自检（R8），违规片段必须命中、合规片段必须不命中。
- `osnorm.py` 是唯一实现，所以它是 R1–R5 的结构性豁免对象；hooks **不裸 import** 它，
  而是经 `.workspace/hooks/osnorm_loader.py` 装载（R41）。装载器的候选顺序、指纹钉死与
  `OSNORM_API` 校验由 `hooks-tests/test_osnorm_loader.py` 的 T1–T4 钉住；只剩下
  纯路径拼接的 adapter（R7）仍按声明子域登记。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / ".workspace" / "hooks"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import osnorm  # noqa: E402

CASES_PATH = ROOT / "tests" / "test_osnorm_cases.json"
ALLOWLIST_PATH = ROOT / "tests" / "osnorm_allowlist.json"
CASES = json.loads(osnorm.read_text(CASES_PATH))

SCAN_ROOTS = (("scripts", SCRIPTS), (".workspace/hooks", HOOKS), ("tests", ROOT / "tests"))
RULES = ("R1", "R2", "R3", "R4", "R5", "R6")

SUBPROCESS_CALLS = frozenset({"subprocess.run", "subprocess.Popen", "subprocess.call",
                              "subprocess.check_output"})
ENCODING_KEYS = ("text", "universal_newlines", "encoding")
NORMALIZER_NAMES = ("canonical", "to_posix", "normalize_path", "_absolute", "_norm_join")


# ---------------------------------------------------------------- 扫描器


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _aliases(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """收集 import 别名：模块别名表 + `from os import replace` 绑定的名字集合。"""
    modules: dict[str, str] = {}
    os_replace_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                scored = f"{node.module}.{alias.name}"
                modules[alias.asname or alias.name] = scored
                if scored == "os.replace":
                    os_replace_names.add(alias.asname or alias.name)
    return modules, os_replace_names


def _resolve(node: ast.AST, modules: dict[str, str]) -> str | None:
    """把 `sp.run` / `replace` 这类别名还原成规范点号名。"""
    dotted = _dotted(node)
    if dotted is None:
        return None
    head, _, tail = dotted.partition(".")
    mapped = modules.get(head)
    if mapped and "." in mapped:
        return mapped
    if mapped:
        return f"{mapped}.{tail}" if tail else mapped
    return dotted


def _kwnames(node: ast.Call) -> list[str]:
    return [kw.arg for kw in node.keywords if kw.arg]


def _is_getattr_os_replace(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name) and node.args[0].id == "os"
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == "replace")


class Violation:
    """一条节点级违规记录（`line` 只用于报告，不参与匹配）。"""

    __slots__ = ("rule", "target", "line_kind", "kind", "line")

    def __init__(self, rule: str, target: str, line_kind: str, kind: str, line: int) -> None:
        self.rule, self.target, self.line_kind = rule, target, line_kind
        self.kind, self.line = kind, line

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.target, self.line_kind)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return f"Violation({self.rule},{self.target},{self.line_kind},L{self.line})"


def scan_source(source: str, filename: str = "<snippet>") -> list[Violation]:
    """AST 遍历一段源码，返回按规则命中的节点级违规（未套豁免）。"""
    tree = ast.parse(source, filename=filename)
    modules, os_replace_names = _aliases(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if any(name == item or (item == "canonical" and name.startswith("canonical"))
                   for item in NORMALIZER_NAMES):
                found.append(Violation("R5", f"def:{name}", "def", "def", node.lineno))
        if not isinstance(node, ast.Call):
            continue
        kws = _kwnames(node)
        dotted = _resolve(node.func, modules)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None

        # R1：文本 I/O 自带编码
        if "encoding" in kws:
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                found.append(Violation("R1", "open", "keyword:encoding=", "call", node.lineno))
            elif attr in ("read_text", "write_text", "open"):
                found.append(Violation("R1", f".{attr}", "keyword:encoding=", "call",
                                       node.lineno))

        # R2：子进程自带文本/编码
        if dotted in SUBPROCESS_CALLS:
            for key in ENCODING_KEYS:
                if key in kws:
                    found.append(Violation("R2", dotted, f"keyword:{key}=", "call",
                                           node.lineno))

        # R3：直接 os.replace
        if dotted == "os.replace" or (isinstance(node.func, ast.Name)
                                      and node.func.id in os_replace_names):
            found.append(Violation("R3", "os.replace", "call", "call", node.lineno))
        elif _is_getattr_os_replace(node.func):
            found.append(Violation("R3", "os.replace", "call", "call", node.lineno))

        # R4：直接 reconfigure stdio
        if attr == "reconfigure":
            found.append(Violation("R4", ".reconfigure", "call", "call", node.lineno))

        # R5：内联 relpath + os.sep 键、内联 normpath(join(...)) 归一
        if attr == "replace" and isinstance(node.func.value, ast.Call):
            inner = _dotted(node.func.value.func)
            if inner == "os.path.relpath":
                found.append(Violation("R5", "expr:relpath_sep_key", "expr", "expr",
                                       node.lineno))
        if dotted == "os.path.normpath" and len(node.args) == 1:
            inner_arg = node.args[0]
            if isinstance(inner_arg, ast.Call) and _dotted(inner_arg.func) == "os.path.join":
                found.append(Violation("R5", "expr:normpath_join", "expr", "expr",
                                       node.lineno))

        # R6：大小写折叠（全局禁止，当前 0 处）
        if dotted == "os.path.normcase":
            found.append(Violation("R6", "os.path.normcase", "call", "call", node.lineno))
    return found


# 结构性豁免：只在规则表「豁免」列写明的那几处（不是存量违规的垃圾桶）。
STRUCTURAL_EXEMPT = (
    {"rule": "R1", "file": "scripts/osnorm.py", "target": None,
     "reason": "osnorm 自身就是唯一实现（§5 R1 豁免列）"},
    {"rule": "R2", "file": "scripts/osnorm.py", "target": None,
     "reason": "run_text 是子进程文本解码的唯一实现（§5 R2 豁免列）"},
    {"rule": "R3", "file": "scripts/osnorm.py", "target": None,
     "reason": "replace_retry 是原子发布的唯一实现（§5 R3 豁免列）"},
    {"rule": "R3", "file": "scripts/local_state.py", "target": "os.replace",
     "reason": "no-follow 路径的重试发布：读侧 sharing 重试是 local_state 私有语义（§5 R3 豁免列）"},
    {"rule": "R4", "file": "scripts/osnorm.py", "target": None,
     "reason": "configure_stdio 是 stdio 重配的唯一实现（§5 R4 豁免列）"},
    {"rule": "R5", "file": "scripts/osnorm.py", "target": None,
     "reason": "canonical_path/family 是归一化的唯一实现（§5 R5 豁免列）"},
    {"rule": "R5", "file": ".workspace/hooks/hook.py", "target": "def:_absolute",
     "reason": "hooks adapter：实例可能没有 scripts/，私有实现按 R7 钉在声明子域上"},
    {"rule": "R5", "file": ".workspace/hooks/gate_core.py", "target": "def:_norm_join",
     "reason": "hooks adapter：实例可能没有 scripts/，私有实现按 R7 钉在声明子域上"},
)


def _exempted(rel: str, violation: Violation) -> dict | None:
    for entry in STRUCTURAL_EXEMPT:
        if entry["rule"] != violation.rule or entry["file"] != rel:
            continue
        if entry["target"] is not None and entry["target"] != violation.target:
            continue
        return entry
    return None


def scan_file(path: Path, rel: str) -> list[Violation]:
    source = osnorm.read_text(path)
    found = scan_source(source, filename=rel)
    return [v for v in found if _exempted(rel, v) is None]


def scan_repo() -> Counter:
    """扫描全量扫描面（已套结构性豁免），返回 {(file, rule, target, line_kind): count}。"""
    counts: Counter = Counter()
    for prefix, root in SCAN_ROOTS:
        for path in sorted(root.glob("*.py")):
            rel = f"{prefix}/{path.name}"
            for violation in scan_file(path, rel):
                counts[(rel,) + violation.key] += 1
    return counts


# ---------------------------------------------------------------- R9：allowlist 审计


def load_allowlist() -> dict:
    return json.loads(osnorm.read_text(ALLOWLIST_PATH))


def audit(actual: Counter, entries: list[dict], baseline: dict[str, dict[str, int]]) -> list[str]:
    """实测、精确登记与历史最低基线三方对账；任一方向漂移都算问题。"""
    problems: list[str] = []
    recorded: dict[tuple, dict] = {}
    for entry in entries:
        for field in ("file", "rule", "kind", "target", "line_kind", "count", "reason",
                      "expires"):
            if field not in entry:
                problems.append(f"allowlist 条目缺字段 {field}：{entry}")
        if entry.get("rule") not in RULES:
            problems.append(f"allowlist 条目规则不在 R1–R6：{entry.get('rule')!r}")
        if not entry.get("reason") or not entry.get("expires"):
            problems.append(f"allowlist 条目缺 reason/expires：{entry}")
        count = entry.get("count")
        if not isinstance(count, int) or count <= 0:
            problems.append(f"allowlist count 必须是正整数（不许填 0）：{entry}")
            count = 0
        if not (ROOT / str(entry.get("file", ""))).is_file():
            problems.append(f"allowlist 条目指向不存在的文件：{entry.get('file')!r}")
        recorded[(entry["file"], entry["rule"], entry["target"], entry["line_kind"])] = entry

    for key, count in sorted(actual.items()):
        entry = recorded.get(key)
        if entry is None:
            problems.append(f"未登记的违规：{key} 实测 {count} 处（要么修掉，要么入表）")
            continue
        limit = entry.get("count") if isinstance(entry.get("count"), int) else 0
        if count != limit:
            problems.append(f"违规数与登记不等：{key} 实测 {count} != count {limit}")

    for key, entry in sorted(recorded.items()):
        if actual.get(key, 0) == 0:
            problems.append(f"登记项已归零或不存在，必须删除该表项：{key}")

    current_by_file_rule = Counter()
    for entry in entries:
        if isinstance(entry.get("count"), int) and entry["count"] > 0:
            current_by_file_rule[(entry.get("file"), entry.get("rule"))] += entry["count"]
    baseline_by_file_rule: dict[tuple, int] = {}
    for file, rules in baseline.items():
        if not isinstance(rules, dict):
            problems.append(f"baseline 文件项必须是 rule→count 对象：{file!r}")
            continue
        for rule, count in rules.items():
            key = (file, rule)
            if rule not in RULES:
                problems.append(f"baseline 条目规则不在 R1–R6：{key}")
            if not isinstance(count, int) or count <= 0:
                problems.append(f"baseline count 必须是正整数：{key}={count!r}")
                count = 0
            baseline_by_file_rule[key] = count
    for key, count in sorted(current_by_file_rule.items()):
        if key not in baseline_by_file_rule:
            problems.append(f"baseline 缺少当前登记项：{key}")
        elif count > baseline_by_file_rule[key]:
            problems.append(
                f"当前 count 高于历史最低 baseline：{key} 当前 {count} > baseline {baseline_by_file_rule[key]}"
            )
    for key in sorted(set(baseline_by_file_rule) - set(current_by_file_rule)):
        problems.append(f"baseline 存在无当前登记项：{key}")
    return problems


# ---------------------------------------------------------------- 测试：R8 规则自检


RULE_FIXTURES = {
    "R1": ("open('f', encoding='utf-8')\n"
           "p.read_text(encoding='utf-8')\n"
           "p.write_text('x', encoding='utf-8')\n",
           "open('f', 'rb')\np.read_bytes()\np.write_bytes(b'x')\n"),
    "R2": ("subprocess.run(['a'], text=True)\n"
           "subprocess.Popen(['a'], universal_newlines=True)\n"
           "subprocess.check_output(['a'], encoding='utf-8')\n",
           "subprocess.run(['a'], capture_output=True)\nosnorm.run_text(['a'])\n"),
    "R3": ("from os import replace\nos.replace(a, b)\nreplace(a, b)\n"
           "getattr(os, 'replace')(a, b)\n",
           "replace_retry(a, b)\nosnorm.atomic_write_text(p, 'x')\n"),
    "R4": ("sys.stdout.reconfigure(encoding='utf-8')\n"
           "stream.reconfigure(errors='replace')\n",
           "osnorm.configure_stdio()\nprint('x')\n"),
    "R5": ("def _norm_join(base, rel):\n    return rel\n"
           "def canonical_local(x):\n    return x\n"
           "out = os.path.relpath(p, root).replace(os.sep, '/')\n"
           "key = os.path.normpath(os.path.join(root, rel))\n",
           "def join_thing(base, rel):\n    return rel\n"
           "value = os.path.normpath(name)\n"),
    "R6": ("os.path.normcase(p)\n", "os.path.normpath(p)\nos.path.normcasex(p)\n"),
}

EXPECTED_TARGETS = {
    "R1": {"open", ".read_text", ".write_text"},
    "R2": {"subprocess.run", "subprocess.Popen", "subprocess.check_output"},
    "R3": {"os.replace"},
    "R4": {".reconfigure"},
    "R5": {"def:_norm_join", "def:canonical_local", "expr:relpath_sep_key",
           "expr:normpath_join"},
    "R6": {"os.path.normcase"},
}

# 正例里应当命中多少处（只查集合会漏掉「别名解析失效」这类退化成 1 处的病）
EXPECTED_COUNTS = {"R1": 3, "R2": 3, "R3": 3, "R4": 2, "R5": 4, "R6": 1}


class RuleSelfCheckTest(unittest.TestCase):
    """R8：每条规则「违规片段必须命中」+「合规片段必须不命中」；osnorm 自身必须干净。"""

    def test_positive_fixtures_hit_every_rule(self) -> None:
        for rule, (bad, _good) in RULE_FIXTURES.items():
            hits = scan_source(bad)
            rule_hits = [v for v in hits if v.rule == rule]
            matched = {v.target for v in rule_hits}
            self.assertTrue(matched, f"{rule} 的正例没有被命中")
            self.assertEqual(matched, EXPECTED_TARGETS[rule],
                             f"{rule} 命中的节点类与预期不符：{matched}")
            self.assertEqual(len(rule_hits), EXPECTED_COUNTS[rule],
                             f"{rule} 命中数不符（别名解析或去重可能失效）：{rule_hits}")

    def test_negative_fixtures_stay_clean(self) -> None:
        for rule, (_bad, good) in RULE_FIXTURES.items():
            hits = [v for v in scan_source(good) if v.rule == rule]
            self.assertEqual(hits, [], f"{rule} 的合规片段被误判：{hits}")

    def test_osnorm_itself_is_clean_under_every_rule(self) -> None:
        """osnorm.py 走完整规则（含结构性豁免）后必须 0 命中。"""
        rel = "scripts/osnorm.py"
        raw = scan_source(osnorm.read_text(ROOT / rel), filename=rel)
        self.assertTrue(raw, "前提不成立：osnorm.py 原始扫描竟然一条都没有（规则可能失效）")
        self.assertEqual(scan_file(ROOT / rel, rel), [],
                         "osnorm.py 在结构性豁免之外仍有违规")

    def test_every_rule_has_fixtures(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), set(EXPECTED_TARGETS))
        self.assertEqual(set(RULE_FIXTURES), set(EXPECTED_COUNTS))
        self.assertEqual(set(RULE_FIXTURES), set(RULES))

    def test_structural_exemptions_are_justified(self) -> None:
        for entry in STRUCTURAL_EXEMPT:
            self.assertTrue(entry["reason"], f"结构性豁免缺理由：{entry}")
            self.assertIn(entry["rule"], RULES)
            self.assertTrue((ROOT / entry["file"]).is_file(), f"豁免指向不存在的文件：{entry}")


# ---------------------------------------------------------------- 测试：R1–R6 全量扫描


class SingleSourceScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actual = scan_repo()

    def test_scan_covers_three_roots(self) -> None:
        """扫描面必须覆盖三个根：逐根枚举、逐个真扫（**不以「该根还有违规」为前提**）。

        R40b 之后 `scripts/` 已清零（8 个脚本全部迁到 osnorm），「三个根都还剩存量违规」这条
        旧前提永久失效；覆盖性改由「逐根枚举 + 真解析」证明，另两个根仍以「有登记违规」
        作为扫描确实落地的旁证（那两处是本轮明令不动的存量）。
        """
        for prefix, root in SCAN_ROOTS:
            files = sorted(root.glob("*.py"))
            self.assertTrue(files, f"{prefix} 根下没有可扫描文件")
            for path in files:
                scan_file(path, f"{prefix}/{path.name}")   # 解析失败会直接抛
        seen = {key[0] for key in self.actual}
        for prefix in (".workspace/hooks", "tests"):
            self.assertTrue(any(f.startswith(prefix + "/") for f in seen),
                            f"{prefix} 的存量违规没有被扫描到")

    def test_r6_normcase_stays_zero(self) -> None:
        hits = [key for key in self.actual if key[1] == "R6"]
        self.assertEqual(hits, [], f"R6（os.path.normcase）必须保持 0 处：{hits}")

    def test_new_osnorm_files_are_clean(self) -> None:
        ours = ("scripts/osnorm.py", "tests/test_osnorm_properties.py",
                "tests/test_osnorm_single_source.py")
        dirty = [key for key in self.actual if key[0] in ours]
        self.assertEqual(dirty, [], f"本轮新增文件不得带任何存量违规：{dirty}")

    def test_no_unregistered_violation(self) -> None:
        doc = load_allowlist()
        problems = audit(self.actual, doc["entries"], doc["baseline"])
        self.assertEqual(problems, [], "\n".join(problems))


class SeamExamplesTest(unittest.TestCase):
    """门禁必须真的盯住已知接缝：规格 §5 点名的存量位置仍然可被检出。"""

    def _scan(self, rel: str) -> list[Violation]:
        return scan_source(osnorm.read_text(ROOT / rel), filename=rel)

    def test_hooks_r2_is_fully_migrated(self) -> None:
        """R41：hooks 的子进程一律走 `osnorm.run_text`，R2 必须保持 0 处。

        检出能力本身由 R8 的 `RULE_FIXTURES` 正例保证（违规片段必须命中），这里钉的是
        迁移结果——不是把规则删掉。回归会立刻红：任何自带文本/编码参数的 subprocess 调用都算。
        """
        for rel in (".workspace/hooks/hook.py", ".workspace/hooks/cc_transcript.py",
                    ".workspace/hooks/gate_core.py", ".workspace/hooks/template_anchor.py"):
            hits = [v for v in self._scan(rel) if v.rule == "R2"]
            self.assertEqual(hits, [], f"{rel} 仍有自带编码的子进程：{hits}")

    def test_hooks_r1_r3_are_fully_migrated(self) -> None:
        """R41：hooks 的文本 I/O 与原子发布一律走 osnorm，R1/R3 必须保持 0 处。"""
        for rel in (".workspace/hooks/hook.py", ".workspace/hooks/cc_transcript.py",
                    ".workspace/hooks/gate_core.py", ".workspace/hooks/template_anchor.py",
                    ".workspace/hooks/delegations.py"):
            for rule in ("R1", "R3"):
                hits = [v for v in self._scan(rel) if v.rule == rule]
                self.assertEqual(hits, [], f"{rel} 仍有 {rule} 违规：{hits}")

    def test_hooks_stdlib_is_reached_only_through_the_loader(self) -> None:
        """hooks 里不得出现裸 `import osnorm` / `from osnorm import`（必须经装载器）。"""
        for path in sorted(HOOKS.glob("*.py")):
            tree = ast.parse(osnorm.read_text(path), filename=path.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertNotIn("osnorm", names,
                                 f"{path.name}:{node.lineno} 出现裸 import osnorm（须经 osnorm_loader）")

    def test_gate_core_relpath_keys_are_detected(self) -> None:
        hits = [v for v in self._scan(".workspace/hooks/gate_core.py")
                if v.rule == "R5" and v.target == "expr:relpath_sep_key"]
        self.assertEqual(len(hits), 2, "gate_core 的 relpath + os.sep 键应为 2 处")


# ---------------------------------------------------------------- 测试：R9 allowlist


class AllowlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actual = scan_repo()
        self.doc = load_allowlist()

    def test_schema(self) -> None:
        self.assertEqual(self.doc["version"], 1)
        self.assertIsInstance(self.doc["entries"], list)
        self.assertIsInstance(self.doc["baseline"], dict)
        self.assertTrue(self.doc["entries"], "第 0 步的存量基线不得为空")

    def test_counts_match_reality(self) -> None:
        problems = audit(self.actual, self.doc["entries"], self.doc["baseline"])
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_entry_has_reason_and_expiry(self) -> None:
        for entry in self.doc["entries"]:
            self.assertTrue(entry["reason"].strip())
            self.assertRegex(entry["expires"], r"^R\d+[a-z]?$")

    def test_counts_never_zero(self) -> None:
        for entry in self.doc["entries"]:
            self.assertGreaterEqual(entry["count"], 1, f"不许填 0：{entry}")
            key = (entry["file"], entry["rule"], entry["target"], entry["line_kind"])
            self.assertGreaterEqual(self.actual.get(key, 0), 1,
                                    f"登记项实测为 0，必须删除：{key}")

    def test_audit_is_not_vacuous(self) -> None:
        """自检：审计器必须能抓到「超上限 / 归零未删 / 未登记」三种病。"""
        entries = [{"file": "tests/test_osnorm_properties.py", "rule": "R1", "kind": "call",
                    "target": "open", "line_kind": "keyword:encoding=", "count": 1,
                    "reason": "自检用假条目", "expires": "R39"}]
        over = Counter({("tests/test_osnorm_properties.py", "R1", "open",
                         "keyword:encoding="): 3})
        baseline = {"tests/test_osnorm_properties.py": {"R1": 1}}
        self.assertTrue(any("与登记不等" in p for p in audit(over, entries, baseline)))
        zeroed = Counter()
        self.assertTrue(any("必须删除" in p for p in audit(zeroed, entries, baseline)))
        unregistered = Counter({("scripts/osnorm.py", "R6", "os.path.normcase", "call"): 1})
        self.assertTrue(any("未登记" in p for p in audit(unregistered, entries, baseline)))
        self.assertTrue(any("必须删除" in p for p in audit(unregistered, entries, baseline)))
        bad_zero = [dict(entries[0], count=0)]
        self.assertTrue(any("正整数" in p for p in audit(over, bad_zero, baseline)))
        no_reason = [dict(entries[0], count=3, reason="")]
        self.assertTrue(any("reason/expires" in p for p in audit(over, no_reason, baseline)))

    def test_t_b2a_count_plus_one_is_red(self) -> None:
        entries = [dict(item) for item in self.doc["entries"]]
        entries[0]["count"] += 1
        self.assertTrue(any("与登记不等" in p
                            for p in audit(self.actual, entries, self.doc["baseline"])))

    def test_t_b2b_count_minus_one_is_red(self) -> None:
        entries = [dict(item) for item in self.doc["entries"]]
        entries[0]["count"] -= 1
        self.assertTrue(any("与登记不等" in p
                            for p in audit(self.actual, entries, self.doc["baseline"])))

    def test_t_b2c_count_above_baseline_is_red(self) -> None:
        entry = dict(self.doc["entries"][0])
        baseline = {entry["file"]: {entry["rule"]: entry["count"]}}
        entry["count"] += 1
        actual = Counter({(entry["file"], entry["rule"], entry["target"],
                           entry["line_kind"]): entry["count"]})
        self.assertTrue(any("高于历史最低 baseline" in p
                            for p in audit(actual, [entry], baseline)))


# ---------------------------------------------------------------- 测试：R7 adapter oracle


ADAPTERS = {
    "hook._absolute": {
        "module": "hook", "attr": "_absolute",
        "subdomain": "逐字路径、非空；返回宿主原生绝对路径（不要求 POSIX 分隔符、不做盘符归一、不剥 \\\\?\\）",
        "not_equivalent": [({"args": ["C:\\a\\b"], "cwd": "/ws"}, "盘符形态：adapter 只做 join+normpath")],
    },
    "gate_core._norm_join": {
        "module": "gate_core", "attr": "_norm_join",
        "subdomain": "仅拼接（expanduser + join + normpath），不是安全围栏，不调用 abspath",
        "not_equivalent": [({"args": ["/ws", "C:\\a\\b"]}, "盘符形态：adapter 不认分隔符与盘符")],
    },
    "cc_transcript.slug_for_cwd": {
        "module": "cc_transcript", "attr": "slug_for_cwd",
        "subdomain": "仅供定位 projects 目录，不参与任何安全判定",
        "not_equivalent": [({"args": ["/home/u/x"]}, "slug 形态与 canonical 无等价关系")],
    },
}


def _hook_module(name: str):
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    return importlib.import_module(name)


# 金表期望只允许这几种「白名单 formula」：名字 → 用同一批 os 原语在**本宿主**上现算的值。
# 表外名字一律 AssertionError；不做 eval/exec，所以 JSON 夹带不了任意代码。
# 禁止的写法：把宿主原生绝对路径（`/h/x`、`C:\Users\u\x`、`\ws\a\b`）当字面量写进金表——
# 同一条行在另一个宿主上必然对不上（`~` 的展开源、`abspath` 的盘符都由宿主决定）。
FORMULAS = frozenset({
    "normpath_join", "normpath", "normpath_expanduser", "abspath_normpath",
    "abspath_normpath_join", "abspath_normpath_expanduser", "abspath_slug",
})


def _formula(spec: dict) -> str:
    name, args = spec["formula"], list(spec.get("args") or [])
    if name not in FORMULAS:
        raise AssertionError(f"未知 formula：{name!r}（只认白名单 {sorted(FORMULAS)}）")
    if name == "normpath_join":
        joined = os.path.join(args[0], args[1])
        return os.path.normpath(joined)
    if name == "normpath":
        return os.path.normpath(args[0])
    if name == "normpath_expanduser":
        # `~` 展开源由宿主决定（POSIX: HOME；Windows: ntpath 走 USERPROFILE），
        # 所以整条 `~/x` 必须在运行期展开，不能把展开结果写进 args。
        return os.path.normpath(os.path.expanduser(args[0]))
    if name == "abspath_normpath":
        return os.path.normpath(os.path.abspath(args[0]))
    if name == "abspath_normpath_join":
        joined = os.path.join(args[0], args[1])
        return os.path.normpath(os.path.abspath(joined))
    if name == "abspath_normpath_expanduser":
        return os.path.normpath(os.path.abspath(os.path.expanduser(args[0])))
    if name == "abspath_slug":
        body = os.path.abspath(args[0]).replace("\\", "/").replace(os.sep, "/").lstrip("/")
        return "-" + body.replace(":", "-").replace("/", "-")
    raise AssertionError(f"未知 formula：{name}")


def _expected(spec) -> str:
    if isinstance(spec, str):
        return spec
    if "host" in spec:
        platform = "windows" if os.name == "nt" else "posix"
        return _expected(spec["host"][platform])
    return _formula(spec)


class AdapterOracleTest(unittest.TestCase):
    """§5.1：三个 adapter 的逐项 oracle——不比「全金表相等」，只比声明子域。"""

    def _call(self, row: dict, module):
        func = getattr(module, ADAPTERS[row["fn"]]["attr"])
        args = list(row.get("args") or [])
        if "cwd" in row:
            return func(*args, cwd=row["cwd"])
        return func(*args)

    def test_rows_use_registered_adapters_only(self) -> None:
        used = {row["fn"] for row in CASES["adapters"]}
        self.assertEqual(used, set(ADAPTERS),
                         f"oracle 表与登记表不一致：{used ^ set(ADAPTERS)}")

    def test_oracle_rows(self) -> None:
        for row in CASES["adapters"]:
            module = _hook_module(ADAPTERS[row["fn"]]["module"])
            saved = {}
            for key, value in (row.get("env") or {}).items():
                saved[key] = os.environ.get(key)
                os.environ[key] = value
            try:
                if "raises" in row:
                    error_type = getattr(module, row["raises"])
                    with self.assertRaises(error_type, msg=f"未抛 {row['raises']}：{row}"):
                        self._call(row, module)
                    continue
                got = self._call(row, module)
                # 期望值必须在注入的 env 里算（`~` 展开依赖 HOME/USERPROFILE）
                expected = _expected(row["expected"])
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(got, expected,
                             f"adapter oracle 不一致：{row['fn']}{row.get('args')}")

    def test_canonical_output_is_never_a_valid_adapter_expectation(self) -> None:
        """反例：拿 canonical_path 的输出当 adapter 期望 → 必须对不上（两者不同域）。"""
        for name, spec in ADAPTERS.items():
            module = _hook_module(spec["module"])
            checked = 0
            for row, why in spec["not_equivalent"]:
                args = list(row["args"])
                func = getattr(module, spec["attr"])
                got = func(*args, cwd=row["cwd"]) if "cwd" in row else func(*args)
                canonical = osnorm.canonical_path(args[-1])
                self.assertNotEqual(got, canonical, f"{name} 与 canonical 在该输入上重合（{why}）")
                checked += 1
            self.assertGreaterEqual(checked, 1, f"{name} 缺反例")

    def test_subdomains_are_declared(self) -> None:
        for name, spec in ADAPTERS.items():
            self.assertTrue(spec["subdomain"].strip(), f"{name} 未声明子域")
            self.assertTrue(spec["not_equivalent"], f"{name} 未登记「不比什么」")

    def test_golden_formulas_stay_whitelisted(self) -> None:
        """金表里出现的每个 formula 都必须在白名单内，且 `_formula` 拒绝表外名字。

        这条是「不允许 eval 任意代码」的机械门禁：期望只能是白名单 formula 的组合
        （运行期用同一批 os 原语现算），不是可执行字符串。
        """
        used: set[str] = set()

        def walk(spec) -> None:
            if isinstance(spec, dict):
                if "formula" in spec:
                    used.add(spec["formula"])
                    walk(spec.get("args"))
                else:
                    for value in spec.values():
                        walk(value)
            elif isinstance(spec, list):
                for item in spec:
                    walk(item)

        for row in CASES["adapters"]:
            walk(row.get("expected"))
        self.assertTrue(used, "前提不成立：金表里没有任何 formula")
        self.assertEqual(used - FORMULAS, set(), f"金表用了白名单外的 formula：{used - FORMULAS}")
        with self.assertRaises(AssertionError, msg="表外 formula 必须报错而不是被解释执行"):
            _formula({"formula": "eval(__import__('os').getcwd())", "args": []})

    def test_slug_rows_are_filename_safe(self) -> None:
        """slug 行的形态判据（宿主无关，不写死字面量）：`-` 前缀、无分隔符、无盘符冒号。

        这条钉的正是 `cc_transcript` 注释里那类事故：Windows 上若不做 `\\`→`/` 折叠，
        slug 会变成 `-C:/Users/...`，带冒号的名字在 NTFS 上直接非法。
        """
        checked = 0
        for row in CASES["adapters"]:
            if row["fn"] != "cc_transcript.slug_for_cwd" or "raises" in row:
                continue
            module = _hook_module(ADAPTERS[row["fn"]]["module"])
            value = self._call(row, module)
            self.assertTrue(value.startswith("-"), f"slug 前缀不符：{value!r}（{row['args']}）")
            for bad in (":", "/", "\\"):
                self.assertNotIn(bad, value,
                                 f"slug 含非法字符 {bad!r}：{value!r}（{row['args']}）")
            checked += 1
        self.assertGreaterEqual(checked, 2, "slug 行不足，形态判据没覆盖到")


# ---------------------------------------------------------------- 测试：§5.2 repo_identity 矩阵


def _load_repo_identity():
    spec = importlib.util.spec_from_file_location("osnorm_ri_probe",
                                                 SCRIPTS / "repo_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# (输入, exists=True 期望, exists=False 期望)；`None` 表示身份判定 fail-closed。
REPO_IDENTITY_MATRIX = [
    ("C:\\work\\repo", "C:/work/repo", None),
    ("C:/work/repo", "C:/work/repo", None),
    ("\\\\?\\C:\\work\\repo", "C:/work/repo", None),
    ("\\\\host\\share\\x", "//host/share/x", None),
    ("\\\\wsl.localhost\\Ubuntu\\home\\u\\x", "//wsl.localhost/Ubuntu/home/u/x", None),
    ("/c:/work/repo", None, None),
    ("file:///C:/work/repo", "C:/work/repo", None),
    ("file://localhost/x", "/x", "/x"),
    ("file://host/share/x", None, None),
    ("C:relative", None, None),
    ("C:", None, None),
    ("C:/", None, None),
    ("//host", None, None),
    ("a/b", None, None),
    ("./a", None, None),
    ("", None, None),
    ("  ", None, None),
]

# 本轮明确不改、只登记的偏差（R39 与「是否委托」一起裁决）。
KNOWN_DEVIATIONS = [
    {"id": "KNOWN-DEVIATION-1", "input": "\\\\.\\C:\\work\\repo",
     "repo_identity": "C:/work/repo", "osnorm": "PathFormError",
     "why": "repo_identity 盲剥 \\\\.\\ 前缀，而 osnorm D7 要求拒绝设备命名空间",
     "handle_at": "R39"},
]


class RepoIdentityContractTest(unittest.TestCase):
    """§5.2：characterization（不是新判据）——任何一行变化即红。"""

    def setUp(self) -> None:
        self.ri = _load_repo_identity()

    def test_matrix_rows(self) -> None:
        for value, if_exists, if_absent in REPO_IDENTITY_MATRIX:
            self.assertEqual(self.ri.convert_format(value, exists=lambda p: True), if_exists,
                             f"exists=True 行不一致：{value!r}")
            self.assertEqual(self.ri.convert_format(value, exists=lambda p: False), if_absent,
                             f"exists=False 行不一致：{value!r}")

    def test_exists_injection_is_effective(self) -> None:
        """存在性把关必须真的生效（否则矩阵里 exists=False 的行没有意义）。"""
        calls = []
        self.ri.convert_format("C:\\work\\repo", exists=lambda p: calls.append(p) or False)
        self.assertEqual(calls, ["C:\\work\\repo"],
                         "exists 注入没有被调用，或用的不是原始写法")

    def test_known_deviation_1_still_stands(self) -> None:
        for item in KNOWN_DEVIATIONS:
            self.assertEqual(self.ri.convert_format(item["input"], exists=lambda p: True),
                             item["repo_identity"],
                             f"{item['id']} 的 repo_identity 侧行为已变化，需重裁")
            with self.assertRaises(osnorm.PathFormError):
                osnorm.canonical_path(item["input"])
            self.assertEqual(item["handle_at"], "R39")

    def test_msys_form_is_a_different_domain(self) -> None:
        """/c:/work/repo 在身份判定里是 None，而 canonical 能归一 —— 这正是 D3 拒绝整段委托的理由。"""
        self.assertIsNone(self.ri.convert_format("/c:/work/repo", exists=lambda p: True))
        self.assertEqual(osnorm.canonical_path("/c:/work/repo"), "C:/work/repo")


if __name__ == "__main__":
    unittest.main()
