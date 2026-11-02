#!/usr/bin/env python3
"""委托记录（delegation）：父 Agent 为子 agent 签发受管写的授权。

设计（政策 §12 R98 扩展 / §13 R102）：
- 父 Agent 用 `mint` 签发：前置校验父会话对 gate 当前修订有完整读证据；无证据拒发。
- 记录字段：id / parent_scope / child_scope / targets[]（绝对路径，禁通配）/ gate_fingerprint
  / issued_at / expires_at / status / user_scope_ref（审计用，不判真伪）/ revoked_at。
- 子写判定（gate 侧）：目标精确命中未失效的委托 + 父会话对当前修订仍有完整读证据。
- 失效：TTL / 父会话 finalize / 显式撤销 / gate 修订变化 / 子会话停止。
- 存储：<ws>/.workspace/.hook-state/delegations.jsonl（已 gitignore）。

信任边界：与 authz/gate 相同——同 uid 可改账本，本模块防「无意识越权」，不防主动伪造。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

STATE_DIRNAME = ".workspace/.hook-state"
LEDGER = "delegations.jsonl"
DEFAULT_TTL = 1800


def _state_dir(ws: str | Path) -> Path:
    d = Path(ws) / STATE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> int:
    return int(time.time())


def _sha256(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def gate_fingerprint(ws: str | Path, gate_rel: str = "AGENT_RULES.md") -> str | None:
    p = Path(ws) / gate_rel
    data = _sha256(p)
    if data is None:
        return None
    return f"c{p.stat().st_size}:{data}"


def _state_file(ws: str | Path, client: str, scope: str) -> Path:
    """cc/codex 的账本带客户端前缀；Hermes 插件的账本直接用会话名。"""
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in scope)[:120]
    name = f"{client}-{safe}.json" if client in ("cc", "codex") else f"{safe}.json"
    return _state_dir(ws) / name


def read_evidence_ok(ws: str | Path, client: str, scope: str, gate_rel: str = "AGENT_RULES.md") -> bool:
    """父/子会话对 gate 当前修订是否有完整读证据。"""
    p = _state_file(ws, client, scope)
    if not p.is_file():
        return False
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    fp = gate_fingerprint(ws, gate_rel)
    if client in ("cc", "codex"):
        entry = (st.get("reads") or {}).get(gate_rel)
        return isinstance(entry, dict) and bool(entry.get("complete")) and entry.get("fingerprint") == fp
    # Hermes 插件：read 是布尔标记，指纹在 coverage_rev
    if not bool((st.get("read") or {}).get(gate_rel)):
        return False
    rev = (st.get("coverage_rev") or {}).get(gate_rel)
    return rev is None or rev == fp


def _rows(ws: str | Path) -> list[dict]:
    p = _state_dir(ws) / LEDGER
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _append(ws: str | Path, row: dict) -> None:
    p = _state_dir(ws) / LEDGER
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def mint(ws: str | Path, parent_scope: str, child_scope: str, targets: list[str],
         client: str = "hermes", ttl: int = DEFAULT_TTL, user_scope_ref: str = "") -> dict:
    """签发委托；父无读证据或 targets 为空/含通配 → 拒绝（fail-closed）。"""
    ws = Path(ws)
    if not targets:
        raise SystemExit("refused: targets 不能为空（禁通配，必须逐项列举绝对路径）")
    for t in targets:
        if any(ch in t for ch in "*?[") or not os.path.isabs(t):
            raise SystemExit(f"refused: 目标必须是绝对路径且禁通配：{t}")
    if not read_evidence_ok(ws, client, parent_scope):
        raise SystemExit("refused: 父会话对 gate 当前修订没有完整读证据，不能签发委托")
    fp = gate_fingerprint(ws)
    row = {
        "type": "delegation-mint",
        "id": hashlib.sha256(f"{parent_scope}|{child_scope}|{_now()}|{os.getpid()}".encode()).hexdigest()[:16],
        "client": client,
        "parent_scope": parent_scope,
        "child_scope": child_scope,
        "targets": [os.path.realpath(t) for t in targets],
        "gate_fingerprint": fp,
        "issued_at": _now(),
        "expires_at": _now() + int(ttl),
        "status": "active",
        "user_scope_ref": user_scope_ref,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _append(ws, row)
    return row


def find_valid(ws: str | Path, child_scope: str, target: str, client: str = "hermes") -> dict | None:
    """返回覆盖该目标的有效委托；不满足任一条件返回 None。"""
    ws = Path(ws)
    tgt = os.path.realpath(target)
    fp = gate_fingerprint(ws)
    now = _now()
    for row in reversed(_rows(ws)):
        if row.get("type") != "delegation-mint" or row.get("status") != "active":
            continue
        if row.get("client") != client or row.get("child_scope") != child_scope:
            continue
        if tgt not in (row.get("targets") or []):
            continue
        if int(row.get("expires_at") or 0) <= now:
            continue
        if row.get("gate_fingerprint") != fp:
            continue  # gate 修订变化 → 旧委托失效
        if not read_evidence_ok(ws, client, row["parent_scope"]):
            continue  # 父证据失效 → 委托失效
        return row
    return None


def revoke(ws: str | Path, delegation_id: str, reason: str = "user-revoked") -> bool:
    ws = Path(ws)
    hit = False
    for row in _rows(ws):
        if row.get("id") == delegation_id and row.get("status") == "active":
            row["status"] = "revoked"
            row["revoked_at"] = _now()
            row["revoke_reason"] = reason
            _append(ws, row)  # 追加式：以最新一条为准（读取时倒序取 active）
            hit = True
    return hit


def main() -> int:
    ap = argparse.ArgumentParser(description="workspace delegation records")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="父 Agent 为子会话签发受管写委托")
    m.add_argument("--workspace", required=True)
    m.add_argument("--parent", required=True)
    m.add_argument("--child", required=True)
    m.add_argument("--target", action="append", required=True)
    m.add_argument("--client", default="hermes")
    m.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    m.add_argument("--ref", default="")

    v = sub.add_parser("check", help="查询某目标是否有有效委托")
    v.add_argument("--workspace", required=True)
    v.add_argument("--child", required=True)
    v.add_argument("--target", required=True)
    v.add_argument("--client", default="hermes")

    r = sub.add_parser("revoke", help="撤销委托")
    r.add_argument("--workspace", required=True)
    r.add_argument("--id", required=True)

    a = ap.parse_args()
    if a.cmd == "mint":
        row = mint(a.workspace, a.parent, a.child, a.target, a.client, a.ttl, a.ref)
        print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.cmd == "check":
        row = find_valid(a.workspace, a.child, a.target, a.client)
        print(json.dumps(row or {"valid": False}, ensure_ascii=False))
        return 0 if row else 1
    if a.cmd == "revoke":
        ok = revoke(a.workspace, a.id)
        print(json.dumps({"revoked": ok}))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
