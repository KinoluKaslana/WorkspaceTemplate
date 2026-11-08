#!/usr/bin/env python3
"""gate 接线状态：克隆之后、把 harness 跑上去之前的一条只读自检。

**为什么需要它**：gate 不是克隆即生效的。三个 harness 的启用条件各不相同，而
dsh **没有项目级自动发现**——不带 `--patch .dsh/workspace-gate/cordis.yml`
启动，就等于完全没有 gate（连读证据和事后审计都不会有）。工作区里文件齐全
会给人「已经受管」的错觉，所以要有办法一条命令判「现在到底有没有接线」。

各 harness 的证据来源（只读取，不写任何东西）：

| harness | 启用条件 | 证据 |
|---|---|---|
| cc | `.claude/settings.json`（项目受信后自动加载） | `.workspace/.hook-state/gate.log` 里 `client=cc` |
| codex | `.codex/hooks.json` | 同上，`client=codex` |
| hermes | `HERMES_ENABLE_PROJECT_PLUGINS=true` + config `plugins.enabled` | 同上，`client=hermes` |
| dsh | **每次启动**都要 `--patch`（无自动发现） | `.workspace/.dsh-gate.log` 的 `apply` 事件 + `.hook-state/dsh-session-*.json` |

退出码：0 = 满足要求（或没给 `--require`）；1 = `--require` 里的 harness 没证据。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

STATE_DIR = Path(".workspace") / ".hook-state"
CLIENTS = ("cc", "codex", "hermes", "dsh")
FUTURE_SKEW_MIN = 2.0     # 时间戳比现在超前超过这个分钟数 → 不算证据（时钟回拨 / 伪造）
GATE_LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*client=(\w+)")
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s")
_HERMES_EVENT_RE = re.compile(r"^(workspace-gate (active|disabled)|pre_tool_call|post_tool_call|pre_llm_call|on_session_(end|finalize))")


def _parse_stamp(s: str):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _age_minutes(ts: float) -> float:
    return round((time.time() - ts) / 60.0, 1)


def collect(workspace: Path) -> dict:
    """返回 {client: {"last_seen_minutes": float, "sources": [...], "sessions": n}}。"""
    state = workspace / STATE_DIR
    out: dict[str, dict] = {c: {"sources": [], "sessions": 0, "last_seen_minutes": None} for c in CLIENTS}

    gate_log = state / "gate.log"
    if gate_log.is_file():
        for line in gate_log.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]:
            m = GATE_LOG_RE.match(line)
            if not m:
                continue
            stamp, client = m.group(1), m.group(2)
            if client not in out:
                continue
            ts = _parse_stamp(stamp)
            age = None if ts is None else _age_minutes(ts)
            if age is None or age < -FUTURE_SKEW_MIN:
                continue                      # 未来时间戳不算证据（时钟回拨/伪造）
            cur = out[client]["last_seen_minutes"]
            if cur is None or age < cur:
                out[client]["last_seen_minutes"] = age
            if "gate.log" not in out[client]["sources"]:
                out[client]["sources"].append("gate.log")

    # dsh 双证：apply（插件确实被加载进 dsh）+ 账本（hook 确实处理过本工作区的事件）。
    # 只要其一，就是「装了没跑」或「跑过没装」——SOL R1：必须同时成立。
    dsh_apply_age = None
    dsh_log = workspace / ".workspace" / ".dsh-gate.log"
    if dsh_log.is_file():
        newest = None
        for line in dsh_log.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") != "apply":
                continue
            ts = _parse_iso(rec.get("at"))
            if ts is not None and (newest is None or ts > newest):
                newest = ts
        if newest is not None:
            age = _age_minutes(newest)
            if age >= -FUTURE_SKEW_MIN:
                dsh_apply_age = age

    ledgers = sorted(state.glob("dsh-session-*.json"))
    ledger_age = None
    for p in ledgers:
        # 账本里的 events[].ts / anchors.().ts 是 hook 自己写的事件时间——只有它算证据；
        # 文件 mtime 一律不算（touch 一下就把「很久以前」变成「刚刚」）。
        ts = None
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            rec = {}
        for ev in (rec.get("events") or []):
            cand = _parse_stamp(str((ev or {}).get("ts") or ""))
            if cand is not None and (ts is None or cand > ts):
                ts = cand
        for anc in (rec.get("anchors") or {}).values():
            cand = _parse_stamp(str((anc or {}).get("ts") or ""))
            if cand is not None and (ts is None or cand > ts):
                ts = cand
        if ts is None:
            # 账本里没有任何 hook 写的事件/锚时间 → 这份账本不构成证据。
            # 不要回退文件 mtime：`touch` 一下就能把「很久以前」变成「刚刚」（SOL/PRO 一致）。
            continue
        age = _age_minutes(ts)
        if age < -FUTURE_SKEW_MIN:
            continue
        if ledger_age is None or age < ledger_age:
            ledger_age = age
    out["dsh"]["sessions"] = len(ledgers)
    if dsh_apply_age is not None and ledger_age is not None:
        out["dsh"]["last_seen_minutes"] = max(dsh_apply_age, ledger_age)   # 两证都必须新鲜
        out["dsh"]["sources"] = [".dsh-gate.log:apply", "dsh-session"]

    # hermes：项目级 plugin 的在场信号是它自己的日志（不经 hook.py，所以 gate.log 里没有 client=hermes）
    hermes_log = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "logs" / "workspace-gate-plugin.log"
    if hermes_log.is_file():
        try:
            # 「active」只在插件加载时写一次，后面全是逐次记账；所以取"最后一条带时间戳的行"求活跃度，
            # 而不是文件 mtime——mtime 会被后续无关写入（甚至 touch）刷新，那就不叫证据了。
            text = hermes_log.read_text(encoding="utf-8", errors="replace")[-2_000_000:]
        except OSError:
            text = ""
        last_ts = None
        for line in text.splitlines():
            m = _STAMP_RE.match(line)
            if not m:
                continue
            rest = line[m.end():].strip()
            # 只认插件自己会写的事件行——无关日志行（或手写一行）不得把活跃度刷新
            if not _HERMES_EVENT_RE.match(rest):
                continue
            last_ts = _parse_stamp(m.group(1))
        if "workspace-gate active" in text and last_ts is not None:
            age = _age_minutes(last_ts)
            if age >= -FUTURE_SKEW_MIN:
                out["hermes"]["last_seen_minutes"] = age
                out["hermes"]["sources"].append("hermes-plugin.log:active")

    for c in CLIENTS:
        if out[c]["last_seen_minutes"] is None:
            out[c]["last_seen_minutes"] = None
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="gate 接线状态（只读）")
    ap.add_argument("--workspace", default=".", help="工作区根目录")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--require", default="", help="要求已接线的 harness，逗号分隔（cc,codex,hermes,dsh）")
    ap.add_argument("--ttl-minutes", type=float, default=30.0,
                    help="只把最近 N 分钟内出现的证据算作「当前活动」（政策 §15 用 30；必须为正）")
    ap.add_argument("--record", action="store_true",
                    help="把结论写入 .workspace/bootstrap.json 的 gate 节（对应 AGENT_RULES §15）")
    ap.add_argument("--user-decision", default="",
                    help="记录用户决定来源（--record 用）；无证据时必填")
    ap.add_argument("--force", action="store_true",
                    help="允许在没有证据时记录 unconfigured（必须同时给 --user-decision）")
    args = ap.parse_args(argv)

    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        print(f"不是目录：{ws}", file=sys.stderr)
        return 2
    if args.ttl_minutes <= 0:
        # TTL=0 会让「很久以前的证据」永远算活跃（SOL R1-4）；证据必须有时效。
        print("--ttl-minutes 必须为正（0 不表示「不看时间」，表示非法）", file=sys.stderr)
        return 2
    data = collect(ws)

    required = [c.strip() for c in args.require.split(",") if c.strip()]
    bad_req = [c for c in required if c not in CLIENTS]
    if bad_req:
        print(f"未知 harness：{bad_req}（可选 {list(CLIENTS)}）", file=sys.stderr)
        return 2

    def active(client: str) -> bool:
        age = data[client]["last_seen_minutes"]
        if age is None:
            return False
        return args.ttl_minutes <= 0 or age <= args.ttl_minutes

    missing = [c for c in required if not active(c)]
    result = {
        "workspace": str(ws),
        "ttl_minutes": args.ttl_minutes,
        "clients": {c: {**data[c], "active": active(c)} for c in CLIENTS},
        "required": required,
        "missing": missing,
        "ok": not missing,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"工作区：{ws}")
        for c in CLIENTS:
            st = "有证据" if active(c) else ("缺证据" if data[c]["last_seen_minutes"] is None else "过期")
            age = data[c]["last_seen_minutes"]
            age_s = "—" if age is None else f"{age} 分钟前"
            print(f"  {c:<7} {st:<6} {age_s:<12} {','.join(data[c]['sources']) or '—'}")
        if missing:
            print(f"\n未接线：{', '.join(missing)}")
            if "dsh" in missing:
                print("dsh 没有自动发现，必须带 patch 启动："
                      "dsh --profile web --patch .dsh/workspace-gate/cordis.yml")
            if "hermes" in missing:
                print("hermes 需要 HERMES_ENABLE_PROJECT_PLUGINS=true 且 config 里 plugins.enabled 含 workspace-gate")
        else:
            print("\nOK：要求的 harness 都有证据")

    if args.record:
        return _record(ws, data, required, missing, args, active)

    return 0 if not missing else 1


def _record(ws: Path, data: dict, required: list[str], missing: list[str],
            args, active) -> int:
    """把结论写进 bootstrap.gate（AGENT_RULES §15 R116）。

    - 无 `--require` 不记录：不知道在判哪个 harness，写进去就是假事实。
    - 有证据 → `configured`；缺证据 → 拒绝，除非 `--force` **且**给了用户决定来源。
    """
    if not required:
        print("--record 必须同时给 --require <harness>（记录的是「哪一端接线了」）", file=sys.stderr)
        return 2
    boot_path = ws / ".workspace" / "bootstrap.json"
    if not boot_path.is_file():
        print(f"没有 {boot_path}，不记录", file=sys.stderr)
        return 2
    if missing:
        if not args.force:
            print(f"缺证据（{', '.join(missing)}），拒绝记录 configured；"
                  f"若用户明确接受无 gate，用 --force --user-decision '<原话>' 记录 unconfigured",
                  file=sys.stderr)
            return 1
        if not args.user_decision.strip():
            print("--force 记录 unconfigured 必须给 --user-decision（用户决定来源）", file=sys.stderr)
            return 2
    try:
        boot = json.loads(boot_path.read_text(encoding="utf-8-sig"))
    except ValueError:
        print(f"{boot_path} 不是合法 JSON，不记录", file=sys.stderr)
        return 2
    status = "unconfigured" if missing else "configured"
    old = boot.get("gate") or {}
    old_h = set(old.get("harness") or [])
    changed = bool(old.get("status")) and (status != old.get("status") or old_h != set(required))
    if changed and not args.user_decision.strip():
        # R117：状态或 harness 集合的任何变化都属翻转，必须有人类决定（含 unconfigured → configured）
        print("该记录会翻转 gate 结论（status 或 harness 集合变化），必须给 --user-decision（用户原话）",
              file=sys.stderr)
        return 2
    boot["gate"] = {
        "status": status,
        "harness": required,
        "evidence": {c: {"active": active(c),
                         "last_seen_minutes": data[c]["last_seen_minutes"],
                         "sources": data[c]["sources"]} for c in required},
        "ttl_minutes": args.ttl_minutes,
        "decision_source": args.user_decision.strip() or "runtime-evidence",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    tmp = boot_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, boot_path)      # 原子替换：避免写一半的 bootstrap 被别的工具读到
    print(f"已记录 gate.status={status}（harness={','.join(required)}）→ {boot_path}")
    return 0 if status == "configured" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
