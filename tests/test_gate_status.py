"""gate_status：克隆后「到底有没有接线」的判定。

判据不是「文件在不在」，而是「有没有运行期证据」。
"""
from __future__ import annotations

import importlib.util
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate_status.py"

# 文本 I/O 单一实现：osnorm（R1/R2 门禁的唯一入口）。夹具与被测产物都经它读写。
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入


def run(ws: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # HERMES_HOME 指向工作区内的空目录：否则本机真实插件日志会把 hermes 判成「有证据」
    base = {**os.environ, "HERMES_HOME": str(ws / "no-hermes-home")}
    if env:
        base.update(env)
    return osnorm.run_text([sys.executable, "-B", str(SCRIPT), "--workspace", str(ws), *args], timeout=60, env=base)


def _load_gate_status_module():
    """按路径加载门禁脚本（测试进程内调用 `_machine_scope`，不走 CLI）。"""
    spec = importlib.util.spec_from_file_location("gate_status_scope_parity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_gate_core_module():
    hooks = ROOT / ".workspace" / "hooks"
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    import gate_core
    return gate_core


class ScopeParity(unittest.TestCase):
    """R48/P5-C8（原 O4）：gate_status 与 gate_core 对同机 scope 必须逐字相同。

    旧实现是两份算法：gate_status 收尾 `or "unknown"`，gate_core 直接返回空串——
    「两边都取不到机器身份」时一个落 `.hook-state/unknown/`、一个落共享根 `.hook-state/`。
    现在 gate_status 委托 gate_core；本组用例既比对返回值，也钉住「永不为空 / 永不回退共享根」。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scope-parity-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.gs = _load_gate_status_module()
        self.core = _load_gate_core_module()

    def _workspace(self, name: str, *, with_scripts: bool) -> Path:
        ws = self.base / name
        (ws / ".workspace").mkdir(parents=True)
        if with_scripts:
            (ws / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts" / "local_state.py", ws / "scripts" / "local_state.py")
        return ws

    def _assert_parity(self, ws: Path, env: dict) -> str:
        with mock.patch.dict(os.environ, env, clear=False):
            self.core._SCOPE_CACHE.clear()
            status_scope = self.gs._machine_scope(ws)
            core_scope = self.core.machine_scope(str(ws))
        self.assertEqual(status_scope, core_scope, f"同机 scope 分叉：{ws} / {env}")
        self.assertRegex(status_scope, r"^[0-9a-f]{12}$", "scope 必须是 12 位 hex")
        self.assertEqual(self.core.state_dir(str(ws)).name, status_scope,
                         "状态目录名必须是同一个 scope")
        return status_scope

    def test_verbatim_parity_with_and_without_local_state(self) -> None:
        with_local = self._workspace("with-local", with_scripts=True)
        without_local = self._workspace("without-local", with_scripts=False)
        a = self._assert_parity(with_local, {"WORKSPACE_CONTEXT_SEED": "machine-A"})
        b = self._assert_parity(without_local, {"WORKSPACE_CONTEXT_SEED": "machine-B"})
        self.assertNotEqual(a, b, "两台机器（不同 seed）落到了同一个 scope")

    def test_verbatim_parity_across_cwd_spellings(self) -> None:
        ws = self._workspace("spellings", with_scripts=True)
        scope = self._assert_parity(ws, {"WORKSPACE_CONTEXT_SEED": "machine-A"})
        for spelling in (str(ws) + os.sep, str(ws / "."), str(ws / "sub" / "..")):
            with self.subTest(spelling=spelling):
                with mock.patch.dict(os.environ, {"WORKSPACE_CONTEXT_SEED": "machine-A"}):
                    self.core._SCOPE_CACHE.clear()
                    self.assertEqual(self.gs._machine_scope(Path(spelling)), scope)
                    self.assertEqual(self.core.machine_scope(spelling), scope)

    def test_empty_identity_falls_back_to_unknown_on_both_sides(self) -> None:
        ws = self._workspace("no-identity", with_scripts=False)
        with mock.patch.object(self.core, "_fallback_machine_material", lambda: b""), \
                mock.patch.object(self.core, "_fallback_seed", lambda: b""):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WORKSPACE_CONTEXT_SEED", None)
                os.environ.pop("XDG_STATE_HOME", None)
                os.environ.pop("LOCALAPPDATA", None)
                self.core._SCOPE_CACHE.clear()
                status_scope = self.gs._machine_scope(ws)
                core_scope = self.core.machine_scope(str(ws))
        self.assertEqual(status_scope, "unknown")
        self.assertEqual(core_scope, "unknown")
        self.assertEqual(self.core.state_dir(str(ws)).name, "unknown",
                         "空身份不得回退到共享根 .hook-state/")
        self.assertNotEqual(self.core.state_dir(str(ws)), ws / ".workspace" / ".hook-state")


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
        # R41：gate_core 经装载器使用 osnorm；单文件夹具也要补齐 loader + 配套 scripts/osnorm.py。
        shutil.copyfile(ROOT / ".workspace" / "hooks" / "osnorm_loader.py", hooks / "osnorm_loader.py")
        (self.ws / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / "osnorm.py", self.ws / "scripts" / "osnorm.py")
        moved = scripts / "local_state.py.moved"
        local_state.rename(moved)
        code = (
            "import importlib.util,json,sys;from pathlib import Path;ws=Path(sys.argv[1]);"
            "spec=importlib.util.spec_from_file_location('gate_status_under_test',sys.argv[2]);"
            "gs=importlib.util.module_from_spec(spec);spec.loader.exec_module(gs);"
            "sys.path.insert(0,str(ws/'.workspace/hooks'));import gate_core;"
            "print(json.dumps([gs._machine_scope(ws),gate_core._fallback_scope()]))"
        )
        result = osnorm.run_text([sys.executable, "-B", "-c", code, str(self.ws), str(SCRIPT)], check=True)
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
        osnorm.write_text(state / "dsh-abc.json", json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), newline="lf")
        osnorm.write_text(self.ws / ".workspace" / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "pid": 1, "cwd": str(self.ws)}) + "\n", newline="lf")
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["clients"]["dsh"]["active"])
        self.assertIn(".dsh-gate.log:apply", data["clients"]["dsh"]["sources"])
        self.assertEqual(list(state.glob("dsh-session-*.json")), [],
                         "旧 glob 找不到真实的 dsh-<sid>.json")

    def test_dsh_gate_log_cannot_replace_either_proof(self):
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        osnorm.write_text(state / "gate.log", f"{stamp} post_read client=dsh session=abc\n")
        apply = self.ws / ".workspace" / ".dsh-gate.log"
        ledger = state / "dsh-abc.json"
        for missing in ("both", "apply", "ledger", "workspace", "invalid-ledger", "foreign-ledger"):
            with self.subTest(missing=missing):
                apply.unlink(missing_ok=True)
                ledger.unlink(missing_ok=True)
                if missing not in ("both", "ledger"):
                    record = {"events": [{"ts": stamp}], "workspace_root": str(
                        self.ws.parent if missing == "foreign-ledger" else self.ws)}
                    osnorm.write_text(ledger, json.dumps([] if missing == "invalid-ledger" else record))
                if missing not in ("both", "apply"):
                    osnorm.write_text(apply, json.dumps({
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply",
                        "cwd": str(self.ws.parent if missing == "workspace" else self.ws),
                    }))
                result = run(self.ws, "--require", "dsh", "--json")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(json.loads(result.stdout)["clients"]["dsh"]["active"])

    def test_migrate_legacy_removes_shared_decision_without_promoting_it(self):
        boot = self._boot()
        payload = {"workspace_status": "active", "gate": {
            "status": "configured", "harness": ["hermes"], "checked_at": "2026-11-04",
            "ttl_minutes": "garbage", "extra": {"preserve": True}}}
        osnorm.write_text(boot, json.dumps(payload), newline="lf")
        r = run(self.ws, "--migrate-legacy", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual((result["migrated"], result["removed_source"], result["source"]),
                         (True, "bootstrap.gate", "none"))
        snapshot = Path(result["snapshot"])
        self.assertEqual(json.loads(osnorm.read_text(snapshot)), payload["gate"])
        self.assertEqual(json.loads(osnorm.read_text(boot)), {"workspace_status": "active"})
        self.assertFalse(list(self.ws.glob(".workspace/.hook-state/*/gate.json")))

        r = run(self.ws, "--record", "--require", "codex", "--force",
                "--user-decision", "接受无 gate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        local, = self.ws.glob(".workspace/.hook-state/*/gate.json")
        before = local.read_bytes()
        osnorm.write_text(boot, json.dumps(payload), newline="lf")
        for migrated in (True, False):
            r = run(self.ws, "--migrate-legacy", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["migrated"], migrated)
            self.assertEqual(json.loads(r.stdout)["source"], "gate.json")
            self.assertEqual(local.read_bytes(), before)
        self.assertEqual(list(snapshot.parent.iterdir()), [snapshot])
        self.assertEqual(json.loads(osnorm.read_text(snapshot)), payload["gate"])

    def test_migration_retries_after_bootstrap_write_failure(self):
        boot = self._boot()
        payload = {"gate": {"extra": ["原值"]}, "keep": True}
        osnorm.write_text(boot, json.dumps(payload))
        gs = _load_gate_status_module()
        write_text = gs.osnorm.write_text

        def fail_cleanup(path, *args, **kwargs):
            if path == boot:
                raise OSError("injected bootstrap write failure")
            return write_text(path, *args, **kwargs)

        with mock.patch.object(gs.osnorm, "write_text", side_effect=fail_cleanup):
            self.assertEqual(gs.main(["--workspace", str(self.ws), "--migrate-legacy"]), 2)
        self.assertEqual(json.loads(osnorm.read_text(boot)), payload)
        snapshot, = self.ws.glob(".workspace/.hook-state/*/gate-history/*.json")
        original = snapshot.read_bytes()
        result = run(self.ws, "--migrate-legacy", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot.read_bytes(), original)
        self.assertEqual(json.loads(osnorm.read_text(boot)), {"keep": True})

    def test_hermes_evidence_from_plugin_log(self):
        """hermes 不走 hook.py，证据是 plugin 自己的在场日志。"""
        home = self.ws / "hermes-home"
        (home / "logs").mkdir(parents=True)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        osnorm.write_text(home / "logs" / "workspace-gate-plugin.log", "2026-11-04 09:00:01 workspace-gate active (v1.9.0, hermes_home=%s)\n" % home +
            f"{now} post_tool_call commit: {self.ws}/AGENT_RULES.md\n", newline="lf")
        r = osnorm.run_text([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--json"], timeout=60, env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_stale_apply_with_fresh_mtime_is_inactive(self):
        """apply 的 at 是 2 小时前、但文件 mtime 是现在 —— 必须判过期（PRO R1：mtime 不是证据）。"""
        ws_dsh = self.ws / ".workspace"
        ws_dsh.mkdir(parents=True, exist_ok=True)
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
        osnorm.write_text(ws_dsh / ".dsh-gate.log", json.dumps({"at": old, "event": "apply", "pid": 1, "cwd": str(self.ws)}) + "\n", newline="lf")
        (ws_dsh / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(ws_dsh / ".hook-state" / "dsh-x.json", "{}", newline="lf")
        os.utime(ws_dsh / ".dsh-gate.log", None)          # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--ttl-minutes", "30", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_stale_hermes_line_with_fresh_mtime_is_inactive(self):
        home = self.ws / "hermes-home"
        (home / "logs").mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        log = home / "logs" / "workspace-gate-plugin.log"
        osnorm.write_text(log, f"{stamp} workspace-gate active (v1.9.0)\n", newline="lf")
        os.utime(log, None)                               # mtime = 现在
        r = osnorm.run_text([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--ttl-minutes", "30", "--json"], timeout=60, env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_template_repo_must_not_ship_configured(self):
        """模板仓提交的 bootstrap 不得带本机运行态（PRO R1-3：克隆会继承假证据）。"""
        boot = json.loads(osnorm.read_text(ROOT / ".workspace" / "bootstrap.json", bom=False, newline="preserve"))
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
        osnorm.write_text(state / "gate.log", f"{stamp} post_read client=cc session=s1 file=AGENT_RULES.md\n", newline="lf")
        return state

    def test_record_writes_scoped_gate_json_not_bootstrap(self):
        """结论落本机分片 gate.json；bootstrap 不再被写。"""
        (self.ws / ".workspace").mkdir(parents=True, exist_ok=True)
        boot_path = self.ws / ".workspace" / "bootstrap.json"
        osnorm.write_text(boot_path, json.dumps({"workspace_status": "active"}), newline="lf")
        hooks = self.ws / ".workspace" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copyfile(ROOT / ".workspace" / "hooks" / "gate_core.py", hooks / "gate_core.py")
        # R41：gate_core 经装载器使用 osnorm；单文件夹具也要补齐 loader + 配套 scripts/osnorm.py。
        shutil.copyfile(ROOT / ".workspace" / "hooks" / "osnorm_loader.py", hooks / "osnorm_loader.py")
        (self.ws / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / "osnorm.py", self.ws / "scripts" / "osnorm.py")
        expected = osnorm.run_text([sys.executable, "-B", "-c",
             "import sys;sys.path.insert(0,sys.argv[1]);import gate_core;print(gate_core._fallback_scope())",
             str(hooks)], errors="replace", check=True).stdout.strip()
        self.assertRegex(expected, r"^[0-9a-f]{12}$")
        self._seed_cc_evidence(expected)
        r = run(self.ws, "--require", "cc", "--record", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / expected / "gate.json"
        self.assertTrue(gj.is_file(), "gate.json 未写到本机作用域目录")
        rec = json.loads(osnorm.read_text(gj, bom=False, newline="preserve"))
        self.assertEqual(rec["status"], "configured")
        self.assertEqual(rec["harness"], ["cc"])
        self.assertEqual(rec["machine_scope"], expected)
        boot = json.loads(osnorm.read_text(boot_path, bom=False, newline="preserve"))
        self.assertNotIn("gate", boot, "bootstrap 不得再被写入 gate 结论")

    def test_other_machine_scope_evidence_is_ignored(self):
        """别的机器同步过来的 gate.log 不算本机证据。"""
        scripts = self.ws / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(scripts / "local_state.py", osnorm.read_text(ROOT / "scripts" / "local_state.py", bom=False, newline="preserve"), newline="lf")
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
        osnorm.write_text(state / "gate.log", f"{stamp} post_read client=cc session=s1 file=AGENT_RULES.md\n", newline="lf")
        r = run(self.ws, "--require", "cc", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["clients"]["cc"]["active"])

    def test_ttl_marks_old_evidence_stale(self):
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        osnorm.write_text(state / "gate.log", f"{stamp} post_read client=codex session=s1 file=x\n", newline="lf")
        r = run(self.ws, "--require", "codex", "--ttl-minutes", "5", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["codex"]["active"])

    # ---- §15 R116：结论落盘 ----
    def _boot(self):
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(boot, json.dumps({"workspace_status": "active"}, ensure_ascii=False), newline="lf")
        return boot

    def test_record_configured_writes_local_gate_json(self):
        boot = self._boot()
        state = self.ws / ".workspace" / ".hook-state"
        state.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(state / "dsh-x.json", json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), newline="lf")
        osnorm.write_text(self.ws / ".workspace" / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "pid": 1, "cwd": str(self.ws)}) + "\n", newline="lf")
        r = run(self.ws, "--require", "dsh", "--ttl-minutes", "30", "--record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        self.assertTrue(gj.is_file(), "结论未落到本机作用域 gate.json")
        gate = json.loads(osnorm.read_text(gj, bom=False, newline="preserve"))
        self.assertNotIn("gate", json.loads(osnorm.read_text(boot, bom=False, newline="preserve")),
                         "bootstrap 不得再被写入 gate 结论")
        self.assertEqual(gate["status"], "configured")
        self.assertEqual(gate["harness"], ["dsh"])
        self.assertTrue(gate["checked_at"])

    def test_record_refuses_configured_without_evidence(self):
        boot = self._boot()
        r = run(self.ws, "--require", "dsh", "--record")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("gate", json.loads(osnorm.read_text(boot, bom=False, newline="preserve")),
                         "无证据时不得写入任何 gate 结论")

    def test_record_unconfigured_requires_user_decision(self):
        boot = self._boot()
        r = run(self.ws, "--require", "dsh", "--record", "--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = run(self.ws, "--require", "dsh", "--record", "--force",
                "--user-decision", "用户明确接受本机无 gate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        gate = json.loads(osnorm.read_text(gj, bom=False, newline="preserve"))
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
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
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
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": future, "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(d / ".hook-state" / "dsh-x.json", "{}", newline="lf")
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_record_shrink_harness_requires_user_decision(self):
        """从 configured{cc,dsh} 缩到 {dsh} 属状态翻转，必须有人类决定。"""
        boot = self._boot()
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(d / ".hook-state" / "dsh-x.json", json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), newline="lf")
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
        osnorm.write_text(boot, json.dumps({"workspace_status": "active",
                                    "gate": {"status": "configured", "harness": ["cc", "dsh"]}}), newline="lf")
        gj = self.ws / ".workspace" / ".hook-state" / "unknown" / "gate.json"
        gj.parent.mkdir(parents=True, exist_ok=True)
        osnorm.write_text(gj, json.dumps({"status": "configured", "harness": ["cc", "dsh"]}),
                          newline="lf")
        r = run(self.ws, "--require", "dsh", "--record")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = run(self.ws, "--require", "dsh", "--record", "--user-decision", "用户：分析机不再用 cc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(osnorm.read_text(gj, bom=False, newline="preserve"))["harness"], ["dsh"])
        self.assertEqual(json.loads(osnorm.read_text(boot, bom=False, newline="preserve"))["gate"]["harness"], ["cc", "dsh"],
                         "旧位置 bootstrap.gate 只读，不得被改写")

    def test_ledger_timestamp_beats_file_mtime(self):
        """账本 events[].ts 是 2 小时前、mtime 是现在：必须判过期（SOL R2-6：mtime 可被 touch）。"""
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        old_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
        osnorm.write_text(d / ".hook-state" / "dsh-x.json", json.dumps({"events": [{"type": "worktree-change", "ts": old_ts}]}), newline="lf")
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
        os.utime(d / ".hook-state" / "dsh-x.json", None)   # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_reverse_flip_requires_user_decision(self):
        """unconfigured → configured 也是翻转（R117），必须有人类决定。"""
        self._boot()
        r = run(self.ws, "--require", "dsh", "--record", "--force",
                "--user-decision", "接受无 gate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(d / ".hook-state" / "dsh-x.json", json.dumps(
            {"events": [{"type": "gate-commit", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}]}), newline="lf")
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
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
        osnorm.write_text(home / "logs" / "workspace-gate-plugin.log", f"{old} workspace-gate active (v1.9.0)\n{new} something unrelated happened\n", newline="lf")
        r = osnorm.run_text([sys.executable, "-B", str(SCRIPT), "--workspace", str(self.ws),
                            "--require", "hermes", "--json"], timeout=60, env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["clients"]["hermes"]["active"])

    def test_ledger_without_ts_is_not_evidence(self):
        """账本里没有可解析的事件/锚时间：不算证据，且不得回退 mtime（touch 就能骗）。

        两类夹具：① 空账本 `{}`；② 有 events/anchors 但缺 ts（SOL R3 验收要求）。
        """
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(d / ".hook-state" / "dsh-x.json", json.dumps({"events": [{"type": "gate-commit"}], "anchors": {"AGENT_RULES.md": {}}}), newline="lf")
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
        os.utime(d / ".hook-state" / "dsh-x.json", None)   # mtime = 现在
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

    def test_empty_ledger_object_is_not_evidence(self):
        d = self.ws / ".workspace"
        (d / ".hook-state").mkdir(parents=True, exist_ok=True)
        osnorm.write_text(d / ".hook-state" / "dsh-empty.json", "{}", newline="lf")
        osnorm.write_text(d / ".dsh-gate.log", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "apply", "cwd": str(self.ws)}) + "\n", newline="lf")
        os.utime(d / ".hook-state" / "dsh-empty.json", None)
        r = run(self.ws, "--require", "dsh", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(json.loads(r.stdout)["clients"]["dsh"]["active"])

if __name__ == "__main__":
    unittest.main()
