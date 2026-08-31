#!/usr/bin/env python3
"""Mark rule clauses in governance files with stable IDs and build a clause registry.

Each numbered/bulleted clause and each section heading gets a trailing
comment-style marker:  <!-- id:R<nn> -->  (or for lines already ending in
HTML comments, an adjacent marker line). The registry
(.workspace/rule-clauses.json) maps ids to clause text (marker-stripped)
so verify_rules.py can detect reordering, edits, deletions, and
insertions without running a diff.

Stdlib only. Modes:
  mark      add/refresh markers in-place (writes files; make a backup first)
  registry  scan markers and (re)build .workspace/rule-clauses.json
  check     report unmarked clauses (read-only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"\s*<!--\s*id:(R\d+)\s*-->\s*$")
CLAUSE_RE = re.compile(r"^(\d+\.|[-*]\s|\d+[).、])")
HEADING_RE = re.compile(r"^#{1,6}\s")
# lines that are pure policy content worth tracking (clauses, headings)
TRACKED_RE = re.compile(r"^(\d+\.|[-*]\s|#{1,6}\s|>\s*\*\*|.*[:：]$)")


def strip_marker(line: str) -> str:
    return MARKER_RE.sub("", line).rstrip()


def next_free_id(used: set[str]) -> str:
    n = 1
    while f"R{n}" in used:
        n += 1
    return f"R{n}"


def scan(path: Path) -> tuple[list[str], dict[str, str]]:
    """Return (marked_lines, registry {id: clause_text})."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    reg: dict[str, str] = {}
    for ln in lines:
        m = MARKER_RE.search(ln)
        if m:
            reg[m.group(1)] = strip_marker(ln)
        out.append(ln)
    return out, reg


def is_trackable(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("<!--") or MARKER_RE.search(line):
        return False
    return bool(CLAUSE_RE.match(s) or HEADING_RE.match(s))


def mark_file(path: Path, dry: bool = False) -> tuple[int, dict[str, str]]:
    lines, reg = scan(path)
    used = set(reg)
    out: list[str] = []
    added = 0
    for ln in lines:
        if MARKER_RE.search(ln):
            out.append(ln)
            continue
        if is_trackable(ln):
            cid = next_free_id(used)
            used.add(cid)
            reg[cid] = strip_marker(ln)
            out.append(f"{ln} <!-- id:{cid} -->")
            added += 1
        else:
            out.append(ln)
    if not dry and added:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return added, reg


def build_registry(files: list[Path], root: Path) -> dict:
    registry: dict[str, dict] = {}
    for f in files:
        _, reg = scan(f)
        rel = f.resolve().relative_to(root).as_posix()
        registry[rel] = {
            "clauses": reg,
            "file_sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            "marked": len(reg),
        }
    return registry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["mark", "registry", "check"])
    ap.add_argument("files", nargs="+", help="governance markdown files")
    ap.add_argument("--root", default=".", help="workspace root for relative paths")
    ap.add_argument("--out", default=".workspace/rule-clauses.json", help="registry path")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    paths = [Path(f) for f in args.files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print(json.dumps({"error": f"missing files: {missing}"}))
        return 2

    if args.mode == "mark":
        total = 0
        regs: dict[str, dict] = {}
        for p in paths:
            added, reg = mark_file(p)
            total += added
            regs[p.resolve().relative_to(root).as_posix()] = {
                "clauses": reg,
                "file_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "marked": len(reg),
            }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(regs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK: marked {total} new clauses; registry -> {out} ({len(regs)} files)")
        return 0

    if args.mode == "check":
        unmarked = 0
        for p in paths:
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if is_trackable(ln) and not MARKER_RE.search(ln):
                    print(f"UNMARKED {p}:{i}: {ln.strip()[:80]}")
                    unmarked += 1
        print(f"{'OK: all clauses marked' if not unmarked else f'{unmarked} unmarked clauses'}")
        return 1 if unmarked else 0

    if args.mode == "registry":
        regs = build_registry(paths, root)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(regs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK: registry rebuilt -> {out} ({len(regs)} files)")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
