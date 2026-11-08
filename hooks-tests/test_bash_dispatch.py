"""W0 席位：CC/Codex Bash 分派回归（真实进程级启动部署 hook，非原生客户端 E2E）。

每个用例独立 session_id + 独立临时工作区副本；断言 exit 0 与输出 JSON 顶层形状。
run_fix_suite.py 之外独立运行：`<workspace>/.venv-314/bin/python -B …`（Windows：`.venv-314\Scripts\python.exe -B …`；或直接用当前解释器 `python3 -B …`）
"""
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import re
import shutil
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
        # 基座自建：/tmp 被清理过（或换机器）时，不应让全部用例集体 ERROR。
        probe_root = Path("/tmp/w0-probe")
        probe_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="w0-dispatch-", dir=str(probe_root))
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / ".git").mkdir()  # 阻止向上冒领真实工作区
        self.ws = self._workspace()
        self.sid = "w0-" + self._testMethodName

    def _workspace(self):
        root = self.base / "ws"
        (root / ".workspace").mkdir(parents=True)
        (root / core.BOOTSTRAP_REL).write_text("{}", encoding="utf-8")
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
                           text=True, encoding="utf-8", capture_output=True, env=env, cwd=str(ws),
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
        return json.loads(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else {"reads": {}}

    def state_events(self, client="codex", ws=None):
        return self.state(client, ws).get("events", [])

    def audit(self, client="codex", ws=None):
        ws = Path(ws) if ws else self.ws
        p = Path(ws) / core.STATE_DIRNAME / f"audit-{client}.jsonl"
        if not p.exists():
            return {"lines": [], "events": []}
        lines = [json.loads(l) for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        events = []
        for ln in lines:
            events.extend(ln.get("events", []))
        return {"lines": lines, "events": events}

    def _real_git_workspace(self):
        root = self.base / "gitws"
        (root / ".workspace").mkdir(parents=True)
        (root / core.BOOTSTRAP_REL).write_text("{}", encoding="utf-8")
        (root / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            if not shutil.which("git"):
                raise unittest.SkipTest("未安装 git：该用例需要真实 git 仓库")
            r = subprocess.run(["git", "-C", str(root), "-c", "user.name=ci", "-c", "user.email=ci@example.com", *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, f"git {args} failed: {r.stderr}")
        (root / "probe.txt").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
        r = subprocess.run(["git", "-C", str(root), "-c", "user.name=ci", "-c", "user.email=ci@example.com", "commit", "-qm", "init"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
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

    def test_c9_wc_l_physical_lines_annotation(self):
        # C9 文案口径回归：core 的 total 用 split(b"\n") 计数，对以 LF 结尾的治理文件
        # 比 wc -l 多 1，模型会误以为「还差最后一行」而无效重读。deny 文案须同时给出
        # 两个口径，且 wc -l 数值必须等于真实文件的物理行数（不硬编码）。
        real = (ROOT / core.GATE_FILE).read_bytes()
        self.assertEqual((self.ws / core.GATE_FILE).read_bytes(), real,
                         "临时工作区的 gate 副本应与真实文件字节一致")
        total = len(real.split(b"\n"))
        physical = len(real.split(b"\n")) - (1 if real.endswith(b"\n") else 0)
        out = self.invoke("pre_write", "cc", tool_name="Write",
                          tool_input={"file_path": "victim.txt"})
        self.assertTrue(self.denied(out))
        reason = self.reason(out)
        self.assertIn("共 ", reason)
        self.assertIn("wc -l = ", reason)
        # 语义不变：缺失区间/总行数仍按 total 口径。
        self.assertIn(f"共 {total} 行", reason)
        m = re.search(r"wc -l = (\d+)", reason)
        got = int(m.group(1)) if m else None
        self.assertEqual(got, physical, f"deny 文案的 wc -l 数值不符：{reason}")

    def test_w8_compound_apply_patch_readable_deny(self):
        # F1 回归：显式 apply_patch 处在不支持的 shell 形态（如 `mkdir && apply_patch` 串联）
        # 时必须是「可读 deny + fail-closed」，不得落到泛异常「内部错误」。
        cmd = "mkdir -p artifacts && " + self.heredoc(self.block(["victim.txt"]))
        out = self.invoke("pre_bash", "codex", tool_name="Bash", tool_input={"command": cmd})
        self.assertTrue(self.denied(out))
        self.assertNotIn("内部错误", self.reason(out))
        self.assertIn("apply_patch", self.reason(out))
        self.assertIn("无法静态确定", self.reason(out))

    def test_w9_compound_apply_patch_denied_after_full_read(self):
        # 形态本身不可静态判定：即使已完整读取治理文件，复合形态仍 deny，
        # 但文案要可读（指向「单独调用 apply_patch」），供模型自愈。
        self.invoke("post_read", "codex", **self.codex_cat_read_payload())
        self.assertTrue(self.state(client="codex")["reads"].get(core.GATE_FILE, {}).get("complete"))
        cmd = "mkdir -p artifacts && " + self.heredoc(self.block(["victim.txt"]))
        out = self.invoke("pre_bash", "codex", tool_name="Bash", tool_input={"command": cmd},
                          ws=self.ws)
        self.assertTrue(self.denied(out))
        self.assertNotIn("内部错误", self.reason(out))
        self.assertIn("单独调用 apply_patch", self.reason(out))

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
        (ws / "probe.txt").write_text("v2\n", encoding="utf-8")  # 未提交改动
        sid = "w3-" + self._testMethodName
        self.invoke("stop", "codex", ws=ws, sid=sid)
        self.invoke("session_end", "codex", ws=ws, sid=sid)
        aud = self.audit("codex", ws)
        wt = [e for e in aud["events"] if e.get("type") == "worktree-change"]
        self.assertTrue(wt, "audit 未出现 worktree-change")
        self.assertTrue(any(e.get("files") for e in wt),
                        "worktree-change 的 files 应非空（有未提交改动）")

    # ---- W10（原生 payload 形态错误 → 可读 deny，不再是「内部错误」）----

    def test_w10_native_patch_missing_shell_readable(self):
        # 原生 apply_patch（tool_input.command）缺 *** End Patch → 可读 deny。
        bad = "*** Begin Patch\n*** Add File: victim.txt\n+hi\n"
        out = self.invoke("pre_write", "codex", tool_name="apply_patch",
                          tool_input={"command": bad})
        self.assertTrue(self.denied(out))
        reason = self.reason(out)
        self.assertNotIn("内部错误", reason)
        self.assertIn("apply_patch", reason)
        self.assertIn("Begin Patch", reason)

    def test_w10_native_patch_bad_header_readable(self):
        # 头行不是 Update/Add/Delete File → 可读 deny，且回显问题行。
        bad = "*** Begin Patch\n*** Copy File: victim.txt\n+hi\n*** End Patch"
        out = self.invoke("pre_write", "codex", tool_name="apply_patch",
                          tool_input={"command": bad})
        self.assertTrue(self.denied(out))
        reason = self.reason(out)
        self.assertNotIn("内部错误", reason)
        self.assertIn("Copy File", reason)

    def test_w11_wrapped_read_credits_evidence(self):
        """v2.8.1：`/usr/bin/zsh -lc "cat AGENT_RULES.md"` 剥壳后应授证（D3）。"""
        raw = (self.ws / core.GATE_FILE).read_bytes().decode("utf-8", "replace")
        out = self.invoke("post_bash", "codex", hook_event_name="PostToolUse", tool_name="Bash",
                          tool_input={"command": '/usr/bin/zsh -lc "cat AGENT_RULES.md"'},
                          tool_response={"stdout": raw, "exit_code": 0})
        self.assertEqual(out.get("returncode", 0), 0)
        st = core.load_state(str(self.ws), "codex", self.sid)
        entry = (st.get("reads") or {}).get(core.GATE_FILE) or {}
        self.assertTrue(entry.get("complete"), f"剥壳后仍未授证：{entry}")
        # 授证后可写
        out2 = self.invoke("pre_bash", "codex", tool_name="Bash",
                           tool_input={"command": "printf 'x' > w11.txt"})
        self.assertFalse(self.denied(out2), "读证据成立后写应放行")

    def test_w12_wrapped_overshoot_still_rejected(self):
        out = self.invoke("post_bash", "codex", hook_event_name="PostToolUse", tool_name="Bash",
                          tool_input={"command": '/usr/bin/zsh -lc "sed -n \'1,400p\' AGENT_RULES.md"'},
                          tool_response={"stdout": "irrelevant", "exit_code": 0})
        st = core.load_state(str(self.ws), "codex", self.sid)
        entry = (st.get("reads") or {}).get(core.GATE_FILE) or {}
        self.assertFalse(entry.get("complete"), "越界读不得授证")

    def test_w13_wrapper_with_expansion_not_stripped(self):
        raw = (self.ws / core.GATE_FILE).read_bytes().decode("utf-8", "replace")
        self.invoke("post_bash", "codex", hook_event_name="PostToolUse", tool_name="Bash",
                    tool_input={"command": '/usr/bin/zsh -lc "cat $HOME/AGENT_RULES.md"'},
                    tool_response={"stdout": raw, "exit_code": 0})
        st = core.load_state(str(self.ws), "codex", self.sid)
        entry = (st.get("reads") or {}).get(core.GATE_FILE) or {}
        self.assertFalse(entry.get("complete"), "含展开的包装不得授证")

    def test_w10_write_missing_path_readable(self):
        # Write 缺 file_path → 可读 deny（指向 file_path），不是内部错误。
        out = self.invoke("pre_write", "cc", tool_name="Write", tool_input={})
        self.assertTrue(self.denied(out))
        reason = self.reason(out)
        self.assertNotIn("内部错误", reason)
        self.assertIn("file_path", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
