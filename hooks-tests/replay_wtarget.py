#!/usr/bin/env python3
"""replay_wtarget.py — W1b 回放基线：把历史真实命令语料回放进 wtarget.classify，
得出「W1 能不能上线」的量化裁决。只读；不接线上链；只写指标 JSON。

用法：
    python3 replay_wtarget.py                 # 默认只跑单元矩阵（无 --corpus）
    python3 replay_wtarget.py --unit          # 同默认
    python3 replay_wtarget.py --corpus <dir>  # 单元矩阵 + 语料回放
    python3 replay_wtarget.py --out <path>    # 指定指标 JSON 输出

路径由 __file__ 推导；任意 cwd 可跑。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / ".workspace" / "hooks"

sys.path.insert(0, str(HOOKS_DIR))
import wtarget  # noqa: E402

GATE_REL = "AGENT_RULES.md"
CWD_MATRIX = str(ROOT)  # 运行时求值，不硬编码绝对路径
GATE_MATRIX = [str(ROOT / GATE_REL)]


# ================================================================ 单元矩阵

def _matrix():
    """(command, expected_kind, expect_gate_hit, note)。覆盖判定纪律 3–8 + 对抗负样本。"""
    W = CWD_MATRIX
    return [
        ("echo hi > AGENT_RULES.md", "write", True, "redirect:>"),
        ("printf 'x' >> AGENT_RULES.md", "write", True, "redirect:>>"),
        ("echo hi 2> AGENT_RULES.md", "write", True, "redirect:2>"),
        ("echo hi 2>> AGENT_RULES.md", "write", True, "redirect:2>>"),
        ("echo hi &> AGENT_RULES.md", "write", True, "redirect:&>"),
        ("echo hi >| AGENT_RULES.md", "write", True, "redirect:>|"),
        ("echo hi >AGENT_RULES.md", "write", True, "redirect:> 紧贴写法"),
        ("tee -a AGENT_RULES.md", "write", True, "tee -a"),
        ("tee AGENT_RULES.md other.txt", "write", True, "tee 多操作数"),
        ("sed -i 's/a/b/' AGENT_RULES.md", "write", True, "sed -i"),
        ("sed -i.bak 's/a/b/' AGENT_RULES.md", "write", True, "sed -i.bak"),
        ("sed -E -i 's/a/b/' AGENT_RULES.md", "write", True, "sed -E -i"),
        ("sed -i -e 's/a/b/' AGENT_RULES.md", "write", True, "sed -i -e（脚本不算文件）"),
        ("perl -i -pe 's/a/b/' AGENT_RULES.md", "write", True, "perl -i"),
        ("perl -pi -e 's/a/b/' AGENT_RULES.md", "write", True, "perl -pi"),
        ("truncate -s 0 AGENT_RULES.md", "write", True, "truncate -s N FILE"),
        ("mv /tmp/x AGENT_RULES.md", "write", True, "mv 最后一个操作数"),
        ("cp /tmp/x AGENT_RULES.md", "write", True, "cp 最后一个操作数"),
        ("install -m 644 /tmp/x AGENT_RULES.md", "write", True, "install"),
        ("dd of=AGENT_RULES.md", "write", True, "dd of="),
        ("cat > AGENT_RULES.md <<EOF\nx\nEOF", "write", True, "cat> heredoc 由重定向覆盖"),
        ("sed -n '1,50p' AGENT_RULES.md", "read", False, "读：sed -n"),
        ("cat AGENT_RULES.md", "read", False, "读：cat"),
        ("grep foo AGENT_RULES.md", "read", False, "读：grep"),
        ("egrep foo AGENT_RULES.md", "read", False, "读：egrep"),
        ("ls", "read", False, "读：ls"),
        ("od AGENT_RULES.md", "read", False, "读：od"),
        ("wc -l AGENT_RULES.md", "read", False, "读：wc"),
        ("head -5 AGENT_RULES.md", "read", False, "读：head"),
        ("tail -5 AGENT_RULES.md", "read", False, "读：tail"),
        ("test -f x", "read", False, "读：test"),
        ("true", "read", False, "读：true"),
        ("mkdir -p artifacts", "other", False, "other：mkdir"),
        ("rm AGENT_RULES.md", "other", False, "other：rm 不是写目标"),
        ("git diff -- AGENT_RULES.md", "unresolved", False, "F2-§1：git 不在已知安全集合 → unresolved"),
        ("python3 -c \"open('AGENT_RULES.md','w')\"", "unresolved", False, "unresolved：python3 -c"),
        ("sh -c 'sed -i s/a/b/ AGENT_RULES.md'", "unresolved", False, "unresolved：sh -c"),
        ("make", "unresolved", False, "unresolved：make"),
        ("git apply x.patch", "unresolved", False, "unresolved：git apply"),
        ("git checkout -- AGENT_RULES.md", "unresolved", False, "unresolved：git checkout --"),
        ("echo \"a > b\" > f", "write", False, "对抗：引号内 > 不是重定向，> f 是"),
        ("echo hi 2>&1", "other", False, "对抗：2>&1 fd 复制"),
        ("sed -n '1,5p' f > /tmp/x", "write", False, "对抗：读命令带重定向→写 /tmp/x"),
        ("cd /x && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd 前缀→整条 unresolved（不猜 cwd）"),
        ("echo ok > ~/.bashrc", "write", False, "对抗：~ 不展开（partial）"),
        ("mv a b/", "unresolved", False, "对抗：mv 目标是目录"),
        ("cp a b c/", "unresolved", False, "对抗：cp 多源→目录"),
        ("echo OK > /dev/null", "other", False, "对抗：>/dev/null 不算目标"),
        ("for f in a; do echo $f; done", "unresolved", False, "对抗：$ 变量"),
        ("sed -i \"$f\" AGENT_RULES.md", "unresolved", False, "对抗：目标为变量"),
        # ---- F 席修复回归（缺陷 A heredoc-first / 缺陷 B cd 前缀）----
        ("cat <<'EOF' > AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：heredoc-first >（定界符后重定向）"),
        ("cat <<EOF >> AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：heredoc-first >>"),
        ("cat <<-EOF > AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：<<- 前缀 >"),
        ("cat > AGENT_RULES.md <<'EOF'\nhi\nEOF", "write", True, "F-A：> 在前 heredoc（回归）"),
        ("cat >> AGENT_RULES.md <<EOF\nhi\nEOF", "write", True, "F-A：>> 在前 heredoc（回归）"),
        ("cat <<'EOF' > /tmp/x\necho a > AGENT_RULES.md\nEOF", "write", False, "F-A：heredoc 正文含 > 不当重定向"),
        ("cat <<EOF > /tmp/notgate\nhi\nEOF", "write", False, "F-A：非 gate 目标不误命中"),
        ("cd /tmp && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd /tmp 前缀不命中 gate"),
        (f"cd {W} && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd 本仓根前缀仍 unresolved（不猜 cwd）"),
        ("cd - && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd - 前缀"),
        ("cd .. && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd .. 前缀"),
        ("pushd /tmp && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：pushd 前缀"),
        (f"sed -i 's/a/b/' {W}/AGENT_RULES.md", "write", True, "F-B：无 cd 绝对路径 gate 写照旧命中"),
        # ---- F2 席修复回归（N4 子 shell / N5 包装前缀 / N6 apply_patch 命令位 / N7 输入重定向）----
        ("(cd /tmp && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：子 shell (cd 前缀 → 整条 unresolved"),
        (f"(cd {W} && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：子 shell cd 本仓根不因 payload cwd 误判"),
        ("(pushd /tmp && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：(pushd 变体"),
        ("(sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：裸括号子 shell"),
        ("echo hi > AGENT_RULES.md)", "unresolved", False, "F2-N4：目标尾括号不拼进路径"),
        ("env X=1 sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：env 包装前缀"),
        ("sudo sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：sudo 包装前缀"),
        ("timeout 5 sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：timeout 包装前缀"),
        ("xargs sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：xargs 包装前缀"),
        ("command sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：command 包装前缀"),
        ("cat > AGENT_RULES.md <<EOF\napply_patch\nEOF", "write", True, "F2-N6：heredoc 正文 apply_patch 不当 patch"),
        ("echo apply_patch > /tmp/x", "write", False, "F2-N6：apply_patch 作参数 → 写非 gate"),
        ("grep apply_patch AGENT_RULES.md", "read", False, "F2-N6：grep apply_patch → read"),
        ("apply_patch x.patch", "patch", False, "F2-N6：apply_patch 命令位仍 patch"),
        ("tee AGENT_RULES.md < /tmp/x", "write", True, "F2-N7：输入重定向 < 非写目标"),
        ("sed -n 1,5p < AGENT_RULES.md", "read", False, "F2-N7：sed -n 输入重定向 read 无 targets"),
        ("ls -l", "read", False, "F2-§1：ls 读命令不回归"),
        ("grep -n x AGENT_RULES.md", "read", False, "F2-§1：grep 读命令不回归"),
        # ---- F3 席修复回归（perl 无 -i → unresolved；-i/-pi/-i.bak → write）----
        ("perl -e 'open OUT, \">AGENT_RULES.md\"'", "unresolved", False, "F3：perl -e 可写 → unresolved"),
        ("perl fix_script.pl", "unresolved", False, "F3：perl 脚本文件 → unresolved"),
        ("perl -i.bak -pe 's/a/b/' AGENT_RULES.md", "write", True, "F3：perl -i.bak 仍 write"),
        ("perl -ne 'print' AGENT_RULES.md", "unresolved", False, "F3：perl -n -e 无 -i → unresolved"),
        ("perl -pe 's/a/b/' AGENT_RULES.md", "unresolved", False, "F3：perl -p 无 -i → unresolved"),
    ]


# ================================================================ 语料抽取

_PATTERNS = (".hooklog", ".native.jsonl", ".rollout.jsonl")


def _find_cwd(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("cwd"), str) and obj["cwd"]:
            return obj["cwd"]
        for v in obj.values():
            r = _find_cwd(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_cwd(v)
            if r:
                return r
    return None


def _reconstruct(argv):
    for i, t in enumerate(argv):
        if t in ("-c", "-lc", "--command"):
            if i + 1 < len(argv):
                return argv[i + 1]
    return argv[-1] if argv else ""


def _collect(obj, out):
    """递归取 command：字符串 / argv 列表（-c 内层）/ arguments JSON 字符串里的 cmd。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "command":
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.append(_reconstruct(v))
            elif k == "arguments" and isinstance(v, str):
                try:
                    sub = json.loads(v)
                except Exception:
                    sub = None
                if isinstance(sub, dict) and isinstance(sub.get("cmd"), str):
                    out.append(sub["cmd"])
                elif isinstance(sub, (dict, list)):
                    _collect(sub, out)
            elif isinstance(v, (dict, list)):
                _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)


