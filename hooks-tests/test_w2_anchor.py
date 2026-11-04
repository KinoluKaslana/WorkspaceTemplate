#!/usr/bin/env python3
"""W2 指纹锚（进程级）：字节变化 → 审计+撤销；mtime 变化 → suspect 不撤销；不新增 deny。

运行：python3 -B hooks-tests/test_w2_anchor.py（任意 cwd 可跑，纯 stdlib）。
"""
from __future__ import annotations

import json
import os
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


class W2Anchor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w2-anchor-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()  # 阻止向上冒领真实工作区
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}")
        (self.ws / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        self.sid = "w2-" + self._testMethodName

    def invoke(self, event, client, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), **fields}
        env = {**os.environ, "GATE_CLIENT": client, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("WORKSPACE_TEMPLATE_CLONE", None)
        p = subprocess.run([PY, "-B", str(HOOK), event], input=json.dumps(payload),
                           text=True, encoding="utf-8", capture_output=True, env=env, cwd=str(self.ws),
                           timeout=30)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def denied(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def state(self):
        p = core._state_path(str(self.ws), "codex", self.sid)
        return json.loads(p.read_text()) if p.exists() else {"reads": {}}

    def state_events(self):
        return self.state().get("events", [])

    def codex_cat_read_payload(self):
        gate = self.ws / core.GATE_FILE
        data = gate.read_bytes()
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "cat AGENT_RULES.md"},
                "tool_response": {"exit_code": 0, "stdout": data.decode("utf-8")}}

    def test_byte_change_revokes_and_audits(self):
        self.invoke("session_start", "codex")
        self.invoke("post_bash", "codex", **self.codex_cat_read_payload())
        self.assertTrue(self.state()["reads"].get(core.GATE_FILE, {}).get("complete"))
        # 改字节 → stop 触发 anchor_check
        gate = self.ws / core.GATE_FILE
        gate.write_bytes(gate.read_bytes() + b"x")
        self.invoke("stop", "codex")
        evs = self.state_events()
        hits = [e for e in evs if e.get("type") == "unsanctioned-gatefile-change"
                and e.get("suspect") is not True]
        self.assertTrue(hits, "未出现 unsanctioned-gatefile-change")
        e = hits[0]
        self.assertEqual(e["file"], core.GATE_FILE)
        self.assertIn("previous", e)
        self.assertIn("current", e)
        self.assertIn("source", e)
        # 读许可被撤销
        self.assertNotIn(core.GATE_FILE, self.state()["reads"])
        revs = self.state().get("revocations", [])
        self.assertEqual(sum(1 for r in revs if r.get("reason") == "revision-changed"), 1,
                         "撤销应恰好一次 revision-changed")
        # 撤销后下次写普通文件仍 deny（既有写前复核产生，非 anchor_check 直接 deny）
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                          tool_input={"command": "printf 'y' >> other.txt"})
        self.assertTrue(self.denied(out))

    def test_mtime_change_suspect_no_revoke(self):
        self.invoke("session_start", "codex")
        self.invoke("post_bash", "codex", **self.codex_cat_read_payload())
        self.assertTrue(self.state()["reads"].get(core.GATE_FILE, {}).get("complete"))
        gate = self.ws / core.GATE_FILE
        # 只改 mtime，字节不变
        t = time.time() + 100
        os.utime(gate, (t, t))
        self.invoke("stop", "codex")
        evs = self.state_events()
        hits = [e for e in evs if e.get("type") == "unsanctioned-gatefile-change"
                and e.get("suspect") is True]
        self.assertTrue(hits, "未出现 suspect:true")
        # 许可不被撤销
        self.assertIn(core.GATE_FILE, self.state()["reads"])
        revs = self.state().get("revocations", [])
        self.assertFalse(any(r.get("reason") == "revision-changed" for r in revs))

    def test_no_new_deny_semantics(self):
        # 未读 → 写普通文件 deny；读全 → allow（与改动前一致，不新增 deny）。
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                          tool_input={"command": "printf 'z' >> victim.txt"})
        self.assertTrue(self.denied(out))
        self.invoke("post_bash", "codex", **self.codex_cat_read_payload())
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                          tool_input={"command": "printf 'z' >> victim.txt"})
        self.assertFalse(self.denied(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
