#!/usr/bin/env python3
"""wtarget.py — 离线「写目标窄识别器」（W1b 席，DeepSeek-V4-Pro）。

纯 stdlib；不读盘；不调用外部进程；单条 <1ms（规格 §9）。

独立模块：不得 import 进 hook.py / gate_core.py（接线是 W1 的事，需另开）。

对外接口：
    classify(command: str, cwd: str, gate_files: list[str]) -> dict
        {
          "kind": "read" | "write" | "patch" | "unresolved" | "other",
          "targets": [...],        # 绝对化后的写目标（仅 write 时非空）
          "gate_targets": [...],   # targets ∩ gate_files（绝对路径）
          "forms": ["redirect:>>", "sed-i", ...],
          "confidence": "static" | "partial" | "opaque",
          "reason": "..."
        }

失败方向安全：宁可 unresolved，不许猜。语义见 TASK-W1B.md 判定纪律 1–9。
"""
from __future__ import annotations

import os
import re

# ---------------------------------------------------------------- 常量

# 规则 7：这些重定向目标不算写目标。
_NULL_TARGETS = {
    "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero",
    "&1", "&2", "&0", "-",
}

# 规则 6：读类命令。
_READ_CMDS = {
    "cat", "nl", "bat", "grep", "egrep", "fgrep", "rg", "ls", "od", "wc",
    "head", "tail", "test", "true", "read", "file",
}

# 规则 6：删除/建目录不是写目标 → other（不写 gate）。
# F2 §1：查询/包装/工具类命令（find/git/codex/command/env/…）移出本集合，
# 落到「未知首词元 → unresolved」的一刀切兜底，不再枚举。
_OTHER_CMDS = {"mkdir", "rm", "rmdir", "unlink"}

# 写类命令：靠重定向判定写目标；无重定向 → other（非写）。
_REDIRECT_ONLY_CMDS = {"echo", "printf"}

# 规则 4：必须判 unresolved 的命令首词元（解释器/构建工具，内层不递归）。
_UNRESOLVED_CMDS = {"python", "python2", "python3", "make", "sh", "bash", "zsh",
                    "dash", "ash", "csh", "tcsh", "fish"}

# 规则 4：目标含这些通配 → unresolved。
_WILDCARD_CHARS = set("*?[{")

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# ---------------------------------------------------------------- B′ 命令形态表
# interpreter_wrapper 的**唯一来源**是下表投影（whitelist 恒等单测锁死，禁人工清单）。
# 新增解释器只加表行；对应正/负例须同时进测试矩阵（缺例即 fail，见 test_wtarget.py）。
_COMMAND_FORM_TABLE = (
    (("python", "python2", "python3"), "interpreter_wrapper"),
    (("sh", "bash", "zsh", "dash"), "interpreter_wrapper"),
    (("perl",), "interpreter_wrapper"),
    (("make",), "interpreter_wrapper"),
    (("env", "sudo", "nice", "nohup", "timeout", "xargs", "command", "exec"),
     "interpreter_wrapper"),
)

WHITELIST = frozenset(
    w for names, kind in _COMMAND_FORM_TABLE if kind == "interpreter_wrapper" for w in names
)


# ---------------------------------------------------------------- 分段（规则 1）

