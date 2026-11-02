#!/usr/bin/env python3
"""委托记录（delegation）回归：R98 扩展（3.0.0）的正反用例。

D1 父已读 → mint → 子写命中委托 → allow
D2 未 mint → 子写 deny
D3 子自行读完政策 → 仍 deny（自读不构成授权）
D4 目标越界 → deny
D5 TTL 过期 → deny
D6 gate 修订变化 → 委托失效 → deny
D7 父证据失效（父状态删除）→ deny
D8 父无读证据时 mint → 拒绝
D9 通配目标 → 拒绝

运行：python3 -B hooks-tests/test_delegation.py
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import delegations as dg  # noqa: E402
import gate_core as core  # noqa: E402


class Delegation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="deleg-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text("{}", encoding="utf-8")
        self.gate = self.ws / "AGENT_RULES.md"
        self.gate.write_text("\n".join(f"line {i}" for i in range(1, 41)) + "\n", encoding="utf-8")
        self.parent = "parent-1"
        self.child = "parent-1.child-1"
        self.target = str(self.ws / "out.txt")

    def mark_read(self, scope):
        body = self.gate.read_bytes()
        fp = f"c{len(body)}:{core.sha256_hex(body)}"
        st = core.load_state(str(self.ws), "cc", scope)
        st["reads"]["AGENT_RULES.md"] = {
            "anchor": "content", "fingerprint": fp, "fingerprint_source": "tool_response",
            "coverage": [[1, len(body.split(b"\n"))]], "complete": True,
        }
        core.save_state(str(self.ws), "cc", scope, st)

    def pre(self, target, scope=None):
        return core.evaluate_pre_write(str(self.ws), "cc", scope or self.child, [target])

    def mint(self, targets=None, ttl=1800):
        return dg.mint(str(self.ws), self.parent, self.child, targets or [self.target], "cc", ttl)

    def test_d1_delegation_allows(self):
        self.mark_read(self.parent)
        self.mint()
        self.assertEqual(self.pre(self.target)["action"], "allow")

    def test_d2_no_delegation_denies(self):
        self.mark_read(self.parent)
        self.assertEqual(self.pre(self.target)["action"], "deny")

    def test_d3_child_self_read_not_enough(self):
        self.mark_read(self.child)
        self.assertEqual(self.pre(self.target)["action"], "deny")

    def test_d4_out_of_scope_denies(self):
        self.mark_read(self.parent)
        self.mint([self.target])
        self.assertEqual(self.pre(str(self.ws / "other.txt"))["action"], "deny")

    def test_d5_expired(self):
        self.mark_read(self.parent)
        self.mint(ttl=1)
        time.sleep(2)
        self.assertEqual(self.pre(self.target)["action"], "deny")

    def test_d6_gate_revision_change(self):
        self.mark_read(self.parent)
        self.mint()
        self.gate.write_text(self.gate.read_text(encoding="utf-8") + "extra\n", encoding="utf-8")
        self.mark_read(self.parent)
        self.assertEqual(self.pre(self.target)["action"], "deny")

    def test_d7_parent_evidence_lost(self):
        self.mark_read(self.parent)
        self.mint()
        (self.ws / ".workspace" / ".hook-state" / f"cc-{self.parent}.json").unlink()
        self.assertEqual(self.pre(self.target)["action"], "deny")

    def test_d8_mint_requires_parent_evidence(self):
        with self.assertRaises(SystemExit):
            self.mint()

    def test_d9_wildcard_rejected(self):
        self.mark_read(self.parent)
        with self.assertRaises(SystemExit):
            dg.mint(str(self.ws), self.parent, self.child, [str(self.ws / "*.txt")], "cc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
