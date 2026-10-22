"""W0 席位：CC/Codex Bash 分派回归（真实进程级启动部署 hook，非原生客户端 E2E）。

每个用例独立 session_id + 独立临时工作区副本；断言 exit 0 与输出 JSON 顶层形状。
run_fix_suite.py 之外独立运行：`.venv-314/bin/python -B hook-adapter-e2e-260917/test_bash_dispatch.py`
"""
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
PY = sys.executable  # 模板仓无 .venv-314：用当前解释器（>=3.10）

sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402


class BashDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w0-dispatch-", dir="/tmp/w0-probe")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()  # 阻止向上冒领真实工作区
        self.ws = self._workspace()
        self.sid = "w0-" + self._testMethodName

    def _workspace(self):
        root = self.base / "ws"
        (root / ".workspace").mkdir(parents=True)
        (root / core.BOOTSTRAP_REL).write_text("{}")
        # 真实 AGENT_RULES.md 字节副本：内容锚定要求 payload content 与磁盘完全一致。
        (root / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        return root

    # ---- 工具 ----

    def invoke(self, event, client, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), **fields}
        env = {**os.environ, "GATE_CLIENT": client, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([PY, "-B", str(HOOK), event], input=json.dumps(payload),
                           text=True, capture_output=True, env=env, cwd=str(self.ws),
                           timeout=20)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        self.assertEqual(p.stderr, "", f"stderr not empty: {p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def denied(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def reason(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def state(self, client="cc"):
        p = core._state_path(str(self.ws), client, self.sid)
        return json.loads(p.read_text()) if p.exists() else {"reads": {}}

    def block(self, targets):
        return "*** Begin Patch\n" + "".join(
            f"*** Add File: {p}\n+test\n" for p in targets) + "*** End Patch"

    def heredoc(self, patch):
        return "apply_patch <<'PATCH'\n" + patch + "\nPATCH"

    def cc_read_payload(self):
        """CC PostToolUse Read 结构化 payload，content 与磁盘字节完全一致。"""
        gate = self.ws / core.GATE_FILE
        data = gate.read_bytes()
        n = len(data.split(b"\n"))  # core 的 total_lines 契约（split 计数，含尾空行）
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(gate), "offset": 1, "limit": n},
            "tool_response": {"type": "text", "file": {
                "filePath": str(gate), "content": data.decode("utf-8"),
                "startLine": 1, "numLines": n, "totalLines": n}},
        }

    # ---- D1–D7 ----

    def test_d1_cc_bash_ls_not_deny(self):
        # W0 核心修复：CC 订阅 Bash 后，`ls` 不再触发全员 deny。
        out = self.invoke("pre_bash", "cc", tool_name="Bash", tool_input={"command": "ls"})
        self.assertFalse(self.denied(out))
        self.assertNotIn("permissionDecision", json.dumps(out))
        self.assertEqual(out, {})

    def test_d2_codex_bash_ls_not_deny(self):
        # 回归：codex 既有行为不被削弱。
        out = self.invoke("pre_bash", "codex", tool_name="Bash", tool_input={"command": "ls"})
        self.assertFalse(self.denied(out))
        self.assertEqual(out, {})

    def test_d3_cc_bash_patch_unread_denies_read_first(self):
        out = self.invoke("pre_bash", "cc", tool_name="Bash",
                       tool_input={"command": self.heredoc(self.block(["victim.txt"]))})
        self.assertTrue(self.denied(out))
        self.assertIn("请先完整 read_file", self.reason(out))
        self.assertNotIn("内部错误", self.reason(out))

    def test_d4_cc_read_then_bash_patch_allows(self):
        # 全文 Read 记账后，CC Bash patch 不再 deny。
        self.invoke("post_read", "cc", **self.cc_read_payload())
        st = self.state()
        self.assertTrue(st["reads"].get(core.GATE_FILE, {}).get("complete"),
                        "CC 全文 Read 未成功记账（content 需与磁盘字节完全一致）")
        out = self.invoke("pre_bash", "cc", tool_name="Bash",
                       tool_input={"command": self.heredoc(self.block(["victim.txt"]))})
        self.assertFalse(self.denied(out))

    def test_d5_codex_pre_bash_non_bash_tool_fail_closed(self):
        # fail-closed 未被放宽：tool_name != Bash 仍 deny + 「内部错误」。
        out = self.invoke("pre_bash", "codex", tool_name="Edit",
                       tool_input={"command": "ls"})
        self.assertTrue(self.denied(out))
        self.assertIn("内部错误", self.reason(out))

    def test_d6_cc_bash_redirection_not_denied_known_boundary(self):
        # W0 已知边界：非 patch 写（> 重定向）仍未纳管，不 deny（显式标注）。
        out = self.invoke("pre_bash", "cc", tool_name="Bash",
                       tool_input={"command": "echo x > /tmp/w0-probe/x"})
        self.assertFalse(self.denied(out))
        self.assertEqual(out, {})

    def test_d7_cc_post_bash_read_not_accounted(self):
        # CC 的 Bash stdout 被截断，不能内容锚定 → post_bash 读命令绝不记账。
        self.invoke("session_start", "cc")  # 确保状态文件存在
        out = self.invoke("post_bash", "cc", tool_name="Bash",
                       tool_input={"command": "sed -n 1,5p AGENT_RULES.md"})
        self.assertFalse(self.denied(out))
        st = self.state()
        self.assertNotIn(core.GATE_FILE, st.get("reads", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
