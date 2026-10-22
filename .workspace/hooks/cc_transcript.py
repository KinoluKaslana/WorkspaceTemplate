#!/usr/bin/env python3
"""CC 转录回放归因（非订阅模式）。

Claude Code 不订阅 Bash hook，但每个 hook payload 都带 `transcript_path`，
且转录 JSONL 完整记录 `Read` / `Bash` 的 `tool_use`（含 command）、
`tool_result`（含正文/成败）与时间戳。本模块只做**事后复算**：

    转录 → 命令分类（wtarget）→ 读证据重建 → would_deny 复算 → 与 git 差集交叉

语义边界（必须与能力一起声明）：
- 事后审计，不是拦截；`would_deny=true` 表示「按当时证据本该 deny」，不代表写入被阻止。
- 只读 payload 精确给出的 `transcript_path`：realpath 必须落在 projects 根内，
  目录 slug 与文件名 `session_id` 匹配，且转录内 `cwd`（若有）与会话 cwd 一致；
  不遍历 `~/.claude/projects/**`。
- 默认只落命令 `digest`；原文留存需 `.workspace/gate.custom.json` 的
  `audit_command_text=true`。
- `unresolved` 形态（如 `git apply`）不判 would_deny，记 `cc-bash-gap`。
- 读证据只认**内容逐行可校验**的 Read：剥离行号与 `<system-reminder>` 尾注后必须与
  参考修订逐行相等；`is_error` 的 Read、末尾 `... [truncated]`、短读未到 EOF、
  内容不符一律不授证。
- 参考修订优先取「第一次写该 gate 之前」的整文件读（CC 没有写前拦截，审计时文件
  已被本次会话改写）；取不到才回落到磁盘现状。
- `tool_use` 与 `tool_result` 可能跨 Stop 分批落盘：未拿到 result 的命令进 pending，
  下一轮补齐后再算，不落 `result=ok`。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile

import wtarget

# 测试可用 CC_TRANSCRIPT_ROOT 指向临时目录；生产默认 CC 的 projects 根。
PROJECTS_ROOT = os.path.expanduser(os.environ.get("CC_TRANSCRIPT_ROOT") or "~/.claude/projects")
MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 20000
DEFAULT_GATE = "AGENT_RULES.md"
_LINENO = re.compile(r"^\s*\d+\t")
_TRUNCATED = re.compile(r"\.\.\. \[truncated\]\s*$")
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*$", re.S)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_command(command: str) -> str:
    return _sha(command)[:16]


def slug_for_cwd(cwd: str) -> str:
    """CC 的 project slug：绝对路径把 '/' 换成 '-'，并前置 '-'。"""
    return "-" + os.path.abspath(cwd).lstrip("/").replace("/", "-")


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def validate_path(transcript_path: object, session_id: str, cwd: str) -> tuple[bool, str]:
    """只接受「本会话、本工作区」的转录：形状 / realpath / slug / 文件名 四重校验。

    realpath 校验用于挡住「projects 内放 symlink 指向根外」的读取逃逸。
    """
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return False, "no-transcript-path"
    root = _real(PROJECTS_ROOT)
    path = _real(transcript_path.strip())
    if not path.startswith(root + os.sep):
        return False, "transcript-outside-projects"
    rel = os.path.relpath(path, root).split(os.sep)
    if len(rel) != 2:
        return False, "transcript-shape"
    slug, name = rel
    if slug != slug_for_cwd(cwd):
        return False, "transcript-slug-mismatch"
    if not name.endswith(".jsonl"):
        return False, "transcript-shape"
    if os.path.splitext(name)[0] != session_id:
        return False, "transcript-session-mismatch"
    if not os.path.isfile(path):
        return False, "transcript-missing"
    return True, "ok"


def read_records(path: str, offset: int = 0) -> dict:
    """从 offset 起读增量行；容忍正在写的半行（保留 offset 不前进）。

    超过字节/条数上限时**不丢已有记录**：截断到上限并标 `incomplete`，
    由调用方记 `cc-bash-gap(transcript-truncated)`。
    """
    size = os.path.getsize(path)
    records, truncated, consumed, incomplete = [], False, offset, False
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                truncated = True  # 半行：不消费
                break
            if consumed - offset + len(raw) > MAX_BYTES or len(records) >= MAX_RECORDS:
                incomplete = True
                break
            consumed += len(raw)
            try:
                records.append(json.loads(raw.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
    return {"records": records, "offset": consumed, "truncated": truncated,
            "incomplete": incomplete, "size": size}


def _blocks(record: dict):
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


def index_records(records: list[dict]) -> tuple[list[dict], dict]:
    """返回（tool_use 列表, tool_use_id → tool_result 块）。"""
    uses, results = [], {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for block in _blocks(rec):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block
            elif block.get("type") == "tool_use":
                uses.append({"kind": block.get("name"), "id": block.get("id"),
                             "input": block.get("input") or {},
                             "ts": rec.get("timestamp"),
                             "sidechain": bool(rec.get("isSidechain"))})
    return uses, results


def _result_of(block: dict | None) -> dict:
    if not isinstance(block, dict):
        return {"text": "", "is_error": None}
    text = block.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    return {"text": text if isinstance(text, str) else "", "is_error": block.get("is_error")}


def steps_from(records: list[dict]) -> list[dict]:
    """兼容入口：返回带 result 的 tool_use 步骤（无 result 的不返回）。"""
    uses, results = index_records(records)
    steps = []
    for use in uses:
        block = results.get(use["id"])
        if block is None:
            continue
        steps.append({**use, "result": _result_of(block)})
    return steps


def total_lines(raw: bytes) -> int:
    """与 gate_core 同口径：split(b"\\n") 计数（含文末空行）。"""
    if not raw:
        return 0
    return len(raw.split(b"\n"))


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    ordered = sorted(([int(a), int(b)] for a, b in intervals if int(b) >= int(a)))
    out: list[list[int]] = []
    for a, b in ordered:
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def missing_intervals(intervals: list[list[int]], total: int) -> list[list[int]]:
    out, cursor = [], 1
    for a, b in merge_intervals(intervals):
        if a > cursor:
            out.append([cursor, min(a - 1, total)])
        cursor = max(cursor, b + 1)
        if cursor > total:
            break
    if cursor <= total:
        out.append([cursor, total])
    return out


def _fmt(intervals: list[list[int]]) -> str:
    return "、".join(str(a) if a == b else f"{a}..{b}" for a, b in intervals) or "无"


def _strip_lineno(text: str) -> str:
    return "\n".join(_LINENO.sub("", line) for line in text.splitlines())


def _read_lines(step: dict) -> list[str] | None:
    """把一次 Read 结果折算成「行列表」（剥行号、剥尾注、去尾部换行）；不可用返回 None。"""
    result = step.get("result") or {}
    if result.get("is_error"):
        return None  # 失败的 Read 不授证
    text = result.get("text") or ""
    if not text:
        return None
    text = _REMINDER.sub("", text)
    if _TRUNCATED.search(text):
        return None
    body = text.rstrip("\n")
    if not body:
        return None
    return _strip_lineno(body).split("\n")


def _verified_interval(step: dict, gate_raw: bytes, total: int) -> list[int] | None:
    """把一次 Read 折算成**内容可校验**的覆盖区间；不可校验一律返回 None。"""
    inp = step.get("input") or {}
    stripped_lines = _read_lines(step)
    if not stripped_lines:
        return None
    try:
        offset = max(1, int(inp.get("offset") or 1))
        limit = int(inp["limit"]) if inp.get("limit") is not None else None
    except (TypeError, ValueError):
        return None
    physical = total - (1 if gate_raw.endswith(b"\n") else 0)
    # 末尾空元素是文件的 EOF 哨兵行（以 LF 结尾时 split 会多一个 ""），不计入读到的行。
    while gate_raw.endswith(b"\n") and stripped_lines and stripped_lines[-1] == "" \
            and offset + len(stripped_lines) - 1 > physical:
        stripped_lines.pop()
    got = len(stripped_lines)
    if got <= 0 or (limit is not None and got > limit):
        return None
    end = offset + got - 1
    if offset > physical or end > physical:
        return None
    if limit is not None and got < limit and end < physical:
        return None  # 短读且未到 EOF
    lines = gate_raw.decode("utf-8", "replace").split("\n")
    if stripped_lines != lines[offset - 1:end]:
        return None
    if gate_raw.endswith(b"\n") and end >= physical:
        end = total  # EOF 哨兵
    return [offset, end]


def _state_rank(name: str) -> int:
    """read_state 的「更弱」排序：missing < partial < complete。"""
    return {"missing": 0, "partial": 1, "complete": 2}.get(name, 0)


def _read_state(coverage: list[list[int]], total: int) -> str:
    if not coverage:
        return "missing"
    merged = merge_intervals(coverage)
    if merged[0][0] > 1 or merged[-1][1] < total or missing_intervals(merged, total):
        return "partial"
    return "complete"


def git_changed_files(ws: str) -> list[str] | None:
    """工作区未提交变更（含未跟踪）的绝对路径；非 git 仓返回 None。"""
    try:
        out = subprocess.run(["git", "-C", ws, "status", "--porcelain", "-z"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    items, parts = [], out.stdout.split("\0")
    idx = 0
    while idx < len(parts):
        entry = parts[idx]
        idx += 1
        if not entry or len(entry) < 4:
            continue
        path = entry[3:]
        if entry[:2].strip() in ("R", "C") and idx < len(parts):
            idx += 1
        items.append(os.path.normpath(os.path.join(ws, path)))
    return items


def _custom_conf(ws: str) -> dict:
    try:
        with open(os.path.join(ws, ".workspace", "gate.custom.json"), encoding="utf-8") as fh:
            conf = json.load(fh)
        return conf if isinstance(conf, dict) else {}
    except Exception:
        return {}


def command_text_enabled(ws: str) -> bool:
    return bool(_custom_conf(ws).get("audit_command_text"))


def resolve_gate_files(ws: str, gate_rel: str | None = None) -> list[str]:
    """gate 文件列表：显式参数 > `gate.custom.json.gate_files` > AGENT_RULES.md。"""
    if gate_rel:
        return [os.path.normpath(os.path.join(ws, gate_rel))]
    conf = _custom_conf(ws)
    names = conf.get("gate_files")
    if not isinstance(names, list) or not names:
        names = [DEFAULT_GATE]
    return [os.path.normpath(os.path.join(ws, str(n))) for n in names]


def _reference_revision(steps: list[dict], gate: str, cwd: str) -> list[str] | None:
    """在「第一次写该 gate 之前」找整文件读，作为该会话看到的参考修订。

    CC 没有写前拦截：审计时文件已被本次会话改写，拿磁盘现状对照会把「先读全再写」
    误判成「没读」。只接受 offset==1 且（无 limit 或行数≥磁盘现有行）的读。
    """
    for step in steps:
        if step["kind"] == "Read":
            target = os.path.normpath(os.path.join(cwd, str(step["input"].get("file_path") or "")))
            if target != gate or int(step["input"].get("offset") or 1) != 1:
                continue
            lines = _read_lines(step)
            if not lines:
                continue
            try:
                disk = total_lines(open(gate, "rb").read())
            except OSError:
                disk = 0
            if step["input"].get("limit") is None or len(lines) >= disk - 1:
                return lines
        elif step["kind"] == "Bash":
            res = wtarget.classify(str(step["input"].get("command") or ""), cwd, [gate])
            if gate in [os.path.normpath(t) for t in res.get("targets", [])]:
                return None  # 已出现 gate 写入，后面的读不算「写前参考」
    return None


def analyze(ws: str, session_id: str, transcript_path: object, cwd: str,
            gate_rel: str | None = None, start_offset: int = 0,
            pending: dict | None = None) -> dict:
    """转录 → 事件列表。纯读取，不写状态（调用方负责持久化 offset/pending）。"""
    ok, why = validate_path(transcript_path, session_id, cwd)
    if not ok:
        return {"events": [{"type": "cc-bash-gap", "reason": why, "source": "transcript"}],
                "offset": start_offset, "pending": pending or {}, "stats": {"rejected": why}}
    path = _real(str(transcript_path))
    read = read_records(path, start_offset)
    events: list[dict] = []
    if read["incomplete"]:
        events.append({"type": "cc-bash-gap", "reason": "transcript-truncated",
                       "source": "transcript", "records": len(read["records"]),
                       "bytes": read["size"]})

    # cwd 绑定：转录自带 cwd 时必须与会话 cwd 一致（防 slug 碰撞跨项目误读）。
    seen_cwd = {os.path.realpath(str(rec.get("cwd"))) for rec in read["records"]
                if isinstance(rec, dict) and rec.get("cwd")}
    if seen_cwd and _real(cwd) not in seen_cwd:
        return {"events": events + [{"type": "cc-bash-gap", "reason": "transcript-cwd-mismatch",
                                     "source": "transcript"}],
                "offset": read["offset"], "pending": {}, "stats": {"rejected": "cwd-mismatch"}}

    uses, results = index_records(read["records"])
    steps: list[dict] = []
    new_pending: dict[str, dict] = {}
    for use in uses:
        block = results.get(use["id"])
        if block is None:
            new_pending[use["id"]] = {"kind": use["kind"], "input": use["input"],
                                      "ts": use["ts"], "sidechain": use["sidechain"]}
        else:
            steps.append({**use, "result": _result_of(block)})
    for pid, info in (pending or {}).items():
        block = results.get(pid)
        if block is None:
            new_pending.setdefault(pid, info)  # 仍未完成，继续挂着
            continue
        steps.append({**info, "id": pid, "result": _result_of(block)})
    steps.sort(key=lambda s: str(s.get("ts") or ""))

    if not steps:
        if not events and start_offset == 0:
            events.append({"type": "cc-bash-gap", "reason": "no-tool-use", "source": "transcript"})
        return {"events": events, "offset": read["offset"], "pending": new_pending,
                "stats": {"steps": 0, "incomplete": bool(read["incomplete"])}}

    gates = resolve_gate_files(ws, gate_rel)
    books: dict[str, dict] = {}
    for gate in gates:
        ref_lines = _reference_revision(steps, gate, cwd)
        if ref_lines:
            raw, source = ("\n".join(ref_lines)).encode("utf-8"), "transcript"
        else:
            try:
                raw = open(gate, "rb").read()
            except OSError:
                raw = b""
            source = "disk"
        books[gate] = {"raw": raw, "total": total_lines(raw),
                       "coverage": [], "revision": source}

    writes: list[dict] = []
    stats = {"steps": len(steps), "reads": 0, "bash": 0, "would_deny": 0,
             "sidechain": 0, "incomplete": bool(read["incomplete"])}

    for step in steps:
        if step.get("sidechain"):
            stats["sidechain"] += 1
        if step["kind"] == "Read":
            target = os.path.normpath(os.path.join(cwd, str(step["input"].get("file_path") or "")))
            book = books.get(target)
            if not book:
                continue
            iv = _verified_interval(step, book["raw"], book["total"])
            if not iv:
                continue
            stats["reads"] += 1
            book["coverage"].append(iv)
            continue
        if step["kind"] != "Bash":
            continue
        command = str(step["input"].get("command") or "")
        if not command:
            continue
        stats["bash"] += 1
        res = wtarget.classify(command, cwd, gates)
        states = {gate: _read_state(book["coverage"], book["total"]) for gate, book in books.items()}
        targets = [os.path.normpath(t) for t in res.get("targets", [])]
        gate_targets = [g for g in gates if g in targets]
        if gate_targets:
            would = any(states[g] != "complete" for g in gate_targets)
            state = min((states[g] for g in gate_targets), key=_state_rank)
            revision = books[gate_targets[0]]["revision"]
        elif res.get("class") == "interpreter_wrapper":
            would = any(states[g] != "complete" for g in gates)
            state = min(states.values(), key=_state_rank) if states else "missing"
            revision = "disk"
        else:
            would = False
            state = min(states.values(), key=_state_rank) if states else "missing"
            revision = "disk"
        if would:
            stats["would_deny"] += 1
        is_error = step["result"].get("is_error")
        event = {
            "type": "cc-bash-write",
            "source": "transcript",
            "tool_use_id": step["id"],
            "digest": digest_command(command),
            "kind": res["kind"],
            "class": res.get("class"),
            "targets": res.get("targets", []),
            "would_deny": would,
            "read_state": state,
            "revision": revision,
            "result": "unknown" if is_error is None else ("error" if is_error else "ok"),
            "ts": step.get("ts"),
        }
        if step.get("sidechain"):
            event["sidechain"] = True
        if command_text_enabled(ws):
            event["command"] = command
        events.append(event)
        writes.append(event)
        if res["kind"] == "unresolved" and res.get("class") != "interpreter_wrapper":
            events.append({"type": "cc-bash-gap", "reason": "unresolved-command",
                           "source": "transcript", "digest": event["digest"],
                           "class": res.get("class"), "tool_use_id": step["id"]})

    changed = git_changed_files(ws)
    if changed is not None:
        covered = {os.path.normpath(t) for ev in writes for t in ev.get("targets", [])}
        uncovered = [p for p in changed if os.path.normpath(p) not in covered]
        if uncovered:
            events.append({"type": "cc-bash-gap", "reason": "changed-but-untargeted",
                           "source": "transcript", "files": sorted(uncovered)[:20]})
    return {"events": events, "offset": read["offset"], "pending": new_pending, "stats": stats}


# ---- 增量偏移与 pending（按会话 sidecar，原子写）----

def offset_path(ws: str, session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:64]
    return os.path.join(ws, ".workspace", ".hook-state", f"cc-transcript-{safe}.json")


def load_state(ws: str, session_id: str) -> dict:
    try:
        with open(offset_path(ws, session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {"offset": int(data.get("offset") or 0),
                    "pending": data.get("pending") if isinstance(data.get("pending"), dict) else {},
                    "stats": data.get("stats") if isinstance(data.get("stats"), dict) else {}}
    except Exception:
        pass
    return {"offset": 0, "pending": {}, "stats": {}}


def load_offset(ws: str, session_id: str) -> int:
    return load_state(ws, session_id)["offset"]


def save_state(ws: str, session_id: str, offset: int, stats: dict, pending: dict) -> None:
    path = offset_path(ws, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-cc-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"offset": int(offset), "stats": stats, "pending": pending},
                      fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def save_offset(ws: str, session_id: str, offset: int, stats: dict) -> None:
    """兼容入口：保留旧 pending。"""
    keep = load_state(ws, session_id).get("pending") or {}
    save_state(ws, session_id, offset, stats, keep)
