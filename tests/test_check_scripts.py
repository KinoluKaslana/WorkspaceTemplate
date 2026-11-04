"""三个「把 manual 变命令」的检查：version_check / notes_index_check / secret_scan。

每个都带红例：违规夹具必须真的失败（否则等于假绿）。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("version_check.py", "notes_index_check.py", "secret_scan.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        for name in SCRIPTS:
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, name, *args):
        return subprocess.run([sys.executable, "-B", str(self.root / "scripts" / name), *args],
                              capture_output=True, text=True, encoding="utf-8", cwd=self.root)


class VersionCheck(Base):
    def write(self, policy="3.4.0", changelog="3.4.0", boot="3.4.0"):
        (self.root / "AGENT_RULES.md").write_text(
            f"# 规则\n\n> 状态。\n> **模板政策版本：`{policy}`。** 变更记录到 CHANGELOG。\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(f"# Changelog\n\n## {changelog}（2026-11-02）条目\n",
                                                encoding="utf-8")
        if boot is not None:
            (self.root / ".workspace").mkdir(exist_ok=True)
            (self.root / ".workspace" / "bootstrap.json").write_text(
                json.dumps({"template": {"current_policy_version": boot}}), encoding="utf-8")

    def test_green(self):
        self.write()
        self.assertEqual(self.run_script("version_check.py").returncode, 0)

    def test_changelog_mismatch_is_red(self):
        self.write(policy="3.4.0", changelog="3.3.1")
        p = self.run_script("version_check.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E4", p.stdout)

    def test_bootstrap_mismatch_is_red(self):
        self.write(boot="3.2.0")
        p = self.run_script("version_check.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E5", p.stdout)

    def test_changelog_absent_skips_unless_required(self):
        self.write()
        (self.root / "CHANGELOG.md").unlink()
        self.assertEqual(self.run_script("version_check.py").returncode, 0)         # 派生工作区
        p = self.run_script("version_check.py", "--require-changelog")
        self.assertEqual(p.returncode, 1)                                           # 模板仓
        self.assertIn("E2", p.stdout)

    def test_missing_changelog_entry_is_red(self):
        self.write()
        (self.root / "CHANGELOG.md").write_text("# Changelog\n\n没有版本标题\n", encoding="utf-8")
        p = self.run_script("version_check.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E2", p.stdout)


class NotesIndexCheck(Base):
    def setUp(self):
        super().setUp()
        notes = self.root / "notes"
        notes.mkdir()
        shutil.copy2(ROOT / "notes" / "rebuild_index.py", notes / "rebuild_index.py")
        shutil.copy2(ROOT / "notes" / "_TEMPLATE.md", notes / "_TEMPLATE.md")
        (notes / "demo-note.md").write_text(
            "---\nname: demo-note\ndescription: 演示 note\ncategory: method\n"
            "techniques: [alpha]\nkeywords: [alpha]\nstatus: active\n---\n\n正文\n",
            encoding="utf-8")

    def build(self):
        p = subprocess.run([sys.executable, "-B", "rebuild_index.py"],
                           cwd=self.root / "notes", capture_output=True, text=True, encoding="utf-8")
        self.assertIn(p.returncode, (0, 1), p.stderr)   # 1 = 有警告/策展到期，仍产出文件

    def test_green_after_build(self):
        self.build()
        self.assertEqual(self.run_script("notes_index_check.py").returncode, 0)

    def test_hand_edited_index_is_red(self):
        self.build()
        idx = self.root / "notes" / "INDEX.md"
        idx.write_text(idx.read_text(encoding="utf-8") + "\n手加一行\n", encoding="utf-8")
        p = self.run_script("notes_index_check.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E3", p.stdout)

    def test_curation_state_from_bootstrap_is_compared(self):
        """索引头部会写策展状态；检查必须带上 bootstrap，否则永远误报。"""
        (self.root / ".workspace").mkdir(exist_ok=True)
        (self.root / ".workspace" / "bootstrap.json").write_text(
            '{"notes": {"curation": {"count_threshold": 25, "interval_days": 90,'
            ' "last_curated_at": "2026-11-02"}}}', encoding="utf-8")
        self.build()
        self.assertEqual(self.run_script("notes_index_check.py").returncode, 0)

    def test_missing_index_is_red(self):
        p = self.run_script("notes_index_check.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E2", p.stdout)


class SecretScan(Base):
    def test_clean_tree_is_green(self):
        (self.root / "README.md").write_text("token 这个词在文档里不算凭据\n", encoding="utf-8")
        self.assertEqual(self.run_script("secret_scan.py").returncode, 0)

    def test_private_key_block_is_red(self):
        # 字面量拆开写，避免本测试文件自己被 secret_scan 命中
        head = "-----BEGIN RSA" + " PRIVATE KEY-----"
        tail = "-----END RSA" + " PRIVATE KEY-----"
        (self.root / "leak.txt").write_text(f"{head}\nMIIE...\n{tail}\n", encoding="utf-8")
        p = self.run_script("secret_scan.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("private-key", p.stdout)

    def test_secretscanignore_is_honoured(self):
        fake = "AKIA" + "IOSFODNN7EXAMPLE"
        (self.root / "vendor").mkdir()
        (self.root / "vendor" / "example.md").write_text(f'AWS = "{fake}"\n', encoding="utf-8")
        self.assertEqual(self.run_script("secret_scan.py").returncode, 1)
        (self.root / ".secretscanignore").write_text("# 第三方示例\nvendor\n", encoding="utf-8")
        self.assertEqual(self.run_script("secret_scan.py").returncode, 0)

    def test_aws_key_is_red_and_masked(self):
        fake = "AKIA" + "IOSFODNN7EXAMPLE"
        (self.root / "conf.py").write_text(f'AWS = "{fake}"\n', encoding="utf-8")
        p = self.run_script("secret_scan.py")
        self.assertEqual(p.returncode, 1)
        self.assertIn("aws-akid", p.stdout)
        self.assertNotIn("IOSFODNN7EXAMPLE", p.stdout)   # 只打印脱敏片段


if __name__ == "__main__":
    unittest.main(verbosity=2)
