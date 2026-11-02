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

    def test_d4_architecture_numbers(self):
        arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        policy = re.search(r"模板政策版本：`([\d.]+)`", (ROOT / "AGENT_RULES.md").read_text(encoding="utf-8")).group(1)
        self.assertIn(policy, arch, f"architecture.md 未写当前政策版本 {policy}")
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("tpl_vr", ROOT / "scripts" / "verify_rules.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            n = len(mod.TEMPLATE_FILES)
        finally:
            sys.path.pop(0)
        self.assertIn(str(n), arch, f"architecture.md 未写 TEMPLATE_FILES 条数 {n}")

    def test_d5_readme_lists_all_skills(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        missing = [n for n in skills() if n not in readme]
        self.assertEqual(missing, [], f"README 未提到：{missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
