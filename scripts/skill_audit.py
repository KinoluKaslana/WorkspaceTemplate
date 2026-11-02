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



def sync_registry(rows: list[dict]) -> int:
    """按磁盘目录重写 REGISTRY 的入口表与计数（幂等）。"""
    reg = REGISTRY.read_text(encoding="utf-8")
    names = sorted(r["skill"] for r in rows if r["skill"] != "(registry)")
    desc = {}
    for name in names:
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        d = frontmatter(text).get("description", "").strip().strip('"')
        desc[name] = d
    table = ["| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |", "|---|---|---|---|"]
    for name in names:
        table.append(f"| {name} | {desc[name]} | `.workspace/skills/{name}/SKILL.md` | workspace-skills |")
    new_table = "\n".join(table)
    # 替换「## Skill 入口」下的第一张表
    m = re.search(r"(## Skill 入口\n\n)((?:\|.*\n)+)", reg)
    if not m:
        return 1
    reg = reg[:m.start(2)] + new_table + "\n" + reg[m.end(2):]
    # 同步根行计数
    reg = re.sub(r"已登记（\d+\s*入口[^）]*）",
                 f"已登记（{len(names)} 入口：{' / '.join(names)}）", reg, count=1)
    REGISTRY.write_text(reg, encoding="utf-8")
    return 0

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--sync-registry", action="store_true",
                    help="按磁盘 skill 目录重写 REGISTRY 的「Skill 入口」表与入口计数（不加则不写）")
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

        # 8 维契约必备节（缺项即 finding）
        required = {"触发": r"触发|Use when", "前置": r"前置", "步骤": r"步骤|流程",
                    "边界": r"边界|不负责|不归", "失败": r"失败|陷阱|已知坑",
                    "验收": r"验收|验证", "维护": r"维护"}
        missing_secs = [k for k, pat in required.items() if not re.search(pat, text)]
        if missing_secs:
            f.append("缺契约节：" + ",".join(missing_secs))

        if f".workspace/skills/{name}/SKILL.md" not in registry:
            f.append("未登记于 skills/REGISTRY.md")
        if name not in tools:
            f.append("未登记于 TOOLS.md")

        bad += bool(f)
        rows.append({"skill": name, "version": fm.get("version"), "ok": not f, "findings": f})

    if a.sync_registry:
        rc = sync_registry([x for x in rows if x["skill"] != "(registry)"])
        print(json.dumps({"sync_registry": "ok" if rc == 0 else "failed"}, ensure_ascii=False))
        return rc

    # 登记名称集合 = 磁盘集合（不只比数量）
    disk_names = {r["skill"] for r in rows if r["skill"] != "(registry)"}
    reg_names = set(re.findall(r"\|\s*([a-z0-9-]+)\s*\|[^\n]*\.workspace/skills/\1/SKILL\.md", registry))
    if disk_names != reg_names:
        rows.append({"skill": "(registry)", "version": "—", "ok": False,
                     "findings": [f"REGISTRY 名称集合与目录不一致：缺 {sorted(disk_names - reg_names)}，多 {sorted(reg_names - disk_names)}"]})
        bad += 1

    # 登记计数自洽：REGISTRY 声明「N 入口」须与实际行数一致
    m = re.search(r"已登记（(\d+)\s*入口", registry)
    declared = int(m.group(1)) if m else None
    listed = len(re.findall(r"\.workspace/skills/[A-Za-z0-9_-]+/SKILL\.md", registry))
    if declared is not None and declared != listed:
        rows.append({"skill": "(registry)", "version": "—", "ok": False,
                     "findings": [f"REGISTRY 声明 {declared} 入口，实际列出 {listed} 行"]})
        bad += 1

    out = {"skills": len(rows), "failing": bad, "rows": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in rows:
            print(("OK   " if r["ok"] else "WARN "), f'{r["skill"]:32s} v{r["version"]}')
            for x in r["findings"]:
                print("      -", x)
        print(f"共 {len(rows)} 个 skill，{bad} 个有 findings")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