def iter_corpus_files(corpus_root):
    files = []
    for dirpath, _, names in os.walk(corpus_root):
        for n in names:
            if n.endswith(_PATTERNS):
                files.append(os.path.join(dirpath, n))
    # cc-probe/hook-events.jsonl 是 hooklog 格式但名为 .jsonl：显式纳入（CC 原生事件流）。
    cc = os.path.join(corpus_root, "cc-probe", "hook-events.jsonl")
    if os.path.isfile(cc) and cc not in files:
        files.append(cc)
    files.sort()
    return files


def iter_commands(corpus_root):
    """yield (source_rel, source_type, line_no, cwd, command)。"""
    for fp in iter_corpus_files(corpus_root):
        rel = os.path.relpath(fp, corpus_root)
        if rel.endswith(".hooklog") or rel.endswith("hook-events.jsonl"):
            stype = "hooklog"
        elif rel.endswith(".native.jsonl"):
            stype = "native"
        else:
            stype = "rollout"
        file_cwd = None
        with open(fp, encoding="utf-8") as f:
            for line_no, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                c = _find_cwd(d)
                if c:
                    file_cwd = c
                cmds: list[str] = []
                _collect(d, cmds)
                seen: set[str] = set()
                for cmd in cmds:
                    if cmd and cmd.strip() and cmd not in seen:
                        seen.add(cmd)
                        yield rel, stype, line_no, file_cwd, cmd


