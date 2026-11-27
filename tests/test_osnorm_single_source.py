#!/usr/bin/env python3
"""osnorm 防复发门禁（规格 §5）：R1–R9 + adapter oracle（§5.1）+ repo_identity 矩阵（§5.2）。

**这是 blocking 门禁**（由 `tests/` discovery 执行，`ci.yml` 已跑；`docs/tools.md` 有登记）：
`scripts/*.py`、`.workspace/hooks/*.py`、`tests/*.py` 里出现自写归一化 / 自带编码 / 自写原子发布
就直接按文件、行号和规则报告，规则级豁免见 `STRUCTURAL_EXEMPT`。
`tests/osnorm_allowlist.json` 的四个账本段必须为空，登记和计数不参与 blocking 判定。

保留的历史账本校验和 oracle 约定：
- 匹配粒度是 `(file, rule, target, line_kind)`，**不按行号**（行号会漂移）。
- allowlist 的 `count` 必须精确等于实测；`baseline` 另钉住每个 `(file, rule)` 的历史最低值。
- allowlist v2 的 `structural` 段是**结构性豁免**（R43）：位置永久保留，但每条必须带一条可执行
  oracle，门禁真跑它（收集 + 通过）并核对 `(file, rule, target=函数名#序数)` 仍能在真实代码里
  定位；判定要么 `(a)` 子域内与 osnorm 同一路径标识，要么 `(b)` 证明 osnorm 产不出该处产物。
  豁免与实测**一一对应**：删条目、加一处 R5、改函数名、oracle 变红，四种情况都必红。
- 每条规则都有正例/反例自检（R8），违规片段必须命中、合规片段必须不命中。
- `osnorm.py` 是唯一实现，所以它是 R1–R5 的结构性豁免对象；hooks **不裸 import** 它，
  而是经 `.workspace/hooks/osnorm_loader.py` 装载（R41）。装载器的候选顺序、指纹钉死与
  `OSNORM_API` 校验由 `hooks-tests/test_osnorm_loader.py` 的 T1–T4 钉住；只剩下
  纯路径拼接的 adapter（R7）仍按声明子域登记。
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import json
import ntpath
import os
import posixpath
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

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
    """一条节点级违规记录（`line` 只用于报告，不参与匹配）。

    `node` 是该违规对应的 AST 节点（只有 R5 的表达式类命中会带上它）：结构性豁免的
    逐处 oracle 要**求值真实代码里的那个表达式**，而不是复制一份文本；复制会漂移成
    第二份实现。
    """

    __slots__ = ("rule", "target", "line_kind", "kind", "line", "node")

    def __init__(self, rule: str, target: str, line_kind: str, kind: str, line: int,
                 node: ast.AST | None = None) -> None:
        self.rule, self.target, self.line_kind = rule, target, line_kind
        self.kind, self.line, self.node = kind, line, node

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
                                       node.lineno, node))
        if dotted == "os.path.normpath" and len(node.args) == 1:
            inner_arg = node.args[0]
            if isinstance(inner_arg, ast.Call) and _dotted(inner_arg.func) == "os.path.join":
                found.append(Violation("R5", "expr:normpath_join", "expr", "expr",
                                       node.lineno, node))

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


def _exempted(rel: str, violation: Violation, index: dict[tuple[str, int], dict] | None = None) -> dict | None:
    """规则级结构性豁免（`STRUCTURAL_EXEMPT`，粒度 = (file, rule, target/表达式)）。"""
    for entry in STRUCTURAL_EXEMPT:
        if entry["rule"] != violation.rule or entry["file"] != rel:
            continue
        if entry["target"] is not None and entry["target"] != violation.target:
            continue
        return entry
    if index:
        hit = index.get((rel, violation.line))
        if hit is not None and hit.get("rule") == violation.rule:
            return hit
    return None


# ---------------------------------------------------------------- structural 段（S3 落地）

STRUCTURAL_FIELDS = ("file", "rule", "target", "expr", "verdict", "count_baseline",
                     "reason", "oracle")


def load_structural() -> list[dict]:
    """`tests/osnorm_allowlist.json` 的 `structural` 段：位置永久保留 + 理由由 oracle 持续验证。"""
    return list(load_allowlist().get("structural") or [])


def load_subdomains() -> dict:
    """`structural` 里 `verdict=a` 条目共用的具名子域注册表（R48/P5）。

    边界文本只在注册表里维护一份；条目只写 `subdomain` 名 + 本条特有理由，门禁校验
    名存在、reason 引用了它、且没有把边界文本再抄一遍。
    """
    subs = load_allowlist().get("subdomains")
    return subs if isinstance(subs, dict) else {}


def _split_target(target: object) -> tuple[str, int]:
    """`函数名#序数` → (函数名, 序数)。

    序数是**该函数内** R5 命中按源码顺序的第几处，不是绝对行号：行号会随上面任何一行
    改动漂移，漂移即红会把门禁变成噪音；函数内序数只在「真的多了一处」时才变。
    """
    name, sep, ordinal = str(target).partition("#")
    if not name or not sep or not ordinal.isdigit() or int(ordinal) < 1:
        raise ValueError(f"structural target 必须是 `函数名#序数`：{target!r}")
    return name, int(ordinal)


@functools.lru_cache(maxsize=None)
def _file_scan(rel: str) -> tuple[frozenset[str], tuple[dict, ...]]:
    """(文件里的函数名集合, 每个 R5 命中的定位)。

    定位 = `{function, expr, ordinal, line, node}`；`node` 供逐处 oracle 现场求值真实表达式。
    """
    return _scan_file_source(osnorm.read_text(ROOT / rel), rel)


def _scan_file_source(source: str, rel: str) -> tuple[frozenset[str], tuple[dict, ...]]:
    tree = ast.parse(source, filename=rel)
    defs = [(node.lineno, node.end_lineno or node.lineno, node.name)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    sites: list[dict] = []
    hits = [violation for violation in scan_source(source, filename=rel) if violation.rule == "R5"]
    # `ast.walk` 是广度优先，命中顺序不是源码顺序；序数必须按行号排，否则「第几处」会随
    # 节点层级而变（这就是第一次实现踩到的坑）。
    seen: Counter = Counter()
    for violation in sorted(hits, key=lambda item: item.line):
        inner = [item for item in defs if item[0] <= violation.line <= item[1]]
        function = min(inner, key=lambda item: item[1] - item[0])[2] if inner else "<module>"
        seen[function] += 1
        sites.append({"function": function, "expr": violation.target,
                      "ordinal": seen[function], "line": violation.line,
                      "node": violation.node})
    return frozenset(name for _start, _end, name in defs), tuple(sites)


def _site_map(entries: list[dict]) -> tuple[dict[tuple[str, int], dict], list[str]]:
    """structural 条目 → {(file, line): 条目}；解析失败的条目进问题清单（不静默跳过）。"""
    index: dict[tuple[str, int], dict] = {}
    problems: list[str] = []
    for entry in entries:
        rel = str(entry.get("file") or "")
        if not (ROOT / rel).is_file():
            continue  # schema 阶段已单独报
        try:
            function, ordinal = _split_target(entry.get("target"))
        except ValueError as exc:
            problems.append(str(exc))
            continue
        names, sites = _file_scan(rel)
        if function not in names:
            problems.append(f"structural 目标函数不存在：{rel}::{function}"
                            f"（改函数名/删函数即视为该豁免失效）")
            continue
        hits = [site for site in sites if site["function"] == function]
        if ordinal > len(hits):
            problems.append(f"structural 目标越界：{rel}::{entry.get('target')}"
                            f" 在该函数里只有 {len(hits)} 处 R5 命中")
            continue
        site = hits[ordinal - 1]
        if site["expr"] != entry.get("expr"):
            problems.append(f"structural 序数处表达式类不符：{rel}::{entry.get('target')} "
                            f"实测 {site['expr']} != 登记 {entry.get('expr')!r}")
            continue
        if (rel, site["line"]) in index:
            problems.append(f"两条 structural 条目指向同一处：{rel}::{entry.get('target')}")
            continue
        index[(rel, site["line"])] = entry
    return index, problems


def _oracle_specs(entry: dict) -> list[str]:
    """条目声明的 oracle 用例（单个字符串或字符串列表；R48 起 gate_key 有 4 条）。"""
    raw = entry.get("oracle")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        return list(raw)
    return []


def _oracle_shape(entry: dict) -> str | None:
    """oracle 引用的形态判据（① 的存在性部分；不跑用例）；列表里每一条都要过。"""
    specs = _oracle_specs(entry)
    if not specs:
        return f"oracle 缺失或形态非法（只认非空字符串 / 字符串列表）：{entry.get('oracle')!r}"
    for spec in specs:
        file_part, _sep, rest = spec.partition("::")
        cls, _sep2, method = rest.partition("::")
        if not file_part.endswith(".py") or not (ROOT / file_part).is_file():
            return f"oracle 指向不存在的测试文件：{spec!r}"
        if not cls.startswith("StructuralOracle"):
            return (f"oracle 必须指向 StructuralOracle* 类里的专用站点用例"
                    f"（防止把门禁自身的审计用例当 oracle 造成递归）：{spec!r}")
        if not method.startswith("test_oracle_"):
            return f"oracle 方法名必须以 test_oracle_ 开头：{spec!r}"
    return None


def _run_oracle(entry: dict) -> list[str]:
    """① 真被收集 + ② 当前通过：列表里每条都跑真实子进程 `unittest`（缺一即红）。"""
    problems: list[str] = []
    who = f"{entry.get('file')}::{entry.get('target')}"
    for spec in _oracle_specs(entry):
        file_part, _sep, rest = spec.partition("::")
        cls, _sep2, method = rest.partition("::")
        module = file_part[:-3].replace("/", ".").replace("\\", ".")
        target = f"{module}.{cls}.{method}"
        proc = osnorm.run_text([sys.executable, "-B", "-m", "unittest", "-v", target],
                               cwd=str(ROOT), timeout=300)
        output = f"{proc.stdout}\n{proc.stderr}"
        # `unittest` 对不存在的用例会造一个 `_FailedTest` 并照样打印 "Ran 1 test"，所以
        # 「真被收集」不能只看计数：出现 `_FailedTest` 就是「查无此例」。
        if "_FailedTest" in output or "Ran 0 tests" in output or "Ran 1 test" not in output:
            problems.append(f"structural 豁免 {who} 失效：oracle 未被收集"
                            f"（`unittest {target}` 没有跑起 1 个用例）：\n{output[-800:]}")
            continue
        if proc.returncode != 0:
            problems.append(f"structural 豁免 {who} 失效：oracle 当前未通过"
                            f"（rc={proc.returncode}）：{spec}\n{output[-1500:]}")
    return problems


def _subdomain_problems(entries: list[dict], subdomains: dict) -> list[str]:
    """R48/P5：a 类条目的公共边界只许在 `subdomains` 注册表里维护一份。"""
    problems: list[str] = []
    for name, spec in subdomains.items():
        if not isinstance(spec, dict):
            problems.append(f"subdomain 必须是对象：{name!r}")
            continue
        for field in ("boundary", "equivalence", "divergence"):
            if not str(spec.get(field) or "").strip():
                problems.append(f"subdomain {name!r} 缺字段 {field}")
    used: Counter = Counter()
    for entry in entries:
        if entry.get("verdict") != "a":
            continue
        name = str(entry.get("subdomain") or "")
        if not name:
            problems.append(f"a 类条目缺 subdomain（边界必须引用注册表）："
                            f"{entry.get('target')!r}")
            continue
        if name not in subdomains:
            problems.append(f"a 类条目引用了未登记的 subdomain {name!r}："
                            f"{entry.get('target')!r}")
            continue
        used[name] += 1
        reason = str(entry.get("reason") or "")
        if f"subdomains.{name}" not in reason:
            problems.append(f"a 类条目的 reason 没有引用 subdomains.{name}："
                            f"{entry.get('target')!r}")
        spec = subdomains.get(name)
        boundary = str((spec or {}).get("boundary") or "") if isinstance(spec, dict) else ""
        if boundary and boundary in reason:
            problems.append(f"a 类条目把 subdomain 边界文本复制进了 reason（文案会漂移）："
                            f"{entry.get('target')!r}")
    for name in subdomains:
        if used[name] == 0:
            problems.append(f"subdomain {name!r} 没有被任何 a 类条目引用")
    return problems


def structural_problems(actual: Counter, entries: list[dict], *,
                        check_oracles: bool = True,
                        subdomains: dict | None = None) -> list[str]:
    """S3 的四项机械校验 + 一一对应 + R48 的 subdomain 单一来源；`actual` 是**未套 structural** 的实测计数。"""
    problems: list[str] = []
    problems.extend(_subdomain_problems(entries, load_subdomains() if subdomains is None
                                       else subdomains))
    rules = {str(entry.get("rule")) for entry in entries}
    for entry in entries:
        for field in STRUCTURAL_FIELDS:
            if field not in entry:
                problems.append(f"structural 条目缺字段 {field}：{entry}")
        if entry.get("rule") not in RULES:
            problems.append(f"structural 条目规则不在 R1–R6：{entry.get('rule')!r}")
        if entry.get("verdict") not in ("a", "b"):
            problems.append(f"structural 条目必须声明判定 (a) 可等价 / (b) 不同域："
                            f"{entry.get('target')!r}")
        if not str(entry.get("reason") or "").strip():
            problems.append(f"structural 条目缺 reason（子域边界/为何不可迁移）："
                            f"{entry.get('target')!r}")
        baseline = entry.get("count_baseline")
        if not isinstance(baseline, int) or baseline <= 0:
            problems.append(f"structural count_baseline 必须是正整数："
                            f"{entry.get('target')!r}={baseline!r}")
        if not (ROOT / str(entry.get("file") or "")).is_file():
            problems.append(f"structural 条目指向不存在的文件：{entry.get('file')!r}")
        shape = _oracle_shape(entry)
        if shape:
            problems.append(shape)

    # ④ 目标必须能在真实代码里定位（函数存在 + 该序数处就是声明的表达式类）
    index, target_problems = _site_map(entries)
    problems.extend(target_problems)

    # ③ 未覆盖即“计数增长”：structural 段声明了哪些规则由它兜，就必须兜满
    for key, count in sorted(actual.items()):
        rel, rule, expr, _line_kind = key
        if rule not in rules:
            continue
        _names, sites = _file_scan(rel)
        uncovered = [site for site in sites
                     if site["expr"] == expr and (rel, site["line"]) not in index]
        if uncovered:
            problems.append(
                f"structural 未覆盖的 {rule} 违规（要么迁移，要么补 oracle 条目）："
                f"{rel} {expr} L{uncovered[0]['line']}"
                f"（该键共 {len(uncovered)} 处未覆盖，实测 {count}）")

    registered: Counter = Counter()
    for entry in entries:
        registered[(str(entry.get("file")), str(entry.get("rule")))] += int(
            entry.get("count_baseline") or 0)
    measured: Counter = Counter()
    for (rel, rule, _expr, _line_kind), count in actual.items():
        if rule in rules:
            measured[(rel, rule)] += count
    for key, count in sorted(measured.items()):
        limit = registered.get(key)
        if limit is None:
            problems.append(f"structural 缺少该 (file, rule) 的登记：{key} 实测 {count}")
        elif count > limit:
            problems.append(f"实测计数高于 count_baseline：{key} 实测 {count} > 登记 {limit}")
        elif count < limit:
            problems.append(f"登记计数高于实测（条目指向已不存在的代码）：{key} "
                            f"实测 {count} < 登记 {limit}")
    for key in sorted(set(registered) - set(measured)):
        problems.append(f"structural 登记项在实测里不存在：{key}")

    # ①② oracle 必须真实存在、被收集、当前通过
    if check_oracles:
        for entry in entries:
            if _oracle_shape(entry) is None:
                problems.extend(_run_oracle(entry))
    return problems


def _site_index() -> dict[tuple[str, int], dict]:
    index, _problems = _site_map(load_structural())
    return index


def scan_file(path: Path, rel: str,
              index: dict[tuple[str, int], dict] | None = None) -> list[Violation]:
    source = osnorm.read_text(path)
    found = scan_source(source, filename=rel)
    site_map = _site_index() if index is None else index
    return [v for v in found if _exempted(rel, v, site_map) is None]


def iter_repo_violations(
    site_map: dict[tuple[str, int], dict],
) -> Iterator[tuple[str, Violation]]:
    for prefix, root in SCAN_ROOTS:
        for path in sorted(root.glob("*.py")):
            rel = f"{prefix}/{path.name}"
            for violation in scan_file(path, rel, site_map):
                yield rel, violation


def scan_repo(with_structural: bool = True, entries: list[dict] | None = None) -> Counter:
    """扫描全量扫描面，返回 {(file, rule, target, line_kind): count}。

    `with_structural=False` 只套规则级豁免（`STRUCTURAL_EXEMPT`），保留 structural 段覆盖的命中。
    """
    site_map = {} if not with_structural else _site_map(
        load_structural() if entries is None else entries)[0]
    return Counter((rel,) + violation.key for rel, violation in iter_repo_violations(site_map))


def blocking_problems(doc: dict) -> list[str]:
    """R51：带定位的实测违规与账本的空值约束分别判定。"""
    hits = sorted(iter_repo_violations({}), key=lambda hit: (hit[0], hit[1].line))
    problems = [f"{rel}:{hit.line}: [{hit.rule}] {hit.target} ({hit.line_kind})"
                for rel, hit in hits]
    if "blocking" in doc:
        problems.append("tests/osnorm_allowlist.json: blocking 字段不允许出现")
    for section, empty in (("entries", []), ("structural", []), ("subdomains", {}),
                           ("baseline", {})):
        if doc.get(section) != empty:
            problems.append(f"tests/osnorm_allowlist.json: {section} 必须为 {empty}")
    return problems


# ---------------------------------------------------------------- R9：allowlist 审计


def load_allowlist() -> dict:
    return json.loads(osnorm.read_text(ALLOWLIST_PATH))


def current_dev_round() -> int | None:
    """当前 dev 轮次（`release_dev.json` 里最大的 `round`）；没有 dev 账本（实例侧）返回 None。

    R48/P5：`expires` 的「过期」必须有可比的当前轮次。实例侧不带 dev 账本，按无轮次跳过；
    模板侧读不到/损坏时同样不猜——只跳过过期判定，不把坏账本当 0。
    """
    path = ROOT / ".workspace" / "release_dev.json"
    try:
        data = json.loads(osnorm.read_text(path, bom=False, newline="preserve"))
    except (OSError, ValueError):
        return None
    rounds = []
    for entry in data if isinstance(data, list) else []:
        value = entry.get("round") if isinstance(entry, dict) else None
        if isinstance(value, int):
            rounds.append(value)
    return max(rounds) if rounds else None


def _expires_round(value: object) -> int | None:
    match = re.fullmatch(r"R(\d+)[a-z]?", str(value or ""))
    return int(match.group(1)) if match else None


def audit(actual: Counter, entries: list[dict], baseline: dict[str, dict[str, int]], *,
          current_round: int | None = None) -> list[str]:
    """实测、精确登记与历史最低基线三方对账；任一方向漂移都算问题。

    `current_round` 非空时额外判「过期」：`expires` 必须是 `R<n>[a-z]?` 且不早于当前轮次。
    R48/P5 选择**收紧**：R39 那批已在 R44–R47 迁完（entries=[]），续期只会把「已不存在的
    条目」开成无限延期；现在起任何早于当前轮次的 `expires` 都红。实例侧没有 dev 账本时
    调用方传 None，跳过该项。
    """
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
        if entry.get("expires") and current_round is not None:
            expires = _expires_round(entry.get("expires"))
            if expires is None:
                problems.append(f"allowlist expires 形态非法（只认 R<n>[a-z]?）："
                                f"{entry.get('expires')!r}")
            elif expires < current_round:
                problems.append(
                    f"allowlist 条目已过期：expires={entry.get('expires')} 早于当前轮次 "
                    f"R{current_round}（{entry.get('file')}::{entry.get('target')}）"
                    "——收紧口径：过期必须迁移或删除，不许续期")
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
        self.actual = scan_repo(with_structural=False)

    def test_scan_covers_three_roots(self) -> None:
        """用实际扫描调用验证三个根都参与检查，归零后仍要扫描 hooks。"""
        with mock.patch(f"{__name__}.scan_file", wraps=scan_file) as scanned:
            scan_repo(with_structural=False)
        roots = {call.args[1].rsplit("/", 1)[0] for call in scanned.call_args_list}
        self.assertEqual(roots, {".workspace/hooks", "scripts", "tests"})
        for prefix, root in SCAN_ROOTS:
            files = sorted(root.glob("*.py"))
            self.assertTrue(files, f"{prefix} 根下没有可扫描文件")
            for path in files:
                scan_file(path, f"{prefix}/{path.name}")   # 解析失败会直接抛
        self.assertFalse(self.actual, f"三个根的非结构性命中必须保持 0 处：{self.actual}")

    def test_r6_normcase_stays_zero(self) -> None:
        hits = [key for key in self.actual if key[1] == "R6"]
        self.assertEqual(hits, [], f"R6（os.path.normcase）必须保持 0 处：{hits}")

    def test_new_osnorm_files_are_clean(self) -> None:
        ours = ("scripts/osnorm.py", "tests/test_osnorm_properties.py",
                "tests/test_osnorm_single_source.py")
        dirty = [key for key in self.actual if key[0] in ours]
        self.assertEqual(dirty, [], f"本轮新增文件不得带任何存量违规：{dirty}")


class WorkspaceBlockingGateTest(unittest.TestCase):
    """逐处诊断排在历史账本测试后，保留在 check_all 展示的输出尾部。"""

    def test_no_unregistered_violation(self) -> None:
        problems = blocking_problems(load_allowlist())
        if problems:
            self.fail("\n".join(problems))


class BlockingGateTest(unittest.TestCase):
    json_control = 'import json\nprint(json.dumps({"ok": True}))\n'
    violations = (
        "import os\n"
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "sys.stderr.reconfigure(encoding='utf-8')\n"
        "def joined_key(base, name):\n"
        "    return os.path.normpath(os.path.join(base, name))\n"
    )
    expected = [
        "scripts/r51_probe.py:5: [R4] .reconfigure (call)",
        "scripts/r51_probe.py:6: [R4] .reconfigure (call)",
        "scripts/r51_probe.py:8: [R5] expr:normpath_join (expr)",
    ]

    def _prepare_workspace(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        scripts = root / "scripts"
        scripts.mkdir()
        self.probe = scripts / "r51_probe.py"
        self.allowlist = root / "osnorm_allowlist.json"
        patch = mock.patch.multiple(__name__, ROOT=root, ALLOWLIST_PATH=self.allowlist,
                                    SCAN_ROOTS=(("scripts", scripts),))
        patch.start()
        self.addCleanup(patch.stop)
        self.ledger = {"entries": [], "structural": [], "subdomains": {}, "baseline": {}}
        osnorm.write_text(self.allowlist, json.dumps(self.ledger))
        osnorm.write_text(self.probe, self.json_control + self.violations)
        gate = WorkspaceBlockingGateTest("test_no_unregistered_violation")
        self.run_gate = gate.test_no_unregistered_violation

    def test_workspace_gate_rejects_violations(self) -> None:
        self._prepare_workspace()
        osnorm.write_text(self.probe, self.json_control)
        self.run_gate()
        osnorm.write_text(self.allowlist, json.dumps(dict(self.ledger, blocking=False)))
        with self.assertRaises(AssertionError) as failure:
            self.run_gate()
        self.assertEqual(str(failure.exception).splitlines(), [
            "tests/osnorm_allowlist.json: blocking 字段不允许出现",
        ])
        osnorm.write_text(self.allowlist, json.dumps(self.ledger))
        osnorm.write_text(self.probe, self.json_control + self.violations)
        with self.assertRaises(AssertionError):
            self.run_gate()

    def test_wrong_field_diagnostic_is_rejected(self) -> None:
        with mock.patch(f"{__name__}.blocking_problems", side_effect=[
            [], ["unexpected field diagnostic"], self.expected,
        ]), self.assertRaises(AssertionError):
            self.test_workspace_gate_rejects_violations()

    def test_workspace_gate_reports_single_violation(self) -> None:
        self._prepare_workspace()
        for source, expected in (
            ("import sys\nsys.stdout.reconfigure()\n",
             "scripts/r51_probe.py:2: [R4] .reconfigure (call)"),
            ("import os\n\nkey = os.path.normpath(os.path.join(base, name))\n",
             "scripts/r51_probe.py:3: [R5] expr:normpath_join (expr)"),
        ):
            with self.subTest(expected=expected):
                osnorm.write_text(self.probe, source)
                with self.assertRaises(AssertionError) as failure:
                    self.run_gate()
                self.assertEqual(str(failure.exception), expected)
                osnorm.write_text(self.probe, self.json_control)
                self.run_gate()

    def _copy_gate_workspace(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir()
        (root / "tests").mkdir()
        for path in (SCRIPTS / "osnorm.py", ROOT / "tests/__init__.py", Path(__file__),
                     *(ROOT / "tests").glob("*.json")):
            shutil.copyfile(path, root / path.parent.name / path.name)
        return root

    def test_workspace_gate_reads_official_ledger_and_rejects_blocking_values(self) -> None:
        root = self._copy_gate_workspace()
        ledger = root / "tests/osnorm_allowlist.json"
        original = osnorm.read_text(ledger)
        doc = json.loads(original)
        argv = [sys.executable, "-m", "unittest",
                "tests.test_osnorm_single_source.WorkspaceBlockingGateTest"]
        for value in (False, True, None, 0, 1, "", "false", [], [1], {}, {"x": 1}):
            with self.subTest(blocking=value):
                osnorm.write_text(ledger, json.dumps(dict(doc, blocking=value)))
                proc = osnorm.run_text(argv, cwd=root, timeout=60)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertRegex(proc.stderr, r"(?m)^AssertionError: tests/osnorm_allowlist\.json: "
                                 r"blocking 字段不允许出现\n\n-+\nRan 1 test\b")
                self.assertIn("FAILED (failures=1)", proc.stderr)
        osnorm.write_text(ledger, original)
        restored = osnorm.run_text(argv, cwd=root, timeout=60)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("Ran 1 test ", restored.stderr)

    def test_daily_stage_runs_workspace_gate(self) -> None:
        import check_all

        root = self._copy_gate_workspace()
        probe = root / "tests/r51_stage_probe.py"
        osnorm.write_text(probe, "import sys\nsys.stdout.reconfigure()\n")
        stages = check_all.select_stages(fix=False, full=False, only=None)
        stage, = [stage for stage in stages if stage.name == "osnorm_gate"]
        # unittest 的 -k 只过滤阶段已收集的测试，同时避免递归运行本用例。
        proc = osnorm.run_text([*stage.argv, "-k", "test_no_unregistered_violation"],
                               cwd=root, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("AssertionError: tests/r51_stage_probe.py:2: [R4] .reconfigure (call)",
                      proc.stderr)
        self.assertIn("FAILED (failures=1)", proc.stderr)

    def test_workspace_gate_reports_each_injected_violation(self) -> None:
        self._prepare_workspace()
        with self.assertRaises(AssertionError) as failure:
            self.run_gate()
        self.assertEqual(str(failure.exception).splitlines(), self.expected)

    def test_wrong_line_number_is_rejected(self) -> None:
        wrong = [self.expected[0].replace(":5:", ":99:"), *self.expected[1:]]
        with mock.patch(f"{__name__}.blocking_problems", return_value=wrong), \
                self.assertRaises(AssertionError):
            self.test_workspace_gate_reports_each_injected_violation()

    def test_workspace_gate_reports_raised_ledger_and_violations(self) -> None:
        self._prepare_workspace()
        self.ledger["blocking"] = False
        self.ledger["structural"] = [
            {"file": "scripts/r51_probe.py", "rule": "R5",
             "target": "joined_key#1", "expr": "expr:normpath_join"},
        ]
        self.ledger["baseline"] = {"scripts/r51_probe.py": {"R4": 2}}
        osnorm.write_text(self.allowlist, json.dumps(self.ledger))
        with self.assertRaises(AssertionError) as failure:
            self.run_gate()
        self.assertEqual(str(failure.exception).splitlines(), self.expected + [
            "tests/osnorm_allowlist.json: blocking 字段不允许出现",
            "tests/osnorm_allowlist.json: structural 必须为 []",
            "tests/osnorm_allowlist.json: baseline 必须为 {}",
        ])

    def test_r51_blocks_r4_and_r5_even_with_raised_ledger(self) -> None:
        empty = {"entries": [], "structural": [], "subdomains": {}, "baseline": {}}
        r4_entry = {"file": "scripts/probe.py", "rule": "R4", "target": ".reconfigure",
                    "kind": "call", "line_kind": "call", "count": 1,
                    "reason": "回归用条目", "expires": "R999"}
        raised = dict(empty, entries=[r4_entry], baseline={"scripts/probe.py": {"R4": 1}})
        r4 = "sys.stdout.reconfigure(encoding='utf-8')\n"
        r5 = "key = os.path.normpath(os.path.join(base, name))\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = root / "probe.py"
            with mock.patch(f"{__name__}.SCAN_ROOTS", (("scripts", root),)):
                osnorm.write_text(probe, "import sys\n" + r4 + "\n" + r5)
                expected = ["scripts/probe.py:2: [R4] .reconfigure (call)",
                            "scripts/probe.py:4: [R5] expr:normpath_join (expr)"]
                self.assertEqual(blocking_problems(empty), expected)
                self.assertEqual(blocking_problems(raised), expected + [
                    "tests/osnorm_allowlist.json: entries 必须为 []",
                    "tests/osnorm_allowlist.json: baseline 必须为 {}",
                ])

                osnorm.write_text(probe, "key = osnorm.canonical_path(name, base=base)\n")
                self.assertEqual(blocking_problems(empty), [])
                self.assertEqual(blocking_problems(dict(
                    empty, structural=[{"file": "scripts/probe.py"}], subdomains={"probe": "域"}
                )), [
                    "tests/osnorm_allowlist.json: structural 必须为 []",
                    "tests/osnorm_allowlist.json: subdomains 必须为 {}",
                ])


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

    def test_gate_core_relpath_keys_are_migrated(self) -> None:
        hits = [v for v in self._scan(".workspace/hooks/gate_core.py")
                if v.rule == "R5" and v.target == "expr:relpath_sep_key"]
        self.assertEqual(hits, [], "gate_core 的相对键必须保持交换形态")


# ---------------------------------------------------------------- 测试：R9 allowlist


class AllowlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actual = scan_repo()
        self.doc = load_allowlist()

    def test_schema(self) -> None:
        self.assertEqual(self.doc["version"], 2)
        self.assertIsInstance(self.doc["entries"], list)
        self.assertIsInstance(self.doc["baseline"], dict)
        self.assertIsInstance(self.doc["structural"], list, "v2 起必须有 structural 段")
        self.assertEqual(self.doc["entries"], [])
        self.assertEqual(self.doc["baseline"], {})

    def test_counts_match_reality(self) -> None:
        problems = audit(self.actual, self.doc["entries"], self.doc["baseline"],
                         current_round=current_dev_round())
        self.assertEqual(problems, [], "\n".join(problems))

    def test_expired_entry_is_red(self) -> None:
        """R48/P5：`expires` 早于当前轮次 → 红（选择收紧，不续期）。"""
        key, entry, baseline = self._count_fixture()
        entry = dict(entry, expires="R39")
        actual = Counter({key: 3})
        problems = audit(actual, [entry], baseline, current_round=47)
        self.assertTrue(any("已过期" in p for p in problems), problems)
        fresh = dict(entry, expires="R47")
        self.assertFalse(any("已过期" in p for p in audit(actual, [fresh], baseline,
                                                          current_round=47)))
        later = dict(entry, expires="R48")
        self.assertFalse(any("已过期" in p for p in audit(actual, [later], baseline,
                                                          current_round=47)))

    def test_expiry_is_skipped_without_dev_ledger(self) -> None:
        """实例侧没有 dev 账本（无轮次可比）时显式跳过，不把缺失当 0。"""
        key, entry, baseline = self._count_fixture()
        entry = dict(entry, expires="R39")
        problems = audit(Counter({key: 3}), [entry], baseline, current_round=None)
        self.assertFalse(any("已过期" in p for p in problems), problems)

    def test_expiry_shape_is_checked(self) -> None:
        key, entry, baseline = self._count_fixture()
        entry = dict(entry, expires="next-round")
        problems = audit(Counter({key: 3}), [entry], baseline, current_round=47)
        self.assertTrue(any("expires 形态非法" in p for p in problems), problems)

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

    @staticmethod
    def _count_fixture() -> tuple[tuple[str, str, str, str], dict, dict]:
        key = (".workspace/hooks/hook.py", "R4", ".reconfigure", "call")
        entry = dict(zip(("file", "rule", "target", "line_kind"), key))
        entry.update(kind="call", count=3, reason="计数判据自检用条目", expires="R39")
        return key, entry, {key[0]: {key[1]: 3}}

    def test_t_b2a_count_plus_one_is_red(self) -> None:
        key, entry, baseline = self._count_fixture()
        entry["count"] += 1
        self.assertTrue(any("与登记不等" in p for p in audit(Counter({key: 3}), [entry], baseline)))

    def test_t_b2b_count_minus_one_is_red(self) -> None:
        key, entry, baseline = self._count_fixture()
        entry["count"] -= 1
        self.assertTrue(any("与登记不等" in p for p in audit(Counter({key: 3}), [entry], baseline)))

    def test_t_b2c_count_above_baseline_is_red(self) -> None:
        key, entry, baseline = self._count_fixture()
        entry["count"] += 1
        actual = Counter({key: entry["count"]})
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


# ---------------------------------------------------------------- 测试：structural 段（R43）

# 站点 oracle 的宿主自适基座：`(a)` 类站点只做 join+normpath，所以「同一路径标识」必须在
# **本宿主**的原生形态与 canonical 的交换形态之间对账，不能把 `/ws` 这类字面量钉死——
# Windows 上 canonical 给 `C:/ws`、原生给 `C:\ws`，写死任一侧在另一宿主上必错。
BASE_NATIVE = "/ws" if os.name != "nt" else "C:\\ws"


def _expand_base(value: object) -> str:
    """把 fixture 里的 `@base` 占位符换成宿主自适的 BASE_NATIVE（不写死宿主形态）。"""
    return str(value or "").replace("@base", BASE_NATIVE)


def _native_join(base: str, rel: str) -> str:
    """宿主原生 join + normpath（拆两句：本文件也在 R5 扫描面内，不写内联惯用法）。"""
    joined = os.path.join(base, rel)
    return os.path.normpath(joined)


def _native_form(canonical: str) -> str:
    """交换形态 → 本宿主原生写法。判据侧用，不参与被验证的实现。"""
    return os.path.normpath(canonical)


def _path_pair(rel: str, base: str | None = None) -> tuple[str, str]:
    """(原生期望, 交换期望)：两个都从 canonical_path 现算，避免把宿主形态写成字面量。"""
    want = osnorm.canonical_path(rel, base=BASE_NATIVE if base is None else base)
    return _native_form(want), want


# ---- R48/P1：比较基准可切换（宿主原生串 ↔ 交换形态）--------------------------
#
# R49 会把 12 处 `normpath_join` 改成 `collapse_path`：站点输出从「宿主原生串」变成
# 「交换形态（或混合分隔符）」。若判据仍只比原生形态，R49 要么大面积假红、要么被迫
# 放宽成「比字符串前缀」之类的假绿。这里把比较基准做成**可切换的判据**：
#   native   —— 逐字比较（历史口径，仍可用于诊断）
#   exchange —— 两侧都先 canonical_path 再比较（R49 迁移后的默认口径）
# 关键约束是**双侧一起改**：只 canonical 实际值会放过「期望还停在原生形态」的分叉；
# `compare_paths` 对两侧同时变换，`ComparisonBaselineTest.test_switch_is_two_sided`
# 用两个方向的反例钉住这一点。

COMPARE_BASELINES = ("native", "exchange")
COMPARE_ENV = "OSNORM_COMPARE_BASELINE"
DEFAULT_COMPARE_BASELINE = "exchange"


def comparison_baseline() -> str:
    """当前比较基准（环境变量可切换）；未知值 fail-closed，不静默退回宽松口径。"""
    mode = (os.environ.get(COMPARE_ENV) or DEFAULT_COMPARE_BASELINE).strip().lower()
    if mode not in COMPARE_BASELINES:
        raise AssertionError(f"未知比较基准 {mode!r}（只认 {COMPARE_BASELINES}）")
    return mode


def exchange_form(value: str) -> str:
    """宿主原生 / 交换形态 → 交换形态（canonical_path 对已交换形态幂等）。"""
    return osnorm.canonical_path(value)


def compare_paths(left: str, right: str, mode: str | None = None) -> bool:
    """按当前基准判断两个绝对路径是否同一标识；exchange 模式**两侧都变换**。"""
    mode = mode or comparison_baseline()
    if mode == "native":
        return left == right
    if mode == "exchange":
        return exchange_form(left) == exchange_form(right)
    raise AssertionError(f"未知比较基准 {mode!r}")


class _BaselineMode:
    """临时切换比较基准（测试用；退出时恢复环境变量）。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._saved: str | None = None

    def __enter__(self) -> str:
        self._saved = os.environ.get(COMPARE_ENV)
        os.environ[COMPARE_ENV] = self.mode
        return self.mode

    def __exit__(self, *_exc) -> None:
        if self._saved is None:
            os.environ.pop(COMPARE_ENV, None)
        else:
            os.environ[COMPARE_ENV] = self._saved


