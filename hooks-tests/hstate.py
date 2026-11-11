#!/usr/bin/env python3
"""hooks-tests 共用：本机作用域状态目录。

`.hook-state/` 按机器作用域分片（`.hook-state/<scope>/`）。夹具工作区里通常没有
`scripts/local_state.py`，但 scope **不再**因此退化为 `"unknown"`：`gate_core` 自带的
`_fallback_scope()` 会用机器身份（或本机种子）派生。断言要能同时适用于两种情形，
所以这里统一**问 gate_core 要目录**，而不是自己猜目录名。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".workspace" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))


def state_dir(ws) -> Path:
    """与守门本体**同一算法**的状态目录（避免测试写到 A、代码写到 B）。"""
    import gate_core
    return Path(gate_core.state_dir(str(ws)))
