"""pwsh 写形态识别（dsh 在 Windows 上的 `pwsh` 工具）。

Windows 实测（2026-11-02，Windows PowerShell 5.1，经 win-host 远程执行）：
下列写法在真机上**都真的创建了文件**（f1–f9 + src.txt），因此都必须在未读状态被拦：
Set-Content / Out-File / Add-Content / New-Item -Value / `>` / `>>` / Tee-Object /
[IO.File]::WriteAllText / Copy-Item。

`cmd /c …` 因本机 PATH 命中 msys64 的 cmd，**未能实测成文件**：分类器仍按写意图拦，
但这里只断言「拦」，不断言「已验证会写」。`Set-Item` 实测 `NotSupported`（不写文件），
所以不断言 deny，而是断言 allow。
P11–P14 只读或无关命令 allow
P15 解释器（iex）在写意图不明时 fail-closed（deny）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"

WRITE_CMDS = [
    "Set-Content -Path 'f1.txt' -Value 'hi'",
    "'hi' | Out-File -FilePath 'f2.txt'",
    "Add-Content -Path 'f3.txt' -Value 'hi'",
    "New-Item -Path 'f4.txt' -ItemType File -Value 'hi' -Force",
    "'hi' > 'f5.txt'",
    "'hi' >> 'f6.txt'",
    "'hi' | Tee-Object -FilePath 'f7.txt'",
    "[IO.File]::WriteAllText('f8.txt','hi')",
    "Copy-Item 'src.txt' 'f9.txt'",
    'cmd /c "echo hi > f10.txt"',
]
ALLOW_CMDS = ["Get-Content 'f1.txt'", "ls", "Get-ChildItem -File", "Measure-Object",
              "2>&1 | Out-Null",
              "Get-Content AGENT_RULES.md # > AGENT_RULES.md",
              "Write-Output 'x > AGENT_RULES.md'",
              "Get-Content AGENT_RULES.md | Select-String 'Set-Content'"]

# 别名 / 脚本块 / .NET 写对象：不识别即绕过（与上面等价）；cmd 内层未在 Windows 实测
BYPASS_CMDS = [
    "sc f11.txt hi",
    "ac f12.txt hi",
    "cp src.txt f13.txt",
    "mv f11.txt f14.txt",
    "New-Object System.IO.StreamWriter('f16.txt')",
    "[IO.File]::OpenWrite('f17.txt')",
    # cmd 内层：分类器按写意图拦；本机 PATH 命中 msys64 的 cmd，Windows 上未能实测成文件
    'cmd /c "copy src.txt f18.txt"',
    'cmd /c "type nul > f19.txt"',
    "& 'Set-Content' -Path f20.txt -Value x",
    "1 | % { Set-Content -Path f21.txt -Value x }",
    "foreach ($i in 1..2) { Add-Content f22.txt $i }",
]


class PwshWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        shutil.copytree(HOOKS, self.ws / ".workspace" / "hooks")
        shutil.copy2(ROOT / "AGENT_RULES.md", self.ws / "AGENT_RULES.md")
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "mcp"},
                        "template": {"source_repo": "https://example.invalid/t"}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def decision(self, command: str):
        payload = {"client": "dsh", "dsh_tool": "pwsh", "hook_event_name": "PreToolUse",
                   "session_id": "pwsh-case", "cwd": str(self.ws), "tool_name": "Bash",
                   "tool_input": {"command": command}}
        p = subprocess.run([sys.executable, str(self.ws / ".workspace/hooks/hook.py"), "pre_bash"],
                           input=json.dumps(payload), capture_output=True, text=True,
                           env={**os.environ, "GATE_CLIENT": "dsh"}, cwd=self.ws)
        out = p.stdout.strip()
        if not out:
            return "allow", ""
        data = json.loads(out)
        hso = data.get("hookSpecificOutput", {})
        return hso.get("permissionDecision", "allow"), hso.get("permissionDecisionReason", "")

    def test_writes_are_denied(self):
        for cmd in WRITE_CMDS:
            with self.subTest(cmd=cmd):
                decision, reason = self.decision(cmd)
                self.assertEqual(decision, "deny", f"{cmd} -> {decision}")
                self.assertIn("守门", reason)

    def test_bypass_forms_are_denied(self):
        for cmd in BYPASS_CMDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decision(cmd)[0], "deny", cmd)

    def test_reads_are_allowed(self):
        for cmd in ALLOW_CMDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decision(cmd)[0], "allow", cmd)

    def test_interpreter_fails_closed(self):
        decision, reason = self.decision("iex (Get-Content x.ps1)")
        self.assertEqual(decision, "deny")
        self.assertIn("无法静态确定", reason)

    def test_after_full_read_write_is_allowed(self):
        raw = (self.ws / "AGENT_RULES.md").read_text(encoding="utf-8")
        lines = raw.split("\n")
        physical = len(lines) - 1 if raw.endswith("\n") else len(lines)
        numbered = "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines[:physical]))
        target = str(self.ws / "AGENT_RULES.md")
        text = (f"<path>{target}</path>\n<type>file</type>\n<content>\n{numbered}\n\n"
                f"(End of file - total {physical} lines)\n</content>")
        post = {"client": "dsh", "hook_event_name": "PostToolUse", "session_id": "pwsh-case",
                "cwd": str(self.ws), "tool_name": "read",
                "tool_input": {"file_path": target, "offset": 1, "limit": physical},
                "tool_response": {"isError": False, "content": [{"type": "text", "text": text}]}}
        subprocess.run([sys.executable, str(self.ws / ".workspace/hooks/hook.py"), "post_read"],
                       input=json.dumps(post), capture_output=True, text=True,
                       env={**os.environ, "GATE_CLIENT": "dsh"}, cwd=self.ws)
        self.assertEqual(self.decision("Set-Content -Path 'after.txt' -Value 'hi'")[0], "allow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
