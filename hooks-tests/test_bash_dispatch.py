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

    def invoke(self, event, client, ws=None, sid=None, **fields):
        ws = Path(ws) if ws else self.ws
        sid = sid or self.sid
        payload = {"session_id": sid, "cwd": str(ws), **fields}
        env = {**os.environ, "GATE_CLIENT": client, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([PY, "-B", str(HOOK), event], input=json.dumps(payload),
                           text=True, capture_output=True, env=env, cwd=str(ws),
                           timeout=20)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        self.assertEqual(p.stderr, "", f"stderr not empty: {p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def denied(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def reason(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def state(self, client="cc", ws=None):
        ws = Path(ws) if ws else self.ws
        p = core._state_path(str(ws), client, self.sid)
        return json.loads(p.read_text()) if p.exists() else {"reads": {}}

    def state_events(self, client="codex", ws=None):
        return self.state(client, ws).get("events", [])

    def audit(self, client="codex", ws=None):
        ws = Path(ws) if ws else self.ws
        p = Path(ws) / core.STATE_DIRNAME / f"audit-{client}.jsonl"
        if not p.exists():
            return {"lines": [], "events": []}
        lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        events = []
        for ln in lines:
            events.extend(ln.get("events", []))
        return {"lines": lines, "events": events}

    def _real_git_workspace(self):
        root = self.base / "gitws"
        (root / ".workspace").mkdir(parents=True)
        (root / core.BOOTSTRAP_REL).write_text("{}")
        (root / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            r = subprocess.run(["git", "-C", str(root), *args],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"git {args} failed: {r.stderr}")
        (root / "probe.txt").write_text("v1\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
        r = subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return root

    def codex_cat_read_payload(self):
        gate = self.ws / core.GATE_FILE
        data = gate.read_bytes()
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "cat AGENT_RULES.md"},
                "tool_response": {"exit_code": 0, "stdout": data.decode("utf-8")}}

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
        # C9 文案：已覆盖/缺失/共 N 行/read_file 四要素。
        self.assertIn("已覆盖", self.reason(out))
        self.assertIn("缺失", self.reason(out))
        self.assertIn("共", self.reason(out))
        self.assertIn("read_file", self.reason(out))
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

    # ---- W1（B′）----

    def test_w1_redirect_write_unread_denies(self):
        # 静态可判写（重定向 >> 命中 gate）未读 → deny（C9）。
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                       tool_input={"command": "printf 'x' >> AGENT_RULES.md"})
        self.assertTrue(self.denied(out))
        self.assertIn("已覆盖", self.reason(out))
        self.assertIn("缺失", self.reason(out))
        self.assertNotIn("内部错误", self.reason(out))

    def test_w1_interpreter_wrapper_unread_denies(self):
        # interpreter_wrapper（python3 -c）未读 → deny。
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                       tool_input={"command": "python3 -c \"open('AGENT_RULES.md','w')\""})
        self.assertTrue(self.denied(out))
        self.assertIn("已覆盖", self.reason(out))
        self.assertNotIn("内部错误", self.reason(out))

    def test_w1_opaque_unresolved_allows_and_audits(self):
        # 其它 unresolved（git status）未读 → allow 且落 unresolved-command 审计。
        out = self.invoke("pre_bash", "codex", tool_name="Bash",
                       tool_input={"command": "git status"}, tool_use_id="tu-1")
        self.assertFalse(self.denied(out))
        evs = self.state_events("codex")
        hits = [e for e in evs if e.get("type") == "unresolved-command"
                and e.get("tool_use_id") == "tu-1"]
        self.assertTrue(hits, "unresolved-command 未落审计")
        self.assertEqual(hits[0]["class"], "opaque")

    def test_w1_after_read_all_three_allow(self):
        # 已完整读后：同三条（重定向写 / interpreter_wrapper / git status）均放行。
        self.invoke("post_bash", "codex", **self.codex_cat_read_payload())
        st = self.state("codex")
        self.assertTrue(st["reads"].get(core.GATE_FILE, {}).get("complete"),
                        "codex 全文读未记账")
        for cmd in ("printf 'x' >> AGENT_RULES.md",
                    "python3 -c \"open('AGENT_RULES.md','w')\"",
                    "git status"):
            out = self.invoke("pre_bash", "codex", tool_name="Bash",
                           tool_input={"command": cmd})
            self.assertFalse(self.denied(out), f"已读后应放行：{cmd}")

    # ---- W3（客户端无关差集审计）----

    def test_w3_worktree_change_audit(self):
        # 真实 git 仓 + 未提交改动 → stop/session_end 后 audit 出现非空 worktree-change。
        ws = self._real_git_workspace()
        (ws / "probe.txt").write_text("v2\n")  # 未提交改动
        sid = "w3-" + self._testMethodName
        self.invoke("stop", "codex", ws=ws, sid=sid)
        self.invoke("session_end", "codex", ws=ws, sid=sid)
        aud = self.audit("codex", ws)
        wt = [e for e in aud["events"] if e.get("type") == "worktree-change"]
        self.assertTrue(wt, "audit 未出现 worktree-change")
        self.assertTrue(any(e.get("files") for e in wt),
                        "worktree-change 的 files 应非空（有未提交改动）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
