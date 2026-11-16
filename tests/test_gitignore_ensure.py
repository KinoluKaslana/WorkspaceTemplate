#!/usr/bin/env python3
"""`template_sync.ensure_ignore_lines`：`.gitignore` 只在缺行时追加，不覆盖用户内容。

为什么需要：`.gitignore` 不在 `TEMPLATE_FILES`（用户所有，同步不能整文件覆盖），
但本机状态目录（`.hook-state/`、`.local/`）必须被忽略；否则运行期状态会进仓库、随同步到处跑。

运行：python3 -B tests/test_gitignore_ensure.py
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("ts_ignore", ROOT / "scripts" / "template_sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EnsureIgnore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gi-")
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)
        self.ts = _load()

    def test_appends_only_missing_lines_and_keeps_user_content(self):
        gi = self.ws / ".gitignore"
        gi.write_text("node_modules/\n.workspace/.hook-state/\n", encoding="utf-8")
        added, err = self.ts.ensure_ignore_lines(self.ws)
        self.assertIsNone(err)
        self.assertEqual(added, [".workspace/.local/"])
        text = gi.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("node_modules/\n"), "用户内容被改了")
        self.assertEqual(text.count(".workspace/.local/"), 1)
        self.assertEqual(self.ts.ensure_ignore_lines(self.ws), ([], None), "重复追加")

    def test_creates_file_when_missing(self):
        added, err = self.ts.ensure_ignore_lines(self.ws)
        self.assertIsNone(err)
        self.assertIn(".workspace/.hook-state/", added)
        self.assertIn(".workspace/.local/", added)
        text = (self.ws / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".workspace/.local/", text)

    def test_write_failure_is_reported_not_swallowed(self):
        """.gitignore 写不进去时必须回报错误，不能静默当成「无需追加」。"""
        (self.ws / ".gitignore").mkdir()          # 目录冒充文件 → 读写都失败
        added, err = self.ts.ensure_ignore_lines(self.ws)
        self.assertEqual(added, [])
        self.assertIsNotNone(err, "写失败被吞掉了")

    def test_no_trailing_newline_is_respected(self):
        gi = self.ws / ".gitignore"
        gi.write_text("a.txt", encoding="utf-8")
        self.ts.ensure_ignore_lines(self.ws)
        text = gi.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("a.txt\n"), f"粘连了：{text!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
