#!/usr/bin/env python3
"""注册表新鲜度门：改过模板层文件后没重建 `.workspace/rule-clauses.json` 时，**单元测试**就红。

为什么单独成例：`verify_rules.py` 本来就是这道门，但它通常在提交前才跑；
本轮已经三次出现「改了 docs/*.md → 忘了重建注册表 → 直到别的用例才炸」。
把它变成 unittest 的一等公民，`unittest discover` 会先报出来，而不是让其他用例替它背锅。

运行：python3 -B tests/test_registry_freshness.py
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_rules.py"


class RegistryFreshness(unittest.TestCase):
    def test_registry_matches_disk(self):
        p = subprocess.run([sys.executable, "-B", str(SCRIPT), "--workspace", str(ROOT)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(
            p.returncode, 0,
            "模板层文件与 .workspace/rule-clauses.json 不一致——改完文件后忘了跑 "
            "`python3 scripts/mark_rules.py registry AGENT_RULES.md`：\n" + p.stdout[-800:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
