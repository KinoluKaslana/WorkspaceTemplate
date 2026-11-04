#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh shell 盲区的事后可见性（post_shell 差集审计）。

背景：`wtarget.classify` 只做字面识别，`f=b.txt; echo x > $f`、`awk 'BEGIN{print "x" > "c"}'`
这类写**写前拦不住**（已声明盲区）。dsh 又没有会话结束事件，所以由插件在**每次 shell 调用后**
触发 `hook.py post_shell`，做一次 worktree 差集：磁盘上有「没有对应 gate-commit 的变化」
就记 `unobserved-write`（report-only，不 deny）。

本测试锁三件事：
1. 基线之前就已经脏的文件**不**被算成本会话的未观测写；
2. 基线之后出现的变化**被**记为 unobserved-write，且 detected_by=worktree-diff、source=post_shell；
3. 审计本身不改变工具结果（post_shell 输出为空 JSON，不产生 deny）。

运行：python3 -B hooks-tests/test_dsh_post_shell.py
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
HOOK = HOOKS / "hook.py"
PY = sys.executable

sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402


class PostShellAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dsh-postshell-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}\n", encoding="utf-8")
        (self.ws / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        # worktree_audit 用 git status 取差集，夹具必须是真 git 仓库且有一个干净基线提交
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline")
        self.sid = "dsh-postshell-" + self._testMethodName

    def git(self, *args):
        r = subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=ci", "-c", "user.email=ci@example.com", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def invoke(self, event, **fields):
        payload = {"session_id": self.sid, "cwd": str(self.ws), "client": "dsh", **fields}
        env = {**os.environ, "GATE_CLIENT": "dsh", "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([PY, "-B", str(HOOK), event], input=json.dumps(payload),
                           text=True, encoding="utf-8", capture_output=True, env=env, cwd=str(self.ws), timeout=60)
        self.assertEqual(p.returncode, 0, f"exit!=0 stderr={p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def events(self):
        st = core.load_state(str(self.ws), "dsh", self.sid)
        return st.get("events") or []

    def unobserved(self):
        return [e for e in self.events() if e.get("type") == "unobserved-write"]

    def test_pre_existing_dirty_is_not_blamed_on_this_session(self):
        (self.ws / "pre.txt").write_text("会话开始前就有的改动\n", encoding="utf-8")
        self.invoke("session_start")                       # 基线 = 当前（含 pre.txt）
        (self.ws / "post.txt").write_text("awk 写出来的\n", encoding="utf-8")  # 盲区写
        out = self.invoke("post_shell", tool_name="bash", source="post_shell")
        self.assertEqual(out, {}, "post_shell 不得产生 tool decision")
        flagged = {Path(u["target"]).name for u in self.unobserved()}
        self.assertIn("post.txt", flagged, f"盲区写应被记未观测：{flagged}")
        self.assertNotIn("pre.txt", flagged, f"会话前就脏的文件不该被算进来：{flagged}")
        wt = [e for e in self.events() if e.get("type") == "worktree-change"]
        self.assertTrue(wt, "应有一条 worktree-change 审计")
        self.assertEqual(wt[-1].get("source"), "post_shell")

    def test_clean_worktree_records_no_unobserved(self):
        self.invoke("session_start")
        self.invoke("post_shell", tool_name="bash", source="post_shell")
        self.assertEqual(self.unobserved(), [], "工作区没变就不该报未观测")

    def test_audit_is_report_only(self):
        self.invoke("session_start")
        (self.ws / "x.txt").write_text("x\n", encoding="utf-8")
        out = self.invoke("post_shell", tool_name="pwsh", source="post_shell")
        self.assertFalse(out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny",
                         "审计不得 deny")
        self.assertTrue(self.unobserved(), "变化应被记录（report-only 不等于不报）")


    def test_audit_survives_event_window_rotation(self):
        """审计记录必须落到 workspace 级 jsonl：state.events 只有 50 条窗口，会被轮转掉。"""
        self.invoke("session_start")
        (self.ws / "gap.txt").write_text("x\n", encoding="utf-8")
        self.invoke("post_shell", tool_name="bash", source="post_shell")
        self.assertTrue(self.unobserved(), "前提：本次审计先记到一条未观测写")
        for i in range(60):   # 灌事件，把 50 条窗口挤满
            core.append_audit_event(str(self.ws), "dsh", self.sid,
                                    {"type": "worktree-change", "files": [f"n{i}"], "source": "post_shell"})
        st = core.load_state(str(self.ws), "dsh", self.sid)
        self.assertFalse(any(e.get("type") == "unobserved-write" for e in (st.get("events") or [])),
                         "前提：窗口轮转后 state 里确实没有这条记录")
        audit = self.ws / ".workspace" / ".hook-state" / "audit-dsh.jsonl"
        self.assertTrue(audit.exists(), "必须落 workspace 级审计文件")
        rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertTrue(
            any(r.get("type") == "unobserved-write" and Path(str(r.get("target", ""))).name == "gap.txt" for r in rows),
            "jsonl 里必须还在（否则审计随窗口静默丢失）")


class PostShellAuditNoGit(PostShellAudit):
    """非 git 工作区（模板政策 §0.3 允许 git 为可选）：审计必须回退到 size:mtime 快照。

    没有这个回退，`worktree_audit` 会在 `git status` 失败时静默返回——「没有 git 的工作区
    等于没有事后可见性」，而 git 在模板里本来就是可选项。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dsh-postshell-nogit-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text("{}\n", encoding="utf-8")
        (self.ws / core.GATE_FILE).write_bytes((ROOT / core.GATE_FILE).read_bytes())
        self.sid = "dsh-postshell-nogit-" + self._testMethodName

    def git(self, *args):  # 该夹具没有 git，任何 git 调用都是测试写错
        raise AssertionError("non-git fixture must not call git")

    def test_non_git_workspace_still_audits(self):
        (self.ws / "pre.txt").write_text("before\n", encoding="utf-8")
        self.invoke("session_start")
        (self.ws / "blind.txt").write_text("awk wrote me\n", encoding="utf-8")
        self.invoke("post_shell", tool_name="bash", source="post_shell")
        flagged = {Path(u["target"]).name for u in self.unobserved()}
        self.assertIn("blind.txt", flagged, f"非 git 工作区也必须事后可见：{flagged}")
        self.assertNotIn("pre.txt", flagged)
        wt = [e for e in self.events() if e.get("type") == "worktree-change"]
        self.assertTrue(wt and wt[-1].get("git") is False, "应记录 git=false 的差集来源")

    def test_clean_worktree_records_no_unobserved(self):
        self.invoke("session_start")
        self.invoke("post_shell", tool_name="bash", source="post_shell")
        self.assertEqual(self.unobserved(), [])

    def test_backdated_mtime_detected_by_hash(self):
        """`cp -p` / `tar -x` / `rsync -t` 这类**保留 mtime** 的写：水位线抓不到，哈希兜底必须抓到。"""
        (self.ws / "kept.txt").write_text("AAAA\n", encoding="utf-8")
        self.invoke("session_start")                     # 建哈希基线
        st = os.stat(self.ws / "kept.txt")
        (self.ws / "kept.txt").write_text("BBBB\n", encoding="utf-8")   # 同尺寸改写
        os.utime(self.ws / "kept.txt", ns=(st.st_atime_ns, st.st_mtime_ns))  # 保留原 mtime
        self.invoke("post_shell", tool_name="pwsh", source="post_shell")
        flagged = {Path(u["target"]).name for u in self.unobserved()}
        self.assertIn("kept.txt", flagged, f"保 mtime 的改写必须被哈希兜底抓到：{flagged}")

    def test_many_files_do_not_create_false_positives(self):
        """>500 文件的非 git 工作区（干净树）不得报假阳性。

        旧实现把整个文件清单存进会话状态并截断到 500 条，700 个文件时每次审计都会把
        第 501..700 个当成"新变化"，报满 20 条假 `unobserved-write`。现在用 mtime 水位线，
        状态是 O(1)，与文件数无关。
        """
        for i in range(700):
            (self.ws / f"f{i:04d}.txt").write_text("x\n", encoding="utf-8")
        self.invoke("session_start")
        self.invoke("post_shell", tool_name="bash", source="post_shell")
        self.assertEqual(self.unobserved(), [], "干净树 + 700 文件不应有未观测写")
        st = core.load_state(str(self.ws), "dsh", self.sid)
        self.assertLessEqual(len(st.get("worktree_baseline") or {}), 1,
                             "非 git 工作区不应把全量文件清单写进会话状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
