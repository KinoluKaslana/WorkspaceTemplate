#!/usr/bin/env python3
"""Unified Claude Code/Codex workspace-gate hook adapter."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import gate_core as core
import wtarget
import cc_transcript

PATCH_HEADER = re.compile(r"\*\*\* (?:(?:Update|Add|Delete) File|Move to): (.+)")


class _PatchParseError(ValueError):
    """写目标无法静态确定（可读 deny）：文案原样输出，不包装成「内部错误」。

    覆盖两类来源：① Bash 里的显式 `apply_patch` 形态不可解析；② 原生
    `apply_patch` / `Write` / `Edit` 的 payload 本身缺字段或形态不合规。
    两者对模型都是「改法明确」的错误，因此必须给出可执行文案（R102/R103）。
    """


def _deny(reason: str, *, ws=None, client=None, sid=None, tool=None, targets=None,
          tool_use_id: str = "", code: str = "") -> None:
    """拒绝 + 一条 report-only 的 `gate-deny` 审计（v2.8.5）。

    拒绝证据此前只存在于客户端转录；审计里补一条，才能在 audit-<client>.jsonl
    里独立核验（作用域键含 .agent_id，子 agent 的拒绝不会记到父账户）。
    审计失败绝不影响 deny 输出。
    """
    if ws and client and sid:
        try:
            core.append_audit_event(ws, client, sid, {
                "type": "gate-deny",
                "tool": tool,
                "tool_use_id": tool_use_id,
                "targets": [str(t) for t in (targets or [])][:8],
                "reason_head": reason[:160],
                "reason_digest": core.sha256_hex(reason.encode("utf-8"))[:16],
            })
        except Exception:
            pass
    if code:
        reason = f"[workspace-gate:{code}] " + reason
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


def _context(text: str, event_name: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name,
        "additionalContext": text,
    }}, ensure_ascii=False))


def _client(data: dict) -> str:
    explicit = data.get("client")
    if explicit in ("cc", "codex", "dsh"):
        return explicit
    tool = str(data.get("tool_name", ""))
    if tool in ("apply_patch", "Bash") or isinstance(data.get("turn_id"), str):
        return "codex"
    if isinstance(data.get("model"), str):
        return "codex"
    return "cc"


def _session(data: dict) -> tuple[str, str]:
    """返回 (作用域键, cwd)。作用域键 = session；子 agent 再拼 `.agent_id`。

    v2.7.0：CC 的主线程与子 agent 共用同一 session_id（实测），只看 session
    会把子 agent 的读写并进父会话；把 agent_id 并入键后各自独立记账。
    """
    sid = data.get("session_id")
    cwd = data.get("cwd")
    if not isinstance(sid, str) or not sid or not isinstance(cwd, str) or not cwd:
        raise ValueError("missing session identity")
    cwd = os.path.abspath(os.path.expanduser(cwd))
    return core.scoped_id(sid, data.get("agent_id")), cwd


def _absolute(path: object, cwd: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise _PatchParseError(
            "写入目标路径缺失或为空，无法确定要写哪个文件；"
            "请在 Write/Edit 的 file_path 或 apply_patch 的文件头给出明确路径")
    value = os.path.expanduser(path.strip())
    if not os.path.isabs(value):
        value = os.path.join(cwd, value)
    return os.path.normpath(os.path.abspath(value))


def _write_targets(data: dict, client: str, cwd: str) -> list[str]:
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        raise _PatchParseError(
            "tool_input 缺失或不是对象，无法确定写入目标；"
            "请单独调用写入工具（不要与其他调用合并）")
    # Branch on payload shape, not client: a Bash-dispatched apply_patch (cc or
    # codex) is reassigned to tool_input={"command": <patch>}, while CC's native
    # Write/Edit carries file_path. This keeps a CC Bash patch parseable without
    # moving its ledger off client="cc" (its read evidence is recorded under cc).
    if client == "dsh":
        # dsh（DeepSeek Harness）：写工具实参名未在真实回合中验证，按已知候选
        # （file_path / path）容错读取；两者都缺即拒绝，不猜目标。
        path = inp.get("file_path") or inp.get("path")
        if not isinstance(path, str) or not path.strip():
            raise _PatchParseError(
                "dsh 写工具未提供可识别的目标路径（file_path / path 均为空）；"
                "请单独调用写入工具并给出明确路径")
        return [_absolute(path, cwd)]
    if "command" in inp:
        patch = inp.get("command")
        if not isinstance(patch, str):
            raise _PatchParseError(
                "apply_patch 的 command 不是字符串，无法确定写入目标；"
                "请把 patch 作为带引号的 heredoc 传入")
        lines = patch.splitlines()
        if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            raise _PatchParseError(
                "apply_patch 必须包含完整的 `*** Begin Patch` 与 `*** End Patch` 外壳；"
                "当前形态无法静态确定目标，请单独调用 apply_patch")
        paths = []
        for line in lines[1:-1]:
            if not line.startswith("*** "):
                continue
            if line == "*** End of File":
                continue
            match = PATCH_HEADER.fullmatch(line)
            if not match:
                raise _PatchParseError(
                    f"apply_patch 的文件头无法解析：{line.strip()[:80]}；"
                    "请使用 `*** Update File:` / `*** Add File:` / `*** Delete File:`")
            path = match.group(1)
            if path != path.strip() or any(ord(c) < 32 for c in path) or path.startswith("~"):
                raise _PatchParseError(
                    f"apply_patch 目标路径含首尾空格、控制字符或 `~` 前缀：{path[:80]!r}；"
                    "请改用干净的工作区相对路径或绝对路径")
            paths.append(_absolute(path, cwd))
        if not paths:
            raise _PatchParseError(
                "apply_patch 未包含任何文件头（`*** Update File:` / `*** Add File:` / "
                "`*** Delete File:`），无法确定写入目标")
        return paths
    return [_absolute(inp.get("file_path"), cwd)]


def _literal_shell_words(command: str) -> list[str] | None:
    """Small literal-only shell subset; shlex alone does not model expansion."""
    quote = None
    for char in command:
        if quote == "'":
            if char == "'":
                quote = None
        elif char in "$`\\":
            return None
        elif quote == '"':
            if char == '"':
                quote = None
        elif char in "'\"":
            quote = char
        elif char in ";&|<>(){}*?[]~#\n\r":
            return None
    if quote:
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


_HEX = set("0123456789abcdefABCDEF")
_OCT = set("01234567")


def _decode_ansi_c(text: str) -> str | None:
    """解码 bash ANSI-C $'...' 内层。

    支持 \\a \\b \\e \\E \\f \\n \\r \\t \\v \\\\ \\' \\" \\? \\nnn(八进制)
    \\xHH \\uHHHH \\UHHHHHHHH \\cX。任何不支持的转义/残缺 → None（宁不猜）。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            return None  # 行尾孤立反斜杠
        e = text[i + 1]
        if e == "a":
            out.append("\a"); i += 2
        elif e == "b":
            out.append("\b"); i += 2
        elif e in ("e", "E"):
            out.append("\x1b"); i += 2
        elif e == "f":
            out.append("\f"); i += 2
        elif e == "n":
            out.append("\n"); i += 2
        elif e == "r":
            out.append("\r"); i += 2
        elif e == "t":
            out.append("\t"); i += 2
        elif e == "v":
            out.append("\v"); i += 2
        elif e in ("\\", "'", '"', "?"):
            out.append(e); i += 2
        elif e in _OCT:
            j = i + 1
            while j < n and j < i + 4 and text[j] in _OCT:
                j += 1
            val = int(text[i + 1:j], 8)
            if val > 255:
                return None
            out.append(chr(val)); i = j
        elif e == "x":
            j = i + 2
            while j < n and j < i + 4 and text[j] in _HEX:
                j += 1
            digits = text[i + 2:j]
            if not digits:
                return None
            out.append(chr(int(digits, 16))); i = j
        elif e == "u":
            digits = text[i + 2:i + 6]
            if len(digits) != 4 or not all(ch in _HEX for ch in digits):
                return None
            out.append(chr(int(digits, 16))); i += 6
        elif e == "U":
            digits = text[i + 2:i + 10]
            if len(digits) != 8 or not all(ch in _HEX for ch in digits):
                return None
            cp = int(digits, 16)
            if cp > 0x10FFFF:
                return None
            out.append(chr(cp)); i += 10
        elif e == "c":
            if i + 2 >= n:
                return None
            out.append(chr(ord(text[i + 2]) & 0x1F)); i += 3
        else:
            return None
    return "".join(out)



