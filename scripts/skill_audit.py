#!/usr/bin/env python3
"""工作区级 skill 健康检查（确定性，只读）。

两层：
  **模板层** `.workspace/skills/*/SKILL.md`（随模板分发，实例只读）
  **本地层** `.workspace/skills-local/{<name>,_drafts/<name>,_retired/<name>}/SKILL.md`（用户所有）

共同检查（两层都做）：
  1. frontmatter 完整（name / description / version），name 与目录名一致；
  2. description 以 `Use when` 或中文触发语开头，长度 ≤ 300；
  3. version 为 semver；正文引用的条款 ID 与仓库内路径存在；
  4. 8 维契约节齐全（职责边界 / 触发 / 前置 / 输入输出 / 流程 / 失败 / 验收 / 维护）。

本地层额外检查：
  - `status` ∈ {draft, approved, deprecated, removed}，且与所在目录一致
    （`_drafts/` 只能 draft；可路由目录不能放 draft；`_retired/` 只能 deprecated/removed）；
  - `approved` 必须有 `approved_by`、`approved_at`、`approved_skill_sha256`（= 当前字节 sha256），
    以及审批来源（`approved_directive` 用户原话，或存在的 `review_artifact` 文件）。

用法：
  python3 scripts/skill_audit.py [--json] [--strict] [--tools-snippet] [--sync-registry]
退出码：0 全部通过 / 1 有 findings。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

FORCE_PRUNE = False
ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".workspace" / "skills"
SKILLS_LOCAL = ROOT / ".workspace" / "skills-local"
BOOTSTRAP = ROOT / ".workspace" / "bootstrap.json"
REGISTRY = ROOT / "skills" / "REGISTRY.md"
TOOLS = ROOT / "TOOLS.md"
CLAUSES = ROOT / ".workspace" / "rule-clauses.json"
PATH_RE = re.compile(r"(?:\.workspace|scripts|notes|docs|tests|skills)/[A-Za-z0-9_./-]+")

# 目录名允许的字符：与 `*/SKILL.md` 的发现能力一致（不窄于发现，否则「磁盘有、正则匹配不到」
# 会被报成集合不一致，且只能靠改名以外的手段缓解）。
NAME_CHARS = r"[A-Za-z0-9._-]+"
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_STATES = {"draft", "approved", "deprecated", "removed"}
ROUTABLE_STATES = {"approved"}


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


def _cells(line: str) -> list[str]:
    """按未转义的 `|` 切列（描述里的 `\\|` 不能撕开表格）。"""
    if not line.strip().startswith("|"):
        return []
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _section(registry: str, title: str) -> str:
    """取受管小节。标题行允许尾部文本（如历史写法 `## 本地层入口（workspace-skills-local）`）。"""
    m = re.search(rf"## {re.escape(title)}[^\n]*\n(?:.*\n)*?(?=\n## |\Z)", registry)
    return m.group(0) if m else ""


def _section_count(registry: str, title: str) -> int:
    return len(re.findall(rf"^## {re.escape(title)}[^\n]*$", registry, re.M))


def _path_name(cell: str, *prefixes: str) -> str | None:
    """从入口列取目录名：不靠字符类（发现器是 `*/SKILL.md`，正则不能比它窄）。"""
    for pre in prefixes:
        m = re.search(rf"{re.escape(pre)}([^/`\s]+)/SKILL\.md", cell)
        if m:
            return m.group(1)
    return None


TOOLS_BEGIN = "<!-- local-skills:begin -->"
TOOLS_END = "<!-- local-skills:end -->"


def _in_tools_block(tools: str, name: str) -> bool:
    m = re.search(rf"{re.escape(TOOLS_BEGIN)}(?:.*?){re.escape(TOOLS_END)}", tools, re.S)
    if not m:
        return False
    return bool(re.search(rf"^\|[^\n]*\*\*{re.escape(name)}\*\*", m.group(0), re.M))


def _artifact_ok(art: str) -> bool:
    """评审产物必须是工作区内相对路径：绝对路径会让 `ROOT / art` 直接丢弃 ROOT。"""
    if not art or os.path.isabs(art):
        return False
    target = (ROOT / art).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        return False
    return target.is_file()


def contract_findings(name: str, text: str, fm: dict, ids: set[str], require_io: bool = False,
                     minimal: bool = False) -> list[str]:
    """两层共用的契约检查。"""
    f: list[str] = []
    for key in ("name", "description", "version"):
        if not fm.get(key):
            f.append(f"frontmatter 缺 {key}")
    if fm.get("name") and fm["name"] != name:
        f.append(f"name({fm['name']}) 与目录({name}) 不一致")
    if not minimal:
        desc = fm.get("description", "")
        if desc and not re.match(r"^(Use (when|before|after|if)|当|用于|在)", desc):
            f.append("description 未以触发语开头（Use when/当/用于/在）")
        if len(desc) > 300:
            f.append(f"description 过长（{len(desc)} 字符）")
    if fm.get("version") and not re.fullmatch(r"\d+\.\d+\.\d+", fm["version"]):
        f.append(f"version 非 semver：{fm['version']}")
    missing_ids = sorted(set(re.findall(r"\bR\d{1,3}\b", text)) - ids)
    if missing_ids:
        f.append(f"引用不存在的条款：{','.join(missing_ids)}")
    SKIP = re.compile(r"(backup-|\.bak|miss-log|template-base|\.index\.json|\.aliases\.json|x\.md$)")
    missing_paths = sorted({
        p for p in (x.rstrip("/.-") for x in PATH_RE.findall(text))
        if re.search(r"\.[A-Za-z0-9]{1,8}$", p) and not SKIP.search(p)
        and not (ROOT / p).exists() and "$(" not in p and "<" not in p})
    if missing_paths:
        f.append(f"引用不存在的路径：{','.join(missing_paths[:4])}")
    if minimal:
        return f
    required = {"触发": r"触发|Use when", "前置": r"前置", "步骤": r"步骤|流程",
                "边界": r"边界|不负责|不归", "失败": r"失败|陷阱|已知坑",
                "验收": r"验收|验证", "维护": r"维护"}
    if require_io:
        required["输入输出"] = r"输入输出|读：|写："
    missing_secs = [k for k, pat in required.items() if not re.search(pat, text)]
    if missing_secs:
        f.append("缺契约节：" + ",".join(missing_secs))
    return f


def template_records() -> list[dict]:
    out = []
    for md in sorted(SKILLS.glob("*/SKILL.md")):
        out.append({"skill": md.parent.name, "path": md, "layer": "template",
                    "state": "approved", "area": "active", "routable": True})
    return out


def local_records() -> list[dict]:
    """本地层：active / _drafts / _retired 三个物理区，目录即状态主事实。"""
    out = []
    if not SKILLS_LOCAL.is_dir():
        return out
    for area, sub in (("active", SKILLS_LOCAL), ("draft", SKILLS_LOCAL / "_drafts"),
                      ("retired", SKILLS_LOCAL / "_retired")):
        for md in sorted(sub.glob("*/SKILL.md")):
            text = md.read_text(encoding="utf-8")
            state = (frontmatter(text).get("status") or "").strip()
            out.append({"skill": md.parent.name, "path": md, "layer": "local",
                        "state": state,
                        "area": area, "routable": area == "active" and state in ROUTABLE_STATES})
    return out


def audit_local_extra(rec: dict, fm: dict, text: str) -> list[str]:
    f: list[str] = []
    state, area = rec["state"], rec["area"]
    if state not in LOCAL_STATES:
        f.append(f"status 缺失或非法：{state!r}（允许 {sorted(LOCAL_STATES)}）")
    elif area == "draft" and state != "draft":
        f.append(f"`_drafts/` 里只能是 draft，实际 {state!r}")
    elif area == "active" and state == "draft":
        f.append("可路由目录里不能放 draft（未批准不得路由）")
    elif area == "active" and state != "approved":
        f.append(f"可路由目录里只能是 approved，实际 {state!r}")
    elif area == "retired" and state not in ("deprecated", "removed"):
        f.append(f"`_retired/` 里只能是 deprecated/removed，实际 {state!r}")
    if state == "approved":
        # 审批证据放同目录 APPROVAL.json：把 sha256 写进被哈希的文件本身是自引用，永远对不上
        ap = rec["path"].parent / "APPROVAL.json"
        if not ap.is_file():
            f.append("approved 缺 APPROVAL.json（审批证据）")
        else:
            try:
                ev = json.loads(ap.read_text(encoding="utf-8"))
            except Exception:
                ev = {}
                f.append("APPROVAL.json 不是合法 JSON")
            for key in ("approved_by", "approved_at", "approved_skill_sha256"):
                if not ev.get(key):
                    f.append(f"APPROVAL.json 缺 {key}")
            want = ev.get("approved_skill_sha256")
            if want:
                actual = hashlib.sha256(rec["path"].read_bytes()).hexdigest()
                if want != actual:
                    f.append("approved_skill_sha256 与当前 SKILL.md 字节不符（内容已变，需重审）")
            art = ev.get("review_artifact")
            if not ev.get("approved_directive") and not (art and _artifact_ok(art)):
                f.append("APPROVAL.json 需 `approved_directive`（用户原话）或存在的 `review_artifact`")
    return f


def _local_table(registry: str) -> dict[str, str]:
    """解析「## 本地层入口」表：name -> state。"""
    out: dict[str, str] = {}
    for line in _section(registry, "本地层入口").splitlines():
        cells = _cells(line)
        if len(cells) < 4:
            continue
        found = _path_name(cells[3], ".workspace/skills-local/_retired/", ".workspace/skills-local/")
        if not found:
            continue
        name = cells[0].strip("`").strip()
        if name != found:
            out[f"!mismatch:{name}"] = found
            continue
        out[name] = cells[1].strip("`").strip()
    return out


