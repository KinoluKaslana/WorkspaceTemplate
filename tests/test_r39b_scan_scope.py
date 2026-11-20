#!/usr/bin/env python3
"""R39b 回归：递归文档扫描的**范围收口**（旁路目录不进判据）。

缺陷现场（破坏性测试暴露）：`README_EN.md` 带 BOM → `check_all.py --fix`（encoding_guard
剥 BOM 并备份）→ 再 `--check` 时 `policy_facts` 把 `.workspace/backup-<ts>-encoding/`
里的**备份副本**当成工作区文档，于是「修复刚做完就变红」。根因是
`policy_facts._iter_md()` 的 `rglob("*.md")` 只按目录名精确匹配跳过。

本文件钉住四条：
  T1 `.workspace/backup-*/` 下的未登记生成块标记 → `policy_facts --check` **必须绿**（跳过备份）
  T2 `sandbox-*/` 下同样内容 → **必须绿**（跳过并行沙箱）
  T3 真实 `README.md` 里加未登记标记 → **必须红**（证明没对真实文档放宽）
  T4 全仓副本上「README_EN.md 带 BOM」→ `check_all.py --fix` rc=0 且随后 `--check` 全绿

文本 I/O 一律走 `osnorm`（R1/R2），不走裸 open/read_text/subprocess 的编码参数。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import osnorm  # noqa: E402
import policy_facts as pf  # noqa: E402

_BOM = b"\xef\xbb\xbf"
# `.venv`/`.git` 之类不进副本：既慢又与本判据无关。
_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo", ".venv*",
                                     "venv", "node_modules", ".local", "backup-*")

BLOCK = f"{pf.BEGIN}\n未登记副本\n{pf.END}\n"


def _run(script: Path, *args: str, cwd: Path):
    return osnorm.run_text([sys.executable, "-B", str(script), *args], cwd=str(cwd))


class ScanScopeCase(unittest.TestCase):
    """每个用例一份全仓副本：不改动被检查的仓，也不受它当前状态影响。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        shutil.copytree(ROOT, self.root, ignore=_COPY_IGNORE, symlinks=True)
        boot = self.root / ".workspace" / "bootstrap.json"
        data = json.loads(osnorm.read_text(boot))
        self.assertEqual(data.get("workspace_status"), "template",
                         "本组用例假定副本以模板身份起跑（模板身份才判真实 README）")

    def check(self) -> list[str]:
        return pf.check(self.root)

    def test_t1_backup_dir_is_skipped(self) -> None:
        """T1：`.workspace/backup-x/` 里的生成块标记不算本仓文档。"""
        target = self.root / ".workspace" / "backup-x" / "README_EN.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(target, BLOCK)
        self.assertEqual(self.check(), [], "备份目录下的 md 不应进入生成块判据")

    def test_t1b_backup_encoding_dir_is_skipped(self) -> None:
        """T1b：encoding_guard 现用形态 `.workspace/backup-encoding/<ts>/` 同样被跳过。"""
        target = self.root / ".workspace" / "backup-encoding" / "20261120-170000" / "README_EN.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(target, BLOCK)
        self.assertEqual(self.check(), [])

    def test_t2_sandbox_dir_is_skipped(self) -> None:
        """T2：`sandbox-y/` 里的生成块标记不算本仓文档。"""
        target = self.root / "sandbox-y" / "README.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(target, BLOCK)
        self.assertEqual(self.check(), [], "并行沙箱目录下的 md 不应进入生成块判据")

    def test_t3_real_readme_is_still_red(self) -> None:
        """T3：真实 `README.md` 里出现未登记生成块 → 仍必须红（没有放宽真实文档）。"""
        readme = self.root / "README.md"
        osnorm.write_text(readme, osnorm.read_text(readme) + "\n" + BLOCK)
        findings = self.check()
        self.assertTrue(any(x.startswith("README.md:") and "未登记" in x for x in findings),
                        f"真实 README 的未登记生成块必须红：{findings}")

    def test_transient_dir_is_single_entry_point(self) -> None:
        """跳过逻辑只有一份：模块级常量 + `_transient_dir()`。"""
        for name in (".git", ".venv", ".venv314", "__pycache__", "node_modules",
                     ".hook-state", "backup-x", "backup-20261120-165845-encoding", "sandbox-y"):
            self.assertTrue(pf._transient_dir(name), name)
        self.assertTrue(pf._transient_dir(".local", ".workspace"))
        for name in ("docs", "notes", "tests", "skills", ".workspace", "backupx", "sandbox"):
            self.assertFalse(pf._transient_dir(name), name)
        self.assertFalse(pf._transient_dir(".local", "docs"))


class CheckAllFixRecheck(unittest.TestCase):
    """T4：`check_all.py --fix` 收尾必须自己复查，修复是否收口不靠推断。"""

    def test_t4_fix_then_recheck_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            shutil.copytree(ROOT, root, ignore=_COPY_IGNORE, symlinks=True)
            script = root / "scripts" / "check_all.py"
            target = root / "README_EN.md"
            original = osnorm.read_bytes(target)
            self.assertFalse(original.startswith(_BOM), "前提：副本 README_EN.md 干净")
            osnorm.write_bytes(target, _BOM + original)

            fix = _run(script, "--fix", cwd=root)
            self.assertEqual(fix.returncode, 0, fix.stdout[-3000:] + fix.stderr[-2000:])
            self.assertIn("修复后复查", fix.stdout, "收尾必须显式打印复查段")
            self.assertFalse(osnorm.read_bytes(target).startswith(_BOM), "BOM 应已被剥掉")
            # 备份落点必须是旁路目录形态，且保留原始字节
            backups = sorted((root / ".workspace" / "backup-encoding").glob("*/README_EN.md"))
            self.assertEqual(len(backups), 1, backups)
            self.assertTrue(osnorm.read_bytes(backups[0]).startswith(_BOM))

            check = _run(script, "--check", cwd=root)
            self.assertEqual(check.returncode, 0, check.stdout[-3000:] + check.stderr[-2000:])
            self.assertNotIn("[FAIL]", check.stdout)


if __name__ == "__main__":
    unittest.main()
