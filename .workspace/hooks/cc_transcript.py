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
- 参考修订三选一（事件里的 `revision` 如实输出）：
  ① 本轮有该 gate 的**整文件写前读** → `transcript`（读到的内容，优先于任意写）；
  ② 本轮**被写且无读** → `persisted`（持久化账本里的 sha/total/coverage，**不重新哈希
  磁盘**：写操作本身就改了磁盘，按写后内容取修订会与旧读账指纹不符，把「轮 1 读全 →
  轮 2 写同一 gate」复算成 `missing` —— 真机假阳性 F3）；③ 本轮既未读也未写 → `disk`
  （外部改动检测）。账本里没有该 gate（首轮就写）时回落 `disk`（coverage 空 ⇒ deny）。
- **写即消费（F3b + F4）**：gate 被写的那一轮，写事件本身仍按**写前**账本判定（F3 的修复），
  但**每处理完一条命中 gate 的写步骤就立即**把该 gate 的读账消费掉（`coverage = []`、
  `revision = post-write`、`consumed = True`）——否则同一轮里「读一次 → 连写两次」的第二条
  仍看到读时的完整 coverage，被误放行（R98：第一条写就消费许可）。轮末再把该 gate 的账本
  收成**写后修订**（`sha` = 当前磁盘内容指纹、`total` = 当前行数、`coverage = []`）供跨
  `Stop` 使用，只有**最后一次写之后**的整文件读能重建 `coverage`。顺序敏感：写中间的读属于
  已不存在的中间修订，后续再有写则前面的读同样作废。账本 `revision` 因此出现第四值
  `post-write`。解释器包装/不可判形态的写目标未知，不消费账本（已知边界，见 `analyze()`）。
- `persisted` 分支的能力边界：会话写与外部改动落在**同一轮**时，转录里没有可区分二者的
  信号，一律按账本放行；gate 文件的外部改动由 `Stop`/`SessionEnd` 的
  `unsanctioned-gatefile-change`（文件级指纹锚）兜底，不靠本复算。
- `tool_use` 与 `tool_result` 可能跨 Stop 分批落盘：未拿到 result 的命令进 pending，
  下一轮补齐后再算，不落 `result=ok`。
- 读证据**跨 Stop 持久**：每轮把每个 gate 的 coverage/revision/total/内容指纹并入 sidecar
  的 `books`，下一轮以它为基线合并；修订（内容指纹）变化即撤销旧读账。CC 的 `Stop`
  每轮触发，不持久会把「轮 1 读全 → 轮 2 写」复算成 `missing`（假阳性）。
- 转录里一条 `cwd` 都没有时 fail-closed：无法排除 slug 碰撞（`/a-b/c` vs `/a/b-c`），
  记 `cc-bash-gap(transcript-cwd-missing)` 并跳过本轮记录（offset 仍前进，不重放）。
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
    """CC 的 project slug：绝对路径把分隔符换成 '-'，并前置 '-'。

    Windows 的盘符冒号不能进目录名（`C:/Users` → `C-Users`）；分隔符统一先转 '/' 再替换，
    否则 Windows 上会得到 `-C:/Users/...` 这种非法名字（hooks-tests 在 Windows CI 上 39 个 error 的根因）。
    这里只用于「定位/隔离 projects 目录」，不参与任何安全判定。
    """
    p = os.path.abspath(cwd).replace("\\", "/").replace(os.sep, "/").lstrip("/")
    return "-" + p.replace(":", "-").replace("/", "-")


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
    base_sid = session_id.split(".")[0]
    if len(rel) == 2:
        # 主会话转录：<slug>/<session_id>.jsonl
        slug, name = rel
        if slug != slug_for_cwd(cwd):
            return False, "transcript-slug-mismatch"
        if not name.endswith(".jsonl"):
            return False, "transcript-shape"
        if os.path.splitext(name)[0] != session_id:
            return False, "transcript-session-mismatch"
    elif len(rel) == 4 and rel[2] == "subagents":
        # 子 agent 转录（v2.8.4）：<slug>/<parent_session_id>/subagents/agent-<agent_id>.jsonl
        slug, parent, _sub, name = rel
        if slug != slug_for_cwd(cwd):
            return False, "transcript-slug-mismatch"
        if parent != base_sid:
            return False, "transcript-session-mismatch"
        if not (name.startswith("agent-") and name.endswith(".jsonl")):
            return False, "transcript-shape"
    else:
        return False, "transcript-shape"
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


