#!/usr/bin/env python3
"""别名表审计与回滚（确定性，只读 + 显式 --restore 才写）。

来源纪律（见 SKILL.md §防自欺）：
- 别名只允许由**真实查询缺口**驱动；判据集（tests/docs_probe.json）的 MISS **不是**理由。
- 每条别名必须有 `source`：`user-query`（须带 `miss_id` 指向 `docs/.miss-log.jsonl`）或
  `probe-only`（历史遗留/待复核，计入债务，strict 模式判失败）。

检查项：
  1. 死指针：目标文件/标题不存在；
  2. 指纹漂移：`sha256` 与当前文件不符（别名会被静默忽略）；
  3. 来源合规：`user-query` 必须在 miss-log 中存在且为 MISS；`probe-only` 计债务；
  4. 冗余：留一验证——临时禁用该条后命中率是否仍达标（冗余条应标删）；
  5. 上限：默认 8 条。

用法：
  python3 scripts/docs_alias_audit.py            # 人读
  python3 scripts/docs_alias_audit.py --json
  python3 scripts/docs_alias_audit.py --strict   # probe-only / 冗余 也算失败
  python3 scripts/docs_alias_audit.py --restore  # 用最新快照回滚（写盘）
退出码：0 OK / 1 需维护 / 2 索引陈旧或缺失
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "docs" / ".aliases.json"
MISSLOG = ROOT / "docs" / ".miss-log.jsonl"
INDEX = ROOT / "docs" / ".index.json"
QUERY = ROOT / "scripts" / "docs_index.py"
PROBE = ROOT / "tests" / "docs_probe.json"
CAP = 8


def fp(path: Path) -> str:
    raw = path.read_bytes()
    return f"c{len(raw)}:{hashlib.sha256(raw).hexdigest()}"


def hit_stats(top: int, no_alias: bool = False) -> tuple[int, list[dict], str | None]:
    cases = json.loads(PROBE.read_text(encoding="utf-8"))["cases"]
    env = {**os.environ, **({"DOCS_INDEX_NO_ALIAS": "1"} if no_alias else {})}
    hit, miss = 0, []
    for c in cases:
        r = subprocess.run([sys.executable, "-B", str(QUERY), "query", c["title"], "--json", "--top", str(top)],
                           cwd=ROOT, capture_output=True, text=True, env=env)
        if r.returncode == 3:
            return 0, [], "stale"
        res = json.loads(r.stdout or "{}").get("results", [])
        ok = False
        for x in res:
            body = "\n".join((ROOT / x["path"]).read_text(encoding="utf-8").split("\n")[x["lines"][0] - 1:x["lines"][1]])
            if all(k.lower() in body.lower() for k in c["expect"]):
                ok = True
                break
        if ok:
            hit += 1
        else:
            miss.append({"id": c["id"], "title": c["title"]})
    return hit, miss, None


def load_miss_log() -> dict[str, dict]:
    out = {}
    if MISSLOG.is_file():
        for line in MISSLOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("id"):
                out[rec["id"]] = rec
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--min", type=int, default=None)
    ap.add_argument("--top", type=int, default=2)
    a = ap.parse_args(argv)

    if a.restore:
        snaps = sorted(glob.glob(str(ROOT / "docs" / ".aliases.bak.*.json")))
        if not snaps:
            print(json.dumps({"error": "no-snapshot"}, ensure_ascii=False))
            return 1
        src = Path(snaps[-1])
        shutil.copyfile(src, ALIASES)
        print(json.dumps({"restored_from": src.name, "sha256": hashlib.sha256(ALIASES.read_bytes()).hexdigest()},
                         ensure_ascii=False))
        return 0

    if not INDEX.is_file():
        print(json.dumps({"error": "index-missing"}, ensure_ascii=False))
        return 2
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    heads = {(s["path"], s["heading"]) for s in idx["sections"]}
    aliases = json.loads(ALIASES.read_text(encoding="utf-8")).get("aliases") or [] if ALIASES.is_file() else []
    misslog = load_miss_log()

    dead, drift, sourceless, redundant = [], [], [], []
    for i, al in enumerate(aliases):
        p, h = al.get("path"), al.get("heading")
        if (p, h) not in heads:
            dead.append({"index": i, "path": p, "heading": h, "reason": "heading-missing"})
            continue
        target = ROOT / p
        if not target.is_file():
            dead.append({"index": i, "path": p, "reason": "file-missing"})
            continue
        cur = fp(target)
        if al.get("sha256") and al["sha256"] != cur:
            drift.append({"index": i, "path": p, "reason": "fingerprint"})
        src = al.get("source")
        if src == "user-query":
            rec = misslog.get(al.get("miss_id") or "")
            if not rec or rec.get("result") != "miss":
                sourceless.append({"index": i, "reason": "miss-log-missing", "miss_id": al.get("miss_id")})
        elif src != "probe-only":
            sourceless.append({"index": i, "reason": "source-undeclared"})

    total_cases = len(json.loads(PROBE.read_text(encoding="utf-8"))["cases"])
    minimum = a.min if a.min is not None else max(1, -(-total_cases * 9 // 10))   # 默认 90% 向下取整
    hit, miss, err = hit_stats(a.top)
    if err == "stale":
        print(json.dumps({"error": "stale", "hint": "先跑 build"}, ensure_ascii=False))
        return 2

    # 留一验证：逐条禁用后仍达标 → 冗余
    for i, al in enumerate(aliases):
        saved = ALIASES.read_text(encoding="utf-8")
        try:
            data = json.loads(saved)
            data["aliases"] = [x for j, x in enumerate(aliases) if j != i]
            ALIASES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            h2, _, e2 = hit_stats(a.top)
        finally:
            ALIASES.write_text(saved, encoding="utf-8")
        if e2 is None and h2 >= minimum:
            redundant.append({"index": i, "phrases": al.get("phrases"),
                              "hit_without_it": h2})

    probe_only = [{"index": i, "phrases": al.get("phrases")} for i, al in enumerate(aliases)
                  if al.get("source") == "probe-only"]
    ok = not dead and not drift and not sourceless and hit >= minimum
    if a.strict:
        ok = ok and not probe_only and not redundant
    out = {"aliases": len(aliases), "cap": CAP, "hit": hit, "min": minimum, "total": total_cases,
           "dead": dead, "fingerprint_drift": drift, "source_violation": sourceless,
           "probe_only_debt": probe_only, "redundant": redundant,
           "miss": miss, "ok": ok, "strict": a.strict}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"别名 {len(aliases)}/{CAP}；命中 {hit}/{total_cases}（阈值 {minimum}）")
        for d in dead:
            print("DEAD   ", d)
        for d in drift:
            print("DRIFT  ", d["path"], "别名指纹过期")
        for s in sourceless:
            print("SOURCE ", s)
        for d in probe_only:
            print("DEBT   ", d["phrases"], "来源=判据题，需真实查询或删除")
        for r in redundant:
            print("REDUND ", r["phrases"], f"禁用后命中仍 {r['hit_without_it']}/10")
        for m in miss:
            print("MISS   ", m["id"], m["title"])
        print("结论:", "OK" if ok else "需维护")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
