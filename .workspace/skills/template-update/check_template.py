#!/usr/bin/env python3
"""Read-only drift check between a WorkspaceTemplate clone and an instantiated workspace.

Outputs a JSON verdict per governance file plus template changelog lines,
so an Agent can apply the template-update skill without guessing.

Stdlib only; reads two directories, writes nothing (except --json path).
"""

from __future__ import annotations

import argparse
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
SHA_RE = re.compile(r"重建脚本 SHA-256 \| `([0-9a-f]{64})`")


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", required=True, help="WorkspaceTemplate clone dir")
    ap.add_argument("--workspace", required=True, help="instantiated workspace dir")
    ap.add_argument("--json", help="optional path to write the JSON report")
    args = ap.parse_args()

    tpl = Path(args.template).resolve()
    ws = Path(args.workspace).resolve()
    missing = [d for d in (tpl, ws) if not d.is_dir()]
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
    tpl_header_set = set(tpl_header)
    ws_header = header_lines(ws_rules)
    ws_header_set = set(ws_header)
    new_lines = [ln for ln in tpl_header if ln not in ws_header_set]
    local_lines = [ln for ln in ws_header if ln not in tpl_header_set and "[local-extension]" not in ln]

    files: dict[str, dict] = {}
    for rel in sorted(TEMPLATE_OWNED | set(MIXED)):
        t, w = tpl / rel, ws / rel
        t_hash, w_hash = sha256(t), sha256(w)
        if t_hash is None:
            continue
        if w_hash is None:
            verdict = "template_new"
            note = "workspace lacks this file (create from template)"
        elif t_hash == w_hash:
            verdict = "in_sync"
            note = ""
        elif rel in TEMPLATE_OWNED:
            verdict = "template_new"
            note = "template-owned file changed: replace whole-file"
        else:
            verdict = "drift"
            note = "mixed ownership: three-way merge (template lines in, local lines kept)"
        files[rel] = {"verdict": verdict, "note": note, "template_sha": t_hash, "workspace_sha": w_hash}

    # rebuild script consistency between the two trees
    tpl_sha = sha256(tpl / "notes/rebuild_index.py")
    ws_sha = sha256(ws / "notes/rebuild_index.py")
    report = {
        "template_version": tpl_ver,
        "workspace_version": ws_ver,
        "base_version": base_ver,
        "version_gap": {"template": tpl_ver, "workspace": ws_ver, "base": base_ver},
        "template_new_lines": new_lines,
        "workspace_local_lines": local_lines,
        "files": files,
        "rebuild_script_synced": tpl_sha == ws_sha,
        "template_dir": str(tpl),
        "workspace_dir": str(ws),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
