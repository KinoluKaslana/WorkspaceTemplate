#!/usr/bin/env python3
"""`scripts/encoding_guard.py` 的契约测试（§3.1 机械统一）。

钉住四件事：
1. `--check` 能分别报出 BOM / 纯 CRLF / 混合换行，`--json` 机器可读；
2. `--fix` 之后 `--check` 必须 rc=0（一键清理真的清干净）；
3. **幂等**：第二次 `--fix` 一个文件都不改（字节与 mtime 都不动，含本来就没问题的文件）；
4. 改前备份保留**原始字节**（含 BOM），修复保留文件权限位。

所有子进程都走 `osnorm.run_text`（R2 单一来源），文本 I/O 走 `osnorm`（R1）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import osnorm  # noqa: E402

BOM = b"\xef\xbb\xbf"
COPIES = ("encoding_guard.py", "osnorm.py", "verify_rules.py")


class GuardCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        for name in COPIES:
            shutil.copy2(SCRIPTS / name, self.root / "scripts" / name)
        (self.root / "notes" / "sub").mkdir(parents=True)
        # 干净文件（必须全程不被碰）
        osnorm.write_bytes(self.root / "AGENT_RULES.md", "# 规则\n\n正文\n".encode("utf-8"))
        osnorm.write_bytes(self.root / "TOOLS.md", "| 工具 |\n|---|\n".encode("utf-8"))
        # 三类问题
        osnorm.write_bytes(self.root / "WORKSPACE.md", BOM + "首页\n".encode("utf-8"))
        osnorm.write_bytes(self.root / "notes" / "n1.md", b"# n1\r\n\r\nbody\r\n")
        osnorm.write_bytes(self.root / "notes" / "sub" / "n2.md", b"a\r\nb\rc\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_guard(self, *args: str):
        return osnorm.run_text([sys.executable, "-B", str(self.root / "scripts" / "encoding_guard.py"),
                                "--root", str(self.root), *args], cwd=self.root)

    def snapshot(self) -> dict:
        out = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and "scripts" not in path.parts:
                rel = path.relative_to(self.root).as_posix()
                if rel.startswith(".workspace/"):
                    continue                       # 备份目录会新增文件，不参与幂等比对
                st = path.stat()
                out[rel] = (osnorm.read_bytes(path), st.st_mtime_ns)
        return out


class CheckTest(GuardCase):
    def test_check_reports_all_three_kinds(self) -> None:
        p = self.run_guard("--check", "--json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        data = json.loads(p.stdout)
        found = {item["path"]: item["problems"] for item in data["findings"]}
        self.assertEqual(found, {"WORKSPACE.md": ["BOM"],
                                 "notes/n1.md": ["CRLF"],
                                 "notes/sub/n2.md": ["MIXED"]})
        # 扫描面 = TEMPLATE_FILES∩存在（3 个脚本 + AGENT_RULES.md + TOOLS.md）+ 用户清单 3 个
        self.assertEqual(data["scanned"], 8)

    def test_clean_tree_is_green(self) -> None:
        for rel in ("WORKSPACE.md", "notes/n1.md", "notes/sub/n2.md"):
            osnorm.write_bytes(self.root / rel, b"clean\n")
        p = self.run_guard("--check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("已是 UTF-8(no BOM) + LF", p.stdout)

    def test_json_is_check_only(self) -> None:
        p = self.run_guard("--fix", "--json")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


class FixTest(GuardCase):
    def test_fix_then_check_is_clean(self) -> None:
        before = self.snapshot()
        p = self.run_guard("--fix")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for rel in ("WORKSPACE.md", "notes/n1.md", "notes/sub/n2.md"):
            self.assertIn(f"fixed {rel}", p.stdout)
        self.assertEqual(self.run_guard("--check").returncode, 0)
        # 修完的字节：无 BOM、无 CR
        self.assertEqual(osnorm.read_bytes(self.root / "WORKSPACE.md"), "首页\n".encode("utf-8"))
        self.assertEqual(osnorm.read_bytes(self.root / "notes" / "n1.md"), b"# n1\n\nbody\n")
        self.assertEqual(osnorm.read_bytes(self.root / "notes" / "sub" / "n2.md"), b"a\nb\nc\n")
        # 干净文件一个字节都没动（含 mtime）
        after = self.snapshot()
        for rel in ("AGENT_RULES.md", "TOOLS.md"):
            self.assertEqual(before[rel], after[rel], f"{rel} 本不该被重写")

    def test_fix_is_idempotent(self) -> None:
        self.assertEqual(self.run_guard("--fix").returncode, 0)
        first = self.snapshot()
        p = self.run_guard("--fix")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("无需修复", p.stdout)
        self.assertEqual(first, self.snapshot(), "第二次 --fix 不得改动任何文件（含 mtime）")

    def test_backup_keeps_original_bytes(self) -> None:
        original = osnorm.read_bytes(self.root / "WORKSPACE.md")
        self.assertEqual(self.run_guard("--fix").returncode, 0)
        backups = sorted((self.root / ".workspace").glob("backup-*-encoding"))
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(osnorm.read_bytes(backups[0] / "WORKSPACE.md"), original)
        self.assertTrue(original.startswith(BOM), "前提：备份的是带 BOM 的原始字节")

    def test_fix_preserves_mode(self) -> None:
        """修复不得改权限位（按本平台的读数比对：Windows 只有只读位这一档）。"""
        target = self.root / "WORKSPACE.md"
        os.chmod(target, 0o640)
        expected = target.stat().st_mode & 0o777
        self.assertEqual(self.run_guard("--fix").returncode, 0)
        self.assertEqual(target.stat().st_mode & 0o777, expected)


if __name__ == "__main__":
    unittest.main()