def registry_invariants(rows: list[dict], registry: str) -> list[str]:
    """模板层两条不变量：名称集合 == 磁盘集合；声明计数 == 入口节实际行数。"""
    problems: list[str] = []
    disk_names = {r["skill"] for r in rows if r.get("layer", "template") == "template"}
    section = _section(registry, "Skill 入口")
    reg_names: set[str] = set()
    for line in section.splitlines():
        cells = _cells(line)
        if len(cells) < 3:
            continue
        name = cells[0].strip("`").strip()
        found = _path_name(cells[2], ".workspace/skills/")
        if not found:
            continue
        if found != name:
            problems.append(f"入口行名称与路径不一致：首列 {name!r} / 路径 {found!r}")
        reg_names.add(name)
    if disk_names != reg_names:
        problems.append(f"REGISTRY 名称集合与目录不一致：缺 {sorted(disk_names - reg_names)}，多 {sorted(reg_names - disk_names)}")
    if _section_count(registry, "Skill 入口") > 1:
        problems.append("REGISTRY 出现多个 `## Skill 入口` 节（旧节会成为死内容）")
    m = re.search(r"已登记（(\d+)\s*入口", registry)
    listed = len([ln for ln in section.splitlines() if _path_name(ln, ".workspace/skills/")])
    if m is None:
        problems.append("REGISTRY 缺『已登记（N 入口…）』计数行")
    elif int(m.group(1)) != listed:
        problems.append(f"REGISTRY 声明 {m.group(1)} 入口，实际列出 {listed} 行")
    return problems


