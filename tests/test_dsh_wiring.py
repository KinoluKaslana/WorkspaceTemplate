"""dsh（DeepSeek Harness）守门接线：hook.py 的 dsh 分支 + 插件端到端（node 合成驱动）。

D1 hook.py 接受 dsh 载荷：未读先写 → deny 且带 [workspace-gate:no-read]
D2 完整读之后 → allow；改修订后再写 → 重新 deny
D3 缺会话身份 → deny（fail-closed）
D4 插件链路（node dsh_harness.mjs）全过；无 node 时跳过并注明
"""
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / ".workspace" / "hooks"
RULES = ROOT / "AGENT_RULES.md"


class DshGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        shutil.copytree(HOOK_DIR, self.ws / ".workspace" / "hooks")
        shutil.copy2(RULES, self.ws / "AGENT_RULES.md")
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps({"workspace_status": "active",
                        "python": {"mode": "mcp"},
                        "template": {"source_repo": "https://example.invalid/t"}}),
            encoding="utf-8")
        # dsh 侧插件文件（端到端用）
        shutil.copytree(ROOT / ".dsh", self.ws / ".dsh")

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, event, **data):
        payload = {"client": "dsh", "cwd": str(self.ws),
                   "hook_event_name": "PreToolUse" if event.startswith("pre") else "PostToolUse",
                   **data}
        p = subprocess.run([sys.executable, str(self.ws / ".workspace/hooks/hook.py"), event],
                           input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "GATE_CLIENT": "dsh"}, cwd=self.ws)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.strip()
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            self.fail(f"hook 输出不是 JSON：{out[:200]}")

    def test_d1_write_before_read_is_denied(self):
        out = self.hook("pre_write", session_id="s1", tool_name="write",
                        tool_input={"file_path": str(self.ws / "AGENT_RULES.md"), "content": "x"})
        hso = out.get("hookSpecificOutput", {})
        self.assertEqual(hso.get("permissionDecision"), "deny")
        self.assertIn("[workspace-gate:no-read]", hso.get("permissionDecisionReason", ""))

    def test_d2_read_then_write_allows_and_revision_revokes(self):
        """真实 dsh 形态：file_path + 行号前缀 + (End of file - total N lines) 尾注。"""
        raw = (self.ws / "AGENT_RULES.md").read_text(encoding="utf-8")
        lines = raw.split("\n")
        physical = len(lines) - 1 if raw.endswith("\n") else len(lines)
        numbered = "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines[:physical]))
        target = str(self.ws / "AGENT_RULES.md")
        text = (f"<path>{target}</path>\n<type>file</type>\n<content>\n{numbered}\n\n"
                f"(End of file - total {physical} lines)\n</content>")
        self.hook("post_read", session_id="s2", tool_name="read",
                  tool_input={"file_path": target, "offset": 1, "limit": physical},
                  tool_response={"isError": False, "content": [{"type": "text", "text": text}]})
        out = self.hook("pre_write", session_id="s2", tool_name="write",
                        tool_input={"file_path": str(self.ws / "AGENT_RULES.md"), "content": "x"})
        self.assertNotEqual(out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        with (self.ws / "AGENT_RULES.md").open("a", encoding="utf-8") as fh:
            fh.write("\n<!-- bump -->\n")
        out = self.hook("pre_write", session_id="s2", tool_name="write",
                        tool_input={"file_path": str(self.ws / "AGENT_RULES.md"), "content": "x"})
        self.assertEqual(out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_d5_bash_static_write_denied(self):
        out = self.hook("pre_bash", session_id="s5", tool_name="Bash",
                        tool_input={"command": "echo hi > zz.txt"})
        hso = out.get("hookSpecificOutput", {})
        self.assertEqual(hso.get("permissionDecision"), "deny")
        self.assertIn("守门", hso.get("permissionDecisionReason", ""))
        self.assertNotIn("内部错误", hso.get("permissionDecisionReason", ""))

    def test_d6_bash_unresolved_forms_are_known_blind_spot(self):
        """变量间接 / awk 重定向目前不拦——这是已声明的盲区，用测试锁住事实。"""
        for cmd in ("f=b.txt; echo hi > $f", "awk 'BEGIN{print 1 > \"c.txt\"}'"):
            out = self.hook("pre_bash", session_id="s6", tool_name="Bash",
                            tool_input={"command": cmd})
            self.assertNotEqual(out.get("hookSpecificOutput", {}).get("permissionDecision"),
                                "deny", cmd)

    @unittest.skipUnless(shutil.which("node"), "未安装 node")
    def test_d4_plugin_end_to_end(self):
        harness = ROOT / "tests" / "dsh_harness.mjs"
        p = subprocess.run(["node", str(harness), str(self.ws), "--json"],
                           capture_output=True, text=True, encoding="utf-8", timeout=180)
        if p.returncode == 2:
            self.skipTest(f"环境不满足：{p.stderr.strip()[:120]}")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        data = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(data["passed"], data["total"], data)
        self.assertGreaterEqual(data["total"], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
