#!/usr/bin/env python3
"""CC 转录回放归因（非订阅模式）。

Claude Code 不订阅 Bash hook，但每个 hook payload 都带 `transcript_path`，
且转录 JSONL 完整记录 `Read` / `Bash` 的 `tool_use`（含 command）、
`tool_result`（含正文/成败）与时间戳。本模块只做**事后复算**：

    转录 → 命令分类（wtarget）→ 读证据重建 → would_deny 复算 → 与 git 差集交叉

语义边界（必须与能力一起声明）：
- 事后审计，不是拦截；`would_deny=true` 表示「按当时证据本该 deny」，不代表写入被阻止。
- 只读 payload 精确给出的 `transcript_path`，且校验 slug/session_id 与当前会话一致；
  不遍历 `~/.claude/projects/**`（避免读入其他项目与其他会话）。
- 默认只落命令 `digest`；原文留存需 `.workspace/gate.custom.json` 的
  `audit_command_text=true` 显式开启。
- `unresolved` 形态（如 `git apply`）不判 would_deny，记 `cc-bash-gap`。
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


def validate_path(transcript_path: object, session_id: str, cwd: str) -> tuple[bool, str]:
    """只接受「本会话、本工作区」的转录：形状/目录/文件名三重校验。"""
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return False, "no-transcript-path"
    path = os.path.abspath(os.path.expanduser(transcript_path.strip()))
    root = os.path.abspath(PROJECTS_ROOT)
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
    """从 offset 起读增量行；容忍正在写的半行（保留 offset 不前进）。"""
    size = os.path.getsize(path)
    if size > MAX_BYTES and offset == 0:
        return {"records": [], "offset": 0, "too_large": True, "truncated": False}
    records, truncated, consumed = [], False, offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                truncated = True  # 半行：不消费
                break
            consumed += len(raw)
            try:
                records.append(json.loads(raw.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
            if len(records) >= MAX_RECORDS:
                return {"records": records, "offset": consumed, "too_large": True, "truncated": False}
    return {"records": records, "offset": consumed, "too_large": False, "truncated": truncated}


def _blocks(record: dict):
    msg = record.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


def steps_from(records: list[dict]) -> list[dict]:
    """按转录顺序还原 tool_use / tool_result；只保留 Read 与 Bash。"""
    steps, results = [], {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for block in _blocks(rec):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block
            elif block.get("type") == "tool_use":
                steps.append({"kind": block.get("name"), "id": block.get("id"),
                              "input": block.get("input") or {}, "ts": rec.get("timestamp")})
    for step in steps:
        res = results.get(step["id"]) or {}
        text = res.get("content")
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        step["result"] = {"text": text if isinstance(text, str) else "",
                          "is_error": res.get("is_error")}
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


def _verified_interval(step: dict, gate_raw: bytes, total: int) -> list[int] | None:
    """把一次 Read 折算成**内容可校验**的覆盖区间；不可校验一律返回 None。

    与 gate_core 同口径：剥掉行号前缀后必须与治理文件对应行**逐行相等**，截断标记
    出现在末尾一律不授证；CC 注入的 `<system-reminder>` 尾注先剥离。短读（既非请求
    上限也未到 EOF）同样不授证——否则「读了一截」会被当成「读了」。
    """
    inp = step.get("input") or {}
    text = (step.get("result") or {}).get("text") or ""
    if not text:
        return None
    text = _REMINDER.sub("", text)
    if _TRUNCATED.search(text):
        return None
    body = text.rstrip("\n")
    if not body:
        return None
    stripped_lines = _strip_lineno(body).split("\n")
    got = len(stripped_lines)
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
    if limit is not None and got > limit:
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


def _read_state(coverage: list[list[int]], total: int) -> str:
    """missing：没有任何可用读证据；partial：有可校验区间但有洞；complete：1..total 全覆盖。"""
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
            idx += 1  # 重命名条目后面跟着原路径
        items.append(os.path.normpath(os.path.join(ws, path)))
    return items


def command_text_enabled(ws: str) -> bool:
    try:
        with open(os.path.join(ws, ".workspace", "gate.custom.json"), encoding="utf-8") as fh:
            return bool(json.load(fh).get("audit_command_text"))
    except Exception:
        return False


def analyze(ws: str, session_id: str, transcript_path: object, cwd: str,
            gate_rel: str = "AGENT_RULES.md", start_offset: int = 0) -> dict:
    """转录 → 事件列表。纯读取，不写状态（调用方负责持久化 offset）。"""
    ok, why = validate_path(transcript_path, session_id, cwd)
    if not ok:
        return {"events": [{"type": "cc-bash-gap", "reason": why, "source": "transcript"}],
                "offset": start_offset, "stats": {"rejected": why}}
    path = os.path.abspath(os.path.expanduser(str(transcript_path)))
    read = read_records(path, start_offset)
    events: list[dict] = []
    if read["too_large"]:
        events.append({"type": "cc-bash-gap", "reason": "transcript-too-large",
                       "source": "transcript", "records": len(read["records"])})
    steps = steps_from(read["records"])
    if not steps:
        # 增量没有新工具调用是常态（Stop 频繁触发），不当成缺口；
        # 只有「首轮就读不到任何 tool_use」才记为可诊断的 gap。
        if not events and start_offset == 0:
            events.append({"type": "cc-bash-gap", "reason": "no-tool-use",
                           "source": "transcript"})
        return {"events": events, "offset": read["offset"], "stats": {"steps": 0}}

    gate_abs = os.path.normpath(os.path.join(ws, gate_rel))
    try:
        gate_raw = open(gate_abs, "rb").read()
    except OSError:
        gate_raw = b""
    total = total_lines(gate_raw)
    gate_sha = hashlib.sha256(gate_raw).hexdigest()

    coverage: list[list[int]] = []
    read_attempts = 0
    writes: list[dict] = []
    stats = {"steps": len(steps), "reads": 0, "bash": 0, "would_deny": 0}

    for step in steps:
        if step["kind"] == "Read":
            target = os.path.normpath(os.path.join(cwd, str(step["input"].get("file_path") or "")))
            if target != gate_abs:
                continue
            read_attempts += 1
            iv = _verified_interval(step, gate_raw, total)
            if not iv:
                continue  # 截断/注入/内容不符：不授证
            stats["reads"] += 1
            coverage.append(iv)
            continue
        if step["kind"] != "Bash":
            continue
        command = str(step["input"].get("command") or "")
        if not command:
            continue
        stats["bash"] += 1
        res = wtarget.classify(command, cwd, [gate_abs])
        state = _read_state(coverage, total)
        would = False
        if state in ("missing", "partial"):
            if res["kind"] == "write" and gate_abs in res.get("targets", []):
                would = True
            elif res.get("class") == "interpreter_wrapper":
                would = True
        if would:
            stats["would_deny"] += 1
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
            "result": "error" if step["result"].get("is_error") else "ok",
            "ts": step.get("ts"),
        }
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
    return {"events": events, "offset": read["offset"], "stats": stats}


# ---- 增量偏移（按会话 sidecar，原子写）----

def offset_path(ws: str, session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:64]
    return os.path.join(ws, ".workspace", ".hook-state", f"cc-transcript-{safe}.json")


def load_offset(ws: str, session_id: str) -> int:
    try:
        with open(offset_path(ws, session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        return int(data.get("offset") or 0)
    except Exception:
        return 0


def save_offset(ws: str, session_id: str, offset: int, stats: dict) -> None:
    path = offset_path(ws, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-cc-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"offset": int(offset), "stats": stats}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