class StructuralSiteMixin:
    """Evaluate the production path expression under the host and ntpath."""

    def site(self, rel: str, target: str, env: dict):
        function, ordinal = _split_target(target)
        tree = ast.parse(osnorm.read_text(ROOT / rel), filename=rel)
        owner, = (node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == function)
        hits = [node for node in ast.walk(owner)
                if isinstance(node, ast.Call) and _dotted(node.func) in {
                    "osn.collapse_host_path", "osn.host_relative_key", "os.path.normpath",
                    "path_form", "_exchange_path",
                } and (_dotted(node.func) == "osn.host_relative_key"
                       or (node.args and isinstance(node.args[0], ast.Call)
                           and _dotted(node.args[0].func) == "os.path.join"))]
        hits.sort(key=lambda node: (node.lineno, node.col_offset))
        self.assertLessEqual(ordinal, len(hits), f"{rel}::{target} 表达式缺失")
        node = hits[ordinal - 1]
        code = compile(ast.Expression(body=node), f"<{rel}:{node.lineno}>", "eval")
        scope = {"os": os, "osn": osnorm, "path_form": osnorm.collapse_host_path,
                 "__builtins__": {"str": str, "int": int}}
        if rel.endswith("/wtarget.py"):
            scope["_exchange_path"] = _hook_module("wtarget")._exchange_path
        got = eval(code, scope, dict(env))  # noqa: S307

        windows_env = {key: value.replace(BASE_NATIVE, r"C:\ws", 1)
                       if isinstance(value, str) else value for key, value in env.items()}
        windows_osn = mock.Mock(
            collapse_host_path=functools.partial(osnorm.collapse_host_path, path_module=ntpath),
            host_relative_key=functools.partial(osnorm.host_relative_key, path_module=ntpath))
        windows_scope = dict(scope, os=mock.Mock(path=ntpath), osn=windows_osn,
                             path_form=windows_osn.collapse_host_path)
        if "_exchange_path" in scope:
            windows_scope["_exchange_path"] = functools.partial(
                scope["_exchange_path"], path_module=ntpath)
        windows = eval(code, windows_scope, windows_env)  # noqa: S307
        self.assertIsInstance(windows, str)
        self.assertNotIn("\\", windows, f"{rel}::{target} 输出了原生分隔符")
        return got

    def assert_same_path(self, got: object, expected: tuple[str, str], msg: str) -> None:
        native_expected, canonical_expected = expected
        self.assertEqual(got, canonical_expected, f"{msg}：输出必须为交换形态")
        self.assertTrue(compare_paths(_native_form(got), native_expected)
                        if comparison_baseline() == "native"
                        else compare_paths(got, native_expected), msg)

    def assert_declared_divergence(self, got: object, rel: str, msg: str) -> None:
        """出界分叉是被声明的：站点仍返回值（hook 不得抛），而 canonical_path 直接拒。"""
        with self.assertRaises(osnorm.PathFormError, msg=f"{msg}：canonical 竟然接受了出界输入"):
            osnorm.canonical_path(rel, base=BASE_NATIVE)
        self.assertIsInstance(got, str, f"{msg}：站点在出界输入上必须返回字符串（fail-safe）")