def local_registry_invariants(local: list[dict], registry: str) -> list[str]:
    """本地层：可路由/退役条目必须与「## 本地层入口」表一一对应（草稿不登记）。"""
    problems: list[str] = []
    expected = {r["skill"]: r["state"] for r in local if r["area"] in ("active", "retired")}
    table = _local_table(registry)
    bad = [k for k in table if k.startswith("!mismatch:")]
    if bad:
        problems.append("本地层入口行名称与路径不一致：" + ", ".join(k.split(":", 1)[1] for k in bad))
    table = {k: v for k, v in table.items() if not k.startswith("!mismatch:")}
    if set(expected) != set(table):
        problems.append(f"本地层登记与目录不一致：缺 {sorted(set(expected) - set(table))}，多 {sorted(set(table) - set(expected))}")
    for name in sorted(set(expected) & set(table)):
        if expected[name] != table[name]:
            problems.append(f"本地层状态不一致：{name} 目录={expected[name]!r} 登记={table[name]!r}")
    if _section_count(registry, "本地层入口") > 1:
        problems.append("REGISTRY 出现多个 `## 本地层入口` 节（旧节会成为死内容）")
    m = re.search(r"已登记（(\d+)\s*个本地\s*skill", registry)
    if expected and m is None:
        problems.append("REGISTRY 缺本地层计数行（`已登记（N 个本地 skill）`）")
    elif m and int(m.group(1)) != len(table):
        problems.append(f"本地层声明 {m.group(1)} 个，表格实际 {len(table)} 行")
    return problems


