#!/usr/bin/env python3
"""W2/B 豁免令牌（进程级）：mint/consume/expire/mismatch/wildcard。

运行：python3 -B hooks-tests/test_authz.py（任意 cwd 可跑，纯 stdlib）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
HOOK = HOOKS / "hook.py"
AUTHZ = HOOKS / "authz.py"
PY = sys.executable

sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402


class AuthzToken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w2-authz-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}")
        (self.ws / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        self.sid = "w2a-" + self._testMethodName

    def mint(self, *targets, ttl=300):
        cmd = [PY, "-B", str(AUTHZ), "mint", "--workspace", str(self.ws),
               "--ttl", str(ttl)]
        for t in targets:
            cmd += ["--target", t]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)

    def invoke(self, event, client, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), **fields}
        env = {**os.environ, "GATE_CLIENT": client, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("WORKSPACE_TEMPLATE_CLONE", None)
        p = subprocess.run([PY, "-B", str(HOOK), event], input=json.dumps(payload),
                           text=True, capture_output=True, env=env, cwd=str(self.ws),
                           timeout=30)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def denied(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def state_events(self):
        p = core._state_path(str(self.ws), "codex", self.sid)
        st = json.loads(p.read_text()) if p.exists() else {}
        return st.get("events", [])

    def write_gate(self):
        return self.invoke("pre_bash", "codex", tool_name="Bash",
                           tool_input={"command": "printf 'x' >> AGENT_RULES.md"})

    def test_mint_allow_and_audit(self):
        p = self.mint("AGENT_RULES.md")
        self.assertEqual(p.returncode, 0, p.stderr)
        nonce = json.loads(p.stdout)["id"]
        out = self.write_gate()
        self.assertFalse(self.denied(out))
        hits = [e for e in self.state_events()
                if e.get("type") == "authorized-gatefile-write"]
        self.assertTrue(hits, "未出现 authorized-gatefile-write")
        self.assertEqual(hits[0]["nonce"], nonce)

    def test_same_token_second_denies(self):
        self.mint("AGENT_RULES.md")
        self.assertFalse(self.denied(self.write_gate()))
        self.assertTrue(self.denied(self.write_gate()))

    def test_expired_denies(self):
        self.mint("AGENT_RULES.md", ttl=0)
        self.assertTrue(self.denied(self.write_gate()))

    def test_target_mismatch_denies(self):
        # token 目标多一个 → 精确集合不相等 → deny
        self.mint("AGENT_RULES.md", "other.txt")
        self.assertTrue(self.denied(self.write_gate()))

    def test_wildcard_mint_errors(self):
        p = self.mint("*.md")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("通配", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