def _content_fingerprint(raw: bytes) -> str:
    """修订指纹：只剥掉文末单个 LF（EOF 哨兵）。

    transcript 回放拼出的参考修订不带文末 LF，磁盘修订带；不归一化会让同一内容
    在两种来源下指纹不同，从而把「上一轮读全」误撤销。
    """
    body = raw[:-1] if raw.endswith(b"\n") else raw
    return hashlib.sha256(body).hexdigest()


def gate_key(ws: str, gate: str) -> str:
    """持久化账本的键：gate 相对工作区的路径（sidecar 不存绝对路径）。"""
    try:
        return os.path.relpath(os.path.normpath(gate), os.path.normpath(ws)).replace(os.sep, "/")
    except ValueError:
        return os.path.normpath(gate).replace(os.sep, "/")


def _book_entry(ws: str, books: object, gate: str) -> dict | None:
    """从持久化账本取某 gate 的旧读账（键为相对路径，容错绝对路径）。"""
    if not isinstance(books, dict):
        return None
    for key in (gate_key(ws, gate), gate):
        entry = books.get(key)
        if isinstance(entry, dict):
            return entry
    return None


def _carry_coverage(prev: object, prev_total: int, total: int) -> list[list[int]]:
    """把上一轮（同内容）的覆盖区间搬到本轮的行号空间。

    上一轮读到 EOF（`b >= 旧 total`）等价于本轮读到 EOF：磁盘修订比 transcript
    参考多一个 EOF 哨兵行，故末端要重锚到本轮 total，否则会误判 partial。
    """
    if not isinstance(prev, list):
        return []
    out: list[list[int]] = []
    for item in prev:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            a, b = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        if prev_total > 0 and b >= prev_total:
            b = total  # 上一轮读到 EOF == 本轮读到 EOF
        out.append([max(1, a), min(b, total)])
    return merge_intervals(out)


def _ledger_coverage(entry: dict, total: int) -> list[list[int]]:
    """原样取账本里的覆盖区间（**不做 EOF 重锚**：覆盖区间与 `total` 同源，不再搬运）。

    与 `_carry_coverage` 的区别：后者要在「账本 total ≠ 本轮 total」时把 EOF 锚点搬到
    新行号空间；本函数用于「本轮被写且无读」的 gate，参考修订直接取账本，两个 total
    是同一个值，搬运只会制造偏移。
    """
    out: list[list[int]] = []
    for item in (entry.get("coverage") or []):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            a, b = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        a = max(1, a)
        b = min(b, total) if total > 0 else b
        if b >= a:
            out.append([a, b])
    return merge_intervals(out)


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


def _consume_book(book: dict) -> None:
    """**轮内写即消费**：把某 gate 的读账就地置为「写后修订、证据已消费」。

    调用点在「命中 gate 的写步骤判定并落事件之后」，因此该写本身仍按**写前**证据
    判定；之后的写（同一轮内后续步骤）看到 `coverage=[]` ⇒ `read_state=missing`。
    写后内容在本轮转录里尚不可知（磁盘现在存的是本轮**最后一次**写的结果，不能拿来
    当中间修订），故只清证据并标记 `consumed`，由之后的全文件读重建参考修订
    （见 `_adopt_post_write_revision`）。
    """
    book["raw"] = b""
    book["total"] = 0
    book["coverage"] = []
    book["sha"] = _content_fingerprint(b"")
    book["revision"] = "post-write"
    book["consumed"] = True


