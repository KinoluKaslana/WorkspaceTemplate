#!/usr/bin/env python3
"""skill_audit.py 回归（契约节 / 双登记 / 计数 / 名称集合 / --sync-registry）。

A1 完整 skill → 通过
A2 缺契约节（维护）→ 报缺节
A3 REGISTRY 计数与行数不符 → 报计数
A4 REGISTRY 名称集合与目录不符 → 报名称
A5 --sync-registry 重写表与计数后 → 审计通过
运行：python3 -B tests/test_skill_audit.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent

FULL = """---
name: {name}
description: "Use when testing {name}."
version: 1.0.0
---

# {name}

## 职责边界

归本 skill：测试。

## 触发条件

进入：测试。

## 前置事实（先读，不可跳过）

1. 无。

## 标准流程（一步）

```bash
true
```

## 失败与回滚

| 失败 | 处置 |
|---|---|
| 无 | 无 |

## 验收（命令 + 期望输出）

```bash
true   # 期望 exit 0
```

## 维护

- 版本历史见 CHANGELOG.md；改动后跑 skill_audit --strict。
"""


def build(tmp: Path, skills: dict[str, str], declared: int | None = None) -> Path:
    ws = tmp / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "skills").mkdir()
    (ws / "TOOLS.md").write_text("# tools\n", encoding="utf-8")
    shutil.copyfile(SRC / "scripts" / "skill_audit.py", ws / "scripts" / "skill_audit.py")
    for name, body in skills.items():
        d = ws / ".workspace" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        (ws / "TOOLS.md").write_text((ws / "TOOLS.md").read_text(encoding="utf-8") + f"| **{name}** | x |\n",
                                     encoding="utf-8")
    n = len(skills) if declared is None else declared
    rows = "\n".join(f"| {k} | t | `.workspace/skills/{k}/SKILL.md` | workspace-skills |" for k in skills)
    (ws / "skills" / "REGISTRY.md").write_text(
        f"# Skills\n\n| 命名空间 | root | 索引 | 路由状态 | TOOLS |\n|---|---|---|---|---|\n"
        f"| `workspace-skills` | `.workspace/skills/` | 无 | 已登记（{n} 入口：{' / '.join(skills)}） | TOOLS |\n\n"
        f"## Skill 入口\n\n| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |\n|---|---|---|---|\n{rows}\n",
        encoding="utf-8")
    return ws


def audit(ws: Path, *args):
    p = subprocess.run([sys.executable, "-B", "scripts/skill_audit.py", *args],
                       cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


class SkillAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skillaudit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a1_complete(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        rc, out = audit(ws, "--strict")
        self.assertEqual(rc, 0, out)

    def test_a2_missing_section(self):
        body = FULL.format(name="demo").replace("## 维护", "## 别的")
        ws = build(self.root, {"demo": body})
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("缺契约节", out)

    def test_a3_count_mismatch(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")}, declared=3)
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("实际列出", out)

    def test_a4_name_mismatch(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8").replace(
            "| demo | t | `.workspace/skills/demo/SKILL.md`", "| ghost | t | `.workspace/skills/ghost/SKILL.md`"),
            encoding="utf-8")
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("名称集合", out)

    def test_a5_sync_registry(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")}, declared=9)
        rc, _ = audit(ws)
        self.assertEqual(rc, 1)
        rc2, out2 = audit(ws, "--sync-registry")
        self.assertEqual(rc2, 0, out2)
        rc3, out3 = audit(ws, "--strict")
        self.assertEqual(rc3, 0, out3)
        self.assertIn("已登记（1 入口：demo）", (ws / "skills" / "REGISTRY.md").read_text(encoding="utf-8"))


    def test_a6_sync_registry_handles_non_kebab_names(self):
        """大写/下划线目录名：读得懂、写得对（不再永久 exit 1），约定问题交给 --strict 提示。

        历史：早期实现用 `[a-z0-9-]+` 匹配首列，而发现器是 `*/SKILL.md`（任意名）——
        两者不一致会让「能通过检查的名字」永久写不干净。现在解析按列结构做，
        命名约定单独提示，不再把约定问题伪装成集合不一致。
        """
        ws = build(self.root, {"Demo": FULL.format(name="Demo")})
        rc, out = audit(ws, "--sync-registry")
        self.assertEqual(rc, 0, out)
        reg = (ws / "skills" / "REGISTRY.md").read_text(encoding="utf-8")
        self.assertIn("`.workspace/skills/Demo/SKILL.md`", reg)
        rc2, out2 = audit(ws, "--strict")
        self.assertEqual(rc2, 1, out2)          # 只提示约定，不改文件
        self.assertIn("lowercase-kebab", out2)

    def test_a8_prose_mention_does_not_inflate_entry_count(self):
        """散文里提到 `.workspace/skills/x/SKILL.md` 不能虚增入口计数（只在入口节内数）。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        audit(ws, "--sync-registry")
        reg = ws / "skills" / "REGISTRY.md"
        text = reg.read_text(encoding="utf-8")
        reg.write_text(text.replace("## Skill 入口", "正文示例：`.workspace/skills/demo/SKILL.md`\n\n## Skill 入口", 1),
                       encoding="utf-8")
        rc, out = audit(ws, "--strict")
        self.assertEqual(rc, 0, out)          # 散文里那次提及不该让计数变成 2

    def test_a9_entry_row_name_must_match_path(self):
        """首列名称与第三列路径不一致时要点名（结构化解析，而不是靠正则巧合）。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        audit(ws, "--sync-registry")
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8").replace(
            ".workspace/skills/demo/SKILL.md", ".workspace/skills/other/SKILL.md", 1),
            encoding="utf-8")
        rc, out = audit(ws, "--strict")
        self.assertEqual(rc, 1, out)
        self.assertIn("名称与路径不一致", out)

    def test_a7_sync_registry_escapes_pipe_in_description(self):
        """描述里带竖线时不能撕开 REGISTRY 表格。"""
        body = FULL.format(name="demo").replace(
            'description: "Use when testing demo."', 'description: "Use when testing demo | pipe."')
        ws = build(self.root, {"demo": body})
        rc, out = audit(ws, "--sync-registry")
        self.assertEqual(rc, 0, out)
        reg = (ws / "skills" / "REGISTRY.md").read_text(encoding="utf-8")
        self.assertIn("demo \\| pipe", reg)
        rc2, out2 = audit(ws, "--strict")
        self.assertEqual(rc2, 0, out2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
