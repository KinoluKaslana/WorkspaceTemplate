#!/usr/bin/env python3
"""Git 仓库身份的三段纯函数流水线：编码、格式、白名单。"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

_SCHEMES = {"http": 80, "https": 443, "ssh": 22, "git": 9418}
_INVISIBLE = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_SCP = re.compile(r"(?P<user>[^@/\\:]+)@(?P<host>[^/\\:]+):(?P<path>.+)")
# `file:///C:/x` 的 urlsplit 结果：盘符前多出一个 "/"
_FILE_DRIVE = re.compile(r"^/[A-Za-z]:/")
# Win32 设备命名空间前缀（`\\?\C:\x` / `\\.\C:\x`）
_DEVICE = re.compile(r"^\\\\[?.]\\")


def normalize_encoding(value: object) -> str | None:
    """只规范字面编码，不解释 URL 或路径语义。"""
    if not isinstance(value, str) or len(value) > 512:
        return None
    text = value
    try:
        for _ in range(5):
            before = text
            if re.search(r"%(?![0-9A-Fa-f]{2})", text):
                return None
            decoded = unquote(text, errors="strict")
            text = unicodedata.normalize("NFKC", decoded)
            if (text.count("/") > before.count("/")
                    or text.count("\\") > before.count("\\")):
                return None
            if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in text):
                return None
            if text == before:
                break
        else:
            return None
    except (UnicodeDecodeError, ValueError):
        return None
    text = text.strip().strip("".join(_INVISIBLE))
    if not text or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in text):
        return None
    if any(ch in _INVISIBLE or unicodedata.category(ch) == "Cf" for ch in text):
        return None
    # 只折叠可无损识别的 scheme/authority；path 的大小写保持不变。
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)(://)([^/]*)", text)
    if match:
        authority = match.group(3)
        at = authority.rfind("@")
        prefix, hostport = (authority[:at + 1], authority[at + 1:]) if at >= 0 else ("", authority)
        text = (match.group(1).lower() + match.group(2) + prefix + hostport.lower()
                + text[match.end():])
    return text


def _segments(path: str) -> list[str] | None:
    if "\\" in path:
        return None
    path = re.sub(r"/+", "/", path).strip("/")
    if not path:
        return None
    parts = path.split("/")
    if any(not part or part in {".", ".."} or len(part) > 255 for part in parts):
        return None
    # `C:` 这类裸盘符不能当路径段：否则 `/C:/x/y` 会被当成合法 POSIX 绝对路径
    # （Windows 上 `Path.as_posix()` 的输出被前置 "/" 时就是这个形态）。
    if any(re.fullmatch(r"[A-Za-z]:", part) for part in parts):
        return None
    if any(any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in part) for part in parts):
        return None
    if parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
        if not parts[-1] or parts[-1] in {".", ".."}:
            return None
    return parts


def _canonical_local(path: str) -> str | None:
    """已解析的本地绝对路径 → 正斜杠规范形，**保留盘符 / UNC 前缀**。

    为什么单独一层：`convert_format` 的 base 分支拿到的是 `Path.resolve()` 的结果，
    POSIX 上是 `/a/b`，Windows 上是 `C:/a/b`（`as_posix()`）。旧实现一律
    `"/" + "/".join(segments)`，在 Windows 上会产出 `/C:/a/b`：既有前导斜杠
    又保留盘符，两边都不成立（畸形值）。这里按族分开归一：
      POSIX  `/a/b`        → `/a/b`
      Drive  `C:/a/b`      → `C:/a/b`（盘符字母大写，斜杠统一）
      UNC    `//host/share/a` → `//host/share/a`
    `wsl.localhost` 这类 UNC 与 `//?/C:/...` 设备前缀不走本函数（设备前缀直接拒绝）。
    """
    text = path.replace("\\", "/")
    if not text:
        return None
    prefix, rest = "", text
    if _DRIVE.match(text):
        prefix, rest = text[0].upper() + ":", text[2:]
    elif text.startswith("//"):
        match = re.fullmatch(r"//([^/]+)/([^/]+)(/.*)?", text)
        if not match:
            return None
        host, share, tail = match.group(1), match.group(2), match.group(3) or ""
        if any(part in {"", ".", "..", "?"} for part in (host, share)):
            return None
        if any(any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in part) for part in (host, share)):
            return None
        prefix, rest = f"//{host}/{share}", tail
        if tail in {"", "/"}:
            return prefix
    parts = _segments(rest)
    if not parts:
        return None
    return f"{prefix}/{'/'.join(parts)}"


def _host(host: str) -> str | None:
    host = host.rstrip(".").lower()
    if not host or len(host) > 255 or "/" in host or "\\" in host or ":" in host:
        return None
    labels = host.split(".")
    if any(not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9-]+", label)
           or label[0] == "-" or label[-1] == "-" for label in labels):
        return None
    return host


def _windows_local(value: str, exists) -> str | None:
    """Windows 绝对路径族（`X:\\…` / `\\\\host\\share\\…` / 设备前缀）→ 规范形；不存在则 None。

    为什么必须**存在性把关**：本地路径是「派生身份」的唯一凭据（远程 URL 才是模板正主）。
    任意字符串只要长得像 `C:\\work\\repo` 就被接纳，等于把「无法证明不是模板正主」的
    输入升级成合法派生身份——R17-01 明确要求这类输入 fail-closed。所以只接纳**磁盘上
    真实存在**的路径；不存在的仍然是 None（Linux 上 `C:\\work\\repo` 永远不存在，
    因此 Ubuntu 侧行为逐条不变）。

    `exists` 是注入点（默认 `os.path.exists`）：判据是纯函数，Windows 族输入可以在
    Linux 上用 `exists=lambda p: True` 直接测，不依赖 `os.name`。
    """
    text = value
    if _DEVICE.match(text):                       # \\?\C:\x → C:\x
        text = text[4:]
    normalized = text.replace("\\", "/")
    if not (_DRIVE.match(normalized) or normalized.startswith("//")):
        return None
    if not exists(value):                         # 用**原始写法**问文件系统（反斜杠是原生的）
        return None
    return _canonical_local(text)


def convert_format(
    value: str | None, base: str | os.PathLike[str] | None = None,
    *, exists=None,
) -> str | None:
    """把规范字面串按族转换为 canonical；相对路径无 base 时 fail-closed。"""
    exists = os.path.exists if exists is None else exists
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    try:
        if _DRIVE.match(value) or value.startswith("\\\\"):
            return _windows_local(value, exists)
        if "://" not in value:
            if value.startswith("/"):
                return _canonical_local(value)
            match = _SCP.fullmatch(value)
            if match:
                host = _host(match.group("host"))
                parts = _segments(match.group("path"))
                return f"{host}/{'/'.join(parts)}" if host and parts else None
            if value in {".", ".."} or value.startswith(("./", "../")):
                return None
            if base is None:
                return None
            root = Path(base).resolve(strict=True)
            candidate = (root / value).resolve(strict=True)
            if not candidate.is_relative_to(root):
                return None
            return _canonical_local(candidate.as_posix())

        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme == "file":
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                return None
            if parsed.port is not None or (parsed.hostname or "").lower() not in {"", "localhost"}:
                return None
            if not parsed.path.startswith("/"):
                return None
            # `file:///C:/x` → urlsplit 给 `/C:/x`：盘符路径要按 Windows 族判存在性
            if _FILE_DRIVE.match(parsed.path):
                return _windows_local(parsed.path[1:], exists)
            parts = _segments(parsed.path)
            return "/" + "/".join(parts) if parts else None
        if scheme not in _SCHEMES or parsed.query or parsed.fragment or parsed.password:
            return None
        host = _host(parsed.hostname or "")
        parts = _segments(parsed.path)
        if not host or not parts or len(parsed.path) > 512:
            return None
        port = parsed.port
        if port is not None and port != _SCHEMES[scheme]:
            host = f"{host}:{port}"
        return f"{host}/{'/'.join(parts)}"
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError):
        return None


def repair(value: object, base: str | os.PathLike[str] | None = None, *,
           exists=None) -> str | None:
    """编码规范化后再转换格式；相对路径仅在显式 base 内解析。"""
    normalized = normalize_encoding(value)
    if normalized is None:
        return None
    converted = convert_format(normalized, base=base, exists=exists)
    if converted is not None:
        return converted
    # canonical remote 本身没有 scheme；只接受严格 host/path，避免接纳任意相对路径。
    if not normalized.startswith("/") and "/" in normalized:
        head, path = normalized.split("/", 1)
        host = _host(head.split(":", 1)[0])
        port = head[len(host):] if host else ""
        parts = _segments(path)
        if (host and ("." in host or host == "localhost")
                and (not port or re.fullmatch(r":\d{1,5}", port)) and parts):
            return f"{host}{port}/{'/'.join(parts)}"
    return None


def classify(
    value: object,
    template_source: object,
    base: str | os.PathLike[str] | None = None,
    *,
    exists=None,
) -> str | None:
    """模板同源、可证明合法派生（含本地路径）、或不可判；base 只锚定相对路径。"""
    canonical = repair(value, base=base, exists=exists)
    template = repair(template_source, base=base, exists=exists)
    if canonical is None or template is None:
        return None
    if canonical == template:
        return "template"
    return "derived"
