#!/usr/bin/env python3
"""W6：apply_patch $'…'（ANSI-C 引用）解码单测 + 进程级回归。

覆盖：CRLF/八进制/\\u 的 $'…' patch 解析出正确目标；不可解码形态返回明确错误而非「内部错误」。
运行：python3 -B hooks-tests/test_apply_patch_ansi.py（任意 cwd 可跑）
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
sys.path.insert(0, str(HOOKS))
import hook  # noqa: E402
import gate_core as core  # noqa: E402


class DecodeAnsiC(unittest.TestCase):
    def test_simple_escapes(self):
        self.assertEqual(hook._decode_ansi_c(r"a\nb"), "a\nb")
        self.assertEqual(hook._decode_ansi_c(r"a\rb"), "a\rb")
        self.assertEqual(hook._decode_ansi_c(r"a\tb"), "a\tb")
        self.assertEqual(hook._decode_ansi_c(r"a\vb"), "a\vb")
        self.assertEqual(hook._decode_ansi_c(r"\e"), "\x1b")
        self.assertEqual(hook._decode_ansi_c(r"\E"), "\x1b")
        self.assertEqual(hook._decode_ansi_c(r"\?"), "?")
        self.assertEqual(hook._decode_ansi_c("\\cG"), "\x07")

    def test_backslash_quote_escapes(self):
        self.assertEqual(hook._decode_ansi_c("\\'"), "'")
        self.assertEqual(hook._decode_ansi_c('\\"'), '"')
        self.assertEqual(hook._decode_ansi_c("\\\\"), "\\")

    def test_octal_hex_unicode(self):
        self.assertEqual(hook._decode_ansi_c("\\101\\102"), "AB")   # 八进制
        self.assertEqual(hook._decode_ansi_c("\\x41\\x42"), "AB")   # 十六进制
        self.assertEqual(hook._decode_ansi_c("\\u0041"), "A")       # \u
        self.assertEqual(hook._decode_ansi_c("\\U0001F600"), "😀")  # \U

    def test_unsupported_escape_returns_none(self):
        self.assertIsNone(hook._decode_ansi_c("\\z"))     # 未知转义
        self.assertIsNone(hook._decode_ansi_c("\\x"))     # \x 无十六进制
        self.assertIsNone(hook._decode_ansi_c("\\u12"))   # \u 不足 4 位
        self.assertIsNone(hook._decode_ansi_c("abc\\"))   # 行尾孤立反斜杠


class BashPatchAnsiC(unittest.TestCase):
    def test_apply_patch_ansi_crlf_target(self):
        patch = hook._bash_patch(
            "apply_patch $'*** Begin Patch\\r\\n*** Add File: AGENT_RULES.md\\r\\n"
            "+<!-- x -->\\r\\n*** End Patch'")
        self.assertIsNotNone(patch)
        self.assertIn("*** Begin Patch", patch)
        self.assertIn("*** Add File: AGENT_RULES.md", patch)
        self.assertIn("\r\n", patch)

    def test_apply_patch_ansi_octal_unicode(self):
        patch = hook._bash_patch(
            "apply_patch $'*** Begin Patch\\n*** Add File: \\101.txt\\n+x\\n*** End Patch'")
        self.assertIsNotNone(patch)
        self.assertIn("A.txt", patch)

    def test_apply_patch_ansi_unsupported_raises_readable(self):
        with self.assertRaises(ValueError) as ctx:
            hook._bash_patch("apply_patch $'\\z'")
        self.assertIn("unsupported ANSI-C escape", str(ctx.exception))
        self.assertNotIn("内部错误", str(ctx.exception))


class ApplyPatchAnsiProcess(unittest.TestCase):
    """进程级：真实启动 hook.py，$'…' patch 不再「内部错误」deny。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w6-ansi-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}")
        (self.ws / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        self.sid = "w6-ansi"

    def invoke(self, event, client, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), **fields}
        env = {**os.environ, "GATE_CLIENT": client, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([sys.executable, "-B", str(HOOK), event],
                           input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True,
                           env=env, cwd=str(self.ws), timeout=20)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def reason(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def test_ansi_c_patch_unread_denies_no_internal_error(self):
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                          tool_input={"command": "apply_patch $'*** Begin Patch\\n"
                                                  "*** Add File: victim.txt\\n+x\\n"
                                                  "*** End Patch'"})
        self.assertIn("已覆盖", self.reason(out))
        self.assertNotIn("内部错误", self.reason(out))

    def test_ansi_c_patch_unsupported_denies_readable(self):
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                          tool_input={"command": "apply_patch $'\\z'"})
        self.assertIn("unsupported ANSI-C escape", self.reason(out))
        self.assertNotIn("内部错误", self.reason(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
