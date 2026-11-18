#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".workspace" / "hooks"))
import gate_core as core  # noqa: E402
import hook  # noqa: E402


class DshReadInterval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dsh-read-interval-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text('{"workspace_status":"active"}\n', encoding="utf-8")
        self.lines = [f"line {i}" for i in range(1, 249)]
        # 夹具必须**逐字节确定**：write_text 在 Windows 上把 "\n" 翻成 "\r\n"，
        # 而 hook._dsh_read_body 已把客户端正文归一成 LF（hook.py:400），
        # 于是磁盘侧 CRLF / 正文侧 LF 会让内容锚（gate_core.py:928 字节全等）拒证，
        # 6 条测试在 Windows 全挂、在 Linux 全绿（r37）。写字节，不写文本。
        self.write_gate()
        self.sid = "dsh-interval-" + self._testMethodName

    def write_gate(self) -> None:
        (self.ws / core.GATE_FILE).write_bytes("\n".join(self.lines).encode("utf-8"))

    def run_read(self, text: str, **tool_input) -> dict:
        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "read", "session_id": self.sid,
            "tool_input": {"file_path": str(self.ws / core.GATE_FILE), **tool_input},
            "tool_response": {"isError": False, "content": [{"type": "text", "text": text}]},
        }
        hook._record_read(payload, "dsh", str(self.ws))
        return core.load_state(str(self.ws), "dsh", self.sid)

    def gate_key(self) -> str:
        """账本键口径的唯一来源：gate_core.reads_key（工作区相对 POSIX 路径）。"""
        return core.reads_key(str(self.ws), core.GATE_FILE)

    def gate_entry(self, state: dict) -> dict:
        """断言键集合 == 期望的相对路径集合，再取条目（不以「有键就算过」）。"""
        self.assertEqual(set(state["reads"]), {self.gate_key()})
        return state["reads"][self.gate_key()]

    def response(self, start=1, end=248, newline="\n", trailer="") -> str:
        numbered = newline.join(f"{i}: {self.lines[i - 1]}" for i in range(start, end + 1))
        suffix = newline + trailer if trailer else ""
        return (f"<path>{self.ws / core.GATE_FILE}</path>{newline}<type>file</type>{newline}"
                f"<content>{newline}{numbered}{suffix}")

    def test_file_path_only_infers_all_248_lines(self):
        entry = self.gate_entry(self.run_read(self.response()))
        self.assertEqual(entry["coverage"], [[1, 248]])
        self.assertTrue(entry["complete"])

    def test_crlf_infers_all_248_lines(self):
        entry = self.gate_entry(self.run_read(self.response(newline="\r\n")))
        self.assertEqual(entry["coverage"], [[1, 248]])

    def test_eof_trailer_is_authoritative(self):
        entry = self.gate_entry(self.run_read(self.response(trailer="(End of file - total 248 lines)")))
        self.assertEqual(entry["coverage"], [[1, 248]])

    def test_showing_trailer_records_partial_interval(self):
        text = self.response(end=100, trailer="(Showing lines 1-100 of 248. Use offset=101 to continue.)")
        entry = self.gate_entry(self.run_read(text))
        self.assertEqual(entry["coverage"], [[1, 100]])
        self.assertFalse(entry["complete"])

    def test_replacement_character_does_not_break_line_numbers(self):
        self.lines[20] = "状�"
        self.write_gate()
        entry = self.gate_entry(self.run_read(self.response()))
        self.assertTrue(entry["complete"])

    def test_processed_read_appends_timestamped_event(self):
        state = self.run_read(self.response())
        # 先断言记账本身成立：账本为空时在此就报「键集合不匹配」，
        # 而不是让下面的 assertTrue(events) 只丢一句「[] is not true」。
        self.gate_entry(state)
        events = state["events"]
        self.assertTrue(events)
        self.assertEqual(events[-1]["type"], "read")
        self.assertEqual(events[-1]["file"], self.gate_key())
        self.assertRegex(events[-1]["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