def sync_registry(rows: list[dict], local: list[dict]) -> int:
    """按磁盘重写模板节 + 本地节 + 两处计数（幂等）；文件不存在时新建。"""
    names = sorted(r["skill"] for r in rows if r.get("layer", "template") == "template")
    if not REGISTRY.is_file():
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(
            "# Skills 路由登记表\n\n"
            "> 本文件只保存路由视图；规范路径、版本/哈希、信任边界与核验日期以 `TOOLS.md` 为准。\n\n"
            "## 已登记 skill roots\n\n"
            "| 命名空间 | Skill root | 根路由/索引 | 路由状态 | TOOLS 登记 |\n|---|---|---|---|---|\n"
            f"| `workspace-skills` | `.workspace/skills/` | 无路由器；每 skill 独立登记 "
            f"| 已登记（{len(names)} 入口：{' / '.join(names)}） | `TOOLS.md` |\n\n"
            "## Skill 入口\n\n", encoding="utf-8")
    reg = REGISTRY.read_text(encoding="utf-8")
    old_local = {k for k in _local_table(reg) if not k.startswith("!mismatch:")}
    keep_local = [r for r in local if r["area"] in ("active", "retired")]
    if old_local and not keep_local and not FORCE_PRUNE:
        print("拒绝清空：本地层登记表非空（" + ", ".join(sorted(old_local)) +
              "），但 `.workspace/skills-local/` 下找不到对应文件。"
              "先恢复目录，或确认后用 --force-prune-local。", file=sys.stderr)
        return 4

    desc = {n: frontmatter((SKILLS / n / "SKILL.md").read_text(encoding="utf-8")).get("description", "").strip().strip('"')
            for n in names}
    table = ["| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |", "|---|---|---|---|"]
    for name in names:
        table.append(f"| {name} | {desc[name].replace('|', chr(92) + '|')} | `.workspace/skills/{name}/SKILL.md` | workspace-skills |")
    section = "## Skill 入口\n\n" + "\n".join(table) + "\n"
    m = re.search(r"## Skill 入口[^\n]*\n(?:.*\n)*?(?=\n## |\Z)", reg)
    reg = (reg[:m.start()] + section + reg[m.end():]) if m else (reg.rstrip() + "\n\n" + section)
    reg = re.sub(r"已登记（\d+\s*入口[^）]*）",
                 f"已登记（{len(names)} 入口：{' / '.join(names)}）", reg, count=1)

    entries = [r for r in local if r["area"] in ("active", "retired")]
    lt = ["| 路由 ID | 状态 | 触发摘要 | 入口 |", "|---|---|---|---|"]
    for r in sorted(entries, key=lambda x: x["skill"]):
        text = r["path"].read_text(encoding="utf-8")
        d = frontmatter(text).get("description", "").strip().strip('"').replace("|", chr(92) + "|")
        sub = "_retired/" if r["area"] == "retired" else ""
        lt.append(f"| {r['skill']} | {r['state'] or 'unknown'} | {d} | `.workspace/skills-local/{sub}{r['skill']}/SKILL.md` |")
    lsection = "## 本地层入口\n\n> 由 `skill_audit.py --sync-registry` 生成；只有 `approved` 可作路由依据。\n\n" + "\n".join(lt) + "\n"
    m = re.search(r"## 本地层入口[^\n]*\n(?:.*\n)*?(?=\n## |\Z)", reg)
    reg = (reg[:m.start()] + lsection + reg[m.end():]) if m else (reg.rstrip() + "\n\n" + lsection)

    # 根表里补一行本地命名空间（只补一次）
    if "`workspace-skills-local`" not in reg:
        anchor = re.search(r"^\| `workspace-skills` \|[^\n]*\n", reg, re.M)
        row = (f"| `workspace-skills-local` | `.workspace/skills-local/` | 无路由器；每 skill 独立登记（用户所有） "
               f"| 已登记（{len(entries)} 个本地 skill） | `TOOLS.md` §4.6 |\n")
        if anchor:
            reg = reg[:anchor.end()] + row + reg[anchor.end():]
        else:
            reg = reg.rstrip() + "\n" + row
    reg = re.sub(r"已登记（\d+\s*个本地\s*skill[^）]*）", f"已登记（{len(entries)} 个本地 skill）", reg, count=1)
    REGISTRY.write_text(reg, encoding="utf-8")
    sync_tools_block(entries)
    sync_bootstrap_roots(bool(entries))
    return 0