_SHELL_WRAPPERS = {"bash", "sh", "zsh"}
_SHELL_FLAGS = {"-c", "-lc"}


def _strip_shell_wrap(command: str, depth: int = 0) -> str:
    """剥掉客户端的 shell 包装（`/usr/bin/zsh -lc "cat …"`），最多 3 层。

    只接受「恰 3 词 + exe ∈ {bash,sh,zsh} + flag ∈ {-c,-lc}」的字面形态；
    其余（含 `$`/反引号/反斜杠/引号不闭合/词数不符）一律原样返回——识别不到
    就不授证，避免把「包一层壳」当成绕过读证据的手段。
    """
    if depth >= 3 or not isinstance(command, str):
        return command
    words = _literal_shell_words(command)
    if not words or len(words) != 3:
        return command
    exe = os.path.basename(words[0])
    if exe in _SHELL_WRAPPERS and words[1] in _SHELL_FLAGS:
        return _strip_shell_wrap(words[2], depth + 1)
    return command

def _bash_patch(command: object, depth: int = 0) -> str | None:
    """Literal heredoc/argument and bash|sh -c|-lc only; never evaluate shell.

    None is outside write coverage. Explicit apply_patch in unsupported syntax
    is denied, including compound commands/cd, redirections and dynamic input.
    This is NOT a general shell parser or a boundary against concealed writes.
    """
    if not isinstance(command, str) or depth > 8:
        raise _PatchParseError("Bash 命令缺失或嵌套过深，无法静态确定 apply_patch 目标")
    command = command.strip()
    words = _literal_shell_words(command)
    if words:
        exe = Path(words[0]).name
        if exe in ("bash", "sh") and len(words) == 3 and words[1] in ("-c", "-lc"):
            return _bash_patch(words[2], depth + 1)
        if exe in ("cat", "ls", "read"):
            return None
        if exe == "apply_patch":
            if len(words) != 2:
                raise _PatchParseError(
                    "显式 apply_patch 只接受一个字面 patch 参数，无法静态确定目标；"
                    "请改用带引号 heredoc 的单条 apply_patch 调用")
            return words[1]
    # W6：apply_patch $'…'（ANSI-C 引用）——先解码再按字面 patch 处理；
    # 解码失败给出可读 ValueError（不再落「内部错误」）。
    ansi = re.fullmatch(
        r"[ \t]*(?:[A-Za-z0-9_./-]*/)?apply_patch[ \t]+\$'((?:[^'\\]|\\.)*)'[ \t]*",
        command)
    if ansi:
        decoded = _decode_ansi_c(ansi.group(1))
        if decoded is None:
            raise _PatchParseError("explicit apply_patch has unsupported ANSI-C escape")
        return decoded
    # Decode literal executable spelling even in unsupported shell forms
    # (e.g. app'ly_patch' < file). Tokens only detect intent, never grant credit.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        explicit = any(Path(token).name == "apply_patch" for token in lexer)
    except ValueError:
        explicit = False
    if not explicit and not re.search(r"(?<![\w.-])apply_patch(?![\w.-])", command):
        return None
    match = re.fullmatch(
        r"(?:[A-Za-z0-9_./-]*/)?apply_patch[ \t]*<<[ \t]*"
        r"(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z_0-9]*)(?P=quote)[ \t]*\n"
        r"(?P<body>.*?)\n(?P=delimiter)", command, re.DOTALL)
    if not match:
        raise _PatchParseError(
            "显式 apply_patch 处于不支持的 shell 形态（与其他命令用 && / ; / cd 串联、"
            "或经重定向输入），无法静态确定目标；请单独调用 apply_patch"
            "（不要加 mkdir / cd 等前缀命令）")
    body = match['body']
    if match['delimiter'] in body.splitlines():
        raise _PatchParseError("apply_patch 出现多个 heredoc 终止符，无法静态确定目标")
    if not match['quote'] and re.search(r"[$`\\]", body):
        raise _PatchParseError(
            "apply_patch 使用会展开的 heredoc（$ ` \\），无法静态确定目标；"
            "请改用带引号的 <<'PATCH'")
    return body


