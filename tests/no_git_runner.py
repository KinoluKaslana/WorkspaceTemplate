"""在「没有 git」的环境里跑全量测试：断言 0 failure/0 error（只允许 skip）。

用法：python3 tools/no_git_runner.py（在模板仓根目录）
"""
import shutil
import sys
import unittest

_real_which = shutil.which


def _fake_which(cmd, *a, **k):
    if cmd == "git":
        return None
    return _real_which(cmd, *a, **k)


shutil.which = _fake_which

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for d in ("tests", "hooks-tests"):
    suite.addTests(loader.discover(d, pattern="test_*.py", top_level_dir=d))

res = unittest.TextTestRunner(verbosity=0).run(suite)
print(f"run={res.testsRun} failures={len(res.failures)} errors={len(res.errors)} skipped={len(res.skipped)}")
sys.exit(0 if not res.failures and not res.errors else 1)