def sync_tools_block(entries: list[dict]) -> None:
    """TOOLS.md 的本地层块：与 REGISTRY 同源生成，避免「同名模板行顶替」的假登记。"""
    if not TOOLS.is_file():
        return
    tools = TOOLS.read_text(encoding="utf-8")
    if not entries and TOOLS_BEGIN not in tools:
        return                                   # 空本地层且没有块：不往模板文档里塞噪声
    lines = [TOOLS_BEGIN, ""]
    if entries:
        lines += ["| Skill | 入口 |", "|---|---|"]
        for r in sorted(entries, key=lambda x: x["skill"]):
            sub = "_retired/" if r["area"] == "retired" else ""
            lines.append(f"| **{r['skill']}** | `.workspace/skills-local/{sub}{r['skill']}/SKILL.md`（{r['state'] or 'unknown'}） |")
        lines.append("")
    lines.append(TOOLS_END)
    block = "\n".join(lines)
    m = re.search(rf"{re.escape(TOOLS_BEGIN)}.*?{re.escape(TOOLS_END)}", tools, re.S)
    if m:
        tools = tools[:m.start()] + block + tools[m.end():]
    else:
        tools = tools.rstrip() + "\n\n## 本地层 skill 入口（自动生成，勿手改）\n\n" + block + "\n"
    TOOLS.write_text(tools, encoding="utf-8")


