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

# 文本 I/O 单一实现：osnorm（R1/R2 门禁的唯一入口）。夹具与被测产物都经它读写。
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入


def _source_ref() -> str:
    """同步来源 ref：优先 HEAD 的提交 sha。

    不能用写死的 `main`：CI 里 `actions/checkout` 是单分支浅克隆（推 dev 时本地只有 dev），
    `--ref main` 会以 `ref-not-found` 失败——那会让 CI 在任何非 main 分支上永久红，
    而测试真正要验的是备份/回滚机制，与分支名无关。
    取 HEAD sha 在完整克隆、浅克隆、detached HEAD 三种形态下都成立。
    """
    try:
        r = osnorm.run_text(["git", "-C", str(ROOT), "rev-parse", "HEAD"], errors="replace")
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
        osnorm.write_text(self.ws / ".workspace" / "bootstrap.json", json.dumps({
            "workspace_status": "active",
            "python": {"mode": "local-venv", "runtime": {"identifier": "/x/.venv-312", "version": "3.12.0"}},
            "template": {"source_repo": str(ROOT)},
        }, ensure_ascii=False, indent=2) + "\n", newline="lf")
        self.old_rules = "# 旧规则\n\n> **模板政策版本：`2.4.0`。**\n"
        osnorm.write_text(self.ws / "AGENT_RULES.md", self.old_rules, newline="lf")
        osnorm.write_text(self.ws / "README.md", "local home\n", newline="lf")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, *args):
        return osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                               "--template", str(ROOT), "--ref", _source_ref(), *args], cwd=ROOT)

    def test_apply_writes_manifest_sections(self):
        p = self.run_sync("--apply")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        man = osnorm.read_text(bk / "MANIFEST.txt", bom=False, newline="preserve")
        # 换行契约（写侧 `osnorm.write_text(newline="lf")` 的对外承诺）：MANIFEST 落盘必须是 LF。
        # 严格读法（newline="preserve"）能看到 CRLF，归一读法看不到——所以这条只能在严格读上成立。
        self.assertEqual(man.split("\n")[0], "# template_sync backup manifest")
        self.assertNotIn("\r", man, "MANIFEST 落盘出现 CR：写侧换行契约被破坏")
        for sec in ("[replaced]", "[added]", "[preserved]"):
            self.assertIn(sec, man)
        replaced = man.split("[replaced]")[1].split("[added]")[0]
        added = man.split("[added]")[1].split("[preserved]")[0]
        self.assertIn("AGENT_RULES.md", replaced)          # 旧文件：放回
        self.assertIn("UPGRADE.md", added)                 # 新文件：回滚时删除
        self.assertFalse((bk / "UPGRADE.md").exists())     # 新增文件无需备份

    def test_apply_appends_gitignore_and_reports_it(self):
        """`--apply` 必须真的接线到 ensure_ignore_lines，并在 JSON 回报里给出追加了哪几行。

        只测 helper 不够：把 apply 里那行调用删掉，helper 测试照样绿。
        """
        gi = self.ws / ".gitignore"
        osnorm.write_text(gi, "node_modules/\n", newline="lf")
        p = self.run_sync("--apply", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual(report.get("gitignore_added"),
                         [".workspace/.hook-state/", ".workspace/.local/"], report)
        text = osnorm.read_text(gi, bom=False, newline="preserve")
        self.assertTrue(text.startswith("node_modules/\n"), "用户内容被改了")
        self.assertEqual(text.count(".workspace/.local/"), 1)
        # 第二次同步：已存在就不再追加，也不再回报
        p2 = self.run_sync("--apply", "--json")
        self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
        self.assertNotIn("gitignore_added", json.loads(p2.stdout),
                         "重复同步又追加了一遍")
        self.assertEqual(osnorm.read_text(gi, bom=False, newline="preserve").count(".workspace/.local/"), 1)

    def test_rollback_restores_and_deletes(self):
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        self.assertTrue((self.ws / "UPGRADE.md").is_file())
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk)], cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(osnorm.read_text(self.ws / "AGENT_RULES.md", bom=False, newline="preserve"), self.old_rules)
        self.assertEqual(osnorm.read_text(self.ws / "README.md", bom=False, newline="preserve"), "local home\n")
        self.assertFalse((self.ws / "UPGRADE.md").exists())
        self.assertFalse((self.ws / "docs").exists())
        self.assertFalse((self.ws / "scripts").exists())
        self.assertTrue((self.ws / ".workspace" / "bootstrap.json").is_file())

    def test_rollback_purges_generated_files(self):
        """第 3 步生成的 REGISTRY/索引：升级前不存在时，--purge-generated 要删掉。"""
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(reg, "# generated\n", newline="lf")
        (self.ws / "notes").mkdir(exist_ok=True)
        osnorm.write_text(self.ws / "notes" / "INDEX.md", "# idx\n", newline="lf")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"], cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(reg.exists())
        self.assertFalse((self.ws / "notes" / "INDEX.md").exists())
        self.assertTrue((self.ws / "AGENT_RULES.md").is_file())

    def test_rollback_without_manifest_fails(self):
        bk = self.ws / ".workspace" / "backup-empty"
        bk.mkdir()
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk)], cwd=ROOT)
        self.assertEqual(p.returncode, 2)
        self.assertIn("MANIFEST", p.stderr + p.stdout)


