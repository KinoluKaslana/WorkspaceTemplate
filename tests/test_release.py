import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_under_test", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

# --verify 内部会跑本套测试；T3 在嵌套运行时必须自我跳过，否则无限递归。
NESTED_VERIFY = os.environ.get("RELEASE_VERIFY_NESTED") == "1"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(
            ".git", ".venv", ".venv-*", "__pycache__", ".hook-state"))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")
        # 版本从账本末条派生：测试必须与仓库当前政策版本无关，
        # 否则 --apply 之后的 `release.py --verify` 里本套测试会自己再失败。
        ledger = json.loads((self.repo/".workspace/releases.json").read_text(encoding="utf-8"))
        self.current = str(ledger[-1]["version"])
        major, minor, _ = (int(x) for x in self.current.split("."))
        self.next = f"{major}.{minor + 1}.0"

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              text=True, capture_output=True)

    def cli(self, *args):
        return subprocess.run([str(Path(__import__("sys").executable)), "-B", "scripts/release.py", *args],
                              cwd=self.repo, text=True, capture_output=True)

    def test_dry_run_does_not_write(self):
        before = self.git("status", "--porcelain").stdout
        result = self.cli("--version", self.next, "--summary", "测试", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)

    def test_apply_synchronizes_four_files(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"模板政策版本：`{self.next}`", (self.repo/"AGENT_RULES.md").read_text())
        self.assertIn(f"## {self.next}（", (self.repo/"CHANGELOG.md").read_text())
        self.assertEqual(json.loads((self.repo/".workspace/bootstrap.json").read_text())["template"]["current_policy_version"], self.next)
        self.assertEqual(json.loads((self.repo/".workspace/releases.json").read_text())[-1]["version"], self.next)

    def test_duplicate_version_rejected(self):
        result = self.cli("--version", self.current, "--summary", "重复", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严格升级", result.stderr)

    def test_downgrade_rejected(self):
        result = self.cli("--version", "3.6.9", "--summary", "降级", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严格升级", result.stderr)

    def test_dirty_tree_rejected(self):
        (self.repo/"dirty.txt").write_text("dirty", encoding="utf-8")
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("工作树不干净", result.stderr)

    def test_failure_rolls_back(self):
        paths = [self.repo/x for x in release.MUTABLE]
        before = {str(p): p.read_bytes() if p.is_file() else None for p in paths}
        with mock.patch.object(release, "run", side_effect=release.ReleaseError("forced --write failure")):
            with self.assertRaises(release.ReleaseError):
                release.apply_release(self.next, "测试", self.repo)
        after = {str(p): p.read_bytes() if p.is_file() else None for p in paths}
        self.assertEqual(after, before)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_ledger_tail_must_match_policy(self):
        spec = importlib.util.spec_from_file_location("facts_under_test", self.repo/"scripts/policy_facts.py")
        facts = importlib.util.module_from_spec(spec); spec.loader.exec_module(facts)
        ledger = json.loads((self.repo/".workspace/releases.json").read_text())
        ledger[-1]["version"] = "3.6.9"
        (self.repo/".workspace/releases.json").write_text(json.dumps(ledger), encoding="utf-8")
        self.assertTrue(any("版本升级必须经 release.py" in x for x in facts.check(self.repo)))

    # ---- T1：合法 --apply 之后 verify_rules 必须 clean（缺陷 1 回归） ----
    def test_apply_leaves_verify_rules_clean(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        vr = subprocess.run([sys.executable, "-B", "scripts/verify_rules.py", "--workspace", "."],
                            cwd=self.repo, text=True, capture_output=True)
        self.assertEqual(vr.returncode, 0, vr.stdout + vr.stderr)
        report = json.loads(vr.stdout)
        self.assertTrue(report["clean"], report["findings"])
        # 账本必须仍在注册表里被哈希校验（不许靠剔除 releases.json 换绿灯）
        self.assertIn(".workspace/releases.json", report["checked_files"])

    # ---- T2：回滚期只读文件 → 其余文件仍恢复 + 未恢复清单 + 非零退出（缺陷 2 回归） ----
    def test_rollback_reports_unrestored_and_restores_the_rest(self):
        baseline = {rel: ((self.repo/rel).read_bytes() if (self.repo/rel).is_file() else None)
                    for rel in release.MUTABLE}
        ledger = self.repo/".workspace/releases.json"
        ledger.chmod(0o444)  # 真实只读：append_ledger 写不动，回滚时也写不动
        try:
            result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        finally:
            ledger.chmod(0o644)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("回滚未恢复清单", result.stderr)
        self.assertIn(".workspace/releases.json", result.stderr)
        self.assertIn("PermissionError", result.stderr)
        self.assertIn("原始异常", result.stderr)
        for rel, before in baseline.items():  # 单文件失败不得中断其余恢复
            p = self.repo/rel
            self.assertEqual(p.read_bytes() if p.is_file() else None, before, rel)
        self.assertEqual(self.git("status", "--porcelain").stdout, "", "回滚后不应残留半发版改动")

    # ---- T2b：既已被改写、又恢复失败的文件 → 清单标出真实缺口，其余照常恢复 ----
    def test_rollback_survives_a_modified_file_that_cannot_be_restored(self):
        baseline_ledger = json.loads((self.repo/".workspace/releases.json").read_text(encoding="utf-8"))
        real_run = release.run

        def saboteur(cmd, root=None):
            out = real_run(cmd, **({} if root is None else {"root": root}))  # 真跑子进程、真写盘
            joined = " ".join(str(c) for c in cmd)
            if not armed and "docs_index.py" in joined:
                (self.repo/"README.md").chmod(0o444)   # 此刻 README.md 已被 policy_facts 改写
                armed.append(True)
            elif armed and "version_check.py" in joined:
                raise release.ReleaseError("injected step failure after all writes")
            return out

        armed = []
        err = io.StringIO()
        try:
            with mock.patch.object(release, "run", saboteur), redirect_stderr(err), redirect_stdout(io.StringIO()):
                with self.assertRaises(release.ReleaseError):
                    release.apply_release(self.next, "测试", self.repo)
        finally:
            (self.repo/"README.md").chmod(0o644)
        report = err.getvalue()
        self.assertIn("回滚未恢复清单", report)
        self.assertIn("README.md", report)
        self.assertIn("PermissionError", report)
        self.assertIn("磁盘内容与备份不一致，需人工恢复", report)
        # 其余文件（含账本）确实被恢复
        self.assertEqual(json.loads((self.repo/".workspace/releases.json").read_text(encoding="utf-8")),
                         baseline_ledger)
        residual = [ln for ln in self.git("status", "--porcelain").stdout.splitlines() if ln.strip()]
        self.assertEqual(residual, [" M README.md"])

    # ---- T3：--apply 成功后 release.py --verify rc=0 ----
    @unittest.skipIf(NESTED_VERIFY, "嵌套的 release --verify 运行：跳过以避免无限递归")
    def test_apply_then_release_verify_passes(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = dict(os.environ, RELEASE_VERIFY_NESTED="1")
        v = subprocess.run([sys.executable, "-B", "scripts/release.py", "--verify"], cwd=self.repo,
                           text=True, capture_output=True, env=env)
        self.assertEqual(v.returncode, 0, (v.stdout + v.stderr)[-4000:])

    # ---- --verify 的覆盖面本身（数据化，不需要真跑全套） ----
    def test_verify_commands_cover_verify_rules(self):
        flat = [" ".join(str(c) for c in cmd) for cmd in release.verify_commands(sys.executable)]
        self.assertTrue(any("verify_rules.py" in c for c in flat), flat)
        self.assertTrue(any("--workspace" in c and "." in c for c in flat if "verify_rules" in c), flat)
        self.assertTrue(any("version_check.py" in c for c in flat), flat)
        self.assertTrue(any("policy_facts.py" in c for c in flat), flat)


if __name__ == "__main__":
    unittest.main()
