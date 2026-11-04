#!/usr/bin/env python3
"""agent 作用域 + intent/commit + unobserved（v2.7.0 / P1）。

覆盖四件事：
1) (session, agent) 作用域键：子 agent 不继承父会话读证据；
2) 写前 gate-intent 记录 target + base_sha；
3) 写后 gate-commit 记录 new_sha；无意图的写落 unobserved-write；
4) worktree 差集查出「有变化但没有 commit」的目标，标 unobserved-write。

运行：python3 -B hooks-tests/test_agent_scope.py（纯 stdlib）。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
HOOK = HOOKS / "hook.py"
PY = sys.executable

sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402


class AgentScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-scope-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}")
        self.gate = self.ws / core.GATE_FILE
        self.gate.write_bytes((ROOT / core.GATE_FILE).read_bytes())
        self.raw = self.gate.read_bytes()

    # -------------------------------------------------- helpers
    def read_full(self, key):
        total = len(self.raw.split(b"\n"))
        st = core.load_state(str(self.ws), "codex", key)
        core.record_read(st, str(self.ws), core.GATE_FILE, "codex", {
            "path": str(self.gate), "offset": 1, "limit": total,
            "tool_response": self.raw.decode("utf-8", "replace"), "read_ok": True})
        core.save_state(str(self.ws), "codex", key, st)

    def events(self, key):
        return core.load_state(str(self.ws), "codex", key).get("events") or []

    # -------------------------------------------------- 1) 作用域键
    def test_scoped_id_basic(self):
        self.assertEqual(core.scoped_id("sid-1", None), "sid-1")
        self.assertEqual(core.scoped_id("sid-1", ""), "sid-1")
        self.assertEqual(core.scoped_id("sid-1", "abc123"), "sid-1.abc123")
        self.assertEqual(core.split_scoped_id("sid-1.abc123"), ("sid-1", "abc123"))
        self.assertEqual(core.split_scoped_id("sid-1"), ("sid-1", None))

    def test_parent_read_does_not_grant_child(self):
        parent = core.scoped_id("s1", None)
        child = core.scoped_id("s1", "a55ac09b5c4f47bcb")
        self.read_full(parent)
        r_child = core.evaluate_pre_write(str(self.ws), "codex", child,
                                          [str(self.ws / "child.txt")])
        self.assertEqual(r_child["action"], "deny", "子 agent 不得继承父会话读证据")
        r_parent = core.evaluate_pre_write(str(self.ws), "codex", parent,
                                           [str(self.ws / "parent.txt")])
        self.assertEqual(r_parent["action"], "allow")

    def test_child_self_read_does_not_authorize_anyone(self):
        """3.0.0：子作用域的写只认委托；子自读既不解锁自己，也不解锁父。"""
        parent = core.scoped_id("s2", None)
        child = core.scoped_id("s2", "agent-x")
        self.read_full(child)
        self.assertEqual(core.evaluate_pre_write(str(self.ws), "codex", child,
                                                 [str(self.ws / "c.txt")])["action"], "deny")
        self.assertEqual(core.evaluate_pre_write(str(self.ws), "codex", parent,
                                                 [str(self.ws / "p.txt")])["action"], "deny")

    # -------------------------------------------------- 2) intent
    def test_allow_records_intent_with_base_sha(self):
        key = core.scoped_id("s3", None)   # 记账用例用主作用域（子作用域需委托）
        self.read_full(key)
        target = self.ws / "target.txt"
        target.write_text("before\n")
        base = core.file_sha(str(target))
        core.evaluate_pre_write(str(self.ws), "codex", key, [str(target)])
        intents = [e for e in self.events(key) if e.get("type") == "gate-intent"]
        self.assertTrue(intents, "allow 必须落 gate-intent")
        self.assertEqual(intents[-1]["target"], str(target))
        self.assertEqual(intents[-1]["base_sha"], base)

    # -------------------------------------------------- 3) commit / unobserved
    def test_post_write_records_commit(self):
        key = core.scoped_id("s4", None)
        self.read_full(key)
        target = self.ws / "out.txt"
        target.write_text("before\n")
        core.evaluate_pre_write(str(self.ws), "codex", key, [str(target)])
        target.write_text("after\n")
        res = core.evaluate_post_write(str(self.ws), "codex", key, [str(target)])
        self.assertTrue(res["committed"], "有意图的写必须落 gate-commit")
        self.assertEqual(res["committed"][0]["new_sha"], core.file_sha(str(target)))
        self.assertFalse(res["unobserved"])

    def test_post_write_without_intent_is_unobserved(self):
        key = core.scoped_id("s5", "agent-noint")
        target = self.ws / "sneaky.txt"
        target.write_text("x\n")
        res = core.evaluate_post_write(str(self.ws), "codex", key, [str(target)])
        self.assertTrue(res["unobserved"], "无意图的写必须记 unobserved-write")
        self.assertEqual(res["unobserved"][0]["reason"], "no-intent")
        types = [e.get("type") for e in self.events(key)]
        self.assertIn("unobserved-write", types)
        self.assertNotIn("gate-commit", types)

    # -------------------------------------------------- 4) worktree 差集
    def test_worktree_diff_flags_changed_without_commit(self):
        key = core.scoped_id("s6", "agent-wt")
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                        "commit", "-qm", "init"], cwd=self.ws, check=True)
        (self.ws / "untracked.txt").write_text("changed\n")
        core.worktree_audit(str(self.ws), "codex", key, "stop")
        recs = [e for e in self.events(key) if e.get("type") == "unobserved-write"]
        self.assertTrue(any(r.get("detected_by") == "worktree-diff" for r in recs),
                        f"差集应报 unobserved-write，实际 {recs}")
        flagged = {r.get("target") for r in recs}
        self.assertIn(str(self.ws / "untracked.txt"), flagged)

    # -------------------------------------------------- 5) 端到端：hook 进程带 agent_id
    def test_hook_subprocess_scopes_state_by_agent(self):
        payload = {
            "session_id": "s7", "cwd": str(self.ws), "agent_id": "agent-e2e",
            "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": "cat AGENT_RULES.md"},
            "tool_response": {"stdout": self.raw.decode("utf-8", "replace"), "exit_code": 0},
        }
        env = {**os.environ, "GATE_CLIENT": "codex", "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([PY, str(HOOK), "post_bash"], input=json.dumps(payload),
                           text=True, encoding="utf-8", capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        files = sorted(x.name for x in (self.ws / ".workspace" / ".hook-state").glob("*.json"))
        self.assertTrue(any("s7.agent-e2e" in f for f in files), f"作用域状态未生成：{files}")


    def test_commit_ledger_rotation_keeps_latest_per_target(self):
        """轮转压缩：同一 target 只保留最新一条，且 committed_targets 仍能命中。"""
        ws = str(self.ws)
        ledger = self.ws / ".workspace" / ".hook-state" / "commits.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        target = str(self.ws / "rot.txt")
        old = "a" * 64
        new = "b" * 64
        pad = "p" * 600  # 撑到超过 1MB 轮转阈值
        with open(ledger, "w", encoding="utf-8") as fh:
            for i in range(2000):
                fh.write(json.dumps({"client": "codex", "session_id": "r",
                                     "type": "gate-commit", "target": target,
                                     "new_sha": old, "ts": str(i), "pad": pad}) + "\n")
        (self.ws / "rot.txt").write_text("rotated\n")
        core._append_commit_ledger(ws, "codex", "r", {"type": "gate-commit",
                                                      "target": target, "new_sha": new})
        lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        # 压缩把 2000 条压成「每 target 1 条」，随后新记录再追加 1 条 → ≤2
        self.assertLessEqual(len(lines), 2, f"压缩未生效，仍有 {len(lines)} 条")
        last = json.loads(lines[-1])
        self.assertEqual(last.get("new_sha"), new, "最后一条应是最新 commit")
        self.assertIn(target, core.committed_targets(ws, {target: new}))
        self.assertNotIn(target, core.committed_targets(ws, {target: "c" * 64}),
                         "sha 不匹配不得算作已 commit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
