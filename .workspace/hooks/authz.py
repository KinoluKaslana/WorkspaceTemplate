#!/usr/bin/env python3
"""authz.py — 治理文件写豁免令牌 CLI（W2/B，纯 stdlib）。

令牌文件：<ws>/.workspace/.hook-state/authz.json（schema 1）。
用法：
  python3 authz.py mint --workspace <ws> --target AGENT_RULES.md [--target …] [--ttl 300]
  python3 authz.py list --workspace <ws>
  python3 authz.py revoke --workspace <ws> --id <nonce>
目标相对路径按 <ws> 绝对化；拒绝通配（* ? [ {）。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_core as core  # noqa: E402

WILDCARD_CHARS = set("*?[{")


def _absolutize(ws: str, target: str) -> str:
    if any(ch in WILDCARD_CHARS for ch in target):
        raise ValueError(f"目标含通配符，拒绝：{target}")
    p = os.path.expanduser(target)
    if not os.path.isabs(p):
        p = os.path.join(ws, p)
    return os.path.normpath(p)


def cmd_mint(args) -> int:
    ws = os.path.abspath(os.path.expanduser(args.workspace))
    if not args.target:
        print(json.dumps({"error": "至少需要一个 --target"}, ensure_ascii=False))
        return 2
    try:
        targets = [_absolutize(ws, t) for t in args.target]
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    nonce = secrets.token_hex(16)
    expires_at = int(time.time()) + int(args.ttl)
    data = core.load_authz(ws)
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        tokens = []
        data["tokens"] = tokens
    tokens.append({
        "id": nonce,
        "targets": targets,
        "expires_at": expires_at,
        "consumed": False,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    core.save_authz(ws, data)
    print(json.dumps({"id": nonce, "expires_at": expires_at, "targets": targets},
                     ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    ws = os.path.abspath(os.path.expanduser(args.workspace))
    data = core.load_authz(ws)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke(args) -> int:
    ws = os.path.abspath(os.path.expanduser(args.workspace))
    data = core.load_authz(ws)
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        tokens = []
        data["tokens"] = tokens
    before = len(tokens)
    data["tokens"] = [t for t in tokens
                      if not (isinstance(t, dict) and t.get("id") == args.id)]
    removed = before - len(data["tokens"])
    core.save_authz(ws, data)
    print(json.dumps({"revoked": removed, "id": args.id}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("mint")
    m.add_argument("--workspace", required=True)
    m.add_argument("--target", action="append", default=[])
    m.add_argument("--ttl", type=int, default=300)
    m.set_defaults(func=cmd_mint)
    l = sub.add_parser("list")
    l.add_argument("--workspace", required=True)
    l.set_defaults(func=cmd_list)
    r = sub.add_parser("revoke")
    r.add_argument("--workspace", required=True)
    r.add_argument("--id", required=True)
    r.set_defaults(func=cmd_revoke)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
