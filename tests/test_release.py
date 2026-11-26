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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import osnorm  # noqa: E402
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_under_test", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

# --verify 内部会跑本套测试；T3 在嵌套运行时必须自我跳过，否则无限递归。
NESTED_VERIFY = os.environ.get("RELEASE_VERIFY_NESTED") == "1"


@unittest.skipUnless((ROOT / "CHANGELOG.md").is_file(),
                     "正式发版用例需要模板仓 CHANGELOG.md")
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(
            ".git", ".venv", ".venv-*", "__pycache__", ".hook-state"))
        # 正式发版夹具模拟 main：R46 S10 规定 main 不携带 dev 专属三文件。
        for rel in (".workspace/SEQUENCE.md", ".workspace/release_dev.json", "CHANGELOG_DEV.md"):
            (self.repo / rel).unlink(missing_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")
        # 版本从账本末条派生：测试必须与仓库当前政策版本无关，
        # 否则 --apply 之后的 `release.py --verify` 里本套测试会自己再失败。
        ledger = json.loads(osnorm.read_text(self.repo/".workspace/releases.json", bom=False, newline="preserve"))
        self.current = str(ledger[-1]["version"])
        major, minor, _ = (int(x) for x in self.current.split("."))
        self.next = f"{major}.{minor + 1}.0"

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return osnorm.run_text(["git", *args], cwd=self.repo, check=True, errors="replace")

    def cli(self, *args):
        return osnorm.run_text([str(Path(__import__("sys").executable)), "-B", "scripts/release.py", *args], cwd=self.repo, errors="replace")

    def test_dry_run_does_not_write(self):
        before = self.git("status", "--porcelain").stdout
        result = self.cli("--version", self.next, "--summary", "测试", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)

    def test_apply_synchronizes_four_files(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 显式 UTF-8：Windows 上 `read_text()` 默认按 locale（cp1252/cp936）解码，
        # 中文断言会 mojibake 或直接 UnicodeDecodeError。
        self.assertIn(f"模板政策版本：`{self.next}`",
                      osnorm.read_text(self.repo/"AGENT_RULES.md", bom=False, newline="preserve"))
        self.assertIn(f"## {self.next}（", osnorm.read_text(self.repo/"CHANGELOG.md", bom=False, newline="preserve"))
        self.assertEqual(json.loads(osnorm.read_text(self.repo/".workspace/bootstrap.json", bom=False, newline="preserve"))["template"]["current_policy_version"], self.next)
        self.assertEqual(json.loads(osnorm.read_text(self.repo/".workspace/releases.json", bom=False, newline="preserve"))[-1]["version"], self.next)

    def test_duplicate_version_rejected(self):
        result = self.cli("--version", self.current, "--summary", "重复", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严格升级", result.stderr)

    def test_downgrade_rejected(self):
        result = self.cli("--version", "3.6.9", "--summary", "降级", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严格升级", result.stderr)

    def test_dirty_tree_rejected(self):
        osnorm.write_text(self.repo/"dirty.txt", "dirty", newline="lf")
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
        ledger = json.loads(osnorm.read_text(self.repo/".workspace/releases.json", bom=False, newline="preserve"))
        ledger[-1]["version"] = "3.6.9"
        osnorm.write_text(self.repo/".workspace/releases.json", json.dumps(ledger), newline="lf")
        self.assertTrue(any("版本升级必须经 release.py" in x for x in facts.check(self.repo)))

    # ---- T1：合法 --apply 之后 verify_rules 必须 clean（缺陷 1 回归） ----
    def test_apply_leaves_verify_rules_clean(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        vr = osnorm.run_text([sys.executable, "-B", "scripts/verify_rules.py", "--workspace", "."], cwd=self.repo, errors="replace")
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
        baseline_ledger = json.loads(osnorm.read_text(self.repo/".workspace/releases.json", bom=False, newline="preserve"))
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
        self.assertEqual(json.loads(osnorm.read_text(self.repo/".workspace/releases.json", bom=False, newline="preserve")),
                         baseline_ledger)
        residual = [ln for ln in self.git("status", "--porcelain").stdout.splitlines() if ln.strip()]
        self.assertEqual(residual, [" M README.md"])

    # ---- T3：--apply 成功后 release.py --verify rc=0 ----
    @unittest.skipIf(NESTED_VERIFY, "嵌套的 release --verify 运行：跳过以避免无限递归")
    def test_apply_then_release_verify_passes(self):
        result = self.cli("--version", self.next, "--summary", "测试", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = dict(os.environ, RELEASE_VERIFY_NESTED="1")
        v = osnorm.run_text([sys.executable, "-B", "scripts/release.py", "--verify"], cwd=self.repo, errors="replace", env=env)
        self.assertEqual(v.returncode, 0, (v.stdout + v.stderr)[-4000:])

    # ---- --verify 的覆盖面本身（数据化，不需要真跑全套） ----
    def test_verify_commands_cover_verify_rules(self):
        flat = [" ".join(str(c) for c in cmd) for cmd in release.verify_commands(sys.executable)]
        self.assertTrue(any("verify_rules.py" in c for c in flat), flat)
        self.assertTrue(any("--workspace" in c and "." in c for c in flat if "verify_rules" in c), flat)
        self.assertTrue(any("version_check.py" in c for c in flat), flat)
        self.assertTrue(any("policy_facts.py" in c for c in flat), flat)


class DevReleaseTests(unittest.TestCase):
    """dev 四段版本账本（`NN.X.Y.Z`）：只写两个 dev 事实位，政策版本链一点不碰。

    用户裁决的硬约束在这里全部落成断言：同版本二次 apply 必须被拒、dev 版本的三段必须等于
    当前政策版本、`AGENT_RULES.md` / `releases.json` 在 dev 发版后逐字节不变。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(
            ".git", ".venv", ".venv-*", "__pycache__", ".hook-state"))
        # 嵌套的正式发布 verify 来源可能是 main（按 S10 不含 dev 两文件）；dev 夹具自行建最小事实位。
        if not (self.repo / "CHANGELOG_DEV.md").is_file():
            osnorm.write_text(self.repo / "CHANGELOG_DEV.md", "# CHANGELOG_DEV\n", newline="lf")
        if not (self.repo / ".workspace/release_dev.json").is_file():
            osnorm.write_text(self.repo / ".workspace/release_dev.json", "[]\n", newline="lf")
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")
        self.policy = release.policy_version(self.repo)
        self.ledger = release.load_dev_ledger(self.repo)
        self.last = str(self.ledger[-1]["version"]) if self.ledger else None
        self.next_round = int(self.ledger[-1]["round"]) + 1 if self.ledger else release.DEV_ROUND_BASE

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return osnorm.run_text(["git", *args], cwd=self.repo, check=True, errors="replace")

    def cli(self, *args):
        return osnorm.run_text([sys.executable, "-B", "scripts/release.py", *args],
                               cwd=self.repo, errors="replace")

    def facts(self):
        return osnorm.run_text([sys.executable, "-B", "scripts/policy_facts.py", "--check"],
                               cwd=self.repo, errors="replace")

    def test_dry_run_derives_current_round_and_does_not_write(self):
        before = self.git("status", "--porcelain").stdout
        result = self.cli("--dev", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        # 不给参数时派生 `<账本末条轮次>.<政策版本>`：空账本才是 44，否则取末条轮次。
        expected_round = self.next_round - 1 if self.ledger else release.DEV_ROUND_BASE
        expected = f"{expected_round}.{self.policy}"
        self.assertIn(expected, result.stdout)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)

    def test_apply_writes_only_dev_facts(self):
        policy_before = (self.repo / "AGENT_RULES.md").read_bytes()
        releases_before = osnorm.read_text(self.repo / ".workspace/releases.json",
                                           bom=False, newline="preserve")
        expected = f"{self.next_round}.{self.policy}"
        result = self.cli("--dev", "--apply", "--round", str(self.next_round), "--summary", "测试 dev")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # ① 政策版本链逐字节不变
        self.assertEqual((self.repo / "AGENT_RULES.md").read_bytes(), policy_before)
        self.assertEqual(osnorm.read_text(self.repo / ".workspace/releases.json",
                                          bom=False, newline="preserve"), releases_before)
        # ② 两条 dev 事实位同步入账
        self.assertEqual(release.dev_heading_versions(self.repo / "CHANGELOG_DEV.md")[0], expected)
        ledger = json.loads(osnorm.read_text(self.repo / ".workspace/release_dev.json",
                                             bom=False, newline="preserve"))
        self.assertEqual(ledger[-1]["version"], expected)
        self.assertEqual(ledger[-1]["round"], self.next_round)
        self.assertIsNone(ledger[-1]["tag"])          # dev 永不打 tag
        # 本轮没有 `.workspace/iterations/R<round>.md` → 两个冻结锚字段都不写（S12 对这条不判）
        self.assertNotIn("iterations", ledger[-1])
        self.assertNotIn("iterations_sha256", ledger[-1])
        # ③ 判据自洽：四段版本不被三段判据误杀
        facts = self.facts()
        self.assertEqual(facts.returncode, 0, facts.stdout)

    # ---- S12：迭代日志存在时，账本必须冻结「节数 + sha256」 ----
    def test_apply_freezes_iterations_anchor(self):
        import hashlib
        numbers = (1, 2)
        body = (f"# R{self.next_round} 迭代日志\n\n" + "".join(
            f"## it{n} · 2026-11-24\n\n- 触发：夹具\n- 改动：夹具\n- 证伪：夹具\n"
            f"- 结论：pending\n- 遗留：L{n}\n\n" for n in numbers))
        iter_dir = self.repo / ".workspace" / "iterations"
        iter_dir.mkdir(parents=True, exist_ok=True)
        iter_path = iter_dir / f"R{self.next_round}.md"
        osnorm.write_text(iter_path, body, newline="lf")
        result = self.cli("--dev", "--apply", "--round", str(self.next_round), "--summary", "带迭代日志")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        entry = json.loads(osnorm.read_text(self.repo / ".workspace/release_dev.json",
                                            bom=False, newline="preserve"))[-1]
        self.assertEqual(entry["iterations"], len(numbers))
        self.assertEqual(entry["iterations_sha256"],
                         hashlib.sha256(iter_path.read_bytes()).hexdigest())
        # 既有字段的顺序与含义不变：两个新字段追加在 files_sha256 之后
        self.assertEqual(list(entry)[:7], ["version", "round", "date", "summary", "commit", "tag",
                                           "files_sha256"])
        # 交叉断言：release.py 与 sequence_check.py 必须按同一形态数节（两处各数一遍会分叉）
        import sequence_check as sc
        self.assertEqual(sc.count_iterations(osnorm.read_text(iter_path))[0], entry["iterations"])
        # 写盘后 S12 直接判绿（冻结值与当前文件一致）
        status, detail, findings = sc.check_s12([entry], self.repo)
        self.assertEqual(status, "PASS", (detail, findings))

    def test_same_round_second_apply_rejected(self):
        first = self.cli("--dev", "--apply", "--round", str(self.next_round), "--summary", "首入")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = osnorm.read_text(self.repo / "CHANGELOG_DEV.md", bom=False, newline="preserve")
        second = self.cli("--dev", "--apply", "--round", str(self.next_round), "--summary", "重复")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("严格升级", second.stderr)
        # 无参 apply 派生同一轮次，同样被拒（版本只升不降）
        third = self.cli("--dev", "--apply")
        self.assertNotEqual(third.returncode, 0)
        self.assertIn("严格升级", third.stderr)
        self.assertEqual(osnorm.read_text(self.repo / "CHANGELOG_DEV.md",
                                          bom=False, newline="preserve"), before)

    def test_version_must_be_four_segments_and_match_policy(self):
        wrong_policy = self.cli("--dev", "--version", f"{self.next_round}.9.9.9",
                                "--apply", "--summary", "错政策号")
        self.assertNotEqual(wrong_policy.returncode, 0)
        self.assertIn("必须等于当前政策版本", wrong_policy.stderr)
        three_seg = self.cli("--dev", "--version",
                             f"{self.next_round}.{'.'.join(self.policy.split('.')[:2])}")
        self.assertNotEqual(three_seg.returncode, 0)
        self.assertIn("非法 dev 版本", three_seg.stderr)
        tagged = self.cli("--dev", "--tag", f"{self.next_round}.{self.policy}")
        self.assertNotEqual(tagged.returncode, 0)
        self.assertIn("不打 tag", tagged.stderr)

    def test_ledger_changelog_drift_is_red(self):
        result = self.cli("--dev", "--apply", "--round", str(self.next_round), "--summary", "测试")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = self.repo / "CHANGELOG_DEV.md"
        text = osnorm.read_text(path, bom=False, newline="preserve")
        expected = f"{self.next_round}.{self.policy}"
        # ① 手改变更日志标题（账本不动）→ 末条对首条必红，--dev --verify 也红
        osnorm.write_text(path, text.replace(f"## {expected}", f"## {self.next_round + 1}.{self.policy}", 1),
                          newline="lf")
        drift = self.facts()
        self.assertEqual(drift.returncode, 1, drift.stdout)
        self.assertIn("末条版本", drift.stdout)
        verify = self.cli("--dev", "--verify")
        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("首个标题", verify.stderr)
        # ② dev 文件里出现三段标题 → 四段格式判据必红（三段版本不得串进 dev 链）
        osnorm.write_text(path, text.replace(f"## {expected}", f"## {self.policy}", 1), newline="lf")
        three = self.facts()
        self.assertEqual(three.returncode, 1, three.stdout)
        self.assertIn("须四段", three.stdout)


if __name__ == "__main__":
    unittest.main()