# ================================================================ 判读辅助

def _read_class(cmd: str) -> bool:
    """语料里的「读类」真值：首词元（basename）∈ 读命令集合，且非写形态。"""
    try:
        i = 0
        n = len(cmd)
        while i < n and cmd[i] in " \t":
            i += 1
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", cmd[i:])
        if m:
            i += m.end()
            while i < n and cmd[i] in " \t":
                i += 1
        j = i
        while j < n and cmd[j] not in " \t\n;|&":
            j += 1
        first = os.path.basename(cmd[i:j])
    except Exception:
        return False
    read_set = {"cat", "grep", "egrep", "fgrep", "ls", "od", "wc", "head", "tail",
                "test", "true", "nl", "bat"}
    if first in read_set:
        return True
    if first == "sed" and "-i" not in cmd.split():
        return True
    return False


def _gate_files_for(cwd):
    if not cwd:
        return []
    return [os.path.normpath(os.path.join(cwd, GATE_REL))]


_UNRESOLVED_SHELLS = {"zsh", "sh", "bash", "dash", "ash", "fish", "csh", "tcsh",
                      "python", "python2", "python3", "make"}


def _unresolved_category(cmd: str, reason: str) -> str:
    """把 unresolved 归入已声明类别；OTHER 表示落出声明类别（必须为 0）。"""
    first = os.path.basename(cmd.split()[0]) if cmd.split() else ""
    if first in _UNRESOLVED_SHELLS:
        return "interpreter-wrapper (rule4)"
    if "解释器" in reason or "内层不递归" in reason:
        return "interpreter-wrapper (rule4)"
    if "工作目录" in reason:
        return "cwd-change (rule4)"
    if "子 shell" in reason or "括号" in reason:
        return "subshell (rule4)"
    if "未知首词元" in reason:
        return "unknown-command (rule4)"
    if "目录" in reason:
        return "dir-target (rule4)"
    if "通配" in reason:
        return "wildcard (rule4)"
    if "unsafe" in reason or "词元化失败" in reason:
        return "unsafe-lex (rule2)"
    return "OTHER"


