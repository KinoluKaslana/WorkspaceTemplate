#!/usr/bin/env python3
"""Two-level verification of governance rules against the clause registry.

Level 1 (file): sha256 of each template-layer rules file vs registry —
any direct edit is drift. NOTE: the registry is LOCAL state — an editor
who re-runs mark_rules.py can regenerate it. For authoritative checks
use --vs-template (byte-compare against the template clone).
Level 2 (clause): per-clause ID comparison vs registry — detects added,
deleted, edited, or reordered clauses even when the file hash was going
to be updated anyway. Also validates custom-file override references
(overrides: R<id>) against the current registry.

Trust chain with --vs-template: GitHub repo (git object hashes) ->
template clone (git status clean + HEAD) -> workspace file (byte equal).
This closes the self-attestation hole: a tampered file whose registry
was regenerated will still differ from the template original.

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

# All template-layer files subject to authoritative --vs-template comparison.
# READMEs carry an explicit exemption: workspaces may replace them with a
# user homepage (reported as REPLACED, not TAMPERED).
TEMPLATE_FILES = [
    "AGENT_RULES.md",
    "AGENTS.md",
    "CLAUDE.md",
    "HERMES.md",
    "README.md",
    "README_EN.md",
    "notes/rebuild_index.py",
    "notes/_TEMPLATE.md",
    "scripts/inspect_skills.py",
    "scripts/mark_rules.py",
    "scripts/verify_rules.py",
    ".workspace/skills/template-update/SKILL.md",
    ".workspace/skills/template-update/check_template.py",
    ".workspace/skills/workspace-init/SKILL.md",
    ".workspace/skills/git-init/SKILL.md",
    ".workspace/rule-clauses.json",
]
README_EXEMPT = {"README.md", "README_EN.md"}


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


def verify_vs_template(ws: Path, tpl: Path) -> tuple[list[str], dict]:
    """Authoritative comparison: workspace template-layer files must be
    byte-identical to the template clone. Trust anchors the clone itself
    via git (status clean + HEAD sha) before trusting the comparison."""
    findings: list[str] = []
    meta: dict = {}
    # anchor 1: clone must be a git repo
    if not (tpl / ".git").exists():
        findings.append("[trust] template clone has no .git — cannot anchor trust")
        return findings, meta
    # anchor 2: clone must be clean (no local edits to the reference itself)
    import subprocess

    try:
        st = subprocess.run(["git", "-C", str(tpl), "status", "--porcelain"], capture_output=True, text=True, timeout=30)
        head = subprocess.run(["git", "-C", str(tpl), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        findings.append(f"[trust] git check failed on template clone: {exc}")
        return findings, meta
    if st.returncode != 0 or head.returncode != 0:
        findings.append("[trust] git command failed on template clone")
        return findings, meta
    meta["template_head"] = head.stdout.strip()
    meta["template_clean"] = not st.stdout.strip()
    if st.stdout.strip():
        dirty = st.stdout.strip().splitlines()
        # only files that matter for the comparison taint the anchor
        relevant = [d for d in dirty if any(f in d for f in TEMPLATE_FILES)]
        if relevant:
            findings.append(f"[trust] template clone dirty on compared files: {relevant[:3]}")
    # byte comparison per file
    for rel in TEMPLATE_FILES:
        t, w = tpl / rel, ws / rel
        if not t.is_file():
            continue  # older template versions may lack some files
        if not w.exists():
            findings.append(f"[vs-template] MISSING {rel}")
        elif t.read_bytes() != w.read_bytes():
            if rel in README_EXEMPT:
                findings.append(f"[vs-template] REPLACED {rel} (README exemption: user homepage allowed)")
            else:
                findings.append(f"[vs-template] TAMPERED {rel} (differs from template original)")
    return findings, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True, help="workspace root to verify")
    ap.add_argument("--registry", default=None, help="rule-clauses.json path (default <workspace>/.workspace/rule-clauses.json)")
    ap.add_argument("--custom-glob", default="*.custom.md", help="glob for custom rule files")
    ap.add_argument("--vs-template", dest="vs_template", default=None, help="template clone dir: authoritative byte-compare + git trust anchor")
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
    template_meta = {}
    if args.vs_template:
        tpl = Path(args.vs_template).resolve()
        if not tpl.is_dir():
            print(json.dumps({"error": f"--vs-template dir not found: {tpl}"}))
            return 2
        vs_findings, template_meta = verify_vs_template(ws, tpl)
        findings += vs_findings
    findings += verify_file_level(ws_files, registry)
    findings += verify_clause_level(ws_files, registry)
    findings += verify_custom_refs(ws, registry, args.custom_glob)

    report = {"clean": not findings, "findings": findings, "checked_files": list(registry), "workspace": str(ws)}
    if template_meta:
        report["template_anchor"] = template_meta
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
