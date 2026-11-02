#!/usr/bin/env python3
"""文档与工作区一致性检查（防止 README/docs 写飘）。

D1 所有相对链接可解析（README.md / README_EN.md / docs/*.md）
D2 docs/skills.md 覆盖磁盘上的每个 skill，且版本号与 SKILL.md frontmatter 一致
D3 docs/tools.md 覆盖 scripts/ 下每个 .py
D4 docs/architecture.md 的政策版本与 TEMPLATE_FILES 条数与实际一致
D5 README.md 提到每个 skill 名
运行：python3 -B tests/test_docs_consistency.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "README_EN.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def skills() -> dict[str, str]:
    out = {}
    for p in (ROOT / ".workspace" / "skills").glob("*/SKILL.md"):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^version:\s*(\S+)", text, re.M)
        out[p.parent.name] = m.group(1) if m else ""
    return out


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
        for md in DOCS:
            if md.name == "README.md" and md.parent == ROOT:
                continue
            text = md.read_text(encoding="utf-8")
            for m in re.finditer(r"政策 \*\*([\d.]+)\*\*|policy \*\*([\d.]+)\*\*", text):
                v = m.group(1) or m.group(2)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
