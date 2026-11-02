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
                       cwd=ws, capture_output=True, text=True)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
