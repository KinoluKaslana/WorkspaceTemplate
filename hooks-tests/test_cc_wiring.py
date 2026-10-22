#!/usr/bin/env python3
"""cc_transcript 接线测试：真实启动 hook.py 的 cc stop / session_end。

验证：转录存在 → `cc-bash-write`（含 would_deny）落审计；无 transcript_path →
`cc-bash-gap(no-transcript-path)`；增量偏移幂等（第二次无重复事件）；
且 stop 永不返回 deny。

运行：python3 -B hooks-tests/test_cc_wiring.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import cc_transcript as ct  # noqa: E402

GATE = "AGENT_RULES.md"


class CcWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-wiring-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text("{}", encoding="utf-8")
        (self.ws / GATE).write_bytes((ROOT / GATE).read_bytes())
        self.projects = self.base / "projects"
        self.sid = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
        self.proj_dir = self.projects / ct.slug_for_cwd(str(self.ws))
        self.proj_dir.mkdir(parents=True)

    def invoke(self, event, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), **fields}
        env = {**os.environ, "GATE_CLIENT": "cc",
               "CC_TRANSCRIPT_ROOT": str(self.projects),
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, "-B", str(HOOKS / "hook.py"), event],
                              input=json.dumps(payload), text=True, capture_output=True,
                              env=env, cwd=str(self.ws), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "", f"stderr: {proc.stderr}")
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    def write_transcript(self, command):
        path = self.proj_dir / f"{self.sid}.jsonl"
        rec = {"type": "assistant", "sessionId": self.sid,
               "timestamp": "2026-10-22T09:00:00.000Z",
               "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "call_x", "name": "Bash",
                    "input": {"command": command}}]}}
        path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)

    def audit_events(self):
        """state 里缓冲的事件 + 已 flush 的 audit 行（stop 只入 state，session_end 才 flush）。"""
        events = []
        state = self.ws / ".workspace" / ".hook-state" / f"cc-{self.sid}.json"
        if state.exists():
            events.extend(json.loads(state.read_text(encoding="utf-8")).get("events", []))
        p = self.ws / ".workspace" / ".hook-state" / "audit-cc.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.extend(json.loads(line).get("events", []))
        return events

    def test_stop_records_would_deny(self):
        path = self.write_transcript(f"printf 'x' >> {GATE}")
        out = self.invoke("stop", transcript_path=path)
        self.assertNotIn("permissionDecision", json.dumps(out), "stop 不得返回 deny")
        events = self.audit_events()
        writes = [e for e in events if e.get("type") == "cc-bash-write"]
        self.assertTrue(writes, events)
        self.assertTrue(writes[0]["would_deny"])
        self.assertEqual(writes[0]["read_state"], "missing")

    def test_missing_transcript_path_is_gap(self):
        self.invoke("stop")
        gaps = [e for e in self.audit_events() if e.get("type") == "cc-bash-gap"]
        self.assertTrue(any(e.get("reason") == "no-transcript-path" for e in gaps), gaps)

    def test_offset_idempotent_across_events(self):
        path = self.write_transcript("echo hi > artifacts/a.txt")
        self.invoke("stop", transcript_path=path)
        self.invoke("session_end", transcript_path=path, reason="exit")
        writes = [e for e in self.audit_events() if e.get("type") == "cc-bash-write"]
        unique = {(e.get("tool_use_id"), e.get("digest")) for e in writes}
        self.assertEqual(len(unique), 1, writes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
