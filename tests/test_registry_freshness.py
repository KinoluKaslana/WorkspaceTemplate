#!/usr/bin/env python3
"""注册表新鲜度门：改过模板层文件后没重建 `.workspace/rule-clauses.json` 时，**单元测试**就红。

为什么单独成例：`verify_rules.py` 本来就是这道门，但它通常在提交前才跑；
本轮已经三次出现「改了 docs/*.md → 忘了重建注册表 → 直到别的用例才炸」。
把它变成 unittest 的一等公民，`unittest discover` 会先报出来，而不是让其他用例替它背锅。

运行：python3 -B tests/test_registry_freshness.py
"""
import hashlib
import json
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

    def test_readme_registry_hash_matches_disk(self):
        """模板仓专用：README.md / README_EN.md 的注册表哈希必须等于磁盘实际字节。

        为什么单独钉这条：`verify_rules.py` 对 README_EXEMPT 只报 info（用户首页可替换），
        于是注册表里留下幽灵哈希（登记 454ed671… / 磁盘 a0c14afd…）时六门全绿——
        豁免降级的是 finding，不是「注册表可以与磁盘漂移」。派生工作区仍按原逻辑豁免。
        两个 README 都受该豁免影响，故必须逐个钉，只钉一个就漏掉另一半。
        """
        try:
            status = json.loads((ROOT / ".workspace" / "bootstrap.json")
                               .read_text(encoding="utf-8")).get("workspace_status", "")
        except Exception:
            status = ""
        if status != "template":
            self.skipTest("派生实例的 README 属用户首页（同步豁免），哈希不归模板管")
        reg = json.loads((ROOT / ".workspace" / "rule-clauses.json").read_text(encoding="utf-8"))
        for name in ("README.md", "README_EN.md"):
            with self.subTest(readme=name):
                want = (reg.get(name) or {}).get("file_sha256")
                self.assertTrue(
                    want,
                    f"注册表缺 {name} 条目——先跑 `mark_rules.py registry AGENT_RULES.md`")
                got = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                self.assertEqual(
                    got, want,
                    f"{name} 注册表哈希漂移（幽灵哈希）——改完 README 后跑 "
                    "`python3 scripts/mark_rules.py registry AGENT_RULES.md`")


if __name__ == "__main__":
    unittest.main(verbosity=2)