def _failed_response(obj: dict) -> bool:
    """Reject explicit failure, ambiguity, and conflicting status fields."""
    for key in ("is_error", "isError", "interrupted", "truncated"):
        if key in obj and obj[key] is not False:
            return True
    if "success" in obj and obj["success"] is not True:
        return True
    if obj.get("error") not in (None, "", False):
        return True
    for key in ("exit_code", "exitCode"):
        if key in obj and (type(obj[key]) is not int or obj[key] != 0):
            return True
    return False


DSH_WRAP = re.compile(r"^\s*<path>.*?</path>\s*\n\s*<type>file</type>\s*\n\s*<content>\n(.*)\n</content>\s*$",
                      re.S)
DSH_NUM = re.compile(r"^(\d+): ?(.*)$")
DSH_TRAILER = re.compile(r"^\((?:Showing lines \d+-\d+ of \d+\. Use offset=\d+ to continue|End of file - total \d+ lines)\.?\)$")


def _dsh_read_body(value: object, offset: int) -> str | None:
    """dsh read 正文带 `<path>/<type>/<content>` 包装与 `N: ` 行号前缀。

    只接受**行号连续、从 offset 开始**的形态；任何不符 → None（不授证，方向安全）。
    """
    raw = _dsh_text(value)
    if not raw:
        return None
    m = DSH_WRAP.match(raw)
    body = m.group(1) if m else raw
    lines = body.split("\n")
    # dsh 在 windowed read 末尾附一行 "(Showing lines A-B of N. Use offset=K to continue.)"
    if lines and DSH_TRAILER.match(lines[-1].strip()):
        lines.pop()
        while lines and lines[-1] == "":
            lines.pop()
    want = offset
    out = []
    for line in lines:
        mm = DSH_NUM.match(line)
        if not mm or int(mm.group(1)) != want:
            return None
        out.append(mm.group(2))
        want += 1
    return "\n".join(out)


