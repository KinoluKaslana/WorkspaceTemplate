#!/usr/bin/env python3
"""`.hook-state/<scope>/` 的兜底派生：工作区缺 `scripts/local_state.py` 时**不得**落进共享 `unknown`。

背景（Windows 真机发现）：测试工作区只拷了 `.workspace/`、没拷 `scripts/`，
`_machine_scope()` 直接返回 `"unknown"` —— 所有这类机器共用一个状态目录，
把别人的会话账本当成自己的读证据，正是第 2 步要修的 fail-open 形态。
"""
from __future__ import annotations

import importlib.util
import hashlib
import hmac
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent / ".workspace" / "hooks" / "gate_core.py"


def load_core():
    spec = importlib.util.spec_from_file_location("gate_core_fallback", HOOKS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FallbackScope(unittest.TestCase):
    def setUp(self):
        self.core = load_core()
        self.core._SCOPE_CACHE.clear()
        self.tmp = tempfile.mkdtemp()
        self.ws = Path(self.tmp) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text("{}", encoding="utf-8")
        # 只设置状态目录，避免污染真实 ~/.local/state
        self.state = Path(self.tmp) / "state"
        self.state.mkdir()
        self._old = os.environ.get("XDG_STATE_HOME")
        self._old_context_seed = os.environ.get("WORKSPACE_CONTEXT_SEED")
        os.environ["XDG_STATE_HOME"] = str(self.state)
        os.environ.pop("WORKSPACE_CONTEXT_SEED", None)

    def tearDown(self):
        self.core._SCOPE_CACHE.clear()
        if self._old is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._old
        if self._old_context_seed is None:
            os.environ.pop("WORKSPACE_CONTEXT_SEED", None)
        else:
            os.environ["WORKSPACE_CONTEXT_SEED"] = self._old_context_seed

    def test_no_scripts_dir_does_not_fall_into_shared_unknown(self):
        """缺 scripts/ ⇒ 用 hook 自带身份派生，scope 必须是 12 位十六进制。"""
        scope = self.core.machine_scope(str(self.ws))
        self.assertNotEqual(scope, "unknown")
        self.assertEqual(len(scope), 12, scope)
        int(scope, 16)          # 非法十六进制会抛 ValueError

    def test_scope_is_stable_across_calls(self):
        a = self.core.machine_scope(str(self.ws))
        self.core._SCOPE_CACHE.clear()
        b = self.core.machine_scope(str(self.ws))
        self.assertEqual(a, b, "同一台机器的 scope 必须稳定")

    def test_two_workspaces_without_scripts_share_machine_scope(self):
        """隔离粒度是**机器**，不是工作区：同机不同工作区应得同一 scope。"""
        other = Path(self.tmp) / "ws2"
        (other / ".workspace").mkdir(parents=True)
        a = self.core.machine_scope(str(self.ws))
        b = self.core.machine_scope(str(other))
        self.assertEqual(a, b)

    def test_no_scripts_honors_explicit_context_seed(self):
        """缺 scripts/ 时也必须与 local_state 的 CI 显式 seed 算法一致。"""
        seed = "ci-explicit-seed"
        os.environ["WORKSPACE_CONTEXT_SEED"] = seed
        self.core._SCOPE_CACHE.clear()
        actual = self.core.machine_scope(str(self.ws))
        expected = hmac.new(seed.encode("utf-8"), b"hook-scope",
                            hashlib.sha256).hexdigest()[:12]
        self.assertEqual(actual, expected)

    def test_no_identity_seed_uses_local_state_domain(self):
        """机器身份缺失时，带/不带 scripts 必须由同一种子得到同一 scope。"""
        with mock.patch.object(self.core, "_fallback_machine_material", return_value=b""):
            self.core._SCOPE_CACHE.clear()
            actual = self.core.machine_scope(str(self.ws))
        seed = (self.state / "workspace-template" / "scope-seed").read_bytes().strip()
        expected = hmac.new(seed, b"hook-scope-local", hashlib.sha256).hexdigest()[:12]
        self.assertEqual(actual, expected)

    def test_fallback_seed_fsyncs_before_publish(self):
        """新种子必须先 fsync 文件，再原子发布。"""
        events = []
        real_fsync = self.core.os.fsync
        real_replace = self.core.os.replace

        def tracked_fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def tracked_replace(src, dst):
            events.append("replace")
            return real_replace(src, dst)

        with mock.patch.object(self.core.os, "fsync", side_effect=tracked_fsync), \
             mock.patch.object(self.core.os, "replace", side_effect=tracked_replace):
            self.assertTrue(self.core._fallback_seed())
        self.assertIn("replace", events)
        self.assertEqual(events[0], "fsync", events)
        self.assertLess(events.index("fsync"), events.index("replace"), events)
        if os.name != "nt":
            seed = self.state / "workspace-template" / "scope-seed"
            self.assertEqual(seed.stat().st_mode & 0o777, 0o600,
                             "fallback seed 必须是 0600")

    def test_fallback_seed_respects_shared_lock(self):
        """持有标准锁时不得另起私锁造种子；确定性杀掉并发串行漂移。"""
        seed = self.state / "workspace-template" / "scope-seed"
        seed.parent.mkdir(parents=True, exist_ok=True)
        lock = seed.with_name(seed.name + ".lock")
        lock.write_bytes(b"")
        with mock.patch.object(self.core.time, "time",
                               side_effect=[0.0, 0.0, 0.0, 3.0]), \
             mock.patch.object(self.core.time, "sleep", return_value=None):
            self.assertEqual(self.core._fallback_seed(), b"")
        self.assertFalse(seed.exists(), "未取得共享锁却创建了 seed")

    def test_denied_write_records_no_commit(self):
        """被拒的写不得产生 gate-commit（否则账本会把「没写」记成「写了」）。"""
        self.core._SCOPE_CACHE.clear()
        state_dir = self.core.state_dir(str(self.ws))
        state_dir.mkdir(parents=True, exist_ok=True)
        commits = state_dir / "commits.jsonl"
        commits.write_text("", encoding="utf-8")
        rules = self.ws / "AGENT_RULES.md"
        rules.write_text("# r\n", encoding="utf-8")

        result = self.core.evaluate_pre_write(
            str(self.ws), "dsh", "s1", [str(rules)])
        denied = (result.get("action") or result.get("decision")
                  or result.get("permissionDecision") or result.get("permission"))
        self.assertIn(str(denied).lower(), ("deny", "block"), result)
        self.assertEqual(commits.read_text(encoding="utf-8").strip(), "",
                         "被拒的写产生了 gate-commit 账本记录")
        state = self.core.load_state(str(self.ws), "dsh", "s1")
        commit_events = [e for e in state.get("events", [])
                         if isinstance(e, dict) and e.get("type") == "gate-commit"]
        self.assertFalse(commit_events, "被拒的写产生了 gate-commit 会话事件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
