#!/usr/bin/env python3
"""Read-only drift check between a WorkspaceTemplate clone and an instantiated workspace.

Outputs a JSON verdict per governance file plus template changelog lines,
so an Agent can apply the template-update skill without guessing.

With --base, adds a mechanical three-way analysis per mixed file:
local_only_lines (must survive the merge), conflict_regions (both sides
changed the same baseline region), and dropped_local_lines when
--verify-backup is given (local lines that vanished during the merge).

Stdlib only; reads directories, writes nothing (except --json path).
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

# Files the template owns outright (safe to replace whole-file).
TEMPLATE_OWNED = {
    "notes/rebuild_index.py",
    "notes/_TEMPLATE.md",
    "scripts/inspect_skills.py",
    ".workspace/skills/template-update/SKILL.md",
    ".workspace/skills/template-update/check_template.py",
}
# Mixed-ownership files (three-way merge; local lines preserved).
MIXED = {
    "AGENT_RULES.md": "AGENT_RULES.md",
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "HERMES.md": "HERMES.md",
    "TOOLS.md": "TOOLS.md",
    ".workspace/bootstrap.json": ".workspace/bootstrap.json",
    "README.md": "README.md",
    "README_EN.md": "README_EN.md",
}
VERSION_RE = re.compile(r"模板政策版本：`(\d+\.\d+\.\d+)`")
VERSION_LINE_RE = re.compile(r"^>.*模板政策版本.*$")
LOCAL_EXT_RE = re.compile(r"\[local-extension\]")


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_version(text: str) -> str | None:
    m = VERSION_RE.search(text)
    return m.group(1) if m else None


def header_lines(text: str) -> list[str]:
    """Lines of the leading blockquote header (start with '>' before first non-'>' line)."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            out.append(line)
        elif out:
            break
        if len(out) > 40:
            break
    return out


def analyze_three_way(base: list[str], local: list[str], template: list[str]) -> dict:
    """Mechanical three-way analysis using stdlib SequenceMatcher.

    Returns local_only_lines (lines the local side added vs base, which must
    survive the merge) and conflict_regions (baseline regions changed by BOTH
    sides). Line-level granularity; deliberately conservative: a local-only
    line inside a both-changed region still counts as conflicted.
    """
    base_hash = {ln: i for i, ln in enumerate(base)}
    sm_lt = difflib.SequenceMatcher(None, base, local, autojunk=False)
    sm_tt = difflib.SequenceMatcher(None, base, template, autojunk=False)

    local_changes: list[tuple[int, int, list[str]]] = []  # (base_lo, base_hi, local_lines)
    for tag, i1, i2, j1, j2 in sm_lt.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            local_changes.append((i1, i2, local[j1:j2]))

    tpl_changes: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in sm_tt.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            tpl_changes.append((i1, i2))

    conflicts: list[dict] = []
    for lo_l, hi_l, lines_l in local_changes:
        for lo_t, hi_t in tpl_changes:
            if lo_l < hi_t and lo_t < hi_l:  # intervals overlap
                conflicts.append(
                    {
                        "base_lines": f"{lo_l + 1}-{hi_l}",
                        "base_excerpt": " | ".join(x.strip() for x in base[lo_l:hi_l])[:120],
                        "local_excerpt": " | ".join(x.strip() for x in lines_l)[:120],
                        "template_excerpt": " | ".join(x.strip() for x in template[lo_t:hi_t])[:120],
                    }
                )
                break

    # local-only added lines: inserts whose base region template did NOT touch
    tpl_intervals = tpl_changes
    local_only: list[str] = []
    for lo_l, hi_l, lines_l in local_changes:
        overlapped = any(lo_l < hi_t and lo_t < hi_l for lo_t, hi_t in tpl_intervals)
        if not overlapped and lines_l:
            local_only.extend(lines_l)

    return {
        "local_only_lines": local_only,
        "conflict_regions": conflicts,
    }


