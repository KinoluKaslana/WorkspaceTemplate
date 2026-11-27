"""`scripts/check_all.py` 的**放行提示**：三席放行 ≠ 用户放行。

为什么要单独钉住：`push` 永远由用户执行（`release.py` 的硬约束），所以「全绿但没 push」是
正常的中间态——它不该判红，但也不能被读成「已放行」。判据只有一条：

  · 当前分支**有上游**且 `ahead > 0`（有未推送提交）
  · **dev 账本非空**（本轮真的有入账条目）
  两条同时满足 → 打印一行 WARN，**退出码不变**（`--json` 时走 stderr，stdout 仍只有一个 JSON）。

这里把四件事分开测：`release_hint()` 的四个输入分支（无账本 / 无上游 / ahead>0 / ahead=0），
以及 `main()` 的接线（提示出现、rc 仍为 0、`--json` 只输出一个 JSON 对象）。
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import osnorm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_all_under_test", ROOT / "scripts/check_all.py")
check_all = importlib.util.module_from_spec(SPEC)
# dataclass + `from __future__ import annotations` 需要模块已在 sys.modules 里（否则
# dataclasses 的 `_is_type` 取不到 `cls.__module__.__dict__`）；按文件加载时必须先登记。
sys.modules[SPEC.name] = check_all
SPEC.loader.exec_module(check_all)


class ReleaseHint(unittest.TestCase):
    """夹具是**真 git 仓库**（本地 bare 当 origin）：不 mock git，判据才看得见真行为。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.origin = base / "origin.git"
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / ".workspace").mkdir()
        self.set_ledger([])
        osnorm.write_text(self.repo / "README.md", "fixture\n", newline="lf")
        self.commit("baseline")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return osnorm.run_text(["git", *args], cwd=self.repo, check=True, errors="replace")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def set_ledger(self, entries):
        osnorm.write_text(self.repo / ".workspace" / "release_dev.json",
                          json.dumps(entries, ensure_ascii=False) + "\n", newline="lf")
        return entries

    def push(self):
        self.git("init", "-q", "--bare", str(self.origin))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "dev")

    def test_no_ledger_entries_never_warns(self):
        """账本为空（或不存在）：即使有未推送提交也不提示。"""
        self.push()
        osnorm.write_text(self.repo / "README.md", "changed\n", newline="lf")
        self.commit("unpushed")
        self.assertIsNone(check_all.release_hint(self.repo))
        (self.repo / ".workspace" / "release_dev.json").unlink()
        self.assertIsNone(check_all.release_hint(self.repo))

    def test_no_upstream_never_warns(self):
        """新分支从未推送：没有可比的远端，交给 S11 的「仅有本地锚」WARN，这里不提示。"""
        self.set_ledger([{"version": "45.3.8.0", "round": 45}])
        self.commit("ledger entry")
        self.assertIsNone(check_all.release_hint(self.repo))

    def test_ahead_of_upstream_warns(self):
        """有上游 + 有未推送提交 + 账本非空 → 提示（文案固定，含「全绿 ≠ 已放行」）。"""
        self.set_ledger([{"version": "45.3.8.0", "round": 45}])
        self.push()
        osnorm.write_text(self.repo / "README.md", "changed\n", newline="lf")
        self.commit("unpushed")
        self.assertEqual(check_all.release_hint(self.repo), check_all.RELEASE_WARN)
        self.assertIn("尚未由用户放行", check_all.RELEASE_WARN)
        self.assertIn("全绿 ≠ 已放行", check_all.RELEASE_WARN)

    def test_up_to_date_never_warns(self):
        """没有未推送提交（ahead=0）→ 不提示。"""
        self.set_ledger([{"version": "45.3.8.0", "round": 45}])
        self.push()
        self.assertIsNone(check_all.release_hint(self.repo))

    def test_not_a_repo_never_warns(self):
        """非 git 目录：提示判据自己失能，不崩也不误报。"""
        lonely = Path(self.tmp.name) / "not-a-repo"
        (lonely / ".workspace").mkdir(parents=True)
        osnorm.write_text(lonely / ".workspace" / "release_dev.json", "[{\"round\": 45}]\n", newline="lf")
        self.assertIsNone(check_all.release_hint(lonely))


class MainWiring(unittest.TestCase):
    """`main()` 的接线：提示在阶段跑完后出现，且**不改变退出码**。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / ".workspace").mkdir()
        osnorm.write_text(self.repo / ".workspace" / "release_dev.json",
                          "[{\"version\": \"45.3.8.0\", \"round\": 45}]\n", newline="lf")
        osnorm.write_text(self.repo / "README.md", "fixture\n", newline="lf")
        self.commit("baseline")
        origin = Path(self.tmp.name) / "origin.git"
        self.git("init", "-q", "--bare", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "-u", "origin", "dev")
        osnorm.write_text(self.repo / "README.md", "changed\n", newline="lf")
        self.commit("unpushed")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return osnorm.run_text(["git", *args], cwd=self.repo, check=True, errors="replace")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def fake_stage(self, stage, quiet=False):
        return {"name": stage.name, "ok": True, "rc": 0, "seconds": 0.0, "tail": []}

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(check_all, "ROOT", self.repo), \
                mock.patch.object(check_all, "run_stage", side_effect=self.fake_stage), \
                redirect_stdout(out), redirect_stderr(err):
            rc = check_all.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_warn_printed_and_exit_code_unchanged(self):
        rc, out, _ = self.run_main(["--check"])
        self.assertEqual(rc, 0, out)                      # 全绿仍是 0：提示不算失败
        self.assertIn("WARN", out)
        self.assertIn(check_all.RELEASE_WARN, out)
        # 提示必须排在阶段汇总之后（「所有阶段跑完后」）
        self.assertLess(out.index("check_all: ok"), out.index("WARN"))

    def test_json_mode_keeps_stdout_machine_readable(self):
        rc, out, err = self.run_main(["--check", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)                             # stdout 只有一个 JSON 对象
        self.assertTrue(data["ok"])
        expected = len([s for s in check_all.STAGES if s.kind == check_all.KIND_CHECK])
        self.assertEqual(len(data["stages"]), expected)
        self.assertIn(check_all.RELEASE_WARN, err)          # 提示走 stderr

    def test_no_upstream_no_warn_in_main(self):
        self.git("remote", "remove", "origin")
        rc, out, err = self.run_main(["--check"])
        self.assertEqual(rc, 0)
        self.assertNotIn("WARN", out + err)


if __name__ == "__main__":
    unittest.main()
