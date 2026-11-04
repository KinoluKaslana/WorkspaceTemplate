#!/usr/bin/env python3
"""文档与工作区一致性检查（防止 README/docs 写飘）。

D1 所有相对链接可解析（README.md / README_EN.md / docs/*.md）
D2 docs/skills.md 覆盖磁盘上的每个 skill，且版本号与 SKILL.md frontmatter 一致
D3 docs/tools.md 覆盖 scripts/ 下每个 .py
D4 政策版本没有漂移（docs/architecture.md、README/README_EN 徽章）
D5 README.md 提到每个 skill 名
D6/D7 计数（TEMPLATE_FILES、skills、scripts）与磁盘一致
D8 docs/skills.md 表格里的版本与 SKILL.md 一致
D9 `.github/` 层不进模板层
D10 shields 静态徽章 URL 结构合法（`/badge/label-message-color`）
运行：python3 -B tests/test_docs_consistency.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "README_EN.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md",
        ROOT / "UPGRADE.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
WORD_NUM = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6,
            "Seven": 7, "Eight": 8, "Nine": 9, "Ten": 10, "Fourteen": 14}


def skills() -> dict[str, str]:
    out = {}
    for p in (ROOT / ".workspace" / "skills").glob("*/SKILL.md"):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^version:\s*(\S+)", text, re.M)
        out[p.parent.name] = m.group(1) if m else ""
    return out


def _is_template_repo() -> bool:
    """本仓是模板正主，还是派生实例。

    派生实例的 `README.md` / `README_EN.md` 属用户首页（`template_sync` 明确豁免），
    计数与版本列由用户自己维护；`.github/` 也不下发。拿模板正主的标准去要求实例，
    会把刚同步干净的实例判红——所以这些检查只在模板仓严格生效。
    """
    boot = ROOT / ".workspace" / "bootstrap.json"
    try:
        data = json.loads(boot.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (data.get("workspace_status") or "").strip() == "template"


class DocsConsistency(unittest.TestCase):
    def test_d1_links_resolve(self):
        bad = []
        for md in DOCS:
            base = md.parent
            for target in LINK.findall(md.read_text(encoding="utf-8")):
                t = target.split("#")[0].strip()
                if not t or t.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (base / t).exists():
                    bad.append(f"{md.relative_to(ROOT)} -> {target}")
        self.assertEqual(bad, [], "断链：\n" + "\n".join(bad))

    def test_d2_skills_doc_covers_all(self):
        doc = (ROOT / "docs" / "skills.md").read_text(encoding="utf-8")
        missing = [n for n in skills() if n not in doc]
        self.assertEqual(missing, [], f"docs/skills.md 未覆盖：{missing}")
        # 版本号一致（允许 3.2.1 这类点号版本直接出现）
        wrong = [f"{n}: doc 未写 {v}" for n, v in skills().items()
                 if v and f"{n}" in doc and v not in doc.split(n, 1)[-1][:400]]
        self.assertEqual(wrong, [], "; ".join(wrong))

    def test_d3_tools_doc_covers_all_scripts(self):
        doc = (ROOT / "docs" / "tools.md").read_text(encoding="utf-8")
        missing = [p.name for p in (ROOT / "scripts").glob("*.py") if p.name not in doc]
        self.assertEqual(missing, [], f"docs/tools.md 未覆盖脚本：{missing}")

    def test_d4_no_stale_version_snapshots(self):
        """文档不得写死易漂移的版本快照；版本只以 AGENT_RULES.md 为准。"""
        policy = re.search(r"模板政策版本：`([\d.]+)`", (ROOT / "AGENT_RULES.md").read_text(encoding="utf-8")).group(1)
        offenders = []
        strict = _is_template_repo()
        for md in DOCS:
            # 实例的 README 是用户首页（同步豁免）；模板正主的 README 必须被查——徽章里的
            # `policy-vX.Y.Z` 曾经一路漂到 v3.5.0 而没人管，正是这条检查漏了 README 与徽章格式。
            if md.name.startswith("README") and not strict:
                continue
            text = md.read_text(encoding="utf-8")
            for m in re.finditer(r"政策 \*\*([\d.]+)\*\*|policy \*\*([\d.]+)\*\*"
                                 r"|policy-v([\d.]+)-|badge/policy-v([\d.]+)", text):
                v = next(g for g in m.groups() if g)
                if v != policy:
                    offenders.append(f"{md.relative_to(ROOT)}: 写了旧版本 {v}（当前 {policy}）")
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_d5_readme_lists_all_skills(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        missing = [n for n in skills() if n not in readme]
        self.assertEqual(missing, [], f"README 未提到：{missing}")

    def test_d6_counts_match_disk(self):
        """文档里的计数必须与磁盘一致（曾出现 docs 写 6 个 skill / 53 个文件）。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        from verify_rules import TEMPLATE_FILES  # noqa: E402
        n_skills = len(skills())
        n_tpl = len(TEMPLATE_FILES)
        arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        m = re.search(r"`TEMPLATE_FILES` \*\*(\d+)\*\* 项", arch)
        self.assertEqual(int(m.group(1)), n_tpl if m else -1,
                         f"architecture 的 TEMPLATE_FILES 计数应为 {n_tpl}")
        m = re.search(r"skills \*\*(\d+)\*\* 个", arch)
        self.assertEqual(int(m.group(1)), n_skills if m else -1,
                         f"architecture 的 skills 计数应为 {n_skills}")
        m = re.search(r"# Workspace skills（(\d+) 个）", (ROOT / "docs" / "skills.md").read_text(encoding="utf-8"))
        self.assertEqual(int(m.group(1)), n_skills if m else -1,
                         f"docs/skills.md 标题计数应为 {n_skills}")

    def test_d7_prose_counts_match_disk(self):
        """散文里的计数也必须与磁盘一致。

        曾经的漏网：`docs/README.md` 写「6 个 skill / 10 个脚本」、`README_EN.md` 写「Six skills」、
        `docs/architecture.md` 的架构图写「R1..R108 / scripts/（10 个）」——d6 只查了少数几处
        固定格式，散文计数就一路漂到发布前才被审计席抓到。这里按「数字 + 单位」扫全部文档，
        少写、多写、写旧版本号都会红。
        """
        disk_skills = len(skills())
        disk_scripts = len(list((ROOT / "scripts").glob("*.py")))
        clause_ids = [int(n) for n in re.findall(r"<!-- id:R(\d+) -->",
                                                 (ROOT / "AGENT_RULES.md").read_text(encoding="utf-8"))]
        max_clause = max(clause_ids) if clause_ids else 0
        bad = []
        user_home = not _is_template_repo()
        for md in DOCS:
            text = md.read_text(encoding="utf-8")
            rel = md.relative_to(ROOT)
            if user_home and md.name.startswith("README"):
                continue          # 实例的 README 是用户首页，计数不归模板管
            for m in re.finditer(r"(\d+)\s*个\s*(?:workspace\s*)?skill", text):
                if int(m.group(1)) != disk_skills:
                    bad.append(f"{rel}: 写 {m.group(1)} 个 skill（磁盘 {disk_skills}）")
            for m in re.finditer(r"(\d+)\s*个\s*脚本", text):
                if int(m.group(1)) != disk_scripts:
                    bad.append(f"{rel}: 写 {m.group(1)} 个脚本（磁盘 {disk_scripts}）")
            for m in re.finditer(r"scripts/（(\d+)\s*个）|scripts/，(\d+)\s*个", text):
                n = int(m.group(1) or m.group(2))
                if n != disk_scripts:
                    bad.append(f"{rel}: 写 scripts {n} 个（磁盘 {disk_scripts}）")
            for m in re.finditer(r"\b(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Fourteen)\s+skills?\b", text):
                n = WORD_NUM[m.group(1)]
                if n != disk_skills:
                    bad.append(f"{rel}: 写 '{m.group(1)} skills'（磁盘 {disk_skills}）")
            for m in re.finditer(r"R1\.\.R(\d+)", text):
                if int(m.group(1)) != max_clause:
                    bad.append(f"{rel}: 条款范围写 R1..R{m.group(1)}（实际 R1..R{max_clause}）")
        self.assertEqual(bad, [], "散文计数漂移：\n" + "\n".join(bad))

    def test_d9_github_layer_is_not_template_layer(self):
        """`.github/` 是模板仓自己的 CI 基础设施，不下发到实例：不得出现在 TEMPLATE_FILES。

        曾经的误判：审计席把 `.github/scripts/release_anchor.py` 未列入清单当成缺口——实际整层
        都不在清单里，属设计而非遗漏。这条断言把这个边界钉住，防止将来有人只加一个文件。
        """
        from verify_rules import TEMPLATE_FILES  # noqa: E402
        bad = [f for f in TEMPLATE_FILES if f.startswith(".github/")]
        self.assertEqual(bad, [], f".github/ 不应进模板层清单：{bad}")
        if _is_template_repo():
            self.assertTrue((ROOT / ".github" / "scripts" / "release_anchor.py").is_file(),
                            "模板仓仍需要该脚本（CI 用），只是不下发")

    def test_d10_shields_badges_are_well_formed(self):
        """shields 静态徽章是 `/badge/<label>-<message>-<color>`。

        message 里出现**裸 `-`** 会被当成字段分隔：`policy-v3.5.4-single%20source…` 里的
        `v3.5.4` 被当成 message、`single source of truth` 被当成颜色段，shields 于是返回
        `404: badge not found`——徽章坏掉但页面照常渲染，肉眼与「链接能不能打开」都看不出来。
        字面量连字符要写 `--`，空格写 `%20`。
        """
        if not _is_template_repo():
            self.skipTest("派生实例的 README 属用户首页（用户自维护）")
        colors = {"brightgreen", "green", "yellowgreen", "yellow", "orange", "red", "blue",
                  "lightgrey", "grey", "gray", "success", "important", "critical",
                  "informational", "inactive", "blueviolet"}
        url_re = re.compile(r"https://img\.shields\.io/badge/([^\s)\"']+)")
        bad = []
        for name in ("README.md", "README_EN.md"):
            path = ROOT / name
            if not path.exists():
                continue
            for raw in url_re.findall(path.read_text(encoding="utf-8")):
                body = raw.split("?")[0].replace("--", "\x00")   # `--` = 合法的字面量连字符
                parts = body.split("-")
                if len(parts) != 3:
                    bad.append(f"{name}: {raw} → 被切成 {len(parts)} 段（message 里的裸 `-` 要写成 `--` 或 `%20`）")
                    continue
                color = parts[2].removesuffix(".svg")
                if not (re.fullmatch(r"[0-9a-fA-F]{3,8}", color) or color.lower() in colors):
                    bad.append(f"{name}: {raw} → 颜色段 `{parts[2]}` 既不是 hex 也不是已知色名")
        self.assertEqual(bad, [], "徽章 URL 结构错误：\n" + "\n".join(bad))

    def test_d8_skill_table_versions_match_skill_md(self):
        """README / README_EN / docs/skills.md 的 skill 表版本列必须等于 SKILL.md 的 version。

        d2 只查 docs/skills.md，两份 README 的版本列无人管——实测漂移过
        （`docs-retrieval-maintenance` 写 1.2.0，SKILL.md 是 1.3.0），发布前才被审计席抓到。
        """
        bad = []
        user_home = not _is_template_repo()
        for name, ver in skills().items():
            if not ver:
                continue
            row = re.compile(r"\|\s*\[" + re.escape(name) + r"\]\([^)]*\)\s*\|\s*([^|]+?)\s*\|")
            for md in DOCS:
                if user_home and md.name.startswith("README"):
                    continue      # 实例的 README 是用户首页（同步豁免），版本列不归模板管
                text = md.read_text(encoding="utf-8")
                for m in row.finditer(text):
                    got = m.group(1).strip()
                    if got != ver:
                        bad.append(f"{md.relative_to(ROOT)}: {name} 写 {got}（SKILL.md {ver}）")
        self.assertEqual(bad, [], "skill 表版本漂移：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
