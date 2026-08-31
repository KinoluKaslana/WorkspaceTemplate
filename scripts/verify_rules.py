#!/usr/bin/env python3
"""Two-level verification of governance rules against the clause registry.

Level 1 (file): sha256 of each template-layer rules file vs registry —
any direct edit is drift.
Level 2 (clause): per-clause ID comparison vs registry — detects added,
deleted, edited, or reordered clauses even when the file hash was going
to be updated anyway. Also validates custom-file override references
(overrides: R<id>) against the current registry.

Stdlib only. Exit 0 = clean; 1 = findings; 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"\s*<!--\s*id:(R\d+)\s*-->\s*$")
OVERRIDE_RE = re.compile(r"overrides:\s*(R\d+)")
EXTENDS_RE = re.compile(r"extends:\s*(R\d+)")


def strip_marker(line: str) -> str:
    return MARKER_RE.sub("", line).rstrip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_clauses(path: Path) -> dict[str, str]:
    reg: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = MARKER_RE.search(ln)
        if m:
            reg[m.group(1)] = strip_marker(ln)
    return reg


def verify_file_level(ws_files: dict[str, Path], registry: dict) -> list[str]:
    findings: list[str] = []
    for rel, entry in registry.items():
        p = ws_files.get(rel)
        if p is None or not p.is_file():
            findings.append(f"[file] MISSING {rel}")
            continue
        actual = sha256(p)
        if actual != entry.get("file_sha256"):
            findings.append(f"[file] HASH-DIFF {rel} (direct edit or newer template version)")
    for rel in ws_files:
        if rel not in registry:
            findings.append(f"[file] EXTRA {rel} (not in registry)")
    return findings


def verify_clause_level(ws_files: dict[str, Path], registry: dict) -> list[str]:
    findings: list[str] = []
    for rel, entry in registry.items():
        reg_clauses: dict[str, str] = entry.get("clauses", {})
        p = ws_files.get(rel)
        if p is None or not p.is_file():
            continue  # already reported at file level
        cur = scan_clauses(p)
        for cid, text in reg_clauses.items():
            if cid not in cur:
                findings.append(f"[clause] DELETED {rel}#{cid}: {text[:60]}")
            elif cur[cid] != text:
                findings.append(f"[clause] EDITED {rel}#{cid}: registry={text[:50]} current={cur[cid][:50]}")
        for cid in cur:
            if cid not in reg_clauses:
                findings.append(f"[clause] ADDED {rel}#{cid}: {cur[cid][:60]}")
        # reorder detection: ID sequence should be non-decreasing in file order
        ids = [m.group(1) for ln in p.read_text(encoding="utf-8").splitlines() if (m := MARKER_RE.search(ln))]
        nums = [int(i[1:]) for i in ids]
        if nums != sorted(nums) and len(set(nums)) == len(nums):
            findings.append(f"[clause] REORDERED {rel}: file order {ids[:12]}...")
    return findings


def verify_custom_refs(ws_root: Path, registry: dict, custom_glob: str) -> list[str]:
    findings: list[str] = []
    all_ids: set[str] = set()
    for entry in registry.values():
        all_ids.update(entry.get("clauses", {}).keys())
    for p in sorted(ws_root.glob(custom_glob)):
        rel = p.relative_to(ws_root).as_posix()
        text = p.read_text(encoding="utf-8")
        for m in OVERRIDE_RE.finditer(text):
            if m.group(1) not in all_ids:
                findings.append(f"[custom] {rel}: overrides 指向不存在的条款 {m.group(1)}（模板条款已删除或改写）")
        for m in EXTENDS_RE.finditer(text):
            if m.group(1) not in all_ids:
                findings.append(f"[custom] {rel}: extends 指向不存在的条款 {m.group(1)}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True, help="workspace root to verify")
    ap.add_argument("--registry", default=None, help="rule-clauses.json path (default <workspace>/.workspace/rule-clauses.json)")
    ap.add_argument("--custom-glob", default="*.custom.md", help="glob for custom rule files")
    ap.add_argument("--json", help="optional findings JSON output path")
    args = ap.parse_args()

    ws = Path(args.workspace).resolve()
    reg_path = Path(args.registry).resolve() if args.registry else ws / ".workspace" / "rule-clauses.json"
    if not reg_path.is_file():
        print(json.dumps({"error": f"registry not found: {reg_path}"}))
        return 2
    registry = json.loads(reg_path.read_text(encoding="utf-8"))

    ws_files = {rel: ws / rel for rel in registry}
    findings = []
    findings += verify_file_level(ws_files, registry)
    findings += verify_clause_level(ws_files, registry)
    findings += verify_custom_refs(ws, registry, args.custom_glob)

    report = {"clean": not findings, "findings": findings, "checked_files": list(registry), "workspace": str(ws)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