def _dsh_text(value: object, depth: int = 0) -> str | None:
    """从 dsh 的 tool_response 里尽力取出正文（形状未在真实回合验证，按候选容错）。

    候选：字符串；{content|text|output|stdout: str}；[{type:'text', text: str}]；
    以上嵌在 {result|data: ...} 里。取不到就返回 None（不入账，方向安全）。
    """
    if depth > 3:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "output", "stdout", "result", "data"):
            got = _dsh_text(value.get(key), depth + 1)
            if got:
                return got
        return None
    if isinstance(value, list):
        parts = [_dsh_text(v, depth + 1) for v in value]
        joined = "".join(p for p in parts if p)
        return joined or None
    return None


def _record_read(data: dict, client: str, cwd: str) -> None:

    if data.get("hook_event_name") != "PostToolUse" or _failed_response(data):
        return
    tool = data.get("tool_name")
    if client == "dsh":
        if tool != "read":
            return
        inp = data.get("tool_input")
        if not isinstance(inp, dict):
            return
        path = inp.get("path") or inp.get("file_path")
        if not isinstance(path, str) or not path.strip():
            return
        off0 = inp.get("offset", 1)
        if type(off0) is not int or off0 < 1:
            return
        body = _dsh_read_body(data.get("tool_response"), off0)
        if body is None:
            return                       # 形态不符 → 不记账（方向安全）
        target = _absolute(path, cwd)
        try:
            total = len(Path(target).read_text(encoding="utf-8", errors="replace").split("\n"))
        except OSError:
            return
        off = inp.get("offset", 1)
        lim = inp.get("limit")
        if type(off) is not int or off < 1:
            return
        if lim is not None and (type(lim) is not int or lim < 1):
            return
        # EOF 哨兵：split("\n") 末尾多一行空行；物理行数要减去它，
        # 否则「读到最后一行」会被判成不完整（gate_core 会在 end==total-1 时自动升格）。
        physical = total - 1 if (total > 1 and Path(target).read_bytes().endswith(b"\n")) else total
        end = physical if lim is None else min(physical, off + lim - 1)
        if end < total and not body.endswith("\n"):
            body += "\n"          # gate_core 对 end<total 的期望片段以 LF 结尾
        read_info = {"read_ok": True, "path": target, "offset": off,
                     "limit": end - off + 1, "total_lines": total,
                     "tool_response": body}
        # 落到与 cc/codex 相同的记账路径
        ws = core.find_workspace(read_info["path"])
        if not ws:
            return
        conf = core.load_conf(ws)
        skey = core.scoped_id(data["session_id"], data.get("agent_id"))
        with core._StateLock(ws, client, skey):
            st = core.load_state(ws, client, skey)
            for gate_rel in conf["gate_files"]:
                if core.record_read(st, ws, gate_rel, client, read_info).get("recorded"):
                    break
            core.save_state(ws, client, skey, st)
        return
    if client == "cc" and tool != "Read":
        return
    if client == "codex" and tool != "Bash":
        return
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        return
    if client == "cc":
        response = data.get("tool_response")
        if (not isinstance(response, dict) or response.get("type") != "text"
                or _failed_response(response)):
            return
        file = response.get("file")
        if not isinstance(file, dict):
            return
        path = _absolute(file.get("filePath"), cwd)
        if path != _absolute(inp.get("file_path"), cwd):
            return
        start, count, total = (file.get(k) for k in ("startLine", "numLines", "totalLines"))
        if any(type(n) is not int or n < 1 for n in (start, count, total)):
            return
        request_start = inp.get("offset", 1)
        if type(request_start) is not int or request_start < 1 or start != request_start:
            return
        if "limit" in inp:
            limit = inp["limit"]
            if type(limit) is not int or limit < 1 or count > limit:
                return
        if start + count - 1 > total:
            return
        read_info = {
            "path": path,
            "offset": start,
            "limit": count,
            "total_lines": total,
            "tool_response": file.get("content"),
            "read_ok": True,
        }
    else:
        command = inp.get("command")
        if isinstance(command, str):
            command = _strip_shell_wrap(command)
        words = _literal_shell_words(command) if isinstance(command, str) else None
        if not words:
            return
        read_info = {"read_ok": True, "path": "", "tool_response": None}
        if len(words) == 2 and words[0] == "cat":
            path = words[1]
        elif len(words) == 4 and words[:2] == ["sed", "-n"]:
            interval = re.fullmatch(r"([1-9][0-9]{0,8}),([1-9][0-9]{0,8})p", words[2])
            if not interval:
                return
            start, end = map(int, interval.groups())
            if end < start:
                return
            path = words[3]
            read_info.update(offset=start, limit=end - start + 1, strict_interval=True)
        else:
            return
        if (not path or path != path.strip() or path.startswith(("-", "~"))
                or any(ord(c) < 32 for c in path)):
            return
        response = data.get("tool_response")
        if isinstance(response, str) and read_info.get("strict_interval"):
            # Native Codex Bash PostToolUse exposes body only, NOT exit status.
            # For this single-file, nonexpanding sed subset the exact requested
            # bytes prove content delivery, not process exit=0. Core must check
            # bounds, body and revision together; syntax alone grants nothing.
            body = response
        elif (isinstance(response, dict) and not _failed_response(response)
              and type(response.get("exit_code")) is int and response["exit_code"] == 0):
            body = response.get("stdout")
        else:
            return
        read_info.update(path=_absolute(path, cwd), tool_response=body)
    ws = core.find_workspace(read_info["path"])
    if not ws:
        return
    conf = core.load_conf(ws)
    recorded = False
    skey = core.scoped_id(data["session_id"], data.get("agent_id"))
    with core._StateLock(ws, client, skey):
        state = core.load_state(ws, client, skey)
        for gate_rel in conf["gate_files"]:
            result = core.record_read(state, ws, gate_rel, client, read_info)
            if result.get("recorded"):
                recorded = True
                break
            last_reason = result.get("reason")
        core.save_state(ws, client, skey, state)
        if not recorded:
            core.log(ws, f"post_read not recorded: {last_reason}")
    if recorded and client == "codex":
        owner = core.find_workspace(cwd)
        if owner:
            core.remember_read_workspace(owner, ws, client, skey)


