#!/usr/bin/env python3
"""cc_transcript 单测：转录回放归因（纯函数，不启动 hook、不联网）。

覆盖：
- validate_path 三重校验（projects 根 / slug / session_id / 存在性）
- 增量读取与半行容错
- steps_from 的 tool_use/tool_result 绑定
- would_deny 复算矩阵（gate 静态写 / 解释器包装 / opaque / 非 gate 写 / 读全后放行）
- 命令原文开关（默认关）
- changed-but-untargeted 缺口（真实 git 仓）
- 偏移幂等（第二次无新事件）
- 跨 Stop 读证据持久化（读全 → 下一轮写仍 complete；修订变化即撤销）
- 同轮写即消费（读 + 连写两条 ⇒ 第二条 missing/deny；读写写读写 ⇒ 两条都 allow）
- 无 cwd 转录 fail-closed（transcript-cwd-missing，不进复算）

运行：python3 -B hooks-tests/test_cc_transcript.py
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

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import cc_transcript as ct  # noqa: E402

GATE = "AGENT_RULES.md"


def _numbered(raw: bytes, offset: int = 1) -> str:
    """把任意字节内容渲染成 CC Read 结果形态（`<行号>\\t<正文>`）。"""
    lines = raw.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(lines[offset - 1:]))


def _records(session_id, steps, cwd=None, id_base=0):
    """steps: [(name, input, result_text, is_error)] → 转录行（tool_use + tool_result）。

    `cwd` 是真实转录每行都带的字段（analyze 用它绑定会话工作区）；传 None 模拟
    「整条转录没有 cwd」的异常形态（必须 fail-closed）。
    """
    out = [{"type": "queue-operation", "sessionId": session_id}]
    if cwd:
        out[0]["cwd"] = cwd
    for idx, (name, inp, text, err) in enumerate(steps):
        tid = f"call_{id_base + idx:02d}"
        use = {"type": "assistant", "sessionId": session_id,
               "timestamp": f"2026-10-22T08:45:{35 + idx:02d}.000Z",
               "message": {"role": "assistant",
                           "content": [{"type": "tool_use", "id": tid, "name": name,
                                        "input": inp}]}}
        res = {"type": "user", "sessionId": session_id,
               "timestamp": f"2026-10-22T08:45:{35 + idx:02d}.500Z",
               "message": {"role": "user",
                           "content": [{"type": "tool_result", "tool_use_id": tid,
                                        "is_error": err, "content": text}]}}
        if cwd:
            use["cwd"] = cwd
            res["cwd"] = cwd
        out.append(use)
        out.append(res)
    return out


class CcTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-transcript-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / GATE).write_bytes((HOOKS.parent.parent / GATE).read_bytes())
        self.projects = self.base / "projects"
        self.sid = "1111aaaa-2222-3333-4444-555566667777"
        self.slug = ct.slug_for_cwd(str(self.ws))
        self.proj_dir = self.projects / self.slug
        self.proj_dir.mkdir(parents=True)
        self.transcript = self.proj_dir / f"{self.sid}.jsonl"
        self._old_root = ct.PROJECTS_ROOT
        ct.PROJECTS_ROOT = str(self.projects)  # 隔离：不碰真实 ~/.claude/projects

    def tearDown(self):
        ct.PROJECTS_ROOT = self._old_root

    # ---- 工具 ----

    def write(self, steps, session_id=None, with_cwd=True):
        sid = session_id or self.sid
        path = self.proj_dir / f"{sid}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for rec in _records(sid, steps, cwd=str(self.ws) if with_cwd else None):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(path)

    def append(self, steps):
        """追加下一轮（新 Stop）的转录行，回放 id 与已有行不冲突。"""
        path = self.proj_dir / f"{self.sid}.jsonl"
        base = len(Path(path).read_text(encoding="utf-8").splitlines())
        with open(path, "a", encoding="utf-8") as fh:
            for rec in _records(self.sid, steps, cwd=str(self.ws), id_base=base):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(path)

    def gate_text(self, offset=1, limit=None):
        raw = (self.ws / GATE).read_bytes().decode("utf-8")
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        end = len(lines) if limit is None else min(offset - 1 + limit, len(lines))
        chunk = lines[offset - 1:end]
        return "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(chunk))

    def analyze(self, path):
        return ct.analyze(str(self.ws), self.sid, path, str(self.ws))

    # ---- 校验 ----

    def test_validate_ok_and_rejects(self):
        path = self.write([("Bash", {"command": "echo x > a.txt"}, "", False)])
        self.assertEqual(ct.validate_path(path, self.sid, str(self.ws)), (True, "ok"))
        self.assertEqual(ct.validate_path(path, "other-sid", str(self.ws))[1],
                         "transcript-session-mismatch")
        self.assertEqual(ct.validate_path(path, self.sid, "/tmp/elsewhere")[1],
                         "transcript-slug-mismatch")
        self.assertEqual(ct.validate_path("/etc/passwd", self.sid, str(self.ws))[1],
                         "transcript-outside-projects")
        ghost = self.proj_dir / f"{self.sid}.jsonl"
        ghost_bytes = ghost.read_bytes()
        ghost.unlink()
        self.assertEqual(ct.validate_path(str(ghost), self.sid, str(self.ws))[1],
                         "transcript-missing")
        ghost.write_bytes(ghost_bytes)  # 复原，后续断言不受影响
        self.assertEqual(ct.validate_path(None, self.sid, str(self.ws))[1], "no-transcript-path")

    def test_validate_subagent_transcript(self):
        """v2.8.4：子 agent 转录 <slug>/<parent>/subagents/agent-<id>.jsonl 可校验，父会话不匹配则拒。"""
        sub = self.proj_dir / self.sid / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        p = sub / "agent-abc123.jsonl"
        p.write_text("", encoding="utf-8")
        scoped = f"{self.sid}.abc123"
        self.assertEqual(ct.validate_path(str(p), scoped, str(self.ws)), (True, "ok"))
        self.assertEqual(ct.validate_path(str(p), "other.abc123", str(self.ws))[1],
                         "transcript-session-mismatch")
        bad = self.proj_dir / self.sid / "agents"
        bad.mkdir(parents=True, exist_ok=True)
        bp = bad / "agent-abc123.jsonl"
        bp.write_text("", encoding="utf-8")
        self.assertEqual(ct.validate_path(str(bp), scoped, str(self.ws))[1], "transcript-shape")
        self.assertEqual(ct.validate_path(str(sub / "nope.jsonl"), scoped, str(self.ws))[1],
                         "transcript-shape")

    def test_incremental_read_tolerates_half_line(self):
        path = self.write([("Bash", {"command": "echo x > a.txt"}, "", False)])
        full = ct.read_records(path)
        self.assertFalse(full["truncated"])
        # 截掉最后一行的一部分 → 半行不消费
        data = Path(path).read_bytes()
        cut = data[: len(data) - 10]
        Path(path).write_bytes(cut)
        part = ct.read_records(path)
        self.assertTrue(part["truncated"])
        self.assertLess(part["offset"], len(cut) + 1)

    # ---- 复算 ----

    def test_partial_read_gate_write_would_deny(self):
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1, "limit": 20}, self.gate_text(1, 20), False),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        res = self.analyze(path)
        writes = [e for e in res["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0]["would_deny"])
        self.assertEqual(writes[0]["read_state"], "partial")
        self.assertEqual(writes[0]["targets"], [str(self.ws / GATE)])
        self.assertNotIn("command", writes[0], "默认不得落命令原文")
        self.assertEqual(res["stats"]["would_deny"], 1)

    def test_interpreter_wrapper_would_deny(self):
        path = self.write([("Bash", {"command": f"python3 -c 'open(\"{GATE}\",\"a\")'"}, "", False)])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertTrue(write["would_deny"])
        self.assertEqual(write["class"], "interpreter_wrapper")
        self.assertEqual(write["read_state"], "missing")

    def test_opaque_command_is_gap_not_deny(self):
        path = self.write([("Bash", {"command": "git apply patch.diff"}, "", False)])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write["would_deny"])
        self.assertEqual(write["class"], "opaque")
        self.assertTrue(any(e.get("reason") == "unresolved-command" for e in res["events"]))

    def test_non_gate_write_not_denied(self):
        path = self.write([("Bash", {"command": "echo hi > artifacts/x.txt"}, "", False)])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write["would_deny"])
        self.assertTrue(write["targets"][0].replace("\\", "/").endswith("artifacts/x.txt"),
                        write["targets"][0])   # Windows 上是反斜杠，比较前归一化

    def test_full_read_then_gate_write_allowed(self):
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1} , self.gate_text(1), False),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write["would_deny"])
        self.assertEqual(write["read_state"], "complete")

    def test_truncated_read_is_not_evidence(self):
        # 带截断标记 / 注入尾注的 Read 结果不得折算成读证据（对齐 gate_core 语义）。
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1, "limit": 30},
             self.gate_text(1, 30) + "\n... [truncated]", False),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertEqual(write["read_state"], "missing")
        self.assertTrue(write["would_deny"])

    def test_mismatched_read_content_is_not_evidence(self):
        # 行号正文与磁盘不符（文件已被改过）→ 不授证。
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1, "limit": 5},
             "1\t# 伪造的内容\n2\tx\n3\ty\n4\tz\n5\tw", False),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertEqual(write["read_state"], "missing")
        self.assertTrue(write["would_deny"])

    def test_command_text_opt_in(self):
        path = self.write([("Bash", {"command": "echo hi > artifacts/y.txt"}, "", False)])
        (self.ws / ".workspace" / "gate.custom.json").write_text(
            json.dumps({"audit_command_text": True}), encoding="utf-8")
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertEqual(write["command"], "echo hi > artifacts/y.txt")
        self.assertIn("digest", write)

    def test_offset_is_idempotent(self):
        path = self.write([("Bash", {"command": "echo hi > artifacts/z.txt"}, "", False)])
        first = self.analyze(path)
        second = ct.analyze(str(self.ws), self.sid, path, str(self.ws), start_offset=first["offset"])
        self.assertEqual(second["events"], [])
        self.assertEqual(second["offset"], first["offset"])

    def test_custom_gate_files_are_honored(self):
        # gate.custom.json 指定一个可弃置 gate 文件 → 对它的未读 Bash 写应 would_deny。
        (self.ws / "TEST-GATE.md").write_text("a\nb\nc\n", encoding="utf-8")
        (self.ws / ".workspace" / "gate.custom.json").write_text(
            json.dumps({"gate_files": ["TEST-GATE.md"]}), encoding="utf-8")
        path = self.write([("Bash", {"command": "printf 'x' >> TEST-GATE.md"}, "", False)])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertTrue(write["would_deny"], write)
        self.assertEqual(write["targets"], [str(self.ws / "TEST-GATE.md")])
        # 读全该文件后同命令 → 放行（同会话重写转录，从头分析）
        body = (self.ws / "TEST-GATE.md").read_bytes().decode()
        lines = body.rstrip("\n").split("\n")
        numbered = "\n".join(f"{i+1}\t{l}" for i, l in enumerate(lines))
        Path(path).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in _records(
            self.sid, [("Read", {"file_path": str(self.ws / "TEST-GATE.md"), "offset": 1}, numbered, False),
                       ("Bash", {"command": "printf 'x' >> TEST-GATE.md"}, "", False)],
            cwd=str(self.ws))) + "\n",
            encoding="utf-8")
        res2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        write2 = [e for e in res2["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write2["would_deny"], write2)
        self.assertEqual(write2["read_state"], "complete")

    def test_second_gate_needs_its_own_read(self):
        (self.ws / "G2.md").write_text("x\ny\n", encoding="utf-8")
        (self.ws / ".workspace" / "gate.custom.json").write_text(
            json.dumps({"gate_files": [GATE, "G2.md"]}), encoding="utf-8")
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1}, self.gate_text(1), False),
            ("Bash", {"command": "printf 'x' >> G2.md"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertTrue(write["would_deny"], "读了 AGENT_RULES 不等于读了 G2.md")
        self.assertEqual(write["read_state"], "missing")

    def test_read_then_write_uses_transcript_revision(self):
        # CC 无写前拦截：审计时文件已被本次会话追加。写前的整文件读必须仍能证明「读过」。
        (self.ws / "G3.md").write_text("a\nb\nc\n", encoding="utf-8")
        (self.ws / ".workspace" / "gate.custom.json").write_text(
            json.dumps({"gate_files": ["G3.md"]}), encoding="utf-8")
        read_text = "1\ta\n2\tb\n3\tc\n4\t\n"
        path = self.write([
            ("Read", {"file_path": str(self.ws / "G3.md"), "offset": 1}, read_text, False),
            ("Bash", {"command": "printf 'd\\n' >> G3.md"}, "", False),
        ])
        (self.ws / "G3.md").write_text("a\nb\nc\nd\n", encoding="utf-8")  # 写已落盘
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write["would_deny"], write)
        self.assertEqual(write["read_state"], "complete")
        self.assertEqual(write["revision"], "transcript")

    def test_read_and_write_same_round_still_allows(self):
        # 同一轮「读 + 写」：参考修订取转录里的写前读（写已真的改了磁盘）⇒ 放行。
        gate = self.ws / GATE
        path = self.write([
            ("Read", {"file_path": str(gate), "offset": 1}, self.gate_text(1), False),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        gate.write_bytes(gate.read_bytes() + b"same-round\n")  # 写真的落盘
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertFalse(write["would_deny"], write)
        self.assertEqual(write["read_state"], "complete")
        self.assertEqual(write["revision"], "transcript")

    def test_partial_read_with_limit_is_not_a_reference(self):
        (self.ws / "G4.md").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
        (self.ws / ".workspace" / "gate.custom.json").write_text(
            json.dumps({"gate_files": ["G4.md"]}), encoding="utf-8")
        path = self.write([
            ("Read", {"file_path": str(self.ws / "G4.md"), "offset": 1, "limit": 2}, "1\ta\n2\tb", False),
            ("Bash", {"command": "printf 'x' >> G4.md"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertTrue(write["would_deny"], write)
        self.assertEqual(write["revision"], "disk")

    def test_failed_read_is_not_evidence(self):
        # is_error=True 的 Read 不授证（LUNA 探针：错误 Read 曾被记 complete）。
        path = self.write([
            ("Read", {"file_path": str(self.ws / GATE), "offset": 1}, self.gate_text(1), True),
            ("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False),
        ])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertTrue(write["would_deny"], write)
        self.assertEqual(write["read_state"], "missing")

    def test_missing_result_is_pending_then_resolved(self):
        # tool_use 已落盘、tool_result 未落盘：不得记 result=ok，须进 pending 待补。
        use = {"type": "assistant", "sessionId": self.sid, "cwd": str(self.ws),
               "timestamp": "2026-10-22T09:00:00.000Z",
               "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "call_p", "name": "Bash",
                    "input": {"command": "echo hi > artifacts/p.txt"}}]}}
        path = self.proj_dir / f"{self.sid}.jsonl"
        path.write_text(json.dumps(use, ensure_ascii=False) + "\n", encoding="utf-8")
        first = ct.analyze(str(self.ws), self.sid, str(path), str(self.ws))
        self.assertEqual([e for e in first["events"] if e["type"] == "cc-bash-write"], [])
        self.assertIn("call_p", first["pending"])
        res_block = {"type": "user", "sessionId": self.sid, "cwd": str(self.ws),
                     "timestamp": "2026-10-22T09:00:01.000Z",
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": "call_p",
                          "is_error": False, "content": ""}]}}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res_block, ensure_ascii=False) + "\n")
        second = ct.analyze(str(self.ws), self.sid, str(path), str(self.ws),
                            start_offset=first["offset"], pending=first["pending"])
        writes = [e for e in second["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, second)
        self.assertEqual(writes[0]["result"], "ok")
        self.assertEqual(second["pending"], {})

    def test_result_unknown_when_is_error_absent(self):
        # is_error 缺失 → result=unknown，不得冒充 ok。
        path = self.write([("Bash", {"command": "echo hi > artifacts/u.txt"}, "", None)])
        res = self.analyze(path)
        write = [e for e in res["events"] if e["type"] == "cc-bash-write"][0]
        self.assertEqual(write["result"], "unknown")

    def test_symlink_escape_rejected(self):
        # projects 目录内的 symlink 指向根外 → 必须拒读（realpath 校验）。
        outside = self.base / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        link = self.proj_dir / f"{self.sid}.jsonl"
        link.symlink_to(outside)
        ok, why = ct.validate_path(str(link), self.sid, str(self.ws))
        self.assertFalse(ok)
        self.assertIn(why, ("transcript-outside-projects", "transcript-missing"))

    def test_cwd_mismatch_rejected(self):
        other = self.base / "other-ws"
        other.mkdir()
        path = self.write([("Bash", {"command": "echo hi > x.txt"}, "", False)])
        data = Path(path).read_text(encoding="utf-8").splitlines()
        rewritten = []
        for line in data:
            rec = json.loads(line)
            rec["cwd"] = str(other)
            rewritten.append(json.dumps(rec, ensure_ascii=False))
        Path(path).write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        res = self.analyze(path)
        self.assertTrue(any(e.get("reason") == "transcript-cwd-mismatch" for e in res["events"]),
                        res["events"])

    def test_oversize_keeps_records_and_flags_incomplete(self):
        path = self.write([("Bash", {"command": "echo hi > artifacts/o.txt"}, "", False)])
        old = ct.MAX_BYTES
        ct.MAX_BYTES = 200  # 强制截断
        try:
            res = self.analyze(path)
        finally:
            ct.MAX_BYTES = old
        self.assertTrue(any(e.get("reason") == "transcript-truncated" for e in res["events"]),
                        res["events"])
        self.assertTrue(res["stats"].get("incomplete"))

    def test_changed_but_untargeted_gap(self):
        # 真实 git 仓 + 未提交改动，且转录里没有对应写入命令 → gap
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=ci", "-c", "user.email=ci@example.com", *args], capture_output=True)
        (self.ws / "probe.txt").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.ws), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=ci", "-c", "user.email=ci@example.com", "commit", "-qm", "init"], capture_output=True)
        (self.ws / "probe.txt").write_text("v2\n", encoding="utf-8")
        path = self.write([("Bash", {"command": "echo hi > artifacts/w.txt"}, "", False)])
        res = self.analyze(path)
        gaps = [e for e in res["events"] if e.get("reason") == "changed-but-untargeted"]
        self.assertTrue(gaps, res["events"])
        self.assertIn(str(self.ws / "probe.txt"), gaps[0]["files"])

    # ---- 跨 Stop 持久化 / 无 cwd fail-closed ----

    def test_read_survives_across_stops(self):
        # CC 的 Stop 每轮触发：轮 1 读全 gate 的读账必须跨轮存活，
        # 否则轮 2 写同一 gate 会复算出 read_state=missing（假阳性 would_deny）。
        # 关键：轮 2 的写**真的改了磁盘**（真机 F3 的形态）。若按写后磁盘重取参考修订，
        # 指纹必与账本不符 ⇒ coverage 被清空 ⇒ 假阳性。
        path = self.write([("Read", {"file_path": str(self.ws / GATE), "offset": 1},
                           self.gate_text(1), False)])
        stop1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        book = stop1["books"][GATE]
        self.assertEqual(book["revision"], "transcript")
        self.assertEqual(ct._read_state(book["coverage"], book["total"]), "complete")
        sha_before = book["sha"]

        gate = self.ws / GATE
        gate.write_bytes(gate.read_bytes() + b"## round-2 write\n")  # 模拟写执行
        self.assertNotEqual(ct._content_fingerprint(gate.read_bytes()), sha_before)

        path = self.append([("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False)])
        stop2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop1["offset"], books=stop1["books"])
        writes = [e for e in stop2["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, stop2["events"])
        self.assertFalse(writes[0]["would_deny"], writes[0])
        self.assertEqual(writes[0]["read_state"], "complete")
        self.assertEqual(writes[0]["revision"], "persisted")
        self.assertEqual(stop2["stats"]["would_deny"], 0)
        # F3b：本轮写过 ⇒ 账本换成**写后修订**（写前读证据已消费），下一轮未读直接写必 deny
        post_raw = gate.read_bytes()
        self.assertEqual(stop2["books"][GATE]["sha"], ct._content_fingerprint(post_raw))
        self.assertEqual(stop2["books"][GATE]["total"], ct.total_lines(post_raw))
        self.assertEqual(stop2["books"][GATE]["coverage"], [])
        self.assertEqual(stop2["books"][GATE]["revision"], "post-write")

    def test_write_without_prior_read_across_stops(self):
        # 首轮只有写（无读）⇒ 账本里没有该 gate，走磁盘、coverage 空 ⇒ deny；
        # 轮 2 再写同一 gate 仍须 deny（空账本不得凭空变成「读过」）。
        gate = self.ws / GATE
        path = self.write([("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False)])
        gate.write_bytes(gate.read_bytes() + b"round-1 write\n")  # 写真的落盘
        stop1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        first = [e for e in stop1["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(first), 1, stop1["events"])
        self.assertTrue(first[0]["would_deny"], first[0])
        self.assertEqual(first[0]["read_state"], "missing")
        self.assertEqual(first[0]["revision"], "disk", "账本无该 gate ⇒ 按现状走磁盘")
        self.assertEqual(stop1["books"][GATE]["coverage"], [])

        path = self.append([("Bash", {"command": f"printf 'y' >> {GATE}"}, "", False)])
        gate.write_bytes(gate.read_bytes() + b"round-2 write\n")
        stop2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop1["offset"], books=stop1["books"])
        writes = [e for e in stop2["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, stop2["events"])
        self.assertTrue(writes[0]["would_deny"], writes[0])
        self.assertEqual(writes[0]["read_state"], "missing")
        self.assertEqual(stop2["books"][GATE]["coverage"], [])

    def test_revision_change_revokes_across_stops(self):
        # 两轮之间 gate 内容变了、且该轮**没有**对它的写（变化无法归因于会话）
        # → 旧修订的读账不得贴到新修订上（撤销后重算）。
        path = self.write([("Read", {"file_path": str(self.ws / GATE), "offset": 1},
                           self.gate_text(1), False)])
        stop1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        gate = self.ws / GATE
        gate.write_bytes(gate.read_bytes() + "\n## 外部追加一节\n".encode("utf-8"))

        # 轮 2：只写非 gate 文件 ⇒ 该 gate 走磁盘分支，外部改动被发现并撤销读账
        path = self.append([("Bash", {"command": "printf 'x' >> artifacts/other.txt"}, "", False)])
        stop2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop1["offset"], books=stop1["books"])
        # 撤销是「清空后重算」，不是把旧 coverage 挂到新修订上
        self.assertEqual(stop2["books"][GATE]["coverage"], [])
        self.assertNotEqual(stop2["books"][GATE]["sha"], stop1["books"][GATE]["sha"])

        # 轮 3：再写同一 gate ⇒ 撤销后的空账本必须 deny，不得复活旧读账
        path = self.append([("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False)])
        stop3 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop2["offset"], books=stop2["books"])
        writes = [e for e in stop3["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, stop3["events"])
        self.assertTrue(writes[0]["would_deny"], writes[0])
        self.assertEqual(writes[0]["read_state"], "missing")

    def test_external_change_in_write_round_uses_ledger(self):
        # 边界（F3 修法的已知取舍）：外部改动与会话写落在**同一轮**时，转录里没有可区分
        # 二者的信号，一律按账本放行 —— gate 的外部改动由 Stop/SessionEnd 的
        # unsanctioned-gatefile-change（文件级指纹锚）兜底，不由本复算负责。
        # F3b 之后边界不变（该轮判定仍取账本、would_deny=false），但账本在写后换成写后
        # sha：写前证据已消费，下一轮未读直接写必 deny（含外部改动在内一律重读）。
        path = self.write([("Read", {"file_path": str(self.ws / GATE), "offset": 1},
                           self.gate_text(1), False)])
        stop1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        sha_before = stop1["books"][GATE]["sha"]
        gate = self.ws / GATE
        gate.write_bytes(gate.read_bytes() + "\n## 外部追加一节\n".encode("utf-8"))

        path = self.append([("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False)])
        stop2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop1["offset"], books=stop1["books"])
        writes = [e for e in stop2["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, stop2["events"])
        self.assertFalse(writes[0]["would_deny"], writes[0])
        self.assertEqual(writes[0]["revision"], "persisted")
        post_fp = ct._content_fingerprint(gate.read_bytes())
        self.assertNotEqual(post_fp, sha_before, "夹具前提：写后内容与账本写前修订不同")
        self.assertEqual(stop2["books"][GATE]["sha"], post_fp,
                         "写后账本换成写后修订：persisted 判定不下沉到账本")
        self.assertEqual(stop2["books"][GATE]["coverage"], [], "写前读证据已消费")

    def test_gate_write_consumes_evidence(self):
        # F3b（写即消费）：R1 读全 → R2 写（allow）→ R3 写（deny，写完未重读）→ R4 重读
        # → R5 写（allow）。R2 的写事件仍按写前账本判定（F3 修复不变），但该轮结束账本换
        # 成写后修订（sha/total = 磁盘现状、coverage = []），否则许可永不撤销。
        gate = self.ws / GATE
        path = self.write([("Read", {"file_path": str(gate), "offset": 1},
                            self.gate_text(1), False)])
        r1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        self.assertEqual(ct._read_state(r1["books"][GATE]["coverage"],
                                        r1["books"][GATE]["total"]), "complete")

        def stop(prev):
            return ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                              start_offset=prev["offset"], books=prev["books"])

        # R2：写（读后、跨 Stop）⇒ 写事件 allow，账本换成写后修订
        gate.write_bytes(gate.read_bytes() + b"w1\n")
        path = self.append([("Bash", {"command": f"printf 'w1\\n' >> {GATE}"}, "", False)])
        r2 = stop(r1)
        w2 = [e for e in r2["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(w2), 1, r2["events"])
        self.assertFalse(w2[0]["would_deny"], w2[0])
        self.assertEqual(w2[0]["read_state"], "complete")
        self.assertEqual(w2[0]["revision"], "persisted")
        post_raw = gate.read_bytes()
        self.assertEqual(r2["books"][GATE]["sha"], ct._content_fingerprint(post_raw))
        self.assertEqual(r2["books"][GATE]["total"], ct.total_lines(post_raw))
        self.assertEqual(r2["books"][GATE]["coverage"], [], "写前读证据已消费")
        self.assertEqual(r2["books"][GATE]["revision"], "post-write")

        # R3：再写（未重读）⇒ 账本 coverage 空 ⇒ deny / missing
        gate.write_bytes(gate.read_bytes() + b"w2\n")
        path = self.append([("Bash", {"command": f"printf 'w2\\n' >> {GATE}"}, "", False)])
        r3 = stop(r2)
        w3 = [e for e in r3["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(w3), 1, r3["events"])
        self.assertTrue(w3[0]["would_deny"], w3[0])
        self.assertEqual(w3[0]["read_state"], "missing")
        self.assertEqual(r3["stats"]["would_deny"], 1)

        # R4：重读（读的是 R3 写后的内容）⇒ 账本覆盖重建为 complete
        path = self.append([("Read", {"file_path": str(gate), "offset": 1},
                            self.gate_text(1), False)])
        r4 = stop(r3)
        self.assertEqual([e for e in r4["events"] if e["type"] == "cc-bash-write"], [],
                         "重读轮没有写事件")
        self.assertEqual(ct._read_state(r4["books"][GATE]["coverage"],
                                        r4["books"][GATE]["total"]), "complete")

        # R5：写 ⇒ 恢复 allow
        gate.write_bytes(gate.read_bytes() + b"w3\n")
        path = self.append([("Bash", {"command": f"printf 'w3\\n' >> {GATE}"}, "", False)])
        r5 = stop(r4)
        w5 = [e for e in r5["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(w5), 1, r5["events"])
        self.assertFalse(w5[0]["would_deny"], w5[0])
        self.assertEqual(w5[0]["read_state"], "complete")

    def test_read_after_write_same_round_rebuilds_coverage(self):
        # 顺序敏感：同一轮「写 → 完整读」时，写本身按当时的空账 deny（读在写之后，不算写前
        # 证据），但该轮结束时写后的这次读把 coverage 重建回 post-write 修订 ⇒ 下一轮写 allow。
        gate = self.ws / GATE
        path = self.write([("Bash", {"command": f"printf 'w\\n' >> {GATE}"}, "", False)])
        gate.write_bytes(gate.read_bytes() + b"w\n")          # 写真的落盘
        path = self.append([("Read", {"file_path": str(gate), "offset": 1},
                            self.gate_text(1), False)])        # 写后重读（读到写后内容）
        stop1 = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        writes = [e for e in stop1["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 1, stop1["events"])
        self.assertTrue(writes[0]["would_deny"], "读在写之后 ⇒ 写那一刻证据为空")
        self.assertEqual(writes[0]["read_state"], "missing")
        self.assertEqual(stop1["books"][GATE]["sha"], ct._content_fingerprint(gate.read_bytes()))
        self.assertEqual(ct._read_state(stop1["books"][GATE]["coverage"],
                                        stop1["books"][GATE]["total"]), "complete")

        gate.write_bytes(gate.read_bytes() + b"w2\n")
        path = self.append([("Bash", {"command": f"printf 'w2\\n' >> {GATE}"}, "", False)])
        stop2 = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=stop1["offset"], books=stop1["books"])
        writes2 = [e for e in stop2["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes2), 1, stop2["events"])
        self.assertFalse(writes2[0]["would_deny"], writes2[0])
        self.assertEqual(writes2[0]["read_state"], "complete")

    def test_second_write_same_round_denied(self):
        # F4（真机 R98：会话 211c332c 同一轮连发两条 printf >> POLICY2.md）：第一条写已消费
        # 许可、修订已变 ⇒ 第二条**未重读**就写必须是 missing/deny。原实现只在轮末清 coverage，
        # 同一轮内后续写仍看到读时的完整 coverage（假阴性）。
        gate = self.ws / GATE
        before = gate.read_bytes()
        path = self.write([
            ("Read", {"file_path": str(gate), "offset": 1}, self.gate_text(1), False),
            ("Bash", {"command": f"printf 'n1' >> {GATE}"}, "", False),
            ("Bash", {"command": f"printf 'n2' >> {GATE}"}, "", False),
        ])
        gate.write_bytes(before + b"n1n2")  # 两条写真的落盘
        res = self.analyze(path)
        writes = [e for e in res["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 2, res["events"])
        self.assertFalse(writes[0]["would_deny"], writes[0])
        self.assertEqual(writes[0]["read_state"], "complete")
        self.assertEqual(writes[0]["revision"], "transcript")
        self.assertTrue(writes[1]["would_deny"], writes[1])
        self.assertEqual(writes[1]["read_state"], "missing")
        self.assertEqual(writes[1]["revision"], "post-write")
        self.assertEqual(res["stats"]["would_deny"], 1)
        # 轮末账本仍是写后修订（跨 Stop 语义不变）
        post_raw = gate.read_bytes()
        self.assertEqual(res["books"][GATE]["sha"], ct._content_fingerprint(post_raw))
        self.assertEqual(res["books"][GATE]["coverage"], [])
        self.assertEqual(res["books"][GATE]["revision"], "post-write")

    def test_read_between_writes_same_round_allows(self):
        # 既有语义不得回退：同一轮里「写之后的整文件读」仍能重建 coverage ⇒ 之后的写 allow。
        gate = self.ws / GATE
        before = gate.read_bytes()
        after_n1 = before + b"n1\n"
        path = self.write([
            ("Read", {"file_path": str(gate), "offset": 1}, _numbered(before), False),
            ("Bash", {"command": f"printf 'n1\\n' >> {GATE}"}, "", False),
            ("Read", {"file_path": str(gate), "offset": 1}, _numbered(after_n1), False),
            ("Bash", {"command": f"printf 'n2\\n' >> {GATE}"}, "", False),
        ])
        gate.write_bytes(after_n1 + b"n2\n")  # 两条写真的落盘
        res = self.analyze(path)
        writes = [e for e in res["events"] if e["type"] == "cc-bash-write"]
        self.assertEqual(len(writes), 2, res["events"])
        for write in writes:
            self.assertFalse(write["would_deny"], write)
            self.assertEqual(write["read_state"], "complete")
        self.assertEqual(writes[0]["revision"], "transcript")
        self.assertEqual(writes[1]["revision"], "post-write")
        self.assertEqual(res["stats"]["would_deny"], 0)

    def test_missing_cwd_is_gap_not_analyzed(self):
        # 整条转录没有 cwd ⇒ 无法排除 slug 碰撞（/a-b/c 与 /a/b-c）：fail-closed，
        # 记 transcript-cwd-missing，不做任何写归因；offset 前进避免每轮重放。
        path = self.write([("Bash", {"command": f"printf 'x' >> {GATE}"}, "", False)],
                          with_cwd=False)
        res = ct.analyze(str(self.ws), self.sid, path, str(self.ws))
        gaps = [e for e in res["events"] if e.get("type") == "cc-bash-gap"]
        self.assertTrue(any(e.get("reason") == "transcript-cwd-missing" for e in gaps), res["events"])
        self.assertEqual([e for e in res["events"] if e["type"] == "cc-bash-write"], [],
                         "无 cwd 的转录不得进入复算")
        self.assertEqual(res["pending"], {})
        self.assertEqual(res["offset"], os.path.getsize(path), "offset 必须前进，避免重放")
        # 下一轮没有新记录 → 不再重复报 gap
        again = ct.analyze(str(self.ws), self.sid, path, str(self.ws),
                           start_offset=res["offset"])
        self.assertFalse([e for e in again["events"]
                          if e.get("reason") == "transcript-cwd-missing"], again["events"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