def sync_bootstrap_roots(has_local: bool) -> None:
    """bootstrap.skills.registered_roots 与磁盘一致：有本地层就登记 root。"""
    if not BOOTSTRAP.is_file():
        return
    try:
        data = json.loads(BOOTSTRAP.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    roots = data.setdefault("skills", {}).setdefault("registered_roots", [])
    before = list(roots)
    if has_local and "workspace-skills-local" not in roots:
        roots.append("workspace-skills-local")
    if not has_local and "workspace-skills-local" in roots:
        roots.remove("workspace-skills-local")
    if roots == before:
        return                                    # 未变不重写，避免无意义 diff
    BOOTSTRAP.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--tools-snippet", action="store_true",
                    help="打印缺失的 TOOLS.md 登记行（不写文件）")
    ap.add_argument("--sync-registry", action="store_true",
                    help="按磁盘重写 REGISTRY 的模板节 / 本地节与计数（不加则不写）")
    ap.add_argument("--force-prune-local", action="store_true",
                    help="本地目录确已清空时，允许把本地层登记表重写为空")
    a = ap.parse_args(argv)
    global FORCE_PRUNE
    FORCE_PRUNE = a.force_prune_local

    ids = clause_ids()
    registry = REGISTRY.read_text(encoding="utf-8") if REGISTRY.is_file() else ""
    tools = TOOLS.read_text(encoding="utf-8") if TOOLS.is_file() else ""
    local = local_records()
    rows: list[dict] = []
    bad = 0

    for rec in template_records() + local:
        name, text = rec["skill"], rec["path"].read_text(encoding="utf-8")
        fm = frontmatter(text)
        f: list[str] = []
        # 退役项：保留 frontmatter / name==目录 / version 校验，只豁免 8 维契约与触发语
        f += contract_findings(name, text, fm, ids, require_io=(rec["layer"] == "local"),
                               minimal=(rec["area"] == "retired"))
        if rec["layer"] == "local":
            f += audit_local_extra(rec, fm, text)
            sub = "_retired/" if rec["area"] == "retired" else ""
            if rec["area"] in ("active", "retired") and f".workspace/skills-local/{sub}{name}/SKILL.md" not in registry:
                f.append("未登记于 skills/REGISTRY.md（本地层入口）")
            if rec["area"] in ("active", "retired") and not _in_tools_block(tools, name):
                f.append("未登记于 TOOLS.md 本地层块（`--sync-registry` 会生成）")
        else:
            if f".workspace/skills/{name}/SKILL.md" not in registry:
                f.append("未登记于 skills/REGISTRY.md")
            if not re.search(rf"^\|[^\n]*\*\*{re.escape(name)}(?=[\s*])", tools, re.M):
                f.append("未登记于 TOOLS.md")
        bad += bool(f)
        rows.append({"skill": name, "version": fm.get("version"), "ok": not f, "findings": f,
                     "layer": rec["layer"], "state": rec["state"], "area": rec["area"]})

    if a.tools_snippet:
        missing = [r for r in rows if any(x.startswith("未登记于 TOOLS.md") for x in r["findings"])]
        for r in missing:
            base = "`.workspace/skills-local" if r["layer"] == "local" else "`.workspace/skills"
            sub = "_retired/" if r.get("area") == "retired" else ""
            print(f"| **{r['skill']}** | 入口 {base}/{sub}{r['skill']}/SKILL.md`（v{r['version']}）。触发见其 frontmatter description |")
        if not missing:
            print("(none: TOOLS.md 已覆盖全部 skill)")
        other = [f for r in rows for f in r["findings"] if not f.startswith("未登记于 TOOLS.md")]
        if other:
            print(f"# 另有 {len(other)} 条非 TOOLS 的 findings（跑 --strict 逐条看）：")
            for x in other[:5]:
                print("#  -", x)
        return 0

    if a.sync_registry:
        rc = sync_registry(rows, local)
        after = REGISTRY.read_text(encoding="utf-8") if REGISTRY.is_file() else ""
        problems = registry_invariants(rows, after) + local_registry_invariants(local, after)
        other = [f for r in rows for f in r["findings"]]
        ok = rc == 0 and not problems
        print(json.dumps({"sync_registry": "ok" if ok else "failed",
                          "registry_problems": problems,
                          "other_findings": other}, ensure_ascii=False))
        if rc != 0:
            return rc                                  # 4 = 拒绝清空用户登记，不能被压成 1
        return 0 if ok else 1

    # 命名约定：模板层与本地层都按 lowercase-kebab；本地层是硬要求（由 authoring skill 生成即合规），
    # 模板层历史遗留只提示。仅在 --strict 下判失败。
    boot_problem: str | None = None
    if a.strict:
        entries = [r for r in local if r["area"] in ("active", "retired")]
        if entries and BOOTSTRAP.is_file():
            try:
                roots = json.loads(BOOTSTRAP.read_text(encoding="utf-8-sig")).get("skills", {}).get("registered_roots", [])
            except Exception:
                roots = []
            if "workspace-skills-local" not in roots:
                boot_problem = "本地层存在但 bootstrap.skills.registered_roots 未登记 workspace-skills-local"
        for r in rows:
            if not KEBAB_RE.match(r["skill"]):
                r["findings"].append(f"目录名 {r['skill']!r} 不符 lowercase-kebab 约定（建议改名）")
                if r["ok"]:
                    r["ok"] = False
                    bad += 1

    problems = registry_invariants(rows, registry) + local_registry_invariants(local, registry)
    if boot_problem:
        problems.append(boot_problem)
    for p in problems:
        rows.append({"skill": "(registry)", "version": "—", "ok": False, "findings": [p]})
        bad += 1

    out = {"skills": len(rows), "failing": bad, "rows": rows}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in rows:
            tag = "" if r.get("layer", "template") == "template" else f'  [{r.get("state")}]'
            print(("OK   " if r["ok"] else "WARN "), f'{r["skill"]:32s} v{r["version"]}{tag}')
            for x in r["findings"]:
                print("      -", x)
        print(f"共 {len(rows)} 个 skill，{bad} 个有 findings")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
