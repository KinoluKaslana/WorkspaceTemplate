"""template_sync 的备份/回滚：MANIFEST 三段 + 回滚放回 replaced、删除 added。"""
import contextlib
import io
import json
import os
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


def _make_fixture_template(base: Path) -> Path:
    """最小的「模板仓」：两个 TEMPLATE_FILES + 一个永远 clean=false 的 verify_rules 桩。"""
    tpl = base / "tpl"
    (tpl / "scripts").mkdir(parents=True)
    (tpl / "AGENT_RULES.md").write_text("# 模板\n\n> **模板政策版本：`9.9.9`。**\n", encoding="utf-8")
    (tpl / "README.md").write_text("template readme\n", encoding="utf-8")
    (tpl / "scripts" / "verify_rules.py").write_text(
        'TEMPLATE_FILES = ["AGENT_RULES.md", "README.md"]\n'
        "import json\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    data = {'clean': False, 'findings': ['forced-by-test']}\n"
        "    if '--json' in sys.argv:\n"
        "        open(sys.argv[sys.argv.index('--json') + 1], 'w', encoding='utf-8').write(json.dumps(data))\n"
        "    print(json.dumps(data))\n",
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tpl)], check=True)
    subprocess.run(["git", "-C", str(tpl), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tpl), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "-m", "tpl"], check=True)
    return tpl


@unittest.skipUnless(shutil.which("git"), "未安装 git：本套用例需要真实 git 仓库")
class Transactional(unittest.TestCase):
    """同步是事务性的：verify 不过或写盘中途失败，默认回到同步前，不留半套状态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.tpl = _make_fixture_template(base)
        self.ws = base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps({
            "workspace_status": "active",
            "python": {"mode": "local-venv", "runtime": {"identifier": "/x/.venv-312", "version": "3.12.0"}},
            "template": {"source_repo": str(ROOT), "current_policy_version": "2.4.0"},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.old_rules = "# 旧规则\n\n> **模板政策版本：`2.4.0`。**\n"
        (self.ws / "AGENT_RULES.md").write_text(self.old_rules, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, *args):
        return subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                               "--template", str(self.tpl), "--ref", "HEAD", *args],
                              capture_output=True, text=True, encoding="utf-8", cwd=ROOT)

    def test_verify_failure_rolls_back(self):
        boot_before = (self.ws / ".workspace" / "bootstrap.json").read_bytes()
        p = self.run_sync("--apply", "--verify")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("自动回滚", p.stderr)
        self.assertEqual((self.ws / "AGENT_RULES.md").read_text(encoding="utf-8"), self.old_rules)
        # bootstrap 逐字节回到同步前（含 python/gate 段，不只是版本字段）
        self.assertEqual((self.ws / ".workspace" / "bootstrap.json").read_bytes(), boot_before)
        self.assertFalse((self.ws / "scripts").exists())      # 本次新增的目录被清掉
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        self.assertEqual((boot.get("template") or {}).get("current_policy_version"), "2.4.0")

    def test_verify_failure_keep_on_fail(self):
        p = self.run_sync("--apply", "--verify", "--keep-on-fail")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("自动回滚", p.stderr)          # 明确要求保留：不回滚
        self.assertIn("9.9.9", (self.ws / "AGENT_RULES.md").read_text(encoding="utf-8"))

    def test_verify_exception_rolls_back(self):
        """校验器本身抛异常（不是 clean:false）也要回滚，rc=3。"""
        import importlib.util
        import unittest.mock as mock

        spec = importlib.util.spec_from_file_location("tpl_sync_mod", SYNC)
        mod = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
        spec.loader.exec_module(mod)                          # type: ignore[union-attr]
        real_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if any("verify_rules.py" in str(c) for c in cmd):
                raise FileNotFoundError("injected: verify 起不来")
            return real_run(cmd, *a, **kw)

        boot_before = (self.ws / ".workspace" / "bootstrap.json").read_bytes()
        # 就地调用会写真实 stdout/stderr：用 StringIO 兜住，避免终端编码（cp1252）影响断言
        with mock.patch.object(mod.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(["--workspace", str(self.ws), "--template", str(self.tpl),
                           "--ref", "HEAD", "--apply", "--verify"])
        self.assertEqual(rc, 3)
        self.assertEqual((self.ws / "AGENT_RULES.md").read_text(encoding="utf-8"), self.old_rules)
        self.assertEqual((self.ws / ".workspace" / "bootstrap.json").read_bytes(), boot_before)

    def test_rollback_failure_returns_4(self):
        """回滚本身失败时返回 4（与「已回滚」的 2/3 区分），提示按 MANIFEST 手工恢复。"""
        import importlib.util
        import unittest.mock as mock

        spec = importlib.util.spec_from_file_location("tpl_sync_mod4", SYNC)
        mod = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
        spec.loader.exec_module(mod)                          # type: ignore[union-attr]
        with mock.patch.object(mod, "_rollback", return_value=2), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(["--workspace", str(self.ws), "--template", str(self.tpl),
                           "--ref", "HEAD", "--apply", "--verify"])
        self.assertEqual(rc, 4)

    @unittest.skipIf(os.name == "nt", "Windows 上创建符号链接需要额外权限")
    def test_write_failure_rolls_back(self):
        # 悬空符号链接：第二个文件复制时抛 FileNotFoundError，前面已写入的 AGENT_RULES.md 必须被放回
        (self.ws / "README.md").symlink_to(self.ws / "missing" / "x.md")
        p = self.run_sync("--apply")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("自动回滚", p.stderr)
        self.assertEqual((self.ws / "AGENT_RULES.md").read_text(encoding="utf-8"), self.old_rules)


    def test_rollback_keeps_registry_with_foreign_namespace(self):
        """REGISTRY 里已有用户自己的命名空间时，--purge-generated 不得删除它。

        删掉后 `skill_audit --sync-registry` 只会重建 workspace-skills 那一节，
        个人库/本地层等行再也回不来 → 静默丢用户数据。
        """
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(
            "# Skills\n\n| 命名空间 | root | 索引 | 路由状态 | TOOLS |\n|---|---|---|---|---|\n"
            "| `workspace-skills` | `.workspace/skills/` | 无 | 已登记（0 入口） | TOOLS |\n"
            "| `skills` | `/home/u/skills` | 根路由 | 已登记 | TOOLS |\n", encoding="utf-8")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(reg.is_file(), "含用户命名空间的 REGISTRY 被删掉了")
        self.assertIn("保留未清除", p.stdout)
        self.assertIn("`skills`", reg.read_text(encoding="utf-8"))

    def test_rollback_snapshots_purged_generated(self):
        """被清除的生成物必须先留快照，否则「清除」等于不可恢复。"""
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("# generated\n", encoding="utf-8")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = subprocess.run([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(reg.exists())
        snap = bk / "purged" / "skills" / "REGISTRY.md"
        self.assertTrue(snap.is_file(), "清除前没有留快照")
        self.assertEqual(snap.read_text(encoding="utf-8"), "# generated\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
