#!/usr/bin/env python3
"""`policy_facts.check()` 只解析一次 `.workspace/bootstrap.json` 的身份判据。

R29 的判据是**读盘次数**，不是产物文本：一次 `check()` 内三态只许解析一次（旧实现 5 次），
且这份解析结果**只在本调用内有效**——同进程连续多次 `check()` 必须每次重新读盘，
否则第一次的身份会残留（测试就反复调 `check()`，缓存会把后续调用钉在旧形态上）。
产物文本/顺序/数量不变由 `tests/test_docs_consistency.py` 的既有用例守。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import policy_facts as pf  # noqa: E402

BOOT = Path(".workspace") / "bootstrap.json"
_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo", ".venv*",
                                     "venv", "node_modules", ".local", "*.log")


def _tmpdir() -> Path:
    """临时目录跟随 TMPDIR；显式给 `WS_DOCS_TMP` 时以它为准（与本仓既有约定一致）。"""
    base = os.environ.get("WS_DOCS_TMP")
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="pf-single-read-", dir=base))
    return Path(tempfile.mkdtemp(prefix="pf-single-read-"))


class _ReadCounter:
    """只数 `bootstrap.json` 的 `Path.read_text`，其它读盘一概不记。"""

    def __init__(self):
        self.paths: list[str] = []
        self._real = Path.read_text

    def __enter__(self):
        real = self._real

        def counting(inner_self, *args, **kwargs):
            if inner_self.name == "bootstrap.json":
                self.paths.append(str(inner_self))
            return real(inner_self, *args, **kwargs)

        self._patch = mock.patch.object(Path, "read_text", counting)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class SingleBootstrapRead(unittest.TestCase):
    def test_one_check_reads_bootstrap_exactly_once(self):
        """一次 `check()` 只读一遍 `bootstrap.json`（旧实现 5 次）。"""
        with _ReadCounter() as counter:
            pf.check(ROOT)
        self.assertEqual(len(counter.paths), 1,
                         f"一次 check() 读了 {len(counter.paths)} 次 bootstrap.json："
                         f"{counter.paths}")

    def test_repeat_check_does_not_reuse_previous_status(self):
        """同进程第二次 `check()` 必须重新读盘；否则身份会被第一次的缓存钉住。"""
        copy = _tmpdir() / "repo"
        shutil.copytree(ROOT, copy, ignore=_COPY_IGNORE, symlinks=True)
        self.addCleanup(shutil.rmtree, copy, True)
        boot = copy / BOOT
        data = json.loads(boot.read_text(encoding="utf-8"))
        self.assertEqual(data.get("workspace_status"), "template",
                         "本用例假定副本以模板身份起跑")
        # 生成块之外写一份当前政策版本的散文副本：template 下必红，instance 下 README 豁免。
        readme = copy / "README.md"
        cur = pf.collect(copy)["policy"]
        readme.write_text(readme.read_text(encoding="utf-8")
                          + f"\n本仓政策版本 {cur} 的散文副本。\n", encoding="utf-8")

        with _ReadCounter() as counter:
            first = pf.check(copy)
            n_first = len(counter.paths)
            data["workspace_status"] = "active"
            boot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            second = pf.check(copy)
            n_second = len(counter.paths) - n_first

        self.assertEqual(n_first, 1, f"第一次 check() 读了 {n_first} 次")
        self.assertEqual(n_second, 1, f"第二次 check() 读了 {n_second} 次（应为重新读盘）")
        self.assertTrue(any("README.md" in x for x in first),
                        f"template 身份必须仍按模板契约查 README：{first}")
        self.assertFalse(any("README.md" in x for x in second),
                         f"翻成 active 后必须走 instance 豁免（身份来自本次读盘，不是缓存）：{second}")

    def test_standalone_checks_still_resolve_status_themselves(self):
        """`status=None` 后路：不传三态时各检查自己解析一次，语义不变（全绿）。"""
        self.assertEqual(pf.check_blocks(ROOT), [])
        self.assertEqual(pf.clause_range_offenders(ROOT), [])
        self.assertEqual(pf.literal_offenders(ROOT), [])


if __name__ == "__main__":
    unittest.main()