def dropped_local_lines(backup: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    """Lines present in backup (pre-merge local state) but absent after merge.

    Returns (dropped, expected) where expected are template-version-line
    replacements that the skill rules anticipate.
    """
    cur_set = set(current)
    dropped = [ln for ln in backup if ln not in cur_set]
    expected = [ln for ln in dropped if VERSION_LINE_RE.match(ln)]
    return dropped, expected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", required=True, help="WorkspaceTemplate clone dir")
    ap.add_argument("--workspace", required=True, help="instantiated workspace dir")
    ap.add_argument("--base", help="optional .workspace/template-base dir (three-way mode)")
    ap.add_argument("--verify-backup", dest="verify_backup", help="backup dir from step 3 (post-merge invariant check)")
    ap.add_argument("--json", help="optional path to write the JSON report")
    args = ap.parse_args()

    tpl = Path(args.template).resolve()
    ws = Path(args.workspace).resolve()
    base_dir = Path(args.base).resolve() if args.base else None
    backup_dir = Path(args.verify_backup).resolve() if args.verify_backup else None
    missing = [str(d) for d in (tpl, ws) if not d.is_dir()]
    if base_dir is not None and not base_dir.is_dir():
        missing.append(str(base_dir))
    if missing:
        print(json.dumps({"error": f"not a directory: {missing}"}))
        return 2

    tpl_rules = (tpl / "AGENT_RULES.md").read_text(encoding="utf-8")
    ws_rules = (ws / "AGENT_RULES.md").read_text(encoding="utf-8")
    tpl_ver = policy_version(tpl_rules) or "0.0.0"
    ws_ver = policy_version(ws_rules) or "0.0.0"

    boot: dict = {}
    boot_path = ws / ".workspace" / "bootstrap.json"
    if boot_path.is_file():
        try:
            boot = json.loads(boot_path.read_text(encoding="utf-8"))
        except ValueError:
            boot = {"_parse_error": True}
    base_ver = (boot.get("template") or {}).get("current_policy_version")

    tpl_header = header_lines(tpl_rules)
    ws_header = header_lines(ws_rules)
    new_lines = [ln for ln in tpl_header if ln not in set(ws_header)]
    local_lines = [ln for ln in ws_header if ln not in set(tpl_header) and not LOCAL_EXT_RE.search(ln)]

    files: dict[str, dict] = {}
    for rel in sorted(TEMPLATE_OWNED | set(MIXED)):
        t, w = tpl / rel, ws / rel
        t_hash, w_hash = sha256(t), sha256(w)
        if t_hash is None:
            continue
        entry: dict = {}
        if w_hash is None:
            entry = {"verdict": "template_new", "note": "workspace lacks this file (create from template)"}
        elif t_hash == w_hash:
            entry = {"verdict": "in_sync", "note": ""}
        elif rel in TEMPLATE_OWNED:
            entry = {"verdict": "template_new", "note": "template-owned file changed: replace whole-file"}
        else:
            entry = {"verdict": "drift", "note": "mixed ownership: three-way merge (template lines in, local lines kept)"}
        entry["template_sha"] = t_hash
        entry["workspace_sha"] = w_hash

        # three-way analysis for mixed files when BASE is available
        # (bootstrap.json is excluded: its local values dominate; the
        # --verify-backup key check covers it instead)
        if base_dir is not None and rel in MIXED and rel != ".workspace/bootstrap.json" and w_hash is not None and t_hash != w_hash:
            b = base_dir / rel
            if b.is_file():
                analysis = analyze_three_way(
                    b.read_text(encoding="utf-8").splitlines(),
                    w.read_text(encoding="utf-8").splitlines(),
                    t.read_text(encoding="utf-8").splitlines(),
                )
                entry["three_way"] = analysis
            else:
                entry["three_way"] = {"error": f"missing in base: {rel}"}

        # post-merge invariant check when backup is given
        if backup_dir is not None and rel in MIXED:
            bk = backup_dir / Path(rel).name
            cur = ws / rel
            if bk.is_file() and cur.is_file():
                dropped, expected = dropped_local_lines(
                    bk.read_text(encoding="utf-8").splitlines(),
                    cur.read_text(encoding="utf-8").splitlines(),
                )
                entry["dropped_local_lines"] = dropped
                entry["expected_drops"] = expected
            elif bk.exists():
                pass  # e.g. bootstrap.json handled by name
        files[rel] = entry

    # bootstrap.json invariant handled specially (values change by design)
    if backup_dir is not None:
        bk = backup_dir / "bootstrap.json"
        cur = ws / ".workspace" / "bootstrap.json"
        if bk.is_file() and cur.is_file():
            entry = files.setdefault(
                ".workspace/bootstrap.json",
                {"verdict": "drift", "note": "post-merge check", "template_sha": None, "workspace_sha": None},
            )
            try:
                old_boot = json.loads(bk.read_text(encoding="utf-8"))
                new_boot = json.loads(cur.read_text(encoding="utf-8"))
            except ValueError:
                entry["dropped_local_lines"] = ["<bootstrap parse error>"]
            else:
                dropped_keys = [k for k in old_boot if k not in new_boot and k not in ("template",)]
                # python/skills top-level keys are workspace-owned values
                entry["dropped_local_lines"] = [f"missing key: {k}" for k in dropped_keys]
                entry["expected_drops"] = []

    report = {
        "template_version": tpl_ver,
        "workspace_version": ws_ver,
        "base_version": base_ver,
        "version_gap": {"template": tpl_ver, "workspace": ws_ver, "base": base_ver},
        "template_new_lines": new_lines,
        "workspace_local_lines": local_lines,
        "files": files,
        "rebuild_script_synced": sha256(tpl / "notes/rebuild_index.py") == sha256(ws / "notes/rebuild_index.py"),
        "template_dir": str(tpl),
        "workspace_dir": str(ws),
    }
    if backup_dir is not None:
        report["verify_backup"] = str(backup_dir)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
