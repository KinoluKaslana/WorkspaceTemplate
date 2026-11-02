#!/usr/bin/env python3
"""docs/ 确定性检索索引（无向量、无 embedding、无外部依赖）。

规范来源：`mc-261030/RAG-CROSS-GLM.md` / `RAG-CROSS-SOL.md` 收敛后的可实现规格。

硬约束（R98/R100）：
- 只索引**实现文档**（默认 `docs/*.md`），不索引政策正文、不索引 `SKILL.md`；
- 索引**不存正文**：只存定位字段（路径/标题/行区间/指纹/条款引用）与词频，用于排序；
- 返回的是**定位**，不是「已读」；需要依据时必须由调用方 `read_file` 读整节；
- 检索结果永不进入 gate 的 `coverage` 账本；
- **过期即拒答**：`query` 先做新鲜度校验（目录清单 + 每文件 SHA + schema），不通过直接返回 `stale`，不返回旧定位。

CLI：
  python3 scripts/docs_index.py build [--docs docs] [--out docs/.index.json]
  python3 scripts/docs_index.py check [--docs docs] [--index docs/.index.json]
  python3 scripts/docs_index.py query <词...> [--index docs/.index.json] [--top 2] [--json]

索引顶层：`schema` / `docs` / `generated_at` / `files{path: sha256}` / `sections[]` / `df{}` / `n_sections`。
每节：`path` / `heading` / `level` / `lines[start,end]` / `sha256` / `clauses[]` / `tf{}` / `tokens` / `body_len`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

SCHEMA = 2
HEADING = re.compile(r"^(#{2,3})\s+(.*)$")
CLAUSE = re.compile(r"\bR(\d{1,3})\b")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[0-9]{2,}")  # 中文只走二元切分，避免整段入索引
STOP = {"the", "and", "for", "with", "that", "this", "are", "not", "but", "you", "can", "from"}
# 查询侧功能词/跨词碎片（只影响检索排序，不影响索引内容）
FUNC = {"怎么", "什么", "哪些", "如何", "是否", "可以", "应该", "几步", "只认", "有哪", "哪几",
        "为什", "么签", "托怎", "的是", "了吗", "一个", "这个", "多长", "多少"}
ALIASES = "docs/.aliases.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for w in WORD.findall(text):
        w = w.lower()
        if w not in STOP:
            out.append(w)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):   # 中文二元切分（确定性近似）
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _sections_for(path: Path, rel: str, fp: str) -> list[dict]:
    text = path.read_bytes().decode("utf-8", "replace")
    lines = text.split("\n")
    secs: list[dict] = []
    cur = {"heading": "(preamble)", "level": 0, "start": 1, "body": []}
    gap = 0
    for i, line in enumerate(lines, start=1):
        m = HEADING.match(line)
        if m:
            if cur["body"]:
                cur["end"] = i - 1
                secs.append(cur)
            cur = {"heading": m.group(2).strip(), "level": len(m.group(1)), "start": i, "body": []}
            gap = 0
            continue
        cur["body"].append(line)
        gap = gap + 1 if not line.strip() else 0
        if gap >= 14:                      # 长节按段落分片，不跨节
            cur["end"] = i
            secs.append(cur)
            cur = {"heading": cur["heading"] + "（续）", "level": cur["level"],
                   "start": i + 1, "body": []}
            gap = 0
    if cur["body"]:
        cur["end"] = len(lines)
        secs.append(cur)
    out = []
    for s in secs:
        body = "\n".join(s.pop("body"))
        tf: dict[str, int] = {}
        for t in tokenize(body + " " + s["heading"]):
            tf[t] = tf.get(t, 0) + 1
        out.append({"path": rel, "heading": s["heading"], "level": s["level"],
                    "start": s["start"], "end": s["end"], "sha256": fp,
                    "clauses": sorted({f"R{n}" for n in CLAUSE.findall(body + " " + s["heading"])}),
                    "tf": tf, "tokens": max(1, sum(1 for _ in tokenize(body))),
                    "body_len": len(body)})
    return out


def build(docs: Path, out: Path) -> dict:
    files = sorted(p for p in docs.glob("*.md") if not p.name.startswith("."))
    sections: list[dict] = []
    filemap: dict[str, str] = {}
    for p in files:
        raw = p.read_bytes()
        fp = f"c{len(raw)}:{sha256_bytes(raw)}"
        rel = str(p.relative_to(docs.parent))
        filemap[rel] = fp
        sections.extend(_sections_for(p, rel, fp))
    df: dict[str, int] = {}
    for s in sections:
        for t in s["tf"]:
            df[t] = df.get(t, 0) + 1
    idx = {"schema": SCHEMA, "docs": docs.name, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "files": filemap, "sections": sections, "df": df, "n_sections": len(sections)}
    tmp = out.with_suffix(out.suffix + ".tmp")      # 原子替换
    tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    return {"sections": len(sections), "files": len(files), "out": str(out)}


def freshness(docs: Path, idx: dict) -> dict:
    if idx.get("schema") != SCHEMA:
        return {"ok": False, "reason": "schema", "detail": idx.get("schema")}
    stale: list[dict] = []
    on_disk = {str(p.relative_to(docs.parent)): p
               for p in docs.glob("*.md") if not p.name.startswith(".")}
    indexed = idx.get("files") or {}
    for rel, fp in indexed.items():
        p = on_disk.get(rel)
        if p is None:
            stale.append({"path": rel, "reason": "missing"})
            continue
        raw = p.read_bytes()
        if f"c{len(raw)}:{sha256_bytes(raw)}" != fp:
            stale.append({"path": rel, "reason": "fingerprint"})
    for rel in sorted(set(on_disk) - set(indexed)):
        stale.append({"path": rel, "reason": "unindexed"})
    return {"ok": not stale, "stale": stale[:10]}


def _bm25(q_terms: list[str], sec: dict, df: dict, n: int, avgdl: float) -> float:
    k1, b = 1.2, 0.75
    dl = max(1, sum(sec["tf"].values()))
    score = 0.0
    for q in q_terms:
        f = sec["tf"].get(q, 0)
        if not f:
            continue
        idf = math.log(1 + (n - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5))
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
    return score


def _weights(terms: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in terms:
        if len(t) <= 1 or t in FUNC or t in STOP:
            continue
        out[t] = 1.5 if re.fullmatch(r"[\u4e00-\u9fff]+", t) else 1.0
    return out


def _load_aliases(path: Path, idx: dict) -> list[dict]:
    """别名只做「短语 → 已有节」的指针；目标缺失或指纹不符即忽略（不致命）。"""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    hashes = idx.get("files") or {}
    for a in data.get("aliases") or []:
        if not isinstance(a, dict):
            continue
        p, h = a.get("path"), a.get("heading")
        if not p or not h or hashes.get(p) is None:
            continue
        if a.get("sha256") and a["sha256"] != hashes.get(p):
            continue
        out.append(a)
    return out


def query(index: Path, docs: Path, terms: list[str], top: int) -> dict:
    if not index.is_file():
        return {"error": "index-missing", "hint": "先跑 build"}
    idx = json.loads(index.read_text(encoding="utf-8"))
    fresh = freshness(docs, idx)
    if not fresh["ok"]:                                  # fail-closed：不返回旧定位
        return {"error": "stale", "detail": fresh, "hint": "重建索引后重试"}
    secs = idx["sections"]
    df, n = idx.get("df", {}), max(1, idx.get("n_sections", len(secs)))
    avgdl = max(1.0, sum(sum(s["tf"].values()) for s in secs) / n)
    q_clauses = {f"R{m}" for m in re.findall(r"\bR?(\d{1,3})\b", " ".join(terms))}
    q_terms = [t for t in dict.fromkeys(tokenize(" ".join(terms))) if len(t) > 1]
    W = _weights(q_terms)
    total_w = sum(W.values()) or 1.0
    aliases = [] if os.environ.get("DOCS_INDEX_NO_ALIAS") == "1" else _load_aliases(index.parent / ".aliases.json", idx)
    hit_alias = [a for a in aliases if any(ph and ph in " ".join(terms) for ph in a.get("phrases") or [])]
    ranked: list[tuple[float, dict]] = []
    for s in secs:
        hit = [t for t in W if s["tf"].get(t)]
        cover = 0.25 + 0.75 * ((sum(W[t] for t in hit) / total_w) ** 2 if total_w else 0.0)
        body_score = _bm25(hit, s, df, n, avgdl) if hit else 0.0
        hl = s["heading"].lower()
        head_score = 3.0 * sum(W[t] * math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                               for t in hit if t in hl)
        score = cover * (body_score + head_score)
        bonus = 0.0
        if q_clauses & set(s["clauses"]):
            bonus += 100.0
        for a in hit_alias:
            if s["path"] == a["path"] and s["heading"] == a["heading"]:
                bonus += float(a.get("boost", 20))
        score += bonus
        if score > 0 and (bonus > 0 or score >= 0.6):   # 噪声查询不硬凑结果
            ranked.append((score, s))
    ranked.sort(key=lambda x: -x[0])
    return {"ok": True, "results": [
        {"score": round(sc, 2), "path": s["path"], "heading": s["heading"],
         "lines": [s["start"], s["end"]], "clauses": s["clauses"],
         "fingerprint": s["sha256"], "node_tokens": s["tokens"]}
        for sc, s in ranked[:top]]}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "check", "query"])
    ap.add_argument("terms", nargs="*")
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--index", default="docs/.index.json")
    ap.add_argument("--out", default="docs/.index.json")
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    docs = Path(a.docs)
    if a.cmd == "build":
        print(json.dumps(build(docs, Path(a.out)), ensure_ascii=False))
        return 0
    index = Path(a.index)
    if a.cmd == "check":
        if not index.is_file():
            print(json.dumps({"ok": False, "reason": "index-missing"}, ensure_ascii=False))
            return 1
        res = freshness(docs, json.loads(index.read_text(encoding="utf-8")))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1
    res = query(index, docs, a.terms, a.top)
    if a.json or not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
    else:
        for r in res["results"]:
            print(f"{r['score']:7.2f}  {r['path']}:{r['lines'][0]}-{r['lines'][1]}  "
                  f"{r['heading']}  [{','.join(r['clauses'][:6]) or '-'}]")
    return 0 if res.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
