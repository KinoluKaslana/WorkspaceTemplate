#!/usr/bin/env python3
"""作用域隔离与旧布局兼容（第 2 步的回归护栏）。

S1 两台机器（不同 seed）的 gate_core 状态互不可见
S2 旧根目录的会话账本可读，下一次写入落分片目录、不改写旧文件
S3 旧根目录的委托账本/父证据可读
S4 旧根目录的 CC transcript sidecar 可读
S5 旧根目录的 hash 基线可读

运行：python3 -B hooks-tests/test_scope_isolation.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HERE))
import cc_transcript  # noqa: E402
import delegations as dg  # noqa: E402
import gate_core as core  # noqa: E402


class ScopeIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scope-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts" / "local_state.py", self.ws / "scripts" / "local_state.py")
        (self.ws / "AGENT_RULES.md").write_text("gate\n", encoding="utf-8")
        self._old_env = os.environ.get("WORKSPACE_CONTEXT_SEED")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        core._SCOPE_CACHE.clear()
        if self._old_env is None:
            os.environ.pop("WORKSPACE_CONTEXT_SEED", None)
        else:
            os.environ["WORKSPACE_CONTEXT_SEED"] = self._old_env

    def scope(self, seed: str) -> str:
        os.environ["WORKSPACE_CONTEXT_SEED"] = seed
        core._SCOPE_CACHE.clear()
        return core.machine_scope(str(self.ws))

    # S1 -----------------------------------------------------------------
    def test_s1_two_machines_do_not_see_each_other(self):
        a, b = self.scope("machine-A"), self.scope("machine-B")
        self.assertNotEqual(a, b)
        self.assertNotIn(a, ("", "unknown"))
        os.environ["WORKSPACE_CONTEXT_SEED"] = "machine-A"
        core._SCOPE_CACHE.clear()
        st = core.load_state(str(self.ws), "cc", "s1")
        st.setdefault("reads", {})["AGENT_RULES.md"] = {"complete": True, "fingerprint": "c5:abc"}
        core.save_state(str(self.ws), "cc", "s1", st)
        f_a = self.ws / ".workspace" / ".hook-state" / a / "cc-s1.json"
        self.assertTrue(f_a.is_file(), f"A 机状态未落自己的作用域目录：{a}")

        os.environ["WORKSPACE_CONTEXT_SEED"] = "machine-B"
        core._SCOPE_CACHE.clear()
        st_b = core.load_state(str(self.ws), "cc", "s1")
        self.assertFalse(st_b.get("reads"), "B 机读到了 A 机的会话证据")
        f_b = core._state_path(str(self.ws), "cc", "s1")
        self.assertEqual(f_b.parent.name, b)
        self.assertNotEqual(f_a, f_b)
        before = f_a.read_bytes()
        st_b.setdefault("reads", {})["AGENT_RULES.md"] = {"complete": False}
        core.save_state(str(self.ws), "cc", "s1", st_b)
        self.assertEqual(f_a.read_bytes(), before, "B 的写入动了 A 机的状态文件")

    # S2 -----------------------------------------------------------------
    def test_s2_legacy_session_readable_then_write_is_scoped(self):
        scope = self.scope("machine-A")
        legacy = self.ws / ".workspace" / ".hook-state" / "cc-old.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"schema": core.SCHEMA, "client": "cc",
                                      "session_id": "old", "workspace_root": str(self.ws),
                                      "reads": {"AGENT_RULES.md": {"complete": True}}}),
                          encoding="utf-8")
        before = legacy.read_bytes()
        st = core.load_state(str(self.ws), "cc", "old")
        self.assertTrue(st.get("reads"), "旧根会话账本读不到")
        core.save_state(str(self.ws), "cc", "old", st)
        self.assertEqual(legacy.read_bytes(), before, "升级后不得改写旧根文件")
        self.assertTrue((self.ws / ".workspace" / ".hook-state" / scope / "cc-old.json").is_file(),
                        "新写入应落分片目录")

    # S3 -----------------------------------------------------------------
    def test_s3_legacy_delegation_ledger_and_parent_evidence(self):
        self.scope("machine-A")
        root = self.ws / ".workspace" / ".hook-state"
        root.mkdir(parents=True, exist_ok=True)
        rec = {"id": "d-1", "parent_scope": "p", "child_scope": "c", "targets": ["x"],
               "client": "cc", "status": "active", "issued_at": int(time.time()),
               "expires_at": int(time.time()) + 600}
        (root / "delegations.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        rows = dg._rows(str(self.ws))
        self.assertTrue(any(r.get("id") == "d-1" for r in rows), "旧根委托账本读不到")

        body = (self.ws / "AGENT_RULES.md").read_bytes()
        fp = f"c{len(body)}:{core.sha256_hex(body)}"
        (root / "cc-parent.json").write_text(json.dumps(
            {"reads": {"AGENT_RULES.md": {"complete": True, "fingerprint": fp}}}), encoding="utf-8")
        self.assertTrue(dg.read_evidence_ok(str(self.ws), "cc", "parent"),
                        "旧根父会话证据读不到（升级后有效委托会全部失效）")

    # S4 -----------------------------------------------------------------
    def test_s4_legacy_transcript_sidecar(self):
        self.scope("machine-A")
        root = self.ws / ".workspace" / ".hook-state"
        root.mkdir(parents=True, exist_ok=True)
        (root / "cc-transcript-s1.json").write_text(
            json.dumps({"offset": 42, "pending": {}, "stats": {}, "books": {"a": 1}}), encoding="utf-8")
        st = cc_transcript.load_state(str(self.ws), "s1")
        self.assertEqual(st["offset"], 42, "旧根 transcript sidecar 读不到")
        cc_transcript.save_state(str(self.ws), "s1", 99, {}, {})
        self.assertTrue((self.ws / ".workspace" / ".hook-state"
                         / self.scope("machine-A") / "cc-transcript-s1.json").is_file())

    # S5 -----------------------------------------------------------------
    def test_s5_legacy_hash_baseline_readable(self):
        scope = self.scope("machine-A")
        root = self.ws / ".workspace" / ".hook-state"
        root.mkdir(parents=True, exist_ok=True)
        (root / "hashes-cc-s1.json").write_text("{}", encoding="utf-8")
        p = core._hash_baseline_path(str(self.ws), "cc", "s1", read=True)
        self.assertEqual(p, root / "hashes-cc-s1.json")
        self.assertEqual(core._hash_baseline_path(str(self.ws), "cc", "s1").parent.name, scope)

    # S6 -----------------------------------------------------------------
    def test_s6_real_fallback_seed_isolates_machines(self):
        """不靠测试钩子：真实「取不到 machine-id → 本机种子」路径也要隔离。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ls_scope_probe", self.ws / "scripts" / "local_state.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ls_scope_probe"] = mod
        spec.loader.exec_module(mod)
        mod._MATERIAL_CACHE = None
        a = mod.machine_scope({"XDG_STATE_HOME": str(Path(self.tmp.name) / "home-a")})
        b = mod.machine_scope({"XDG_STATE_HOME": str(Path(self.tmp.name) / "home-b")})
        self.assertNotIn(a, ("", "unknown"))
        self.assertNotEqual(a, b, "两台机器（不同状态目录）落到了同一个 scope")

    # S7 -----------------------------------------------------------------
    def test_s7_hash_baseline_never_written_to_legacy(self):
        """旧根基线只读：审计写入必须落分片目录，旧文件字节不变。"""
        scope = self.scope("machine-A")
        root = self.ws / ".workspace" / ".hook-state"
        root.mkdir(parents=True, exist_ok=True)
        legacy = root / "hashes-cc-s1.json"
        legacy.write_text("{}", encoding="utf-8")
        before = legacy.read_bytes()
        core._write_hash_baseline(str(self.ws), "cc", "s1", {"a.txt": "deadbeef"})
        self.assertEqual(legacy.read_bytes(), before, "审计写回了旧根基线")
        self.assertTrue((root / scope / "hashes-cc-s1.json").is_file())

    def test_s8_worktree_audit_does_not_touch_legacy_baseline(self):
        scope = self.scope("machine-A")
        root = self.ws / ".workspace" / ".hook-state"
        root.mkdir(parents=True, exist_ok=True)
        legacy = root / "hashes-cc-s1.json"
        legacy.write_text("{}", encoding="utf-8")
        before = legacy.read_bytes()
        core.worktree_audit(str(self.ws), "cc", "s1", source="stop")
        self.assertEqual(legacy.read_bytes(), before, "worktree_audit 改写了旧根基线")
        self.assertTrue((root / scope / "hashes-cc-s1.json").is_file())

    # S9 -----------------------------------------------------------------
    def test_s9_template_workspace_with_local_mode_is_taken_over(self):
        """模板态目录里只要本机状态配了 mode，就按「已接管」守门（python.mode 已下沉）。"""
        os.environ["WORKSPACE_CONTEXT_SEED"] = "machine-A"
        core._SCOPE_CACHE.clear()
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.write_text(json.dumps({"workspace_status": "template"}), encoding="utf-8")
        self.assertFalse(core.workspace_is_taken_over(str(self.ws)),
                         "模板态 + 本机无 mode 应判未接管")
        # 走真实链路：用工作区里那份 local_state 写 id + mode
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ls_scope_bind", self.ws / "scripts" / "local_state.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ls_scope_bind"] = mod
        spec.loader.exec_module(mod)
        _wid = mod.ensure_workspace_id(self.ws)
        ident = mod.resolve_identity({}, mod.detect_platform_info())
        mod.bind(self.ws, _wid, ident, "mcp", None, "", ephemeral=False)
        self.assertTrue(core.workspace_is_taken_over(str(self.ws)),
                        "本机已配 mode 的模板态目录必须按已接管守门")


if __name__ == "__main__":
    unittest.main(verbosity=2)