def _split_segments(command: str) -> list[str]:
    """按未引用的 ; && || | & 换行 切段；跳过 heredoc 正文（规则 3 不解码正文）。

    &>/>&/>| 是重定向，不算分隔。<<DELIM ... DELIM 的正文整段丢弃，
    以免把正文里的行误判为写命令。
    """
    segs: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(command)
    quote: str | None = None
    last_ns = ""  # 最近一个非空白字符
    heredoc: str | None = None  # heredoc 终止符；非 None 表示正在正文中
    pending_heredoc: str | None = None  # 已见 << 定界符，正文从下一行起才生效

    def flush() -> None:
        nonlocal cur, last_ns
        s = "".join(cur).strip()
        if s:
            segs.append(s)
        cur = []
        last_ns = ""

    while i < n:
        c = command[i]
        if heredoc is not None:
            j = command.find("\n", i)
            if j == -1:
                break  # 未闭合 heredoc：丢弃剩余
            line = command[i:j]
            i = j + 1
            if line.strip() == heredoc:
                heredoc = None
            continue
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = None
            last_ns = c
            i += 1
            continue
        if quote == '"':
            cur.append(c)
            if c == '"':
                quote = None
            last_ns = c
            i += 1
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            last_ns = c
            i += 1
            continue
        if c == "\n":
            if pending_heredoc is not None:
                # 定界符行结束：正文从下一行起才进入跳过模式，
                # 先让定界符行的剩余内容（含 > 重定向）正常成段。
                heredoc = pending_heredoc
                pending_heredoc = None
            flush()
            i += 1
            continue
        if c == ";":
            flush()
            i += 1
            continue
        if c == "|":
            if last_ns == ">":  # >| 覆盖重定向
                cur.append(c)
            else:
                flush()
            i += 1
            continue
        if c == "&":
            if i + 1 < n and command[i + 1] == "&":  # &&
                flush()
                i += 2
                continue
            if i + 1 < n and command[i + 1] == ">":  # &> 重定向
                cur.append("&")
                cur.append(">")
                last_ns = ">"
                i += 2
                continue
            if last_ns == ">":  # >& fd 复制
                cur.append("&")
                i += 1
                continue
            flush()  # 裸 & 作业控制
            i += 1
            continue
        if c == "<":
            if i + 1 < n and command[i + 1] == "<":
                # heredoc：把「<<[-] 定界符」连同定界符行剩余内容保留进段文本
                # （这样定界符之后的 > / >> 重定向也能被 _lex 识别），
                # 只丢弃定界符下一行起的正文。
                heredoc_start = i
                i += 2
                if i < n and command[i] == "-":
                    i += 1
                while i < n and command[i] in " \t":
                    i += 1
                delim: list[str] = []
                q: str | None = None
                while i < n and command[i] not in "\n":
                    ch = command[i]
                    if q:
                        if ch == q:
                            q = None
                        else:
                            delim.append(ch)
                    elif ch in "'\"":
                        q = ch
                    elif ch in " \t":
                        break
                    else:
                        delim.append(ch)
                    i += 1
                pending_heredoc = "".join(delim)
                cur.append(command[heredoc_start:i])
                if i > heredoc_start:
                    last_ns = command[i - 1]
                continue
            cur.append("<")
            i += 1
            continue
        cur.append(c)
        if c not in " \t":
            last_ns = c
        i += 1
    flush()
    return segs


# ---------------------------------------------------------------- 词元化（规则 2）

