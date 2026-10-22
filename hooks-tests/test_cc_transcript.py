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

运行：python3 -B hooks-tests/test_cc_transcript.py
"""
import json
import os
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


def _records(session_id, steps):
    """steps: [(name, input, result_text, is_error)] → 转录行（tool_use + tool_result）。"""
    out = [{"type": "queue-operation", "sessionId": session_id}]
    for idx, (name, inp, text, err) in enumerate(steps):
        tid = f"call_{idx:02d}"
        out.append({"type": "assistant", "sessionId": session_id,
                    "timestamp": f"2026-10-22T08:45:{35 + idx:02d}.000Z",
                    "message": {"role": "assistant",
                                "content": [{"type": "tool_use", "id": tid, "name": name,
                                             "input": inp}]}})
        out.append({"type": "user", "sessionId": session_id,
                    "timestamp": f"2026-10-22T08:45:{35 + idx:02d}.500Z",
                    "message": {"role": "user",
                                "content": [{"type": "tool_result", "tool_use_id": tid,
                                             "is_error": err, "content": text}]}})
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

    def write(self, steps, session_id=None):
        sid = session_id or self.sid
        path = self.proj_dir / f"{sid}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for rec in _records(sid, steps):
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
        self.assertTrue(write["targets"][0].endswith("artifacts/x.txt"))

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
                       ("Bash", {"command": "printf 'x' >> TEST-GATE.md"}, "", False)])) + "\n",
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
            subprocess.run(["git", "-C", str(self.ws), *args], capture_output=True)
        (self.ws / "probe.txt").write_text("v1\n")
        subprocess.run(["git", "-C", str(self.ws), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(self.ws), "commit", "-qm", "init"], capture_output=True)
        (self.ws / "probe.txt").write_text("v2\n")
        path = self.write([("Bash", {"command": "echo hi > artifacts/w.txt"}, "", False)])
        res = self.analyze(path)
        gaps = [e for e in res["events"] if e.get("reason") == "changed-but-untargeted"]
        self.assertTrue(gaps, res["events"])
        self.assertIn(str(self.ws / "probe.txt"), gaps[0]["files"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