def _adopt_post_write_revision(step: dict, book: dict) -> bool:
    """被消费的账本遇到「写之后的全文件读」：用该读内容当新的参考修订。

    只有 `offset==1` 且未给 `limit` 的整文件读才算（拿局部读当整篇修订会错位行号）。
    中间修订（写后、下一次写前）没有任何权威副本可对照——磁盘已被后来的写改过，
    写前转录修订已失效——故这里以该读自身为修订基线；这是同轮「写 → 读」重建
    coverage 的唯一可用证据，已在模块 docstring 记为已知边界。
    """
    inp = step.get("input") or {}
    try:
        if int(inp.get("offset") or 1) != 1 or inp.get("limit") is not None:
            return False
    except (TypeError, ValueError):
        return False
    lines = _read_lines(step)
    if not lines:
        return False
    raw = "\n".join(lines).encode("utf-8")
    book.update({"raw": raw, "total": total_lines(raw), "coverage": [],
                 "sha": _content_fingerprint(raw), "revision": "post-write",
                 "consumed": False})
    return True


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
                with open(gate, "rb") as fh:
                    disk = total_lines(fh.read())
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
            pending: dict | None = None, books: dict | None = None) -> dict:
    """转录 → 事件列表。纯读取，不写状态（调用方负责持久化 offset/pending/books）。

    `books` 是上一轮持久化的读账（键为 gate 相对路径），本轮以它为基线：
    内容指纹相同才继承覆盖区间，指纹变化即撤销（旧修订的读账不得贴到新修订上）。

    参考修订三选一，事件里的 `revision` 如实输出：
    - `transcript`：本轮有该 gate 的整文件写前读；
    - `persisted`：本轮**被写且无读** ⇒ 取账本里的写前修订（sha/total/coverage），
      **不重新哈希磁盘**（写操作本身就改了磁盘）；
    - `disk`：本轮既未读也未写（外部改动检测），或账本里没有该 gate（首轮就写）。

    写即消费：**每处理完一条命中 gate 的写步骤就立即**消费该 gate 的读账（`coverage=[]`、
    `revision=post-write`、`consumed=True`，该写自身仍按写前证据判定），使**同一轮内之后的
    写**看到 `read_state=missing`；该轮结束时再把被写 gate 的账本收成**写后修订**
    （`sha`/`total` = 当前磁盘内容与行数、`coverage = []`、`revision = post-write`），
    只有**最后一次写之后**的整文件读能把 coverage 重建回来（顺序敏感）。因此「读完连写两次」
    的第二条（不分轮内外）会 `read_state=missing` / `would_deny=true`，重读后恢复 allow。

    已知边界：`interpreter_wrapper` 与 `unresolved` 形态的写目标不可静态确定，不消费
    任何 gate 的账本（否则一次无关的 `python3 script.py` 也会消耗全部 gate 读许可）。
    同轮「写 → 读」重建 coverage 时，中间修订没有权威副本可对照（磁盘已被后来的写改过，
    写前转录修订已失效），只能以该次整文件读自身为修订基线（局部读不采用 ⇒ deny）。

    返回值里的 `books` 供调用方写回 sidecar。
    """
    baseline = books if isinstance(books, dict) else {}
    ok, why = validate_path(transcript_path, session_id, cwd)
    if not ok:
        return {"events": [{"type": "cc-bash-gap", "reason": why, "source": "transcript"}],
                "offset": start_offset, "pending": pending or {}, "books": dict(baseline),
                "stats": {"rejected": why}}
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
                "offset": read["offset"], "pending": {}, "books": dict(baseline),
                "stats": {"rejected": "cwd-mismatch"}}
    if read["records"] and not seen_cwd:
        # 一条 cwd 都没有 ⇒ 无法排除 slug 碰撞（/a-b/c 与 /a/b-c 同 slug）：fail-closed，
        # 不分析这批记录；offset 仍前进，避免同一批记录每轮重放成事件洪水。
        return {"events": events + [{"type": "cc-bash-gap", "reason": "transcript-cwd-missing",
                                     "source": "transcript", "records": len(read["records"])}],
                "offset": read["offset"], "pending": {}, "books": dict(baseline),
                "stats": {"rejected": "cwd-missing", "records": len(read["records"])}}

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
                "books": dict(baseline),
                "stats": {"steps": 0, "incomplete": bool(read["incomplete"])}}

    gates = resolve_gate_files(ws, gate_rel)
    gate_set = {os.path.normpath(g) for g in gates}

    # F3：先预扫本轮 Bash 的静态写目标，再建 books。gate 在本轮「被写且无读」时参考修订
    # 必须取持久化账本——写操作本身就改了磁盘，按写后磁盘重取修订会与账本指纹不符，
    # 把「轮 1 读全 → 轮 2 写同一 gate」复算成 missing（真机假阳性）。
    classified: dict[str, dict] = {}
    written_gates: set[str] = set()
    for step in steps:
        if step["kind"] != "Bash":
            continue
        command = str(step["input"].get("command") or "")
        if not command:
            continue
        res = wtarget.classify(command, cwd, gates)
        classified[step["id"]] = res
        for target in res.get("targets", []):
            if os.path.normpath(target) in gate_set:
                written_gates.add(os.path.normpath(target))

    # 本轮读账：每格 = 本轮参考修订 + 继承自上一轮的覆盖区间。
    books_state: dict[str, dict] = {}
    for gate in gates:
        ref_lines = _reference_revision(steps, gate, cwd)
        prev = _book_entry(ws, baseline, gate)
        if ref_lines:
            raw, source = ("\n".join(ref_lines)).encode("utf-8"), "transcript"
            total = total_lines(raw)
            fingerprint = _content_fingerprint(raw)
            coverage: list[list[int]] = []
            if prev and prev.get("sha") == fingerprint:
                # 同内容才继承；指纹不同 = 修订已变，旧读账一律作废（清空后按本轮重算）。
                coverage = _carry_coverage(prev.get("coverage"), int(prev.get("total") or 0), total)
        elif gate in written_gates and isinstance(prev, dict) and prev.get("sha"):
            # 本轮被写、本轮无该 gate 的（写前整文件）读：参考修订 = 账本里的**写前**修订。
            # 不重新哈希磁盘（磁盘已被本轮写改过），total/coverage 与账本同源，原样继承。
            raw, total = b"", int(prev.get("total") or 0)
            fingerprint, source = str(prev.get("sha")), "persisted"
            coverage = _ledger_coverage(prev, total)
        else:
            # 既未读也未写（或账本里没有该 gate）：用磁盘现状检测外部改动。
            try:
                with open(gate, "rb") as fh:
                    raw = fh.read()
            except OSError:
                raw = b""
            source = "disk"
            total = total_lines(raw)
            fingerprint = _content_fingerprint(raw)
            coverage = []
            if prev and prev.get("sha") == fingerprint:
                coverage = _carry_coverage(prev.get("coverage"), int(prev.get("total") or 0), total)
        books_state[gate] = {"raw": raw, "total": total, "coverage": coverage,
                             "revision": source, "sha": fingerprint}

    writes: list[dict] = []
    last_write_at: dict[str, int] = {}
    stats = {"steps": len(steps), "reads": 0, "bash": 0, "would_deny": 0,
             "sidechain": 0, "incomplete": bool(read["incomplete"])}

    for step_no, step in enumerate(steps):
        if step.get("sidechain"):
            stats["sidechain"] += 1
        if step["kind"] == "Read":
            target = os.path.normpath(os.path.join(cwd, str(step["input"].get("file_path") or "")))
            book = books_state.get(target)
            if not book:
                continue
            if book.get("consumed") and not _adopt_post_write_revision(step, book):
                continue  # 写后的局部读不能当整篇修订 → 不授证（fail-closed）
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
        res = classified.get(step["id"]) or wtarget.classify(command, cwd, gates)
        states = {gate: _read_state(book["coverage"], book["total"])
                  for gate, book in books_state.items()}
        targets = [os.path.normpath(t) for t in res.get("targets", [])]
        gate_targets = [g for g in gates if g in targets]
        if gate_targets:
            would = any(states[g] != "complete" for g in gate_targets)
            state = min((states[g] for g in gate_targets), key=_state_rank)
            revision = books_state[gate_targets[0]]["revision"]
            for g in gate_targets:
                last_write_at[g] = step_no
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
        # F4：同一轮内**写完立刻消费**该 gate 的读账（本条写已按写前证据判定完毕）。
        # 不清账的话，本轮之后的写仍会看到读时的完整 coverage ⇒ 「同一轮读一次、连写两次」
        # 的第二条被误放行（R98 要求第一条写即消费许可）。
        for g in gate_targets:
            _consume_book(books_state[g])
        if res["kind"] == "unresolved" and res.get("class") != "interpreter_wrapper":
            events.append({"type": "cc-bash-gap", "reason": "unresolved-command",
                           "source": "transcript", "digest": event["digest"],
                           "class": res.get("class"), "tool_use_id": step["id"]})

    # F3b：写治理文件即消费许可。上面的写事件仍按**写前**账本判定（F3 修复不变），但该轮
    # 结束必须把被写 gate 的账本换成**写后修订**——否则下一轮「未读直接写」继续以写前账本
    # 放行，许可永不撤销。顺序敏感：只有**最后一次写之后**的读能重建 coverage。
    for gate, step_no in last_write_at.items():
        try:
            with open(gate, "rb") as fh:
                post_raw = fh.read()
        except OSError:
            post_raw = b""
        post_total = total_lines(post_raw)
        post_coverage: list[list[int]] = []
        for step in steps[step_no + 1:]:
            if step["kind"] != "Read":
                continue
            target = os.path.normpath(os.path.join(cwd, str(step["input"].get("file_path") or "")))
            if target != gate:
                continue
            iv = _verified_interval(step, post_raw, post_total)
            if iv:
                post_coverage.append(iv)
        books_state[gate] = {"raw": post_raw, "total": post_total,
                             "coverage": merge_intervals(post_coverage),
                             "revision": "post-write",
                             "sha": _content_fingerprint(post_raw)}

    changed = git_changed_files(ws)
    if changed is not None:
        covered = {os.path.normpath(t) for ev in writes for t in ev.get("targets", [])}
        uncovered = [p for p in changed if os.path.normpath(p) not in covered]
        if uncovered:
            events.append({"type": "cc-bash-gap", "reason": "changed-but-untargeted",
                           "source": "transcript", "files": sorted(uncovered)[:20]})
    return {"events": events, "offset": read["offset"], "pending": new_pending,
            "books": _persist_books(ws, books_state), "stats": stats}