def _lex(segment: str) -> tuple[list[tuple[str, str]], str | None]:
    """词元化一段。返回 (tokens, error)。

    tokens: list[tuple[str, str]]，第一元 "word"|"op"，第二元文本。
    error: None 或原因字符串（出现未在单引号内的 $ ` \\ 或未闭合引号）。
    出错时返回 ([], error)：tokens 恒为 list。
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(segment)
    while i < n:
        c = segment[i]
        if c in " \t":
            i += 1
            continue
        if c == "'":
            j = segment.find("'", i + 1)
            if j == -1:
                return [], "unclosed-single-quote"
            tokens.append(("word", segment[i + 1:j]))
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and segment[j] != '"':
                if segment[j] in "$`\\":
                    return [], "unsafe-in-double-quote"
                buf.append(segment[j])
                j += 1
            if j >= n:
                return [], "unclosed-double-quote"
            tokens.append(("word", "".join(buf)))
            i = j + 1
            continue
        if c in "$`\\":
            return [], "unsafe-char"
        if c == ">":
            if i + 1 < n and segment[i + 1] == ">":
                tokens.append(("op", ">>"))
                i += 2
                continue
            if i + 1 < n and segment[i + 1] == "|":
                tokens.append(("op", ">|"))
                i += 2
                continue
            if i + 1 < n and segment[i + 1] == "&":
                tokens.append(("op", ">&"))
                i += 2
                continue
            tokens.append(("op", ">"))
            i += 1
            continue
        if c == "&":
            if i + 1 < n and segment[i + 1] == ">":
                tokens.append(("op", "&>"))
                i += 2
                continue
            tokens.append(("op", "&"))
            i += 1
            continue
        if c == "<":
            if i + 1 < n and segment[i + 1] == "<":
                tokens.append(("op", "<<"))
                i += 2
                continue
            tokens.append(("op", "<"))
            i += 1
            continue
        if c == "|":
            tokens.append(("op", "|"))
            i += 1
            continue
        if c == ";":
            tokens.append(("op", ";"))
            i += 1
            continue
        j = i
        buf = []
        while j < n and segment[j] not in " \t\"'$`\\><&|;":
            buf.append(segment[j])
            j += 1
        if not buf:
            return [], "lex-error"
        tokens.append(("word", "".join(buf)))
        i = j
    return tokens, None


def _command_word(tokens: list[tuple[str, str]]) -> str:
    """首词元（跳过 VAR=val 赋值），取 basename。"""
    for kind, text in tokens:
        if kind != "word":
            continue
        if _ASSIGN_RE.match(text):
            continue
        return os.path.basename(text)
    return ""


def _first_command_word(segment: str) -> str:
    """段首命令词元（basename）。轻量、不做完整词元化：跳过前导空白与
    VAR=val 赋值后取第一个裸词。仅在 _lex 失败时用于判定「段首是否为
    apply_patch」（apply_patch 补丁正文常含 $'…' 等 _lex 会拒绝的字符，
    但命令位就是 apply_patch，应判 patch 而非 unresolved）。"""
    i = 0
    n = len(segment)
    while i < n and segment[i] in " \t":
        i += 1
    while i < n:
        j = i
        while j < n and (segment[j].isalnum() or segment[j] == "_"):
            j += 1
        if j < n and segment[j] == "=" and j > i:
            # VAR=val 赋值：跳过赋值值（值内空格属极端边缘，不完整处理）
            i = j + 1
            while i < n and segment[i] not in " \t":
                i += 1
            while i < n and segment[i] in " \t":
                i += 1
            continue
        break
    j = i
    while j < n and segment[j] not in " \t\n;|&<>()":
        j += 1
    return os.path.basename(segment[i:j])


def _lead_command_word(seg: str) -> str:
    """命令首词元（basename），供 B′ interpreter_wrapper 判定。词元化失败时退回轻量提取。"""
    tokens, err = _lex(seg)
    if err is None:
        return _command_word(tokens)
    return _first_command_word(seg)


def _has_unbalanced_paren(token: str) -> bool:
    """目标词元里出现未闭合的 ( 或 )（如子 shell 尾括号污染路径）→ True。"""
    depth = 0
    for ch in token:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


def _operands(tokens: list[tuple[str, str]]) -> list[str]:
    """命令词之后的位置参数（跳过命令名与前置 VAR=val 赋值）。

    F2 §4：遇输入重定向 `<` / `<<` 立即停止——其后词元是输入源（或 heredoc
    定界符），不是命令操作数，不得进写目标。"""
    words: list[str] = []
    seen_cmd = False
    for kind, text in tokens:
        if kind == "op" and text in ("<", "<<"):
            break
        if kind != "word":
            continue
        if not seen_cmd:
            if _ASSIGN_RE.match(text):
                continue
            seen_cmd = True
            continue
        words.append(text)
    return words


# ---------------------------------------------------------------- 写形态解析

def _skip_options(words: list[str], opts_taking_value: set[str] | None = None) -> list[str]:
    """跳过选项及其取值，返回位置操作数。"""
    taking = opts_taking_value or set()
    out: list[str] = []
    i = 0
    n = len(words)
    end_of_opts = False
    while i < n:
        w = words[i]
        if not end_of_opts and w == "--":
            end_of_opts = True
            i += 1
            continue
        if not end_of_opts and w in taking:
            i += 2
            continue
        if not end_of_opts and len(w) > 1 and w[0] == "-" and w != "-":
            i += 1
            continue
        out.append(w)
        i += 1
    return out


def _redirect_targets(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """重定向 > >> 2> 2>> &> >| 的 (op, 目标)。>& 是 fd 复制，不算写。"""
    out: list[tuple[str, str]] = []
    i = 0
    n = len(tokens)
    while i < n:
        kind, text = tokens[i]
        if kind == "op" and text in (">", ">>", "2>", "2>>", "&>", ">|"):
            j = i + 1
            while j < n and tokens[j][0] != "word":
                j += 1
            if j < n:
                out.append((text, tokens[j][1]))
            i = j + 1
        else:
            i += 1
    return out


def _script_tool_targets(operands: list[str], script_opts: tuple[str, ...]) -> list[str]:
    """sed -i / perl -i：脚本经 -e/-f（或 -E）给出时，位置参数都是文件；
    否则第一个位置参数是脚本，其后才是文件。"""
    has_script_opt = False
    files: list[str] = []
    i = 0
    n = len(operands)
    while i < n:
        w = operands[i]
        if w in script_opts:
            has_script_opt = True
            i += 2
            continue
        if len(w) > 1 and w[0] == "-":
            i += 1
            continue
        files.append(w)
        i += 1
    return files if has_script_opt else files[1:]


def _dd_of_targets(tokens: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    for _, t in tokens:
        if t.startswith("of=") and len(t) > 3:
            out.append(t[3:])
    return out


# ---------------------------------------------------------------- 目标解析（规则 7/8）

def _resolve_target(token: str, cwd: str | None):
    """返回 (target_or_None, status)。status ∈ {"static","partial","unresolved"}。"""
    if token in _NULL_TARGETS or token in ("&1", "&2", "&0"):
        return None, "static"
    if any(ch in _WILDCARD_CHARS for ch in token):
        return None, "unresolved"
    if "~" in token:
        return token, "partial"
    if os.path.isabs(token):
        return os.path.normpath(token), "static"
    if cwd is None:
        return token, "partial"
    return os.path.normpath(os.path.join(cwd, token)), "static"


# ---------------------------------------------------------------- 单段判定

def _classify_segment(tokens: list[tuple[str, str]], cwd: str | None):
    """返回 (kind, targets, forms, confidence, note)。"""
    if not tokens:
        return "other", [], [], "static", "empty-segment"

    cmd = _command_word(tokens)
    if not cmd:
        return "other", [], [], "static", "no-command-word"

    if cmd.startswith("("):
        # F2 §3：段首为 `(`（或 `(cd`/`(pushd`）→ 整条 unresolved（不剥括号、不猜）。
        return "unresolved", [], [], "opaque", "子 shell 括号，整条命令写目标不可静态确定"

    if cmd in ("cd", "pushd"):
        # 缺陷 B 修复：cd/pushd 改变工作目录，后续段的相对路径不能用 payload cwd 解析。
        # 保守起见整条命令落 unresolved（不递归、不猜 cwd），与规则 4「宁 unresolved 不猜」一致。
        return "unresolved", [], [], "opaque", f"{cmd} 改变工作目录，整条命令写目标不可静态解析"

    if cmd == "apply_patch":
        return "patch", [], ["apply_patch"], "opaque", "apply_patch 词元，交 _bash_patch"

    if cmd in _UNRESOLVED_CMDS:
        return "unresolved", [], [], "opaque", f"{cmd} 解释器/包装，内层不递归"

    if cmd == "tee":
        words = _skip_options(_operands(tokens))
        if not words:
            return "other", [], ["tee"], "static", "tee 无文件操作数"
        return "write", words, ["tee"], "static", "tee 操作数"
    if cmd == "truncate":
        words = _skip_options(_operands(tokens), opts_taking_value={"-s"})
        if not words:
            return "other", [], ["truncate"], "static", "truncate 无文件操作数"
        return "write", [words[-1]], ["truncate"], "static", "truncate 最后一个非选项操作数"
    if cmd == "sed":
        if any(k == "word" and t.startswith("-i") and len(t) > 1 for k, t in tokens):
            files = _script_tool_targets(_operands(tokens), ("-e", "-f"))
            if not files:
                return "other", [], ["sed-i"], "static", "sed -i 无文件操作数"
            return "write", files, ["sed-i"], "static", "sed -i 就地写"
        return "read", [], [], "static", "sed 无 -i，只读"
    if cmd == "perl":
        has_i = any(k == "word" and (t.startswith("-i") or (t.startswith("-p") and "i" in t[1:3]))
                    for k, t in tokens)
        if has_i:
            files = _script_tool_targets(_operands(tokens), ("-e", "-E", "-f"))
            if not files:
                return "other", [], ["perl-i"], "static", "perl -i 无文件操作数"
            return "write", files, ["perl-i"], "static", "perl -i 就地写"
        return "unresolved", [], [], "opaque", "perl 解释器，无 -i（-e/-n/-p/脚本文件）内层可写，不递归"
    if cmd in ("cp", "mv"):
        words = _skip_options(_operands(tokens))
        if not words:
            return "other", [], [cmd], "static", f"{cmd} 无操作数"
        target = words[-1]
        if target.endswith("/") or len(words) > 2:
            return "unresolved", [], [cmd], "opaque", f"{cmd} 目标是目录且不可静态确定"
        return "write", [target], [cmd], "partial", f"{cmd} 最后一个操作数（目录性不可静态确认）"
    if cmd == "install":
        words = _skip_options(_operands(tokens), opts_taking_value={"-m", "-o", "-g"})
        if not words:
            return "other", [], [cmd], "static", "install 无操作数"
        target = words[-1]
        if target.endswith("/") or len(words) > 2:
            return "unresolved", [], [cmd], "opaque", "install 目标是目录且不可静态确定"
        return "write", [target], [cmd], "partial", "install 最后一个操作数（目录性不可静态确认）"
    if cmd == "dd":
        ofs = _dd_of_targets(tokens)
        if not ofs:
            return "other", [], ["dd"], "static", "dd 无 of= 操作数"
        return "write", ofs, ["dd-of"], "static", "dd of= 目标"

    if cmd in _READ_CMDS:
        return "read", [], [], "static", f"{cmd} 读命令"
    if cmd in _OTHER_CMDS:
        return "other", [], [], "static", f"{cmd} 非写命令"
    if cmd in _REDIRECT_ONLY_CMDS:
        return "other", [], [], "static", f"{cmd} 无重定向，非写"

    # F2 §1：不在已知安全集合内的首词元 → 整条 unresolved（一刀切，
    # 关闭「包装前缀」类：env/sudo/nice/nohup/timeout/xargs/command/exec/find/…）。
    return "unresolved", [], [], "opaque", f"未知首词元 {cmd}，整条命令写目标不可静态确定"


# ---------------------------------------------------------------- 主入口

def classify(command: str, cwd: str, gate_files: list[str]) -> dict:
    """识别一条 Bash 命令的写目标（离线、窄、失败方向安全）。"""
    gate_set = {os.path.normpath(g) for g in gate_files if isinstance(g, str)}

    if not isinstance(command, str) or not command.strip():
        return {"kind": "other", "targets": [], "gate_targets": [], "forms": [],
                "confidence": "static", "class": "static", "reason": "空命令"}

    # F2 §2：apply_patch 不再按「整条命令出现 apply_patch 词元」判定——
    # 只在分段后的段首命令位判定，heredoc 正文/引号内文本/非命令位参数一律不参与。

    segments = _split_segments(command)
    if not segments:
        return {"kind": "other", "targets": [], "gate_targets": [], "forms": [],
                "confidence": "static", "class": "static", "reason": "无有效分段"}

    lead = _lead_command_word(segments[0])  # 命令首词元（B′ 判 interpreter_wrapper 用）
    kinds: list[str] = []
    all_targets: list[str] = []
    all_forms: list[str] = []
    all_conf: list[str] = []
    reasons: list[str] = []

    for seg in segments:
        tokens, err = _lex(seg)
        if err is not None:
            # F2 §2：段首命令位是 apply_patch 时，即便其余内容（$'…' 补丁正文）
            # 无法词元化，也判 patch——补丁正文本就是 opaque，不该因 $ 落 unresolved。
            if _first_command_word(seg) == "apply_patch":
                kinds.append("patch")
                all_conf.append("opaque")
                reasons.append("apply_patch 词元（段首命令位），交 _bash_patch")
                continue
            kinds.append("unresolved")
            all_conf.append("opaque")
            reasons.append(f"词元化失败：{err}")
            all_forms.append("unresolved:lex")
            continue

        seg_unresolved = False
        seg_targets: list[str] = []
        seg_forms: list[str] = []
        seg_conf = "static"

        # 1) 重定向写目标（独立于命令名：echo>/printf>>/cat>）。
        for op, t in _redirect_targets(tokens):
            if _has_unbalanced_paren(t):
                seg_unresolved = True
                reasons.append(f"重定向目标含未闭合括号：{t}")
                all_forms.append("redirect:unresolved")
                break
            tr, status = _resolve_target(t, cwd)
            if status == "unresolved":
                seg_unresolved = True
                reasons.append(f"重定向目标含通配：{t}")
                all_forms.append("redirect:unresolved")
                break
            if tr is None:
                continue
            seg_targets.append(tr)
            seg_forms.append(f"redirect:{op}")
            if status == "partial":
                seg_conf = "partial"
        if seg_unresolved:
            kinds.append("unresolved")
            all_conf.append("opaque")
            continue

        # 2) 写形态命令（tee/sed -i/truncate/cp/mv/install/dd）。
        kind, targets, forms, conf, note = _classify_segment(tokens, cwd)
        if kind == "patch":
            kinds.append("patch")
            all_conf.append("opaque")
            reasons.append(note)
            continue
        if kind == "unresolved":
            kinds.append("unresolved")
            all_conf.append("opaque")
            reasons.append(note)
            all_forms.extend(forms)
            continue
        if kind == "write":
            for t in targets:
                if _has_unbalanced_paren(t):
                    seg_unresolved = True
                    reasons.append(f"写目标含未闭合括号：{t}")
                    all_forms.append("write:unresolved")
                    break
                tr, status = _resolve_target(t, cwd)
                if status == "unresolved":
                    seg_unresolved = True
                    reasons.append(f"写目标含通配：{t}")
                    all_forms.append("write:unresolved")
                    break
                if tr is None:
                    continue
                seg_targets.append(tr)
            if seg_unresolved:
                kinds.append("unresolved")
                all_conf.append("opaque")
                continue
            seg_forms.extend(forms)
            if conf == "partial":
                seg_conf = "partial"

        if seg_targets:
            kinds.append("write")
            all_targets.extend(seg_targets)
            all_forms.extend(seg_forms)
            all_conf.append(seg_conf)
            reasons.append(note)
        else:
            kinds.append(kind)
            all_conf.append("static")
            reasons.append(note)

    # 整条聚合（失败方向安全）。
    if "patch" in kinds:
        final_kind = "patch"
    elif "unresolved" in kinds:
        final_kind = "unresolved"
    elif "write" in kinds:
        final_kind = "write"
    elif "read" in kinds:
        final_kind = "read"
    else:
        final_kind = "other"

    if final_kind == "write":
        seen: list[str] = []
        targets: list[str] = []
        for t in all_targets:
            if t not in seen:
                seen.append(t)
                targets.append(t)
        gate_targets = [t for t in targets if t in gate_set]
        confidence = "partial" if "partial" in all_conf else "static"
        return {"kind": "write", "targets": targets, "gate_targets": gate_targets,
                "forms": sorted(set(all_forms)), "confidence": confidence,
                "class": "static", "reason": "；".join(reasons)}
    if final_kind == "patch":
        return {"kind": "patch", "targets": [], "gate_targets": [], "forms": ["apply_patch"],
                "confidence": "opaque", "class": "static",
                "reason": "apply_patch 词元（命令位），交 _bash_patch"}
    if final_kind == "unresolved":
        cls = "interpreter_wrapper" if lead in WHITELIST else "opaque"
        return {"kind": "unresolved", "targets": [], "gate_targets": [],
                "forms": sorted(set(all_forms)), "confidence": "opaque",
                "class": cls, "reason": "；".join(reasons)}
    return {"kind": final_kind, "targets": [], "gate_targets": [],
            "forms": sorted(set(all_forms)), "confidence": "static",
            "class": "static", "reason": "；".join(reasons)}


# ---------------------------------------------------------------- PowerShell（pwsh）
# 依据：dsh 在 Windows 上提供 `pwsh` 工具；其写形态与 POSIX 不同（Set-Content / Out-File /
# Tee-Object / New-Item -Value / [IO.File]::WriteAllText / `>` 均可写文件）。
# 实测（Windows PowerShell 5.1）：写形态见 tests/test_pwsh_writes.py，全部真的创建文件。

_PS_WRITE_CMDLETS = {
    "set-content": ("path", "filepath", "literalpath"),
    "add-content": ("path", "filepath", "literalpath"),
    "clear-content": ("path", "filepath", "literalpath"),
    "out-file": ("filepath", "path", "literalpath"),
    "tee-object": ("filepath", "path", "literalpath"),
    "export-csv": ("path", "literalpath"),
    "export-clixml": ("path", "literalpath"),
    "new-item": ("path", "name"),
    "copy-item": ("destination",),
    "move-item": ("destination",),
    "rename-item": ("newname",),
    "remove-item": ("path", "literalpath"),
    "set-item": ("path",),
    "set-itemproperty": ("path",),
}
_PS_ALIASES = {
    "sc": "set-content", "ac": "add-content", "ni": "new-item", "cp": "copy-item",
    "copy": "copy-item", "mv": "move-item", "move": "move-item", "ren": "rename-item",
    "rename": "rename-item", "ri": "remove-item", "rm": "remove-item", "del": "remove-item",
    "erase": "remove-item", "clc": "clear-content", "tee": "tee-object", "si": "set-item",
}
_PS_INTERP = {"invoke-expression", "iex", "start-process", "start-job", "invoke-command"}
_PS_DEST_LAST = {"copy-item", "move-item", "rename-item"}
_PS_NULL = {"$null", "nul", "/dev/null", "null"}
_PS_SEPARATORS = {";", "|", "||", "&", "&&", "(", ")", "{", "}"}
_PS_REDIR_OPS = {">", ">>", "1>", "1>>", "*>", "*>>", "2>"}
_PS_NET_WRITE = re.compile(
    r"\[(?:System\.)?IO\.File\]::(?:WriteAllText|AppendAllText|WriteAllLines|WriteAllBytes|"
    r"Create|CreateText|AppendText|OpenWrite)\s*\(\s*(?:\"([^\"]+)\"|'([^']+)')")
_PS_NEWOBJ = re.compile(
    r"New-Object\s+(?:System\.IO\.)?(?:StreamWriter|FileStream|BinaryWriter)\s*\(\s*"
    r"(?:\"([^\"]+)\"|'([^']+)')", re.I)


def _ps_tokens(command: str) -> list[tuple[str, bool, bool]]:
    """PowerShell 粗分词 → (文本, 是否被引号包裹, 是否操作符)。

    - 注释（引号外的 `#` 到行尾）整段丢弃；
    - `'...'` / `"..."` 作为**单个** token，内容去掉引号；
    - 操作符 `| ; & && || ( ) { } > >> 1> 2> *>` 单独成 token。
    """
    out: list[tuple[str, bool, bool]] = []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "#":
            while i < n and command[i] != "\n":
                i += 1
            continue
        if ch == "`" and i + 1 < n:          # 反引号转义
            out.append((command[i + 1], False, False))
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            buf = []
            while i < n:
                if command[i] == quote:
                    if quote == "'" and i + 1 < n and command[i + 1] == "'":
                        buf.append("'"); i += 2; continue
                    i += 1
                    break
                if quote == '"' and command[i] == "`" and i + 1 < n:
                    buf.append(command[i + 1]); i += 2; continue
                buf.append(command[i]); i += 1
            out.append(("".join(buf), True, False))
            continue
        if ch in "|;&(){}":
            two = command[i:i + 2]
            if two in ("||", "&&", ">>"):
                out.append((two, False, True)); i += 2; continue
            out.append((ch, False, True)); i += 1; continue
        if ch == ">":
            out.append((">>" if command[i:i + 2] == ">>" else ">", False, True))
            i += 2 if command[i:i + 2] == ">>" else 1
            continue
        j = i
        while j < n and command[j] not in " \t\r\n|;&(){}'\"#>":
            j += 1
        word = command[i:j]
        if word and word[0].isdigit() and word.endswith(">"):
            out.append((word, False, True))     # 1> 2> *>
        elif word:
            out.append((word, False, False))
        i = j
    return out


def _ps_segments(tokens):
    """按分隔符切段；括号/花括号内容各自成段（脚本块里的命令也会被检查）。"""
    seg, segs = [], []
    for tok, quoted, is_op in tokens:
        if is_op and tok in _PS_SEPARATORS:
            if seg:
                segs.append(seg); seg = []
            continue
        seg.append((tok, quoted))
    if seg:
        segs.append(seg)
    return segs


def classify_pwsh(command: str, cwd: str, gate_files: list[str],
                  shell: str = "pwsh") -> dict:
    """PowerShell 写目标识别：命中写形态则给目标；含写意图但目标不可解析 → unresolved。"""
    if not isinstance(command, str) or not command.strip():
        return {"kind": "other", "targets": [], "gate_targets": [], "forms": [],
                "confidence": "static", "reason": "empty"}

    tokens = _ps_tokens(command)
    targets: list[str] = []
    forms: list[str] = []
    unresolved_hits: list[str] = []

    # 重定向：> / >> / 1> / 2> / *>，目标取其后一个 token（允许带引号）
    for idx, (tok, _q, is_op) in enumerate(tokens):
        if not is_op or tok not in _PS_REDIR_OPS:
            continue
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if nxt is not None and nxt[2] and nxt[0] == "&":
            continue                     # 2>&1 这类流合并，不是写文件
        if nxt is None or nxt[2]:
            unresolved_hits.append("redirect-no-target")
            continue
        val = nxt[0]
        if val.lower() in _PS_NULL or tok.startswith("2"):
            continue
        targets.append(val)
        forms.append("ps-redirect:" + tok)

    for m in _PS_NET_WRITE.finditer(command):
        raw = m.group(1) or m.group(2)
        if raw:
            targets.append(raw); forms.append("ps-dotnet-write")
    for m in _PS_NEWOBJ.finditer(command):
        raw = m.group(1) or m.group(2)
        if raw:
            targets.append(raw); forms.append("ps-newobject-writer")

    write_intent = False
    for seg in _ps_segments(tokens):
        words = [w for w, _q in seg]
        first = seg[0][0]
        base = os.path.basename(first).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        base = _PS_ALIASES.get(base, base)

        if base in _PS_INTERP:
            unresolved_hits.append("interpreter:" + base)
            continue

        if base in ("cmd", "cmd.exe"):
            if len(seg) >= 3 and seg[2][1]:          # cmd /c "<整条命令>"
                sub = classify_pwsh(seg[2][0], cwd, gate_files)
                if sub["kind"] != "other":
                    targets.extend(sub["targets"]); forms.extend(sub["forms"])
                    write_intent = True
                    unresolved_hits.extend([] if sub["kind"] == "write" else ["cmd-inner-unresolved"])
                    continue
            inner = [w for w, _q in seg[1:] if w.lower() not in ("/c", "/k", "/s")]
            if inner:
                ib = inner[0].lower()
                rest = inner[1:]
                if ib in ("copy", "move", "xcopy", "robocopy") and len(rest) >= 2:
                    targets.append(rest[-1]); forms.append("cmd:" + ib); write_intent = True
                elif ib in ("ren", "rename") and len(rest) >= 2:
                    targets.append(rest[1]); forms.append("cmd:ren"); write_intent = True
                elif ib in ("del", "erase") and rest:
                    targets.append(rest[0]); forms.append("cmd:del"); write_intent = True
                elif ib in ("type", "echo") and not any(
                        t in _PS_REDIR_OPS for t, _q, op in tokens if op):
                    write_intent = write_intent or False
            continue

        if base in _PS_WRITE_CMDLETS:
            write_intent = True
            params = _PS_WRITE_CMDLETS[base]
            found = None
            for i, w in enumerate(words[1:], 1):
                token = w.lstrip("-")
                if "=" in token:
                    name, _, val = token.partition("=")
                    if name.lower() in params and val:
                        found = val
                        break
                    continue
                if token.lower() in params and i + 1 < len(words):
                    found = words[i + 1]
                    break
            if found is None and base in _PS_DEST_LAST:
                positionals = [w for w in words[1:] if not w.startswith("-")]
                if len(positionals) >= 2:
                    found = positionals[-1]
            if found is None:
                for w in words[1:]:
                    if w.startswith("-"):
                        continue
                    if any(ch in w for ch in ("\\", "/", ".")) or w.lower().endswith(
                            ("txt", "md", "json", "ps1", "log")):
                        found = w
                        break
            if found:
                targets.append(found); forms.append("ps-cmdlet:" + base)
            else:
                forms.append("ps-cmdlet-nopath:" + base)

    def absolute(token: str) -> str:
        v = os.path.expanduser(str(token).strip().strip("'\""))
        if re.match(r"^[A-Za-z]:[\\/]", v) or v.startswith("\\\\"):
            return os.path.normpath(v)
        return os.path.normpath(os.path.join(cwd, v))

    resolved, unresolved = [], []
    for token in targets:
        if any(ch in token for ch in ("$", "*", "?")) or token.startswith("&"):
            unresolved.append(token)
        else:
            resolved.append(absolute(token))

    gate_abs = {os.path.normpath(os.path.expanduser(g)) for g in gate_files}
    if resolved:
        return {"kind": "write", "targets": sorted(set(resolved)),
                "gate_targets": sorted({t for t in resolved if t in gate_abs}),
                "forms": forms, "confidence": "static", "reason": "pwsh-write"}
    if unresolved or unresolved_hits or (write_intent and not targets):
        return {"kind": "unresolved", "targets": [], "gate_targets": [],
                "forms": forms + unresolved_hits or ["ps-write-nopath"],
                "confidence": "opaque", "reason": "pwsh-target-unresolved"}
    return {"kind": "other", "targets": [], "gate_targets": [], "forms": forms,
            "confidence": "static", "reason": "pwsh-no-write"}