# ================================================================ 主流程

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--unit", action="store_true", help="只跑单元矩阵")
    ap.add_argument("--corpus", default=None, help="语料根目录；缺省只跑单元矩阵")
    ap.add_argument("--out", default=None, help="指标 JSON 输出路径（默认 <脚本目录>/W1B-METRICS.json）")
    args = ap.parse_args(argv)

    corpus_root = args.corpus
    out_json = args.out or str(Path(__file__).parent / "W1B-METRICS.json")
    only_unit = args.unit or corpus_root is None

    # --- 单元矩阵 ---
    matrix = _matrix()
    unit_cases = []
    n_pass = n_fail = 0
    for cmd, exp_kind, exp_hit, note in matrix:
        r = wtarget.classify(cmd, CWD_MATRIX, GATE_MATRIX)
        got_kind = r["kind"]
        got_hit = bool(r["gate_targets"])
        ok = (got_kind == exp_kind) and (got_hit == exp_hit)
        if ok:
            n_pass += 1
        else:
            n_fail += 1
        unit_cases.append({
            "command": cmd, "note": note,
            "expected": {"kind": exp_kind, "gate_hit": exp_hit},
            "actual": {"kind": got_kind, "gate_hit": got_hit,
                       "targets": r["targets"], "forms": r["forms"],
                       "confidence": r["confidence"], "class": r["class"]},
            "pass": ok,
        })

    if only_unit:
        print(f"unit matrix: {n_pass} pass / {n_fail} fail / {len(matrix)} total")
        for c in unit_cases:
            if not c["pass"]:
                print(f"  FAIL {c['note']}: {c['command']!r}\n"
                      f"        expected={c['expected']} actual={c['actual']['kind']},"
                      f"gate_hit={c['actual']['gate_hit']}")
        wtarget_sha = hashlib.sha256(
            (HOOKS_DIR / "wtarget.py").read_bytes()).hexdigest()
        metrics = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "classifier": {"file": ".workspace/hooks/wtarget.py",
                                  "sha256": wtarget_sha},
                   "corpus_root": None,
                   "unit_matrix": {"total": len(matrix), "passed": n_pass,
                                   "failed": n_fail, "cases": unit_cases}}
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f"指标已写: {out_json}")
        return 0 if n_fail == 0 else 1

    # --- 语料回放 ---
    records = []
    for rel, stype, line_no, cwd, cmd in iter_commands(corpus_root):
        records.append((rel, stype, line_no, cwd, cmd))

    per_source: dict[str, int] = {}
    per_type: dict[str, int] = {}
    for rel, stype, *_ in records:
        per_source[rel] = per_source.get(rel, 0) + 1
        per_type[stype] = per_type.get(stype, 0) + 1

    unique_commands = sorted({cmd for *_, cmd in records})

    kind_dist: dict[str, int] = {}
    kind_by_type: dict[str, dict[str, int]] = {}
    false_reject_cases: list[dict] = []
    unresolved_by_token: dict[str, int] = {}
    unresolved_by_type: dict[str, int] = {}
    unresolved_categories: dict[str, int] = {}
    unresolved_total = 0
    per_cmd_ms: list[float] = []

    t0 = time.perf_counter()
    for rel, stype, line_no, cwd, cmd in records:
        tic = time.perf_counter()
        r = wtarget.classify(cmd, cwd, _gate_files_for(cwd))
        per_cmd_ms.append((time.perf_counter() - tic) * 1000.0)
        k = r["kind"]
        kind_dist[k] = kind_dist.get(k, 0) + 1
        kind_by_type.setdefault(stype, {})[k] = kind_by_type.setdefault(stype, {}).get(k, 0) + 1
        if k == "unresolved":
            unresolved_total += 1
            unresolved_by_type[stype] = unresolved_by_type.get(stype, 0) + 1
            tok = cmd.split()[0] if cmd.split() else ""
            unresolved_by_token[tok] = unresolved_by_token.get(tok, 0) + 1
            cat = _unresolved_category(cmd, r["reason"])
            unresolved_categories[cat] = unresolved_categories.get(cat, 0) + 1
        if k == "write" and r["gate_targets"] and _read_class(cmd):
            false_reject_cases.append({
                "source": rel, "line": line_no, "cwd": cwd, "command": cmd,
                "gate_targets": r["gate_targets"], "forms": r["forms"],
            })
    total_s = time.perf_counter() - t0

    ground_truth_gate_writes = [
        "printf '%s\\n' '<!-- probe-appended -->' >> AGENT_RULES.md",
        "printf '\\n<!-- probe-appended -->\\n' >> AGENT_RULES.md",
        "truncate -s 24223 AGENT_RULES.md",
    ]
    miss_cases: list[dict] = []
    gtw_results: list[dict] = []
    for gt in ground_truth_gate_writes:
        found = [(rel, ln, cwd) for rel, st, ln, cwd, cmd in records if cmd == gt]
        r = wtarget.classify(gt, CWD_MATRIX, GATE_MATRIX)
        caught = (r["kind"] == "write" and bool(r["gate_targets"]))
        gtw_results.append({"command": gt, "instances_in_corpus": len(found),
                            "classified": r["kind"], "gate_targets": r["gate_targets"],
                            "caught": caught, "locations": found})
        if found and not caught:
            miss_cases.append({"command": gt, "classified": r["kind"],
                               "instances_in_corpus": len(found)})
    _SHELLS = {"zsh", "sh", "bash", "dash", "ash", "fish", "csh", "tcsh"}
    for rel, st, ln, cwd, cmd in records:
        first = cmd.split()[0] if cmd.split() else ""
        if os.path.basename(first) in _SHELLS:
            continue
        if "AGENT_RULES.md" in cmd and (" >> " in cmd or "truncate" in cmd):
            r = wtarget.classify(cmd, cwd, _gate_files_for(cwd))
            if r["kind"] != "write":
                miss_cases.append({"source": rel, "line": ln, "command": cmd,
                                   "classified": r["kind"]})

    sorted_ms = sorted(per_cmd_ms)

    def pct(p):
        if not sorted_ms:
            return 0.0
        idx = int(round((p / 100.0) * (len(sorted_ms) - 1)))
        return sorted_ms[idx]

    unresolved_ratio = (unresolved_total / len(records)) if records else 0.0

    wtarget_sha = hashlib.sha256((HOOKS_DIR / "wtarget.py").read_bytes()).hexdigest()

    metrics = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "classifier": {"file": ".workspace/hooks/wtarget.py", "sha256": wtarget_sha},
        "corpus_root": corpus_root,
        "extraction_rule": (
            "递归取 command 字段：字符串 / argv 列表(-c 内层重建) / arguments JSON 字符串内的 cmd；"
            "逐行去重；cwd 取行内或文件级 session_meta；cc-probe/hook-events.jsonl 为 hooklog 格式，显式纳入。"),
        "total_instances": len(records),
        "unique_commands": len(unique_commands),
        "per_source": per_source,
        "per_source_type": per_type,
        "kind_distribution": kind_dist,
        "kind_by_source_type": kind_by_type,
        "false_reject": {"count": len(false_reject_cases), "cases": false_reject_cases},
        "miss": {"count": len(miss_cases),
                 "ground_truth_gate_writes": gtw_results,
                 "cases": miss_cases},
        "unresolved": {"count": unresolved_total, "ratio": unresolved_ratio,
                       "by_source_type": unresolved_by_type,
                       "by_first_token": dict(sorted(unresolved_by_token.items(),
                                                     key=lambda x: -x[1])),
                       "by_declared_category": dict(sorted(unresolved_categories.items(),
                                                          key=lambda x: -x[1]))},
        "unit_matrix": {"total": len(matrix), "passed": n_pass, "failed": n_fail,
                        "cases": unit_cases},
        "timing": {"total_seconds": total_s,
                   "per_command_p50_ms": pct(50), "per_command_p95_ms": pct(95),
                   "per_command_mean_ms": (sum(per_cmd_ms) / len(per_cmd_ms)) if per_cmd_ms else 0.0},
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # --- stdout 摘要 ---
    print("=" * 72)
    print("W1b 回放基线 摘要")
    print("=" * 72)
    print(f"classifier sha256: {wtarget_sha[:16]}…")
    print(f"语料实例 total={len(records)}  unique_commands={len(unique_commands)}")
    print(f"per_source_type: {json.dumps(per_type, ensure_ascii=False)}")
    print(f"kind 分布: {json.dumps(kind_dist, ensure_ascii=False)}")
    print(f"误拒 false_reject: {len(false_reject_cases)}  (目标必须为 0)")
    for c in false_reject_cases:
        print(f"  !! {c['source']}:{c['line']} {c['command']!r} -> {c['gate_targets']}")
    print(f"漏判 miss: {len(miss_cases)}  (必须为 0)")
    for g in gtw_results:
        print(f"  ground-truth {g['caught']!s:5} {g['command'][:55]!r} -> "
              f"{g['classified']} gate_targets={g['gate_targets']}")
    for c in miss_cases:
        print(f"  !! {c}")
    print(f"unresolved: {unresolved_total}  ratio={unresolved_ratio:.4f}")
    print(f"  按来源: {json.dumps(unresolved_by_type, ensure_ascii=False)}")
    print(f"  按首词元: {json.dumps(dict(list(sorted(unresolved_by_token.items(), key=lambda x:-x[1]))[:8]), ensure_ascii=False)}")
    print(f"  按已声明类别: {json.dumps(dict(sorted(unresolved_categories.items(), key=lambda x:-x[1])), ensure_ascii=False)}")
    print(f"单元矩阵: {n_pass} pass / {n_fail} fail / {len(matrix)} total")
    for c in unit_cases:
        if not c["pass"]:
            print(f"  FAIL {c['note']}: {c['command']!r} expected={c['expected']} "
                  f"actual={c['actual']['kind']},hit={c['actual']['gate_hit']}")
    print(f"计时: 全语料 {total_s*1000:.2f} ms, 单条 p50={pct(50):.3f} ms, p95={pct(95):.3f} ms")
    print(f"指标已写: {out_json}")

    verdict = (len(false_reject_cases) == 0 and len(miss_cases) == 0 and n_fail == 0)
    print("=" * 72)
    print(f"裁决: W1 {'可以上线' if verdict else '不可以上线'}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
