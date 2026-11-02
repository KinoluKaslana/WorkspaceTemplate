#!/usr/bin/env python3
"""工作区级 skill 健康检查（确定性，只读）。

检查每个 `.workspace/skills/*/SKILL.md`：
  1. frontmatter 完整（name / description / version），name 与目录名一致；
  2. description 以 `Use when` 或中文触发语开头，长度 ≤ 300 字（触发词太长等于没有）；
  3. version 为 semver；
  4. 正文引用的条款 ID（`R\\d+`）在 `.workspace/rule-clauses.json` 中存在；
  5. 正文引用的仓库内路径（`.workspace/…`、`scripts/…`、`notes/…`、`docs/…`、`tests/…`）存在；
  6. 已登记于 `skills/REGISTRY.md`；
  7. 已登记于 `TOOLS.md`（workspace-skills 小节）。

用法：
  python3 scripts/skill_audit.py [--json] [--strict]
退出码：0 全部通过 / 1 有 findings。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".workspace" / "skills"
REGISTRY = ROOT / "skills" / "REGISTRY.md"
TOOLS = ROOT / "TOOLS.md"
CLAUSES = ROOT / ".workspace" / "rule-clauses.json"
PATH_RE = re.compile(r"(?:\.workspace|scripts|notes|docs|tests|skills)/[A-Za-z0-9_./-]+")


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def clause_ids() -> set[str]:
    try:
        data = json.loads(CLAUSES.read_text(encoding="utf-8"))
    except Exception:
        return set()
    ids: set[str] = set()
    for val in data.values():
        if isinstance(val, dict) and isinstance(val.get("clauses"), dict):
            ids |= set(val["clauses"])
    return ids


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)

    ids = clause_ids()
    registry = REGISTRY.read_text(encoding="utf-8") if REGISTRY.is_file() else ""
    tools = TOOLS.read_text(encoding="utf-8") if TOOLS.is_file() else ""
    rows, bad = [], 0

    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        name = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")
        fm = frontmatter(text)
        f: list[str] = []

        for key in ("name", "description", "version"):
            if not fm.get(key):
                f.append(f"frontmatter 缺 {key}")
        if fm.get("name") and fm["name"] != name:
            f.append(f"name({fm['name']}) 与目录({name}) 不一致")
        desc = fm.get("description", "")
        if desc and not re.match(r"^(Use (when|before|after|if)|当|用于|在)", desc):
            f.append("description 未以触发语开头（Use when/当/用于/在）")
        if len(desc) > 300:
            f.append(f"description 过长（{len(desc)} 字符）")
        if fm.get("version") and not re.fullmatch(r"\d+\.\d+\.\d+", fm["version"]):
            f.append(f"version 非 semver：{fm['version']}")

        used = set(re.findall(r"\bR\d{1,3}\b", text))
        missing_ids = sorted(used - ids)
        if missing_ids:
            f.append(f"引用不存在的条款：{','.join(missing_ids)}")

        # 只核对"仓库内结构性引用"：带扩展名的具体文件，且排除生成物/示例/历史目录
        SKIP = re.compile(r"(backup-|\.bak|miss-log|template-base|\.index\.json|\.aliases\.json|x\.md$)")
        missing_paths = sorted({
            p for p in (x.rstrip("/.-") for x in PATH_RE.findall(text))
            if re.search(r"\.[A-Za-z0-9]{1,8}$", p) and not SKIP.search(p)
            and not (ROOT / p).exists() and "$(" not in p and "<" not in p})
        if missing_paths:
            f.append(f"引用不存在的路径：{','.join(missing_paths[:4])}")

        if f".workspace/skills/{name}/SKILL.md" not in registry:
            f.append("未登记于 skills/REGISTRY.md")
        if name not in tools:
            f.append("未登记于 TOOLS.md")

        bad += bool(f)
        rows.append({"skill": name, "version": fm.get("version"), "ok": not f, "findings": f})

    out = {"skills": len(rows), "failing": bad, "rows": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in rows:
            print(("OK   " if r["ok"] else "WARN "), f'{r["skill"]:32s} v{r["version"]}')
            for x in r["findings"]:
                print("      -", x)
        print(f"共 {len(rows)} 个 skill，{bad} 个有 findings")
    return 0 if (bad == 0 or not a.strict) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
