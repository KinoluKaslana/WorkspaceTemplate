#!/usr/bin/env python3
"""条款属性门：校验 AGENT_RULES.md 正文用词与 .workspace/clauses.meta.json 声明一致。

错误（exit 1）：
  E1 meta 键在正文中不存在            E2 正文条款缺 meta
  E3 severity 非法                    E4 缺字段 / trigger 为空
  E5 强条款（MUST/MUST_NOT）缺 check  E6 manual 理由为空
  E7 正文没有与声明档位相符的情态词    E8 可机械判定的强条款标了 manual
  E9 强条款未复核（reviewed=false）    E7b check 不是可执行前缀
警告（默认 exit 0，--strict 算失败）：
  W1 同条款混用必须/不得   W2 模糊量词（尽量/必要时/建议/通常）
  W3 强条款里出现「可」    W4 声明档位与正文情态词不匹配（弱）
  W5 声明 INFO 却含强情态词
用法：--json / --strict / --report；退出码 0 通过、1 findings、2 用法或 IO。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "AGENT_RULES.md"
META = ROOT / ".workspace" / "clauses.meta.json"

SEVERITIES = ("MUST", "MUST_NOT", "SHOULD", "MAY", "INFO")
# 用正则而非子串：「禁止事项/禁止动作」是名词，不表示禁令
MODALS = {"MUST": [r"必须"],
          "MUST_NOT": [r"不得", r"禁止(?!事项|动作|、例外|、)"],
          "SHOULD": [r"应当", r"应"],
          "MAY": [r"可"]}
VAGUE = ("尽量", "必要时", "建议", "通常")
# 这些触发条件下的事实可由脚本判定：强条款不许用 manual 糊过去
MACHINE_CHECKABLE = {"hook", "gate-hermes", "template-sync",
                     "skills-registry", "docs", "docs-index", "policy-language",
                     "rule-layering"}
# 引用禁令本身的条款（正文列举被禁词）不触发 W2
VAGUE_EXEMPT = {"R110"}


def load():
    if not POLICY.is_file() or not META.is_file():
        sys.exit("缺少 AGENT_RULES.md 或 .workspace/clauses.meta.json")
    text = POLICY.read_text(encoding="utf-8")
    marks = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"<!--\s*id:(R\d+)\s*-->", text)]
    bodies, prev, dup = {}, 0, []
    for pos, end, rid in marks:
        if rid in bodies:
            dup.append(rid)
        bodies[rid] = text[prev:pos]
        prev = end
    tail = text[prev:]
    if tail.strip() and not re.fullmatch(r"[\s#\-]*", tail):
        dup.append("<tail>")
    return bodies, json.loads(META.read_text(encoding="utf-8")), dup


def lint() -> list[dict]:
    bodies, meta, leftovers = load()
    out = []

    def add(level, rid, code, msg):
        out.append({"level": level, "clause": rid, "code": code, "message": msg})

    for rid in sorted(set(leftovers) - {"<tail>"}):
        add("error", rid, "E10", "条款 ID 重复（后一条覆盖前一条）")
    if "<tail>" in leftovers:
        add("error", "<eof>", "E11", "文末存在未标记的条款文本（会逃过注册表）")
    for rid in sorted(set(meta) - set(bodies)):
        add("error", rid, "E1", "meta 里的条款在正文中不存在")
    for rid in sorted(set(bodies) - set(meta), key=lambda r: int(r[1:])):
        add("error", rid, "E2", "正文条款缺 meta 条目")

    for rid in sorted(set(meta) & set(bodies), key=lambda r: int(r[1:])):
        item, body = meta[rid], bodies[rid]
        severity = item.get("severity")
        if severity not in SEVERITIES:
            add("error", rid, "E3", f"severity 非法：{severity!r}")
            continue
        for field in ("trigger", "check", "reviewed"):
            if field not in item:
                add("error", rid, "E4", f"缺字段 {field}")
        if not str(item.get("trigger") or "").strip():
            add("error", rid, "E4", "trigger 为空")
        check = str(item.get("check") or "").strip()
        reviewed = bool(item.get("reviewed"))
        strong = severity in ("MUST", "MUST_NOT")

        if strong:
            if not check:
                add("error", rid, "E5", "强条款缺 check")
            elif check.startswith("manual:") and len(check[7:].strip()) < 4:
                add("error", rid, "E6", "manual 理由为空或过短")
            if not reviewed:
                add("error", rid, "E9", "强条款未复核（reviewed=false）")
            if check.startswith("manual:") and item.get("trigger") in MACHINE_CHECKABLE:
                add("error", rid, "E8", f"trigger={item.get('trigger')} 可机械判定，不允许 manual")
        if check and not check.startswith("manual:"):
            if not (check.startswith("python3 ") or check.startswith("git ")):
                add("error", rid, "E7b", f"check 不是可执行前缀：{check[:40]}")

        has = {k: any(re.search(w, body) for w in ws) for k, ws in MODALS.items()}
        if severity == "MUST" and not has["MUST"]:
            add("error", rid, "E7", "声明 MUST，正文无「必须」")
        if severity == "MUST_NOT" and not has["MUST_NOT"]:
            add("error", rid, "E7", "声明 MUST_NOT，正文无「不得/禁止」")
        if severity == "MUST" and not has["MUST"]:
            add("warn", rid, "W4", "声明 MUST，正文只有「不得」")
        if severity == "SHOULD" and not has["SHOULD"]:
            add("warn", rid, "W4", "声明 SHOULD，正文无「应当/应」")
        if severity == "MAY" and not has["MAY"]:
            add("warn", rid, "W4", "声明 MAY，正文无「可」")
        if severity == "INFO" and (has["MUST"] or has["MUST_NOT"]):
            add("warn", rid, "W5", "声明 INFO 但含「必须/不得」")
        for sentence in ([] if rid in VAGUE_EXEMPT else re.split(r"(?<=[。；])", body)):
            if re.search(r"必须", sentence) and re.search(r"不得", sentence):
                add("warn", rid, "W1", "同一句里混用「必须」与「不得」，建议拆条")
                break
        vague = [] if rid in VAGUE_EXEMPT else [w for w in VAGUE if w in body]
        if vague:
            add("warn", rid, "W2", f"模糊量词：{','.join(vague)}")
        if strong and rid not in VAGUE_EXEMPT:
            for sentence in re.split(r"(?<=[。；])", body):
                if "可以" in sentence and re.search(r"必须|不得", sentence):
                    add("warn", rid, "W3", "同一句里出现「可以」授权与强义务，确认不是能力描述")
                    break
    return out


def report() -> int:
    _, meta, _ = load()
    by_sev, by_kind, manual = {}, {}, []
    for rid, item in meta.items():
        sev = item.get("severity", "?")
        by_sev[sev] = by_sev.get(sev, 0) + 1
        ck = str(item.get("check") or "")
        kind = "manual" if ck.startswith("manual:") else ("command" if ck else "none")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if kind == "manual":
            manual.append({"clause": rid, "reason": ck[7:].strip()})
    print(json.dumps({"clauses": len(meta), "severity": by_sev, "check_kind": by_kind,
                      "reviewed": sum(1 for v in meta.values() if v.get("reviewed")),
                      "manual": manual}, ensure_ascii=False, indent=1))
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="兼容旧用法：等价于默认")
    ap.add_argument("--lenient", action="store_true", help="只把 errors 视为失败（警告放行）")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        return report()
    findings = lint()
    errs = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    if a.json:
        print(json.dumps({"errors": errs, "warnings": warns,
                          "ok": not errs and (not a.strict or not warns)}, ensure_ascii=False))
    else:
        for f in findings:
            print(f"{f['level'].upper():5} {f['clause']:5} {f['code']:4} {f['message']}")
        print(f"\n{len(errs)} errors / {len(warns)} warnings / {len(load()[1])} clauses")
    if a.lenient:
        return 0 if not errs else 1
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