def _persist_books(ws: str, books_state: dict[str, dict]) -> dict:
    """把本轮读账折成 sidecar 形态：`{gate 相对路径: {coverage, revision, total, sha}}`。

    只留可复用的最小字段（不落 raw 正文），区间先合并以免 sidecar 随 Stop 轮数膨胀。
    """
    out: dict[str, dict] = {}
    for gate, book in books_state.items():
        out[gate_key(ws, gate)] = {
            "coverage": merge_intervals(book["coverage"]),
            "revision": book["revision"],
            "total": book["total"],
            "sha": book["sha"],
        }
    return out


# ---- 增量偏移与 pending（按会话 sidecar，原子写）----

def offset_path(ws: str, session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:64]
    return os.path.join(ws, ".workspace", ".hook-state", f"cc-transcript-{safe}.json")


def load_state(ws: str, session_id: str) -> dict:
    """读 sidecar。旧 sidecar 无 `books` 时按空字典迁移（offset/pending 原样保留）。"""
    try:
        with open(offset_path(ws, session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {"offset": int(data.get("offset") or 0),
                    "pending": data.get("pending") if isinstance(data.get("pending"), dict) else {},
                    "stats": data.get("stats") if isinstance(data.get("stats"), dict) else {},
                    "books": data.get("books") if isinstance(data.get("books"), dict) else {}}
    except Exception:
        pass
    return {"offset": 0, "pending": {}, "stats": {}, "books": {}}


def save_state(ws: str, session_id: str, offset: int, stats: dict, pending: dict,
               books: dict | None = None) -> None:
    """写 sidecar。`books` 省略（旧调用）时沿用已持久化的读账，不静默清空。"""
    path = offset_path(ws, session_id)
    keep = books if isinstance(books, dict) else load_state(ws, session_id).get("books") or {}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-cc-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"offset": int(offset), "stats": stats, "pending": pending,
                       "books": keep}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def save_offset(ws: str, session_id: str, offset: int, stats: dict) -> None:
    """兼容入口：保留旧 pending。"""
    keep = load_state(ws, session_id).get("pending") or {}
    save_state(ws, session_id, offset, stats, keep)
