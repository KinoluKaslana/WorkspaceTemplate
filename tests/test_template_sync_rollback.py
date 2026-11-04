"""template_sync 的备份/回滚：MANIFEST 三段 + 回滚放回 replaced、删除 added。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "template_sync.py"


def _source_ref() -> str:
    """同步来源 ref：优先 HEAD 的提交 sha。

    不能用写死的 `main`：CI 里 `actions/checkout` 是单分支浅克隆（推 dev 时本地只有 dev），
    `--ref main` 会以 `ref-not-found` 失败——那会让 CI 在任何非 main 分支上永久红，
    而测试真正要验的是备份/回滚机制，与分支名无关。
    取 HEAD sha 在完整克隆、浅克隆、detached HEAD 三种形态下都成立。
    """
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        sha = r.stdout.strip()
        if r.returncode == 0 and sha:
            return sha
    except OSError:
        pass
    return "main"


@unittest.skipUnless(shutil.which("git"), "未安装 git：本套用例需要真实 git 仓库")
class Rollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps({
            "workspace_status": "active",
            "python": {"mode": "local-venv", "runtime": {"identifier": "/x/.venv-312", "version": "3.12.0"}},
            "template": {"source_repo": str(ROOT)},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.old_rules = "# 旧规则\n\n> **模板政策版本：`2.4.0`。**\n"
        (self.ws / "AGENT_RULES.md").write_text(self.old_rules, encoding="utf-8")
        (self.ws / "README.md").write_text("local home\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, *args):
        return subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                               "--template", str(ROOT), "--ref", _source_ref(), *args],
                              capture_output=True, text=True, encoding="utf-8", cwd=ROOT)

    def test_apply_writes_manifest_sections(self):
        p = self.run_sync("--apply")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        man = (bk / "MANIFEST.txt").read_text(encoding="utf-8")
        for sec in ("[replaced]", "[added]", "[preserved]"):
            self.assertIn(sec, man)
        replaced = man.split("[replaced]")[1].split("[added]")[0]
        added = man.split("[added]")[1].split("[preserved]")[0]
        self.assertIn("AGENT_RULES.md", replaced)          # 旧文件：放回
        self.assertIn("UPGRADE.md", added)                 # 新文件：回滚时删除
        self.assertFalse((bk / "UPGRADE.md").exists())     # 新增文件无需备份

    def test_rollback_restores_and_deletes(self):
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        self.assertTrue((self.ws / "UPGRADE.md").is_file())
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.ws / "AGENT_RULES.md").read_text(encoding="utf-8"), self.old_rules)
        self.assertEqual((self.ws / "README.md").read_text(encoding="utf-8"), "local home\n")
        self.assertFalse((self.ws / "UPGRADE.md").exists())
        self.assertFalse((self.ws / "docs").exists())
        self.assertFalse((self.ws / "scripts").exists())
        self.assertTrue((self.ws / ".workspace" / "bootstrap.json").is_file())

    def test_rollback_purges_generated_files(self):
        """第 3 步生成的 REGISTRY/索引：升级前不存在时，--purge-generated 要删掉。"""
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("# generated\n", encoding="utf-8")
        (self.ws / "notes").mkdir(exist_ok=True)
        (self.ws / "notes" / "INDEX.md").write_text("# idx\n", encoding="utf-8")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(reg.exists())
        self.assertFalse((self.ws / "notes" / "INDEX.md").exists())
        self.assertTrue((self.ws / "AGENT_RULES.md").is_file())

    def test_rollback_without_manifest_fails(self):
        bk = self.ws / ".workspace" / "backup-empty"
        bk.mkdir()
        p = subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 2)
        self.assertIn("MANIFEST", p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