def _cc_attribution(ws: str, client: str, sid: str, cwd: str, data: dict, source: str) -> None:
    """CC 的 Bash 写事后归因（**不订阅** Bash）：转录回放 → would_deny 复算。

    report-only：只往审计槽追加 `cc-bash-write` / `cc-bash-gap`，任何异常仅记 log，
    绝不影响判定、输出或退出码。转录只读 payload 精确给出的路径（cc_transcript 校验）。
    """
    if client != "cc":
        return
    try:
        path = data.get("transcript_path")
        if not isinstance(path, str) or not path.strip():
            core.append_audit_event(ws, client, sid, {
                "type": "cc-bash-gap", "reason": "no-transcript-path", "source": source})
            return
        cached = cc_transcript.load_state(ws, sid)
        result = cc_transcript.analyze(ws, sid, path, cwd, start_offset=cached["offset"],
                                       pending=cached.get("pending") or {},
                                       books=cached.get("books") or {})
        for record in result.get("events", []):
            core.append_audit_event(ws, client, sid, record)
        cc_transcript.save_state(ws, sid, int(result.get("offset") or 0),
                                 result.get("stats", {}), result.get("pending") or {},
                                 result.get("books") or {})
    except Exception as exc:  # 审计失败不得影响生命周期输出
        core.log(ws, f"cc_attribution failed: {type(exc).__name__}: {exc}")