def _make_fixture_template(base: Path) -> Path:
    """最小的「模板仓」：两个 TEMPLATE_FILES + 一个永远 clean=false 的 verify_rules 桩。"""
    tpl = base / "tpl"
    (tpl / "scripts").mkdir(parents=True)
    osnorm.write_text(tpl / "AGENT_RULES.md", "# 模板\n\n> **模板政策版本：`9.9.9`。**\n", newline="lf")
    osnorm.write_text(tpl / "README.md", "template readme\n", newline="lf")
    osnorm.write_text(tpl / "scripts" / "verify_rules.py", 'TEMPLATE_FILES = ["AGENT_RULES.md", "README.md"]\n'
        "import json\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    data = {'clean': False, 'findings': ['forced-by-test']}\n"
        "    if '--json' in sys.argv:\n"
        "        open(sys.argv[sys.argv.index('--json') + 1], 'w', encoding='utf-8').write(json.dumps(data))\n"
        "    print(json.dumps(data))\n", newline="lf")
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
        osnorm.write_text(self.ws / ".workspace" / "bootstrap.json", json.dumps({
            "workspace_status": "active",
            "python": {"mode": "local-venv", "runtime": {"identifier": "/x/.venv-312", "version": "3.12.0"}},
            "template": {"source_repo": str(ROOT), "current_policy_version": "2.4.0"},
        }, ensure_ascii=False, indent=2) + "\n", newline="lf")
        self.old_rules = "# 旧规则\n\n> **模板政策版本：`2.4.0`。**\n"
        osnorm.write_text(self.ws / "AGENT_RULES.md", self.old_rules, newline="lf")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, *args):
        return osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                               "--template", str(self.tpl), "--ref", "HEAD", *args], cwd=ROOT)

    def test_verify_failure_rolls_back(self):
        boot_before = osnorm.read_bytes(self.ws / ".workspace" / "bootstrap.json")
        p = self.run_sync("--apply", "--verify")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("自动回滚", p.stderr)
        self.assertEqual(osnorm.read_text(self.ws / "AGENT_RULES.md", bom=False, newline="preserve"), self.old_rules)
        # bootstrap 逐字节回到同步前（含 python/gate 段，不只是版本字段）
        self.assertEqual(osnorm.read_bytes(self.ws / ".workspace" / "bootstrap.json"), boot_before)
        self.assertFalse((self.ws / "scripts").exists())      # 本次新增的目录被清掉
        boot = json.loads(osnorm.read_text(self.ws / ".workspace" / "bootstrap.json", bom=False, newline="preserve"))
        self.assertEqual((boot.get("template") or {}).get("current_policy_version"), "2.4.0")

    def test_verify_failure_keep_on_fail(self):
        p = self.run_sync("--apply", "--verify", "--keep-on-fail")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("自动回滚", p.stderr)          # 明确要求保留：不回滚
        self.assertIn("9.9.9", osnorm.read_text(self.ws / "AGENT_RULES.md", bom=False, newline="preserve"))

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

        boot_before = osnorm.read_bytes(self.ws / ".workspace" / "bootstrap.json")
        # 就地调用会写真实 stdout/stderr：用 StringIO 兜住，避免终端编码（cp1252）影响断言
        with mock.patch.object(mod.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(["--workspace", str(self.ws), "--template", str(self.tpl),
                           "--ref", "HEAD", "--apply", "--verify"])
        self.assertEqual(rc, 3)
        self.assertEqual(osnorm.read_text(self.ws / "AGENT_RULES.md", bom=False, newline="preserve"), self.old_rules)
        self.assertEqual(osnorm.read_bytes(self.ws / ".workspace" / "bootstrap.json"), boot_before)

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
        self.assertEqual(osnorm.read_text(self.ws / "AGENT_RULES.md", bom=False, newline="preserve"), self.old_rules)


    def test_rollback_keeps_registry_with_foreign_namespace(self):
        """REGISTRY 里已有用户自己的命名空间时，--purge-generated 不得删除它。

        删掉后 `skill_audit --sync-registry` 只会重建 workspace-skills 那一节，
        个人库/本地层等行再也回不来 → 静默丢用户数据。
        """
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(reg, "# Skills\n\n| 命名空间 | root | 索引 | 路由状态 | TOOLS |\n|---|---|---|---|---|\n"
            "| `workspace-skills` | `.workspace/skills/` | 无 | 已登记（0 入口） | TOOLS |\n"
            "| `skills` | `/home/u/skills` | 根路由 | 已登记 | TOOLS |\n", newline="lf")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"], cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(reg.is_file(), "含用户命名空间的 REGISTRY 被删掉了")
        self.assertIn("保留未清除", p.stdout)
        self.assertIn("`skills`", osnorm.read_text(reg, bom=False, newline="preserve"))

    def test_rollback_keeps_registry_with_local_layer_namespace(self):
        """本地层命名空间 `workspace-skills-local` 也是用户数据：purge 不得删。

        历史 bug：判定用 `startswith("workspace-skills")`，而本地层恰好以它开头 →
        被判成模板原生 → 一个刚引入本地层的全新工作区会被静默删掉整张 REGISTRY。
        """
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(reg, "# Skills\n\n| 命名空间 | root | 索引 | 路由状态 | TOOLS |\n|---|---|---|---|---|\n"
            "| `workspace-skills` | `.workspace/skills/` | 无 | 已登记（0 入口） | TOOLS |\n"
            "| `workspace-skills-local` | `.workspace/skills-local/` | 无 | 已登记（1 个本地 skill） | TOOLS |\n", newline="lf")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"], cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(reg.is_file(), "含本地层命名空间的 REGISTRY 被删掉了")
        self.assertIn("workspace-skills-local", osnorm.read_text(reg, bom=False, newline="preserve"))

    def test_rollback_snapshots_purged_generated(self):
        """被清除的生成物必须先留快照，否则「清除」等于不可恢复。"""
        self.assertEqual(self.run_sync("--apply").returncode, 0)
        reg = self.ws / "skills" / "REGISTRY.md"
        reg.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(reg, "# generated\n", newline="lf")
        bk = sorted((self.ws / ".workspace").glob("backup-*-sync"))[-1]
        p = osnorm.run_text([sys.executable, str(SYNC), "--workspace", str(self.ws),
                            "--rollback", str(bk), "--purge-generated"], cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(reg.exists())
        snap = bk / "purged" / "skills" / "REGISTRY.md"
        self.assertTrue(snap.is_file(), "清除前没有留快照")
        self.assertEqual(osnorm.read_text(snap, bom=False, newline="preserve"), "# generated\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
