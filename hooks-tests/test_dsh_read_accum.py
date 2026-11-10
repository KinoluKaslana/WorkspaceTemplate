#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh 分次读必须能累加（真实用户反馈 问题 A 的回归）。

缺口：`_record_read` 的 dsh 分支只在 body 不以 LF 结尾时补 LF，而 `gate_core.record_read`
对非 EOF 区间**恒补**一个 LF。区间末行恰好是空行时 body 已带 LF → 少补 → 内容锚定 sha256 不等
→ `no-admissible-evidence` → 分次读永远记不上（与 R98「分次可累加」冲突）。

本测试走真实代码路径（`hook._record_read` + `gate_core.record_read`）：构造一个第 130 行为空行的
治理文件，先读 1..130，再读 131..EOF，断言 coverage 合并为 [[1, 总行数]]。

运行：python3 -B hooks-tests/test_dsh_read_accum.py
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONUTF8", "1")
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402
import hook  # noqa: E402


def numbered(lines: list[str], start: int) -> str:
    """dsh read 的正文形态：`N: 内容`。"""
    return "\n".join(f"{start + i}: {ln}" for i, ln in enumerate(lines))


class DshReadAccum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dsh-read-accum-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text('{"workspace_status": "active"}\n', encoding="utf-8")
        # 240 行正文：第 130 行（1-based）是空行 —— 正是触发条件
        lines = [f"line {i}" for i in range(1, 241)]
        lines[129] = ""
        self.lines = lines
        (self.ws / core.GATE_FILE).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        self.sid = "dsh-read-" + self._testMethodName

    def payload(self, offset: int, limit: int) -> dict:
        chunk = self.lines[offset - 1: offset - 1 + limit]
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "read",
            "session_id": self.sid,
            "tool_input": {"path": str(self.ws / core.GATE_FILE), "offset": offset, "limit": limit},
            "tool_response": numbered(chunk, offset),
        }

    def entry(self) -> dict:
        st = core.load_state(str(self.ws), "dsh", core.scoped_id(self.sid, None))
        return st["reads"].get(core.GATE_FILE, {})

    def test_l1_partial_read_ending_on_blank_line_is_recorded(self):
        hook._record_read(self.payload(1, 130), "dsh", str(self.ws))
        self.assertEqual(self.entry().get("coverage"), [[1, 130]],
                         "1..130（末行为空行）必须入账，否则分次读永远累加不上")

    def test_l2_two_partial_reads_merge_to_complete(self):
        hook._record_read(self.payload(1, 130), "dsh", str(self.ws))
        hook._record_read(self.payload(131, 111), "dsh", str(self.ws))
        e = self.entry()
        self.assertEqual(e.get("coverage"), [[1, 241]])
        self.assertTrue(e.get("complete"))

    def test_l3_whole_file_read_still_works(self):
        hook._record_read(self.payload(1, 241), "dsh", str(self.ws))
        e = self.entry()
        self.assertTrue(e.get("complete"))
        self.assertEqual(e.get("coverage"), [[1, 241]])

    def test_l4_partial_reads_not_on_blank_line_still_merge(self):
        """末行非空时 body 不带 LF → 补一个正好；不得多补成两个。"""
        hook._record_read(self.payload(1, 10), "dsh", str(self.ws))
        hook._record_read(self.payload(11, 10), "dsh", str(self.ws))
        self.assertEqual(self.entry().get("coverage"), [[1, 20]])

    def _case(self, lines: list[str], trailing_lf: bool, windows: list[tuple[int, int]]) -> dict:
        gate = self.ws / core.GATE_FILE
        text = "\n".join(lines) + ("\n" if trailing_lf else "")
        gate.write_bytes(text.encode("utf-8"))
        for off, lim in windows:
            hook._record_read(self.payload_with(bytes(text, "utf-8"), off, lim), "dsh", str(self.ws))
        return self.entry()

    def payload_with(self, data: bytes, offset: int, limit: int) -> dict:
        lines = data.decode("utf-8").split("\n")
        chunk = lines[offset - 1: offset - 1 + limit]
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "read",
            "session_id": self.sid,
            "tool_input": {"path": str(self.ws / core.GATE_FILE), "offset": offset, "limit": limit},
            "tool_response": numbered(chunk, offset),
        }

    def test_l5_table_driven(self):
        """表驱动：有/无末尾 LF × 内部/边界空行 × 多种切法，都必须能累加成完整覆盖。"""
        variants = {
            "no_trailing_lf": [f"L{i}" for i in range(1, 61)],
            "trailing_lf": [f"L{i}" for i in range(1, 61)],
            "blank_at_30_trailing": [("" if i == 30 else f"L{i}") for i in range(1, 61)],
        }
        for name, lines in variants.items():
            tlf = name != "no_trailing_lf"
            total = len(lines) + (1 if tlf else 0)
            windows_sets = [
                [(1, 30), (31, 30)],
                [(1, 15), (16, 15), (31, 30)],
                [(1, len(lines))],
            ]
            for idx, windows in enumerate(windows_sets):
                with self.subTest(variant=name, windows=idx):
                    self.sid = f"{name}-{idx}"
                    e = self._case(lines, tlf, windows)
                    self.assertTrue(e.get("complete"), (name, windows, e))
                    self.assertEqual(e.get("coverage"), [[1, total]], (name, windows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