class StructuralOracleTest(StructuralSiteMixin, unittest.TestCase):
    """R49：守住迁移后的路径表达式及其边界行为。"""

    CT = ".workspace/hooks/cc_transcript.py"
    CORE = ".workspace/hooks/gate_core.py"
    WT = ".workspace/hooks/wtarget.py"

    # ---- 3 处相对键

    def test_oracle_cc_gate_key_relative_key(self) -> None:
        gate = os.path.join(BASE_NATIVE, "a", "b.md")
        got = _hook_module("cc_transcript").gate_key(BASE_NATIVE, gate)
        self.assertEqual(got, "a/b.md", "账本键必须是相对路径且用 '/' 分隔")
        self.assertFalse(os.path.isabs(got))
        self.assertNotEqual(got, osnorm.canonical_path(gate), "相对键与 canonical 不是同一个东西")
        with self.assertRaises(osnorm.PathFormError):
            osnorm.canonical_path("a/b.md")   # 无 base 的相对路径 canonical 直接拒 → 产不出相对键

    def test_oracle_cc_gate_key_escape_allowed(self) -> None:
        """逃逸不是错误：gate_key 允许工作区外的 gate，返回带 `..` 的相对键（不抛、不绝对化）。"""
        row = (CASES.get("gate_key") or {}).get("escape") or {}
        cc_transcript = _hook_module("cc_transcript")
        gate = _expand_base(row.get("gate"))
        got = cc_transcript.gate_key(BASE_NATIVE, gate)
        self.assertEqual(got, row.get("expect"), "逃逸必须保留相对键（允许），不能抛错或退回绝对键")
        self.assertFalse(os.path.isabs(got), "逃逸键仍必须是相对形态")
        self.assertTrue(got.startswith(".."), f"逃逸键必须保留前导 ..：{got!r}")

    def test_oracle_cc_gate_key_cross_drive_no_throw(self) -> None:
        """跨盘键必须明确回退为绝对交换形态。"""
        ct = _hook_module("cc_transcript")
        ws, gate = r"C:\ws", r"d:\a\b.md"
        with mock.patch.object(ct, "os", mock.Mock(path=ntpath, sep="\\")):
            self.assertEqual(ct.gate_key(ws, gate), "D:/a/b.md")
            self.assertEqual(ct._legacy_gate_key(ws, gate), "d:/a/b.md")

    def test_oracle_cc_gate_key_two_cwds_same_key(self) -> None:
        """两个 cwd 写法（尾斜杠 / `.` / `sub/..`）必须同键；不同 cwd 必须异键（负例防恒真）。"""
        rows = (CASES.get("gate_key") or {}).get("rows") or []
        cc_transcript = _hook_module("cc_transcript")
        gate = os.path.join(BASE_NATIVE, "a", "b.md")
        self.assertGreaterEqual(len(rows), 2, "fixture 至少要有同键行与异键行")
        expects = {row.get("expect") for row in rows}
        self.assertIn("a/b.md", expects, "fixture 缺「同键」行")
        self.assertTrue(any(value != "a/b.md" for value in expects),
                        "fixture 缺「异键」负例：只断言同键的判据会退化成恒真")
        for row in rows:
            with self.subTest(name=row.get("name")):
                cwd = _expand_base(row.get("cwd"))
                self.assertEqual(cc_transcript.gate_key(cwd, gate), row.get("expect"),
                                 f"cwd={cwd!r} 的键不符")

    def test_oracle_gate_core_recent_files_relative_key(self) -> None:
        path = os.path.join(BASE_NATIVE, "sub", "a.md")
        got = self.site(self.CORE, "_recent_files#1", {"p": path, "ws_root": BASE_NATIVE})
        self.assertEqual(got, "sub/a.md")
        self.assertNotEqual(got, osnorm.canonical_path(path))
        with self.assertRaises(osnorm.PathFormError):
            osnorm.canonical_path("sub/a.md")

    def test_oracle_gate_core_scan_hashes_relative_key(self) -> None:
        path = os.path.join(BASE_NATIVE, "sub", "a.md")
        got = self.site(self.CORE, "_scan_hashes#1", {"p": path, "ws_root": BASE_NATIVE})
        self.assertEqual(got, "sub/a.md", "哈希基线键必须与 _recent_files 同口径")
        self.assertNotEqual(got, osnorm.canonical_path(path))
        with self.assertRaises(osnorm.PathFormError):
            osnorm.canonical_path("sub/a.md")

    # ---- 12 处路径拼接

    def test_oracle_cc_git_changed_files_join(self) -> None:
        got = self.site(self.CT, "git_changed_files#1", {"ws": BASE_NATIVE, "path": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "git_changed_files#1")
        escaped = self.site(self.CT, "git_changed_files#1", {"ws": BASE_NATIVE, "path": "../x.md"})
        self.assert_declared_divergence(escaped, "../x.md", "git_changed_files#1 逃逸")

    def test_oracle_cc_resolve_gate_files_argument_join(self) -> None:
        got = self.site(self.CT, "resolve_gate_files#1",
                        {"ws": BASE_NATIVE, "gate_rel": "AGENT_RULES.md"})
        self.assert_same_path(got, _path_pair("AGENT_RULES.md"), "resolve_gate_files#1")
        escaped = self.site(self.CT, "resolve_gate_files#1",
                            {"ws": BASE_NATIVE, "gate_rel": "../x.md"})
        self.assert_declared_divergence(escaped, "../x.md", "resolve_gate_files#1 逃逸")

    def test_oracle_cc_resolve_gate_files_config_list_join(self) -> None:
        got = self.site(self.CT, "resolve_gate_files#2", {"ws": BASE_NATIVE, "n": "docs/x.md"})
        self.assert_same_path(got, _path_pair("docs/x.md"), "resolve_gate_files#2")
        numeric = self.site(self.CT, "resolve_gate_files#2", {"ws": BASE_NATIVE, "n": 3})
        self.assert_same_path(numeric, _path_pair("3"), "resolve_gate_files#2 str(n)")

    def test_oracle_cc_reference_revision_read_target_join(self) -> None:
        env = {"cwd": BASE_NATIVE, "step": {"input": {"file_path": "g.md"}}}
        got = self.site(self.CT, "_reference_revision#1", env)
        self.assert_same_path(got, _path_pair("g.md"), "_reference_revision#1")
        empty = self.site(self.CT, "_reference_revision#1",
                          {"cwd": BASE_NATIVE, "step": {"input": {}}})
        self.assert_declared_divergence(empty, "", "_reference_revision#1 空 file_path")

    def test_oracle_cc_analyze_pre_write_read_target_join(self) -> None:
        env = {"cwd": BASE_NATIVE, "step": {"input": {"file_path": "docs/g.md"}}}
        got = self.site(self.CT, "analyze#1", env)
        self.assert_same_path(got, _path_pair("docs/g.md"), "analyze#1")
        empty = self.site(self.CT, "analyze#1", {"cwd": BASE_NATIVE, "step": {"input": {}}})
        self.assert_declared_divergence(empty, "", "analyze#1 空 file_path")

    def test_oracle_cc_analyze_post_write_read_target_join(self) -> None:
        env = {"cwd": BASE_NATIVE, "step": {"input": {"file_path": "docs/g.md"}}}
        got = self.site(self.CT, "analyze#2", env)
        self.assert_same_path(got, _path_pair("docs/g.md"), "analyze#2")
        empty = self.site(self.CT, "analyze#2", {"cwd": BASE_NATIVE, "step": {"input": {}}})
        self.assert_declared_divergence(empty, "", "analyze#2 空 file_path")

    def test_oracle_gate_core_worktree_snapshot_join(self) -> None:
        got = self.site(self.CORE, "worktree_snapshot#1",
                        {"ws_root": BASE_NATIVE, "rel": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "worktree_snapshot#1")
        escaped = self.site(self.CORE, "worktree_snapshot#1",
                            {"ws_root": BASE_NATIVE, "rel": "../x.md"})
        self.assert_declared_divergence(escaped, "../x.md", "worktree_snapshot#1 逃逸")

    def test_oracle_gate_core_worktree_audit_abs_sha_git(self) -> None:
        got = self.site(self.CORE, "worktree_audit#1",
                        {"ws_root": BASE_NATIVE, "rel": "a/b.md", "sha": "0" * 64})
        self.assert_same_path(got, _path_pair("a/b.md"), "worktree_audit#1")

    def test_oracle_gate_core_worktree_audit_abs_sha_nongit(self) -> None:
        got = self.site(self.CORE, "worktree_audit#2",
                        {"ws_root": BASE_NATIVE, "rel": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "worktree_audit#2")

    def test_oracle_gate_core_worktree_audit_pending_abs_path(self) -> None:
        got = self.site(self.CORE, "worktree_audit#3",
                        {"ws_root": BASE_NATIVE, "rel": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "worktree_audit#3")

    def test_oracle_wtarget_resolve_target_join(self) -> None:
        got = self.site(self.WT, "_resolve_target#1", {"cwd": BASE_NATIVE, "token": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "_resolve_target#1")
        escaped = self.site(self.WT, "_resolve_target#1", {"cwd": BASE_NATIVE, "token": "../x.md"})
        self.assert_declared_divergence(escaped, "../x.md", "_resolve_target#1 逃逸")

    def test_oracle_wtarget_absolute_join(self) -> None:
        got = self.site(self.WT, "absolute#1", {"cwd": BASE_NATIVE, "v": "a/b.md"})
        self.assert_same_path(got, _path_pair("a/b.md"), "absolute#1")


class ComparisonBaselineTest(unittest.TestCase):
    """R48/P1：比较基准（宿主原生 ↔ 交换形态）是**可切换且双侧**的判据 + fixture。

    这组用例不验证生产行为，验证的是「R49 改调用点时判据不会假绿/假红」：
    - fixture 的每一行证明 native / exchange 两种写法是同一路径标识；
    - 切换开关在 native 模式下必须恢复逐字比较，未知值 fail-closed；
    - 两个方向的反例证明 exchange 模式**两侧都变换**（只变一侧会漏掉一侧的分叉）；
    - 15 处迁移站点在两种比较基准下都必须全绿。
    """

    def _fixture(self) -> dict:
        return CASES.get("baseline") or {}

    def test_fixture_rows_are_same_identifier(self) -> None:
        rows = self._fixture().get("rows") or []
        self.assertGreaterEqual(len(rows), 6, "比较基准 fixture 行数不足")
        for row in rows:
            with self.subTest(rel=row.get("rel")):
                base = BASE_NATIVE
                if row.get("base_rel"):
                    base = os.path.join(BASE_NATIVE, row["base_rel"])
                native = _native_join(base, row["rel"])
                exchange = osnorm.canonical_path(
                    row["rel"], base=osnorm.canonical_path(base))
                self.assertEqual(exchange_form(native), exchange,
                                 f"fixture 行不是同一标识：{row!r}")

    def test_fixture_diverging_rows_are_declared(self) -> None:
        rows = self._fixture().get("diverging") or []
        self.assertGreaterEqual(len(rows), 2, "出界分叉 fixture 不足")
        for row in rows:
            with self.subTest(rel=row.get("rel")):
                native = _native_join(BASE_NATIVE, row["rel"])
                self.assertIsInstance(native, str, "原生串在出界行上仍必须可得")
                with self.assertRaises(osnorm.PathFormError):
                    osnorm.canonical_path(row["rel"], base=BASE_NATIVE)

    def test_switch_is_two_sided(self) -> None:
        # 原生形态保留 `a/..` 未折叠（仍是合法路径写法），交换形态已折叠：同一标识、不同串。
        native = os.path.join(BASE_NATIVE, "a", "..", "b.md")
        exchange = osnorm.canonical_path("b.md", base=osnorm.canonical_path(BASE_NATIVE))
        self.assertNotEqual(native, exchange, "前提不成立：两种形态竟然逐字相同")
        with _BaselineMode("exchange"):
            self.assertTrue(compare_paths(native, exchange), "exchange：原生→交换方向没比较")
            self.assertTrue(compare_paths(exchange, native), "exchange：交换→原生方向没比较")
        with _BaselineMode("native"):
            self.assertFalse(compare_paths(native, exchange), "native：不该把交换形态当等值")
            self.assertFalse(compare_paths(exchange, native), "native：不该把原生形态当等值")

    def test_unknown_baseline_is_fail_closed(self) -> None:
        for mode in ("lenient", "illegal"):
            with self.subTest(mode=mode), _BaselineMode(mode):
                with self.assertRaisesRegex(AssertionError, "未知比较基准"):
                    comparison_baseline()
                with self.assertRaisesRegex(AssertionError, "未知比较基准"):
                    compare_paths(BASE_NATIVE, BASE_NATIVE)

    def test_all_structural_oracles_pass_under_both_baselines(self) -> None:
        for mode in COMPARE_BASELINES:
            with self.subTest(mode=mode), _BaselineMode(mode):
                suite = unittest.TestLoader().loadTestsFromTestCase(StructuralOracleTest)
                result = unittest.TestResult()
                suite.run(result)
                problems = result.failures + result.errors
                self.assertFalse(problems, f"{mode} 基准下 structural oracle 未全绿："
                                 + "\n".join(f"{test}: {detail[:800]}" for test, detail in problems))


class OracleMutationControlTest(unittest.TestCase):
    """R48：oracle 自身必须可证伪——对「已知坏实现」必须转红。

    为什么单有 oracle 不够：把 `assertEqual(gate_key(cwd, gate), expect)` 改成
    `assertEqual(gate_key(...), gate_key(...))` 之类的「放水」，oracle 会永远绿，门禁却看不出来。
    这里对三条 gate_key oracle 各注入一个坏实现，要求 oracle **必须失败**；oracle 被放水时
    本组用例转红，放水者无法静默通过。
    """

    def _result(self, name: str) -> unittest.TestResult:
        case = StructuralOracleTest(name)
        result = unittest.TestResult()
        case.run(result)
        return result

    @staticmethod
    def _bad_constant_key(_ws: str, _gate: str) -> str:
        return "a/b.md"          # 恒同键：不同 cwd 也返回同键

    @staticmethod
    def _bad_absolute_key(_ws: str, _gate: str) -> str:
        return _native_join(_ws, _gate)   # 逃逸被绝对化，不再保留相对 ..

    @staticmethod
    def _bad_uncatchable_key(ws: str, gate: str) -> str:
        return ntpath.relpath(gate, ws)

    def _assert_red(self, method: str, bad, msg: str) -> None:
        cc_transcript = _hook_module("cc_transcript")
        original = cc_transcript.gate_key
        cc_transcript.gate_key = bad
        try:
            result = self._result(method)
        finally:
            cc_transcript.gate_key = original
        self.assertTrue(result.failures or result.errors,
                        f"{msg}：oracle 对坏实现没有转红（判据被放水）")

    def test_same_key_oracle_rejects_constant_key(self) -> None:
        self._assert_red("test_oracle_cc_gate_key_two_cwds_same_key",
                         self._bad_constant_key, "两 cwd 同键恒真")

    def test_escape_oracle_rejects_absolute_key(self) -> None:
        self._assert_red("test_oracle_cc_gate_key_escape_allowed",
                         self._bad_absolute_key, "逃逸被绝对化")

    def test_cross_drive_oracle_rejects_uncatchable_key(self) -> None:
        self._assert_red("test_oracle_cc_gate_key_cross_drive_no_throw",
                         self._bad_uncatchable_key, "跨盘不捕获 ValueError")


class StructuralRealCallTest(StructuralSiteMixin, unittest.TestCase):
    """oracle 不能只对着表达式求值：这几条走**真实函数**，证明站点在生产路径上真被执行。"""

    def test_resolve_gate_files_returns_exchange_targets(self) -> None:
        cc_transcript = _hook_module("cc_transcript")
        got = cc_transcript.resolve_gate_files(BASE_NATIVE, "AGENT_RULES.md")
        self.assertEqual(got, [osnorm.canonical_path("AGENT_RULES.md", base=BASE_NATIVE)])
        self.assertEqual(osnorm.canonical_path(got[0]), osnorm.canonical_path("AGENT_RULES.md", base=BASE_NATIVE))

    def test_gate_key_falls_back_when_relpath_cannot_relate(self) -> None:
        ct = _hook_module("cc_transcript")
        self.assertEqual(ct.gate_key("C:/ws", "C:/ws/a/b.md"), "a/b.md")
        self.assertEqual(ct.gate_key("C:/ws", "D:/a/b.md"), "D:/a/b.md")

    def test_recent_files_keys_are_relative_and_posix(self) -> None:
        gate_core = _hook_module("gate_core")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "a.md").write_bytes(b"x")
            (Path(tmp) / "b.md").write_bytes(b"y")
            keys = set(gate_core._recent_files(tmp, 0))
        self.assertEqual(keys, {"sub/a.md", "b.md"})

    def test_resolve_target_returns_exchange_absolute(self) -> None:
        wtarget = _hook_module("wtarget")
        target, status = wtarget._resolve_target("a/b.md", BASE_NATIVE)
        self.assertEqual(status, "static")
        self.assert_same_path(target, _path_pair("a/b.md"), "_resolve_target 真实调用")

    def test_ntpath_preserves_volume_roots_and_rejects_cross_volume_relative_keys(self) -> None:
        self.assertEqual(osnorm.collapse_host_path(r"C:\a\..\..\b", path_module=ntpath),
                         "C:/b")
        self.assertEqual(osnorm.collapse_host_path(r"\\host\share\a\..\..\b",
                                                  path_module=ntpath), "//host/share/b")
        self.assertIsNone(osnorm.host_relative_key(r"D:\b", r"C:\a", path_module=ntpath))
        self.assertIsNone(osnorm.host_relative_key(r"\\host\one\b", r"\\host\two\a",
                                                   path_module=ntpath))

    def test_wtarget_runs_without_loader_or_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            osnorm.write_bytes(Path(tmp) / "wtarget.py", osnorm.read_bytes(HOOKS / "wtarget.py"))
            probe = (
                "import sys, wtarget; "
                "result = wtarget.classify('echo x > a.md', sys.argv[1], []); "
                "assert result['targets'] == [sys.argv[2]], result; "
                "assert 'osnorm_loader' not in sys.modules"
            )
            result = osnorm.run_text([sys.executable, "-B", "-S", "-c", probe, tmp,
                                      osnorm.exchange_path(os.path.join(tmp, "a.md"))], cwd=tmp)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wtarget_exchange_form_matches_osnorm(self) -> None:
        wt = _hook_module("wtarget")
        cases = {
            posixpath: ("", ".", "/ws/a.md ", r"/ws/a\b.md", "//ws/a/../b", "a/../../b"),
            ntpath: (r"c:\ws\a\..\b.md", r"c:\..\b", r"c:relative.md", r"c:dir\..",
                     r"\\HOST\Share\..\g.md", r"\\host\share\.",
                     r"\\?\c:\ws\g.md", r"\\?\UNC\HOST\Share\g.md", r"\\.\PIPE\name"),
        }
        for host, paths in cases.items():
            for path in paths:
                with self.subTest(host=host.__name__, path=path):
                    self.assertEqual(wt._exchange_path(path, path_module=host),
                                     osnorm.exchange_path(path, path_module=host))
        with mock.patch.object(wt, "os", mock.Mock(wraps=os, path=ntpath, environ=os.environ)):
            ws = r"c:\ws"
            target = "C:/ws/g.md"
            for command, classify in (
                ('echo x > "//?/c:/ws/g.md"', wt.classify),
                ('Set-Content -LiteralPath "//?/c:/ws/g.md" -Value x', wt.classify_pwsh),
            ):
                result = classify(command, ws, [r"c:\ws\g.md"])
                self.assertEqual((result["targets"], result["gate_targets"]), ([target], [target]))
            self.assertEqual(wt.classify("echo x > D:relative.md", ws, [])["kind"], "unresolved")
            self.assertEqual(wt.classify_pwsh(
                "Set-Content -Path D:relative.md -Value x", ws, [])["kind"], "unresolved")
            self.assertEqual(wt.classify('echo x > "//?/GLOBALROOT/device.md"',
                                         ws, [])["kind"], "unresolved")
        with mock.patch.object(wt, "os", mock.Mock(wraps=os, path=posixpath, sep="/",
                                                   environ={"HOME": "/home/current"})), \
                mock.patch.object(posixpath, "expanduser", side_effect=AssertionError("home lookup")):
            self.assertEqual(wt.classify_pwsh("Set-Content ~/g.md -Value x", "/ws", [])["targets"],
                             ["/home/current/g.md"])
            self.assertEqual(wt.classify_pwsh("Set-Content ~other/g.md -Value x",
                                              "/ws", [])["kind"], "unresolved")

    def test_core_commit_matches_windows_intent_and_legacy_ledger(self) -> None:
        core = _hook_module("gate_core")
        native = r"c:\ws\g.md"
        extended = r"\\?\C:\ws\g.md"
        target = "C:/ws/g.md"
        osn = core._osn()
        windows_osn = mock.Mock(wraps=osn, exchange_path=functools.partial(
            osn.exchange_path, path_module=ntpath))
        with tempfile.TemporaryDirectory() as ws, \
                mock.patch.object(core, "_osn", return_value=windows_osn), \
                mock.patch.object(core, "file_sha", return_value="same-sha"):
            sid = "r49-ntpath-commit"
            state = core.load_state(ws, "codex", sid)
            self.assertEqual(core.record_intent(state, [native], ws)[0]["target"], target)
            state["events"][-1]["target"] = native  # R48 intent on disk
            core.save_state(ws, "codex", sid, state)
            result = core.evaluate_post_write(ws, "codex", sid, [extended])
            self.assertEqual([e["target"] for e in result["committed"]], [target])
            self.assertEqual(result["unobserved"], [])
            ledger = core._state_dir(ws) / "commits.jsonl"
            self.assertEqual(json.loads(osnorm.read_text(ledger))["target"], target)
            self.assertEqual(core.committed_targets(ws, {target: "same-sha"}), {target})
            osnorm.write_text(ledger, json.dumps({"target": native, "new_sha": "same-sha"}) + "\n")
            self.assertEqual(core.committed_targets(ws, {target: "same-sha"}), {target})
            self.assertEqual(core.committed_targets(ws, {target: "changed-sha"}), set())

    def test_core_workspace_path_compares_extended_and_native_windows_spellings(self) -> None:
        core = _hook_module("gate_core")
        osn = mock.Mock(exchange_path=functools.partial(osnorm.exchange_path, path_module=ntpath))
        with mock.patch.object(core, "_osn", return_value=osn), \
                mock.patch.object(core, "os", mock.Mock(wraps=os, path=ntpath)):
            self.assertTrue(core._is_within(r"\\?\C:\ws\g.md", r"c:\ws"))
            self.assertFalse(core._is_within(r"D:\ws\g.md", r"c:\ws"))

    @unittest.skipUnless(os.name == "posix", "POSIX literal filename semantics")
    def test_sidecar_keys_keep_distinct_posix_spellings(self) -> None:
        ct = _hook_module("cc_transcript")
        gates = ("/ws/a.md", "/ws/a.md ", r"/ws/a\b.md", "/ws/a/b.md")
        self.assertEqual([ct.gate_key("/ws", gate) for gate in gates],
                         ["a.md", "a.md ", r"a\b.md", "a/b.md"])


class StructuralRegistryTest(unittest.TestCase):
    """S3 门禁：①②③④ + 一一对应；每条判据都要能红（非空自检在下方各自的用例里）。"""

    def setUp(self) -> None:
        self.doc = load_allowlist()
        self.entries = list(self.doc.get("structural") or [])
        self.raw = scan_repo(with_structural=False)

    def test_registry_satisfies_s3(self) -> None:
        problems = structural_problems(self.raw, self.entries)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_ledger_totals_are_conserved(self) -> None:
        self.assertEqual(self.doc["entries"], [])
        self.assertEqual(self.entries, [])
        r5 = {key: count for key, count in self.raw.items() if key[1] == "R5"}
        self.assertEqual(r5, {}, f"structural R5 实测必须为 0：{r5}")


class StructuralRegistryControlTest(unittest.TestCase):
    """账本归零后继续用两处可扫描的历史模式测试 S3 的拒绝条件。"""

    def setUp(self) -> None:
        rel = ".workspace/hooks/cc_transcript.py"
        source = """
def old_relative(ws, gate):
    return os.path.relpath(gate, ws).replace(os.sep, "/")

def old_join(ws, gate):
    return os.path.normpath(os.path.join(ws, gate))
"""
        names, sites = _scan_file_source(source, rel)
        patcher = mock.patch(f"{__name__}._file_scan", return_value=(names, sites))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.raw = Counter((rel, v.rule, v.target, v.line_kind) for v in scan_source(source, rel))
        oracle = "tests/test_osnorm_single_source.py::StructuralOracleTest::test_oracle_"
        self.entries = [
            dict(file=rel, rule="R5", target="old_relative#1", expr="expr:relpath_sep_key",
                 verdict="b", count_baseline=1, reason="相对键",
                 oracle=[oracle + "cc_gate_key_" + case for case in (
                     "relative_key", "escape_allowed", "cross_drive_no_throw", "two_cwds_same_key")]),
            dict(file=rel, rule="R5", target="old_join#1", expr="expr:normpath_join",
                 verdict="a", count_baseline=1, reason="见 subdomains.host-native-join",
                 subdomain="host-native-join", oracle=oracle + "cc_git_changed_files_join"),
        ]
        self.subdomains = {
            "host-native-join": dict(boundary="宿主绝对路径与相对段",
                                     equivalence="路径标识相同", divergence="出界由 canonical 拒绝"),
        }

    def problems(self, raw, entries):
        return structural_problems(raw, entries, check_oracles=False, subdomains=self.subdomains)

    def test_targets_are_unique_and_resolvable(self) -> None:
        index, problems = _site_map(self.entries)
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertEqual(len(index), len(self.entries))
        self.assertEqual(self.problems(self.raw, self.entries), [])

    def test_target_out_of_range_is_red(self) -> None:
        broken = [dict(item) for item in self.entries]
        broken[0]["target"] = broken[0]["target"].split("#")[0] + "#9"
        problems = self.problems(self.raw, broken)
        self.assertTrue(any("目标越界" in item for item in problems), problems)

    def test_missing_function_is_red(self) -> None:
        broken = [dict(self.entries[0], target="no_such_function#1")]
        problems = self.problems(self.raw, broken)
        self.assertTrue(any("目标函数不存在" in item for item in problems), problems)

    def test_removed_entry_leaves_violation_uncovered(self) -> None:
        problems = self.problems(self.raw, self.entries[1:])
        self.assertTrue(any("未覆盖的 R5" in item for item in problems), problems)

    def test_count_growth_is_red(self) -> None:
        grown = Counter(self.raw)
        key = next(key for key in grown if key[1] == "R5")
        grown[key] += 1
        problems = self.problems(grown, self.entries)
        self.assertTrue(any("高于 count_baseline" in item for item in problems), problems)

    def test_expressed_class_mismatch_is_red(self) -> None:
        broken = [dict(item) for item in self.entries]
        broken[0]["expr"] = "expr:normpath_join"
        problems = self.problems(self.raw, broken)
        self.assertTrue(any("表达式类不符" in item for item in problems), problems)

    def test_verdict_and_reason_are_required(self) -> None:
        for field, bad in (("verdict", "c"), ("reason", ""), ("count_baseline", 0)):
            broken = [dict(self.entries[0], **{field: bad})]
            problems = self.problems(self.raw, broken)
            self.assertTrue(problems, f"{field}={bad!r} 竟然没有报错")

    def test_subdomains_are_single_source(self) -> None:
        self.assertEqual(load_subdomains(), {})
        broken = [dict(entry) for entry in self.entries]
        boundary = self.subdomains["host-native-join"]["boundary"]
        broken[1]["reason"] += boundary
        self.assertTrue(any("边界文本" in problem for problem in self.problems(self.raw, broken)))

    def test_missing_subdomain_is_red(self) -> None:
        broken = [dict(item) for item in self.entries]
        a_index = next(i for i, item in enumerate(broken) if item.get("verdict") == "a")
        broken[a_index].pop("subdomain", None)
        problems = self.problems(self.raw, broken)
        self.assertTrue(any("缺 subdomain" in item for item in problems), problems)

    def test_conflicting_subdomain_reason_is_red(self) -> None:
        broken = [dict(item) for item in self.entries]
        a_index = next(i for i, item in enumerate(broken) if item.get("verdict") == "a")
        broken[a_index]["reason"] = "没有引用注册表的理由"
        problems = self.problems(self.raw, broken)
        self.assertTrue(any("没有引用 subdomains." in item for item in problems), problems)

    def test_oracle_is_run_for_real(self) -> None:
        """①② 走真实子进程：通过则无问题，改名后必须报「未被收集」。"""
        first = self.entries[0]
        self.assertEqual(_run_oracle(first), [], "oracle 引用的用例必须当前通过")
        specs = _oracle_specs(first)
        self.assertEqual(len(specs), 4)
        ghost = dict(first, oracle=[specs[0].replace("test_oracle_", "test_oracle_ghost_")])
        self.assertTrue(any("未被收集" in item for item in _run_oracle(ghost)))

    def test_oracle_shape_rules(self) -> None:
        first = self.entries[0]
        cases = (
            (dict(first, oracle="tests/nope.py::StructuralOracleTest::test_oracle_x"),
             "不存在的测试文件"),
            (dict(first, oracle="tests/test_osnorm_single_source.py::AllowlistTest::test_schema"),
             "StructuralOracle*"),
            (dict(first, oracle="tests/test_osnorm_single_source.py::StructuralOracleTest::test_x"),
             "test_oracle_"),
            (dict(first, oracle=[]), "oracle"),
        )
        for entry, needle in cases:
            self.assertIn(needle, _oracle_shape(entry) or "", str(entry["oracle"]))


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