def _template_anchor_gate(ws: str) -> dict:
    """W4：运行 template_anchor.py（timeout 10）。advisory，绝不产生 deny 决策。"""
    anchor_script = Path(__file__).resolve().parent / "template_anchor.py"
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(anchor_script), "--workspace", ws, "--json"],
            capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "completion-gate 超时（10s）",
                "systemMessage": "[workspace-gate] 完成声明门禁：template_anchor 超时（10s）"}
    except Exception as exc:
        return {"ok": False, "reason": f"completion-gate 无法运行：{exc}",
                "systemMessage": f"[workspace-gate] 完成声明门禁：无法运行 template_anchor（{exc}）"}
    ok = False
    reason = f"template_anchor exit={proc.returncode}"
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        if isinstance(payload, dict):
            ok = bool(payload.get("ok"))
            reason = payload.get("reason", reason)
    except Exception:
        pass
    msg = ("[workspace-gate] 完成声明门禁：clean"
           if ok else f"[workspace-gate] 完成声明门禁：未通过（{reason}）")
    return {"ok": ok, "reason": reason, "systemMessage": msg}


def handle(event: str, data: dict) -> None:
    client = _client(data)
    sid, cwd = _session(data)
    # W2 指纹锚：先 anchor_check（可能撤销读许可并落审计），再 reconcile，
    # 保证同一改动只产生一条 revision-changed + 一条 unsanctioned-gatefile-change。
    if event in ("pre_bash", "pre_write", "stop", "session_end"):
        owner = core.find_workspace(cwd)
        if owner:
            core.anchor_check(owner, client, sid, event)
    if client == "codex" and event in ("pre_bash", "pre_write", "stop"):
        owner = core.find_workspace(cwd)
        if owner:
            core.reconcile_session_reads(owner, client, sid,
                                         "Stop" if event == "stop" else "PreToolUse")
    if event == "subagent_stop":
        ws = core.find_workspace(cwd)
        if ws:
            core.append_audit_event(ws, client, sid, {
                "type": "agent-stop",
                "agent_id": data.get("agent_id"),
                "agent_type": data.get("agent_type"),
                "transcript": (data.get("agent_transcript_path") or "")[:200],
            })
            # v2.8.4：子 agent 的 shell 写同样只能事后归因——按 agent_transcript_path
            # 单独回放子转录（作用域键已含 .agent_id，账本与主会话互不覆盖）。
            agent_tp = data.get("agent_transcript_path")
            if client == "cc" and isinstance(agent_tp, str) and agent_tp.strip():
                _cc_attribution(ws, client, sid, cwd,
                                {**data, "transcript_path": agent_tp}, "subagent_stop")
            with core._StateLock(ws, client, sid):
                core._append_audit(ws, client, core.load_state(ws, client, sid))
        print("{}")
        return
    if event == "subagent_start":
        ws = core.find_workspace(cwd)
        if ws:
            with core._StateLock(ws, client, sid):
                st = core.load_state(ws, client, sid)
                st.setdefault("agents", {})
                core.save_state(ws, client, sid, st)
            core.append_audit_event(ws, client, sid, {
                "type": "agent-start",
                "agent_id": data.get("agent_id"),
                "agent_type": data.get("agent_type"),
                "parent_session_id": data.get("session_id"),
            })
        print("{}")
        return
    if event == "stop":
        # W3：客户端无关差集审计 + W4 完成声明门禁（均 report-only / advisory）。
        # 恒不 deny：从不声明完成或请求续轮。
        ws = core.find_workspace(cwd)
        if ws:
            core.worktree_audit(ws, client, sid, "stop")
            _cc_attribution(ws, client, core.split_scoped_id(sid)[0], cwd, data, "stop")
            gate = _template_anchor_gate(ws)
            core.append_audit_event(ws, client, sid, {
                "type": "completion-gate",
                "ok": bool(gate.get("ok")),
                "reason": gate.get("reason", ""),
            })
            print(json.dumps({"systemMessage": gate.get("systemMessage", "")},
                             ensure_ascii=False))
            return
        print("{}")
        return
    if event in ("pre_bash", "post_bash"):
        if client not in ("cc", "codex", "dsh") or data.get("tool_name") != "Bash":
            raise ValueError("Bash dispatch identity mismatch")
        inp = data.get("tool_input")
        command = inp.get("command") if isinstance(inp, dict) else None
        cmd_str = _strip_shell_wrap(command) if isinstance(command, str) else ""
        patch = _bash_patch(cmd_str)
        if patch is not None:
            data = {**data, "tool_input": {"command": patch}}
            event = "pre_write" if event == "pre_bash" else "post_write"
        elif event == "pre_bash":
            # W1 接线：patch 之外的 Bash 按 wtarget.classify 分派。
            # write → pre_write（逐目标路由 + deny）；interpreter_wrapper 未读 → deny；
            # 其余 unresolved → 审计放行（unresolved-command）。CC 不订阅 Bash，此分支实际不达。
            ws = core.find_workspace(cwd)
            gate_files: list[str] = []
            if ws:
                conf = core.load_conf(ws)
                gate_files = [core._norm_join(ws, g) for g in conf["gate_files"]]
            is_pwsh = str(data.get("dsh_tool") or "").lower() == "pwsh"
            res = (wtarget.classify_pwsh(cmd_str, cwd, gate_files)
                   if is_pwsh else wtarget.classify(cmd_str, cwd, gate_files))
            if res["kind"] == "write" and res["targets"]:
                result = core.evaluate_pre_write(cwd, client, sid, res["targets"])
                if result.get("action") == "deny":
                    _deny(result["reason"], ws=ws, client=client, sid=sid, tool="Bash",
                          targets=res["targets"], tool_use_id=data.get("tool_use_id") or "",
                          code=("no-read" if client == "dsh" else ""))
            elif res.get("class") == "interpreter_wrapper":
                owner = ws or cwd
                req = core.evaluate_read_required(owner, client, sid)
                if req.get("action") == "deny":
                    _deny(req["reason"], ws=ws, client=client, sid=sid, tool="Bash",
                          tool_use_id=data.get("tool_use_id") or "",
                          code=("no-read" if client == "dsh" else ""))
            elif is_pwsh and res["kind"] == "unresolved":
                # PowerShell 是解释器：写意图存在但目标不可静态确定 → fail-closed
                _deny("PowerShell 写目标无法静态确定（命令含写意图但解析不出目标）；"
                      "请把目标写成字面路径重试。",
                      ws=ws, client=client, sid=sid, tool="pwsh",
                      tool_use_id=data.get("tool_use_id") or "",
                      code=("unresolved" if client == "dsh" else ""))
            elif ws and res["kind"] == "unresolved":
                core.append_audit_event(ws, client, sid, {
                    "type": "unresolved-command",
                    "tool_use_id": data.get("tool_use_id") or data.get("turn_id") or "",
                    "client": client,
                    "digest": core.sha256_hex(cmd_str.encode("utf-8"))[:16],
                    "class": res["class"],
                })
            return
        else:
            # post_bash + patch is None
            if client == "codex":
                ws = core.find_workspace(cwd)
                if ws:
                    conf = core.load_conf(ws)
                    gfs = [core._norm_join(ws, g) for g in conf["gate_files"]]
                    res = wtarget.classify(cmd_str, cwd, gfs)
                    if res.get("kind") == "write" and res.get("targets"):
                        core.evaluate_post_write(ws, client, sid, res["targets"])
                        return
                event = "post_read"
            else:
                # CC 的 Bash stdout 被截断，无法做内容锚定；绝不记账。
                # 不能偷偷走 post_read（会破坏 CC「只认 Read」的记账契约）。
                return
    # Boundaries belong to cwd's workspace, including failed/unsupported reads.
    # Activity changes lifecycle, never permission.
    if event in ("pre_write", "post_write", "post_read"):
        owner = core.find_workspace(cwd)
        if owner:
            core.note_activity(owner, client, sid)
    if event == "session_start":
        ws = core.find_workspace(cwd)
        if ws:
            core.anchor_baseline(ws, client, sid)
        result = (core.evaluate_session_start(ws, client, sid, data.get("permission_mode"))
                  if ws else {"action": "none"})
        if result.get("action") == "context":
            _context(result["text"], "SessionStart")
    elif event == "prompt_submit":
        ws = core.find_workspace(cwd)
        result = core.evaluate_prompt_submit(ws, client, sid) if ws else {"action": "none"}
        if result.get("action") == "context":
            _context(result["text"], "UserPromptSubmit")
    elif event == "pre_write":
        targets = _write_targets(data, client, cwd)
        # cwd is only a hint; core discovers each target's own workspace.
        # Never skip the core merely because the first target is outside.
        result = core.evaluate_pre_write(cwd, client, sid, targets)
        if result.get("action") == "deny":
            _deny(result["reason"], ws=core.find_workspace(cwd), client=client, sid=sid,
                  tool=data.get("tool_name"), targets=targets,
                  tool_use_id=data.get("tool_use_id") or "",
                  code="no-read" if client == "dsh" else "")
    elif event == "post_read":
        _record_read(data, client, cwd)
    elif event == "post_write":
        targets = _write_targets(data, client, cwd)
        grouped = {}
        for target in targets:
            ws = core.find_workspace(target)
            if ws:
                grouped.setdefault(ws, []).append(target)
        for ws, paths in sorted(grouped.items()):
            core.evaluate_post_write(ws, client, sid, paths)
            core.log(ws, f"post_write client={client} session={sid} targets={paths}")
    elif event == "session_end":
        ws = core.find_workspace(cwd)
        if ws:
            if client == "codex":
                core.reconcile_session_reads(ws, client, sid, "SessionEnd")
            # W3：先落 worktree-change（report-only），再 evaluate_session_end
            # 把累积事件一并 flush 进 audit-<client>.jsonl。
            core.worktree_audit(ws, client, sid, "session_end")
            _cc_attribution(ws, client, core.split_scoped_id(sid)[0], cwd, data, "session_end")
            core.evaluate_session_end(ws, client, sid, data.get("completed"),
                                      data.get("interrupted"), reason=data.get("reason"))
    else:
        raise ValueError("unknown event")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    event = argv[0] if argv else ""
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("payload must be object")
        # 部署配置经 GATE_CLIENT 显式声明客户端；payload 探测（_client）仅作兜底。
        # 实测依据（2026-09-17 /tmp/payload-dumps/session_end-*.json）：Codex SessionEnd
        # payload 无 turn_id/model，纯字段探测会误判为 cc，导致账本前缀分裂。
        env_client = os.environ.get("GATE_CLIENT")
        if env_client in ("cc", "codex", "dsh"):
            data["client"] = env_client
        handle(event, data)
    except _PatchParseError as exc:
        # 可读 deny 文案：apply_patch 形态无法静态确定，不包装成「内部错误」。
        if event in ("pre_write", "pre_bash"):
            _deny(str(exc))
        else:
            print(json.dumps({"systemMessage": str(exc)}, ensure_ascii=False))
    except Exception as exc:
        message = f"Workspace gate 内部错误；请人工检查。({type(exc).__name__})"
        # Only configured PreToolUse dispatches can return a deny decision.
        # Use argv, not payload metadata: JSON/identity parsing may itself fail.
        if event in ("pre_write", "pre_bash"):
            _deny(message)
        else:
            # Lifecycle/post-tool failures are advisory, never tool decisions
            # or requests to continue a turn (including Stop/SessionEnd).
            print(json.dumps({"systemMessage": message}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
