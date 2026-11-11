#!/usr/bin/env python3
"""hooks-tests 共用：本机作用域状态目录。

`.hook-state/` 现在按机器作用域分片（`.hook-state/<scope>/`）。测试夹具的工作区里通常没有
`scripts/local_state.py`，此时 scope 为 `"unknown"`；为了让断言不写死分支，这里统一解析：
已存在唯一子目录就用它，否则用 `unknown`。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".workspace" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))


def state_dir(ws) -> Path:
    d = Path(ws) / ".workspace" / ".hook-state"
    try:
        subs = [p for p in d.iterdir() if p.is_dir() and not p.name.startswith("__")]
    except OSError:
        subs = []
    if len(subs) == 1:
        return subs[0]
    return d / "unknown"
