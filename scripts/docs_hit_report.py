#!/usr/bin/env python3
"""检索命中报告（确定性，不调模型）。

判据：对 `tests/docs_probe.json` 的每道题，用 `docs_index.py query <title>` 取 top-N，
若其中任一节的**正文**同时包含该题全部 `expect` 关键词 → 判命中；否则 MISS。

用途：`docs-retrieval-maintenance` skill 的机械验收步骤。
退出码：命中数 < --min（默认 9）→ 1；索引陈旧/缺失 → 2。

用法：
  python3 scripts/docs_hit_report.py                 # 人读
  python3 scripts/docs_hit_report.py --json          # 机器读
  python3 scripts/docs_hit_report.py --min 10 --top 2
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "scripts" / "docs_index.py"
PROBE = ROOT / "tests" / "docs_probe.json"
DOCS = ROOT / "docs"


def load_cases() -> list[dict]:
    return json.loads(PROBE.read_text(encoding="utf-8"))["cases"]


def query(title: str, top: int) -> tuple[list[dict], str | None]:
    p = subprocess.run([sys.executable, "-B", str(INDEX), "query", title, "--json", "--top", str(top)],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode == 3:
        return [], "stale"
    if p.returncode != 0:
        return [], (p.stdout or p.stderr).strip()[:200]
    return json.loads(p.stdout or "{}").get("results", []), None


def section_body(path: str, start: int, end: int) -> str:
    lines = (ROOT / path).read_text(encoding="utf-8").split("\n")
    return "\n".join(lines[start - 1:end])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=9)
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows, hit = [], 0
    for c in load_cases():
        res, err = query(c["title"], a.top)
        if err == "stale":
            print(json.dumps({"error": "stale", "hint": "先跑 scripts/docs_index.py build"}, ensure_ascii=False))
            return 2
        ok, where = False, []
        for r in res:
            body = section_body(r["path"], r["lines"][0], r["lines"][1])
            where.append(f'{r["path"]}:{r["lines"][0]}')
            if all(k.lower() in body.lower() for k in c["expect"]):
                ok = True
                break
        hit += ok
        rows.append({"id": c["id"], "title": c["title"], "hit": ok,
                     "top": where, "score": res[0]["score"] if res else None})
    total = len(rows)
    out = {"hit": hit, "total": total, "min": a.min, "pass": hit >= a.min, "cases": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in rows:
            print(("HIT " if r["hit"] else "MISS"), r["id"], r["title"], r["top"])
        print(f"命中 {hit}/{total}（阈值 {a.min}）→ {'PASS' if out['pass'] else 'FAIL'}")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
