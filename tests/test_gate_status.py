"""gate_status：克隆后「到底有没有接线」的判定。

判据不是「文件在不在」，而是「有没有运行期证据」。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate_status.py"


def run(ws: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # HERMES_HOME 指向工作区内的空目录：否则本机真实插件日志会把 hermes 判成「有证据」
    base = {**os.environ, "HERMES_HOME": str(ws / "no-hermes-home")}
    if env:
        base.update(env)
    return subprocess.run([sys.executable, "-B", str(SCRIPT), "--workspace", str(ws), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=60,
                          env=base)


class GateStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_workspace_has_no_evidence(self):
        r = run(self.ws, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(all(not v["active"] for v in data["clients"].values()))
        self.assertEqual(data["missing"], [])

    def test_scope_matches_gate_core_after_local_state_is_moved_away(self):
        scripts = self.ws / "scripts"
        hooks = self.ws / ".workspace" / "hooks"
        scripts.mkdir(parents=True)
        hooks.mkdir(parents=True)
        local_state = scripts / "local_state.py"
        shutil.copyfile(ROOT / "scripts" / "local_state.py", local_state)
        shutil.copyfile(ROOT / ".workspace" / "hooks" / "gate_core.py", hooks / "gate_core.py")
        moved = scripts / "local_state.py.moved"
        local_state.rename(moved)
        code = (
            "import importlib.util,json,sys;from pathlib import Path;ws=Path(sys.argv[1]);"
            "spec=importlib.util.spec_from_file_location('gate_status_under_test',sys.argv[2]);"
            "gs=importlib.util.module_from_spec(spec);spec.loader.exec_module(gs);"
            "sys.path.insert(0,str(ws/'.workspace/hooks'));import gate_core;"
            "print(json.dumps([gs._machine_scope(ws),gate_core._fallback_scope()]))"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(self.ws), str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8", check=True)
        actual, expected = json.loads(result.stdout)
        self.assertRegex(actual, r"^[0-9a-f]{12}$")
        self.assertEqual(actual, expected)

    def test_require_fails_without_evidence(self):
        r = run(self.ws, "--require", "dsh")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("dsh", r.stdout)

    def test_dsh_evidence_from_apply_log_and_ledger(self):
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True)
        (state / "dsh-session-abc.json").write_text(json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), encoding="utf-8")
        (self.ws / ".workspace" / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "pid": 1, "cwd": str(self.ws)}) + "\n",
            encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["clients"]["dsh"]["active"])
        self.assertIn(".dsh-gate.log:apply", data["clients"]["dsh"]["sources"])

    def test_hermes_evidence_from_plugin_log(self):
        """hermes 不走 hook.py，证据是 plugin 自己的在场日志。"""
        home = self.ws / "hermes-home"
        (home / "logs").mkdir(parents=True)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        (home / "logs" / "workspace-gate-plugin.log").write_text(
            "2026-11-04 09:00:01 workspace-gate active (v1.9.0, hermes_home=%s)\n" % home +
            f"{now} post_tool_call commit: {self.ws}/AGENT_RULES.md\n",
            encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--json"],
                           capture_output=True, text=True, encoding="utf-8", timeout=60,
                           env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_stale_apply_with_fresh_mtime_is_inactive(self):
        """apply 的 at 是 2 小时前、但文件 mtime 是现在 —— 必须判过期（PRO R1：mtime 不是证据）。"""
        ws_dsh = self.ws / ".workspace"
        ws_dsh.mkdir(parents=True, exist_ok=True)
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
        (ws_dsh / ".dsh-gate.log").write_text(
            json.dumps({"at": old, "event": "apply", "pid": 1, "cwd": str(self.ws)}) + "\n",
            encoding="utf-8")
        (ws_dsh / ".hook-state").mkdir(parents=True, exist_ok=True)
        (ws_dsh / ".hook-state" / "dsh-session-x.json").write_text("{}", encoding="utf-8")
        os.utime(ws_dsh / ".dsh-gate.log", None)          # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--ttl-minutes", "30", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_stale_hermes_line_with_fresh_mtime_is_inactive(self):
        home = self.ws / "hermes-home"
        (home / "logs").mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        log = home / "logs" / "workspace-gate-plugin.log"
        log.write_text(f"{stamp} workspace-gate active (v1.9.0)\n", encoding="utf-8")
        os.utime(log, None)                               # mtime = 现在
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--ttl-minutes", "30", "--json"],
                           capture_output=True, text=True, encoding="utf-8", timeout=60,
                           env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_template_repo_must_not_ship_configured(self):
        """模板仓提交的 bootstrap 不得带本机运行态（PRO R1-3：克隆会继承假证据）。"""
        boot = json.loads((ROOT / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        if boot.get("workspace_status") != "template":
            self.skipTest("派生工作区，允许记录本机 gate 结论")
        gate = boot.get("gate") or {}
        self.assertNotEqual(gate.get("status"), "configured",
                            "模板仓不得提交 gate.status=configured（运行期事实不是模板事实）")
        self.assertNotIn("gate", boot,
                         "模板 bootstrap 不得分发任何 gate 节（R116：结论在本机状态）")

    def _seed_cc_evidence(self, scope: str) -> Path:
        state = self.ws / ".workspace" / ".hook-state" / scope
        state.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        (state / "gate.log").write_text(
            f"{stamp} post_read client=cc session=s1 file=AGENT_RULES.md\n", encoding="utf-8")
        return state

    def test_record_writes_scoped_gate_json_not_bootstrap(self):
        """结论落本机分片 gate.json；bootstrap 不再被写。"""
        (self.ws / ".workspace").mkdir(parents=True, exist_ok=True)
        boot_path = self.ws / ".workspace" / "bootstrap.json"
        boot_path.write_text(json.dumps({"workspace_status": "active"}), encoding="utf-8")
        hooks = self.ws / ".workspace" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copyfile(ROOT / ".workspace" / "hooks" / "gate_core.py", hooks / "gate_core.py")
        expected = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys;sys.path.insert(0,sys.argv[1]);import gate_core;print(gate_core._fallback_scope())",
             str(hooks)], capture_output=True, text=True, check=True).stdout.strip()
        self.assertRegex(expected, r"^[0-9a-f]{12}$")
        self._seed_cc_evidence(expected)
        r = run(self.ws, "--require", "cc", "--record", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / expected / "gate.json"
        self.assertTrue(gj.is_file(), "gate.json 未写到本机作用域目录")
        rec = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "configured")
        self.assertEqual(rec["harness"], ["cc"])
        self.assertEqual(rec["machine_scope"], expected)
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        self.assertNotIn("gate", boot, "bootstrap 不得再被写入 gate 结论")

    def test_other_machine_scope_evidence_is_ignored(self):
        """别的机器同步过来的 gate.log 不算本机证据。"""
        scripts = self.ws / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "local_state.py").write_text(
            (ROOT / "scripts" / "local_state.py").read_text(encoding="utf-8"), encoding="utf-8")
        sys.path.insert(0, str(ROOT / "scripts"))
        import local_state
        other = local_state.machine_scope({"WORKSPACE_CONTEXT_SEED": "machine-A"})
        mine = local_state.machine_scope({"WORKSPACE_CONTEXT_SEED": "machine-B"})
        self.assertNotEqual(other, mine)
        self._seed_cc_evidence(other)
        r_other = run(self.ws, "--require", "cc", env={"WORKSPACE_CONTEXT_SEED": "machine-A"})
        r_mine = run(self.ws, "--require", "cc", env={"WORKSPACE_CONTEXT_SEED": "machine-B"})
        self.assertEqual(r_other.returncode, 0, r_other.stdout)
        self.assertEqual(r_mine.returncode, 1, "另一台机器的证据被当成了本机证据")

    def test_cc_evidence_from_gate_log(self):
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        (state / "gate.log").write_text(f"{stamp} post_read client=cc session=s1 file=AGENT_RULES.md\n",
                                        encoding="utf-8")
        r = run(self.ws, "--require", "cc", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["clients"]["cc"]["active"])

    def test_ttl_marks_old_evidence_stale(self):
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        (state / "gate.log").write_text(f"{stamp} post_read client=codex session=s1 file=x\n",
                                        encoding="utf-8")
        r = run(self.ws, "--require", "codex", "--ttl-minutes", "5", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["codex"]["active"])

    # ---- §15 R116：结论落盘 ----
    def _boot(self):
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.parent.mkdir(parents=True, exist_ok=True)
        boot.write_text(json.dumps({"workspace_status": "active"}, ensure_ascii=False), encoding="utf-8")
        return boot

    def test_record_configured_writes_local_gate_json(self):
        boot = self._boot()
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "dsh-session-x.json").write_text(json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), encoding="utf-8")
        (self.ws / ".workspace" / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "pid": 1}) + "\n", encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--ttl-minutes", "30", "--record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        self.assertTrue(gj.is_file(), "结论未落到本机作用域 gate.json")
        gate = json.loads(gj.read_text(encoding="utf-8"))
        self.assertNotIn("gate", json.loads(boot.read_text(encoding="utf-8")),
                         "bootstrap 不得再被写入 gate 结论")
        self.assertEqual(gate["status"], "configured")
        self.assertEqual(gate["harness"], ["dsh"])
        self.assertTrue(gate["checked_at"])

    def test_record_refuses_configured_without_evidence(self):
        boot = self._boot()
        r = run(self.ws, "--require", "dsh", "--record")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("gate", json.loads(boot.read_text(encoding="utf-8")),
                         "无证据时不得写入任何 gate 结论")

    def test_record_unconfigured_requires_user_decision(self):
        boot = self._boot()
        r = run(self.ws, "--require", "dsh", "--record", "--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = run(self.ws, "--require", "dsh", "--record", "--force",
                "--user-decision", "用户明确接受本机无 gate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        gate = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "unconfigured")
        self.assertEqual(gate["decision_source"], "用户明确接受本机无 gate")

    def test_record_requires_require(self):
        self._boot()
        r = run(self.ws, "--record")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


    def test_dsh_apply_without_ledger_is_inactive(self):
        """只有 apply、没有账本 = 插件加载了但没处理过本工作区事件（SOL R1-4：双证缺一不算）。"""
        d = self.ws / ".workspace"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply"}) + "\n", encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_zero_ttl_is_rejected(self):
        """TTL=0 会让任意旧证据永远有效（SOL R1-4）——必须直接拒绝。"""
        r = run(self.ws, "--ttl-minutes", "0", "--json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_future_timestamp_is_not_evidence(self):
        """时间戳在未来（时钟回拨/伪造）不算证据。"""
        d = self.ws / ".workspace"
        d.mkdir(parents=True, exist_ok=True)
        future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 7200))
        (d / ".dsh-gate.log").write_text(json.dumps({"at": future, "event": "apply"}) + "\n",
                                         encoding="utf-8")
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        (d / ".hook-state" / "dsh-session-x.json").write_text("{}", encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_record_shrink_harness_requires_user_decision(self):
        """从 configured{cc,dsh} 缩到 {dsh} 属状态翻转，必须有人类决定。"""
        boot = self._boot()
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        (d / ".hook-state" / "dsh-session-x.json").write_text(json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), encoding="utf-8")
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply"}) + "\n", encoding="utf-8")
        boot.write_text(json.dumps({"workspace_status": "active",
                                    "gate": {"status": "configured", "harness": ["cc", "dsh"]}}),
                        encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--record")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = run(self.ws, "--require", "dsh", "--record", "--user-decision", "用户：分析机不再用 cc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        self.assertEqual(json.loads(gj.read_text(encoding="utf-8"))["harness"], ["dsh"])
        self.assertEqual(json.loads(boot.read_text(encoding="utf-8"))["gate"]["harness"], ["cc", "dsh"],
                         "旧位置 bootstrap.gate 只读，不得被改写")

    def test_ledger_timestamp_beats_file_mtime(self):
        """账本 events[].ts 是 2 小时前、mtime 是现在：必须判过期（SOL R2-6：mtime 可被 touch）。"""
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        old_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        (d / ".hook-state" / "dsh-session-x.json").write_text(
            json.dumps({"events": [{"type": "worktree-change", "ts": old_ts}]}), encoding="utf-8")
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply"}) + "\n", encoding="utf-8")
        os.utime(d / ".hook-state" / "dsh-session-x.json", None)   # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_reverse_flip_requires_user_decision(self):
        """unconfigured → configured 也是翻转（R117），必须有人类决定。"""
        boot = self._boot()
        boot.write_text(json.dumps({"workspace_status": "active",
                                    "gate": {"status": "unconfigured", "harness": []}}), encoding="utf-8")
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        (d / ".hook-state" / "dsh-session-x.json").write_text(json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), encoding="utf-8")
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply"}) + "\n",
            encoding="utf-8")
        r = run(self.ws, "--require", "dsh", "--record")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = run(self.ws, "--require", "dsh", "--record", "--user-decision", "用户：dsh 已接线")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_hermes_unrelated_fresh_line_does_not_keep_it_active(self):
        """旧 active + 一条无关的新日志：不算活跃（PRO R2-A：无关写入不得刷新证据）。"""
        home = self.ws / "hermes-home"
        (home / "logs").mkdir(parents=True)
        old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        new = time.strftime("%Y-%m-%d %H:%M:%S")
        (home / "logs" / "workspace-gate-plugin.log").write_text(
            f"{old} workspace-gate active (v1.9.0)\n{new} something unrelated happened\n",
            encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--json"],
                           capture_output=True, text=True, encoding="utf-8", timeout=60,
                           env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_ledger_without_ts_is_not_evidence(self):
        """账本里没有可解析的事件/锚时间：不算证据，且不得回退 mtime（touch 就能骗）。

        两类夹具：① 空账本 `{}`；② 有 events/anchors 但缺 ts（SOL R3 验收要求）。
        """
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        (d / ".hook-state" / "dsh-session-x.json").write_text(
            json.dumps({"events": [{"type": "gate-commit"}], "anchors": {"AGENT_RULES.md": {}}}),
            encoding="utf-8")
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply"}) + "\n",
            encoding="utf-8")
        os.utime(d / ".hook-state" / "dsh-session-x.json", None)   # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_empty_ledger_object_is_not_evidence(self):
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        (d / ".hook-state" / "dsh-session-empty.json").write_text("{}", encoding="utf-8")
        (d / ".dsh-gate.log").write_text(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply"}) + "\n",
            encoding="utf-8")
        os.utime(d / ".hook-state" / "dsh-session-empty.json", None)
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

if __name__ == "__main__":
    unittest.main()
