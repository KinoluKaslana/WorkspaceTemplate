#!/usr/bin/env python3
"""Unified Claude Code/Codex workspace-gate hook adapter."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

import gate_core as core

PATCH_HEADER = re.compile(r"\*\*\* (?:(?:Update|Add|Delete) File|Move to): (.+)")


def _deny(reason: str) -> None:
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
    if explicit in ("cc", "codex"):
        return explicit
    tool = str(data.get("tool_name", ""))
    if tool in ("apply_patch", "Bash") or isinstance(data.get("turn_id"), str):
        return "codex"
    if isinstance(data.get("model"), str):
        return "codex"
    return "cc"


def _session(data: dict) -> tuple[str, str]:
    sid = data.get("session_id")
    cwd = data.get("cwd")
    if not isinstance(sid, str) or not sid or not isinstance(cwd, str) or not cwd:
        raise ValueError("missing session identity")
    cwd = os.path.abspath(os.path.expanduser(cwd))
    return sid, cwd


def _absolute(path: object, cwd: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("missing path")
    value = os.path.expanduser(path.strip())
    if not os.path.isabs(value):
        value = os.path.join(cwd, value)
    return os.path.normpath(os.path.abspath(value))


def _write_targets(data: dict, client: str, cwd: str) -> list[str]:
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        raise ValueError("missing tool_input")
    # Branch on payload shape, not client: a Bash-dispatched apply_patch (cc or
    # codex) is reassigned to tool_input={"command": <patch>}, while CC's native
    # Write/Edit carries file_path. This keeps a CC Bash patch parseable without
    # moving its ledger off client="cc" (its read evidence is recorded under cc).
    if "command" in inp:
        patch = inp.get("command")
        if not isinstance(patch, str):
            raise ValueError("missing apply_patch command")
        lines = patch.splitlines()
        if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            raise ValueError("apply_patch must be a complete standard patch")
        paths = []
        for line in lines[1:-1]:
            if not line.startswith("*** "):
                continue
            if line == "*** End of File":
                continue
            match = PATCH_HEADER.fullmatch(line)
            if not match:
                raise ValueError("unparseable apply_patch header")
            path = match.group(1)
            if path != path.strip() or any(ord(c) < 32 for c in path) or path.startswith("~"):
                raise ValueError("ambiguous apply_patch path")
            paths.append(_absolute(path, cwd))
        if not paths:
            raise ValueError("apply_patch must contain standard file headers")
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


def _bash_patch(command: object, depth: int = 0) -> str | None:
    """Literal heredoc/argument and bash|sh -c|-lc only; never evaluate shell.

    None is outside write coverage. Explicit apply_patch in unsupported syntax
    is denied, including compound commands/cd, redirections and dynamic input.
    This is NOT a general shell parser or a boundary against concealed writes.
    """
    if not isinstance(command, str) or depth > 8:
        raise ValueError("missing or excessively nested Bash command")
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
                raise ValueError("explicit apply_patch requires one literal patch")
            return words[1]
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
        raise ValueError("explicit apply_patch has unsupported shell syntax")
    body = match['body']
    if match['delimiter'] in body.splitlines():
        raise ValueError("multiple heredoc terminators")
    if not match['quote'] and re.search(r"[$`\\]", body):
        raise ValueError("expanding heredoc cannot determine patch targets")
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


def _record_read(data: dict, client: str, cwd: str) -> None:
    if data.get("hook_event_name") != "PostToolUse" or _failed_response(data):
        return
    tool = data.get("tool_name")
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
    with core._StateLock(ws, client, data["session_id"]):
        state = core.load_state(ws, client, data["session_id"])
        for gate_rel in conf["gate_files"]:
            result = core.record_read(state, ws, gate_rel, client, read_info)
            if result.get("recorded"):
                recorded = True
                break
        core.save_state(ws, client, data["session_id"], state)
    if recorded and client == "codex":
        owner = core.find_workspace(cwd)
        if owner:
            core.remember_read_workspace(owner, ws, client, data["session_id"])


def handle(event: str, data: dict) -> None:
    client = _client(data)
    sid, cwd = _session(data)
    if client == "codex" and event in ("pre_bash", "pre_write", "stop"):
        owner = core.find_workspace(cwd)
        if owner:
            core.reconcile_session_reads(owner, client, sid,
                                         "Stop" if event == "stop" else "PreToolUse")
    if event == "stop":
        # Advisory reconciliation only: never claim completion or ask continuation.
        print("{}")
        return
    if event in ("pre_bash", "post_bash"):
        if client not in ("cc", "codex") or data.get("tool_name") != "Bash":
            raise ValueError("Bash dispatch identity mismatch")
        inp = data.get("tool_input")
        patch = _bash_patch(inp.get("command") if isinstance(inp, dict) else None)
        if patch is None:
            if event == "pre_bash":
                return  # A read must not enter pre_write (or create write state).
            if client == "codex":
                event = "post_read"
            else:
                # CC 的 Bash stdout 被截断，无法做内容锚定；绝不记账。
                # 不能偷偷走 post_read（会破坏 CC「只认 Read」的记账契约）。
                return
        else:
            data = {**data, "tool_input": {"command": patch}}
            event = "pre_write" if event == "pre_bash" else "post_write"
    # Boundaries belong to cwd's workspace, including failed/unsupported reads.
    # Activity changes lifecycle, never permission.
    if event in ("pre_write", "post_write", "post_read"):
        owner = core.find_workspace(cwd)
        if owner:
            core.note_activity(owner, client, sid)
    if event == "session_start":
        ws = core.find_workspace(cwd)
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
            _deny(result["reason"])
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
        if env_client in ("cc", "codex"):
            data["client"] = env_client
        handle(event, data)
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
