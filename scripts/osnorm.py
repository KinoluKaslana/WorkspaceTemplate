#!/usr/bin/env python3
"""osnorm — 文件系统路径的交换格式与文本 I/O 的单一实现（纯 stdlib 叶子模块）。

管：路径词法 canonical、路径族判定、文本/字节 I/O（含并发追加）、子进程文本解码、原子发布、stdio 重配。
不管：业务校验、git 语义、网络、锁与事务、身份派生、扫描范围、命令语义、权限与 ACL。

设计约束：
- **叶子模块**：只 import 标准库，不 import 本仓任何其他模块（避免 `__main__` 与包布局依赖）。
- **import 期无全局副作用**：本模块顶层只有常量与函数定义；`configure_stdio()` 必须由
  调用方显式执行。`_REPLACE` 只为可测性注入（测试换成会抛 PermissionError 的假实现），
  不改任何全局状态。
- **不折叠大小写**（D1）：`canonical_path` 保留路径段字面大小写，只把盘符大写；
  不提供 `path_key`/`rel_key` 之类的键（键口径仍是「工作区相对 POSIX 路径」）。
- **折叠只有一份**：路径写法的裸折叠只在变换层 `collapse_path`（不抛、不知道平台）；
  `canonical_path` 只做判定 / 平台语义 / 组装 / 逃逸，内部不再自己折叠。
- **不猜编码**：一律 UTF-8；UTF-16/GBK 在 `strict` 下上抛，`ignore/replace` 只在调用点显式声明。
- **BOM 一处决定**（§3.1）：`BOM_POLICY` 决定 `read_text(bom=None)` 是否剥 BOM；写入侧永不产生 BOM，
  清理入口是 `scripts/encoding_guard.py`。

`.workspace/hooks/*.py` **不得裸 import** 本模块（`python -I` 会把脚本目录移出 `sys.path`，
实例也可能暂时没有 `scripts/`）。hooks 一律经 `.workspace/hooks/osnorm_loader.py` 装载本模块：
装载器按候选顺序（hook 自身树 → 请求期 `ws_root` → fail-closed）解析，钉住进程内唯一版本
（路径 + size + mtime_ns + sha256），并校验 `OSNORM_API`；任何一步不满足都按「不可用」拒绝。
本模块保留 `OSNORM_API` 常量作为装载器的版本判据；`R1–R5` 的结构性豁免仍只针对本模块自身。
"""
from __future__ import annotations

import errno
import os
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from urllib.parse import unquote, urlsplit

# 版本契约（N2）：hooks 装载器在 `exec_module` 之后、**任何 API 调用之前**校验本常量。
# 语义变化（不是「加函数」）才允许 +1；装载器只认它支持的值。
#
# 2：路径词法拆成「变换层 collapse_path + 判定层 canonical_path」两层——`..` 折叠只剩
#    一份实现（此前盘符族/UNC 族/POSIX 族各写一套、语义不一致）。canonical_path 的
#    外部输出逐字不变，但可用的折叠原语变了，装载器必须与实现同版本。
# 3：hooks 通过 read_stdin_text / emit_stdout / emit_stderr 处理每次 stdio 读写。
OSNORM_API = 3

__all__ = [
    "BOM_POLICY",
    "OSNORM_API",
    "PathFormError",
    "append_text",
    "atomic_write_text",
    "canonical_path",
    "collapse_path",
    "configure_stdio",
    "emit_stderr",
    "emit_stdout",
    "family",
    "read_bytes",
    "read_stdin_text",
    "read_text",
    "replace_retry",
    "run_text",
    "utf8_env",
    "write_bytes",
    "write_text",
]

# 原子发布的目标函数；测试注入点（替换成会抛 PermissionError 的假实现来验退避与耗尽）。
_REPLACE = os.replace

# Win32 保留设备名（D7：命中即拒绝，不盲剥前缀当普通文件处理）。
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

_DRIVE = re.compile(r"^([A-Za-z]):(.*)$", re.DOTALL)
_MSYS_DRIVE = re.compile(r"^/[A-Za-z]:(/|$)")
_WSL_MOUNT = re.compile(r"^/mnt/[A-Za-z](/|$)")
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")


class PathFormError(ValueError):
    """路径词法形式不可接受（歧义、逃逸、设备名、非法 URL 等）。"""


# ---------------------------------------------------------------- 路径族判定


def _text(p: object) -> str | None:
    """把入参收敛成 str；非路径类型返回 None（不抛，供 family 判 invalid）。"""
    if isinstance(p, str):
        return p
    if isinstance(p, bytes):
        return None
    if isinstance(p, os.PathLike):
        try:
            return os.fspath(p)
        except TypeError:  # pragma: no cover - 畸形 PathLike
            return None
    return None


def _segments(s: str) -> list[str]:
    """按两种分隔符切段，丢掉空段（盘符/UNC 前缀由调用方先剥掉）。"""
    return [seg for seg in s.replace("\\", "/").split("/") if seg]


def _is_reserved(seg: str) -> bool:
    """Windows 保留设备名判定：比第一个 `.` 之前的 stem，大小写无关。

    `NUL` 与 `NUL.txt` 在 Win32 上都是设备，所以按 stem 判定而不是整段判定。
    """
    return seg.split(".", 1)[0].upper() in _RESERVED_NAMES


def _reserved_hit(segments: list[str]) -> str | None:
    for seg in segments:
        if _is_reserved(seg):
            return seg
    return None


def _strip_device_prefix(s: str) -> tuple[str, str | None]:
    r"""剥 `\\?\` / `//?/` 前缀，并把 `\\.\` 与 `\\?\GLOBALROOT` 判错（D7 第三方案）。

    返回 (剩余串, 错误标签)；错误标签非 None 时调用方必须拒绝，不得盲剥。
    """
    if s.startswith(("\\\\", "//")):
        q = s.replace("\\", "/")
        if q.startswith("//?/"):
            rest = q[4:]
            if re.match(r"^[A-Za-z]:", rest):
                return rest, None
            if rest.upper() == "UNC" or rest.upper().startswith("UNC/"):
                return "//" + rest[3:].lstrip("/"), None
            return rest, "设备命名空间 \\\\?\\GLOBALROOT（不是文件路径）"
        if q.startswith("//./"):
            return q[4:], "设备命名空间 \\\\.\\（不是文件路径）"
    return s, None


def family(p: str | os.PathLike[str]) -> str:
    """路径族：windows-drive|unc|wsl-mount|posix|relative|file-url|empty|invalid。

    只做分类，不归一化、不抛异常。`invalid` 覆盖：非字符串、含 NUL、保留设备名、
    无根的盘符路径（`C:relative`/裸 `C:`）、缺 share 的 UNC、非法设备前缀。
    """
    text = _text(p)
    if text is None:
        return "invalid"
    if "\x00" in text:
        return "invalid"
    s = text.strip()
    if not s:
        return "empty"
    if _SCHEME.match(s):
        return "file-url" if s[:5].lower() == "file:" else "invalid"

    stripped, err = _strip_device_prefix(s)
    if err:
        return "invalid"
    if stripped is not s:
        inner = family(stripped)
        return inner if inner in {"windows-drive", "unc"} else "invalid"

    if s.startswith(("\\\\", "//")):
        parts = _segments(s)
        if len(parts) < 2:
            return "invalid"  # //host 缺 share
        return "invalid" if _reserved_hit(parts) else "unc"

    match = _DRIVE.match(s)
    if match:
        rest = match.group(2)
        if not rest.startswith(("/", "\\")):
            return "invalid"  # 盘符相对路径 / 裸 C:
        return "invalid" if _reserved_hit(_segments(rest)) else "windows-drive"

    if s.startswith("/"):
        if _MSYS_DRIVE.match(s):
            return "invalid" if _reserved_hit(_segments(s[1:][2:])) else "windows-drive"
        if _reserved_hit(_segments(s)):
            return "invalid"
        return "wsl-mount" if _WSL_MOUNT.match(s) else "posix"

    return "invalid" if _reserved_hit(_segments(s)) else "relative"


# ---------------------------------------------------------------- 路径词法：变换层 + 判定层
#
#   变换层  collapse_path(p) -> str      纯词法折叠：单入单出、不抛、不知道平台
#   判定层  canonical_path(v, *, base)   形态判定 + 平台语义 + 组装 + 逃逸；
#                                        其内部**不再做任何折叠**，一律委托上面这一层


def collapse_path(p: str) -> str:
    """按 '/' 分段折叠路径**写法**（单入单出、纯词法、不抛、不查文件系统）。

    - 消掉 '' 与 '.' 段；连续分隔符合一；去掉尾部斜杠（根 '/' 除外）
    - '..' 折叠：可消则消（与上一段配对）；不可消时
        绝对形态 → 丢弃（折到根即停）
        相对形态 → 保留（结果可含前导 '..'）
    - 不抛异常、不做任何越界判断、不查文件系统
    - 不知道盘符 / UNC / 设备前缀 / 保留名 —— 那些属于判定层
    - 输入 '' 返回 ''；输入 '.' 返回 '.'

    `'\\'` 在这层是**普通字符**，不是分隔符：反斜杠是 Windows 语义，由判定层按路径族
    决定要不要先换成 '/'。折叠只该有这一份实现——`canonical_path` 的各族薄层一律调用它。
    """
    if p == "":
        return ""
    absolute = p.startswith("/")
    out: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out and out[-1] != "..":
                out.pop()          # 与上一段配对消掉
            elif not absolute:
                out.append("..")   # 相对形态：保留前导 '..'
            continue               # 绝对形态：折到根即停（丢弃）
        out.append(seg)
    body = "/".join(out)
    return "/" + body if absolute else (body or ".")


def collapse_host_path(p: str, *, path_module=None) -> str:
    """Fold a host path, preserving its original volume spelling for I/O."""
    host = os.path if path_module is None else path_module
    if host.sep == "/":
        # POSIX allows a distinct root for exactly two leading slashes.
        prefix = "/" if p.startswith("//") and not p.startswith("///") else ""
        return prefix + (collapse_path(p) or ".")
    volume, tail = host.splitdrive(p.replace(host.sep, "/"))
    if not tail and volume:
        return volume
    return volume + (collapse_path(tail) or ".")


def exchange_path(p: str, *, path_module=None) -> str:
    """Lexical comparison form for host paths, preserving literal path segments."""
    host = os.path if path_module is None else path_module
    if host.sep == "/":
        return collapse_host_path(p, path_module=host)
    text = p.replace(host.sep, "/")
    unprefixed, error = _strip_device_prefix(text)
    if error is None:
        text = unprefixed
    folded = collapse_host_path(text, path_module=host)
    volume, tail = host.splitdrive(folded)
    if _DRIVE.match(volume):
        return volume.upper() + ("" if tail == "." else tail)
    if volume.startswith("//") and tail == "/":
        return volume
    return folded


def host_relative_key(p: str, base: str, *, path_module=None) -> str | None:
    """Return an exchange relative key, or None when the volumes differ."""
    host = os.path if path_module is None else path_module
    cwd = os.getcwd()
    target = exchange_path(host.join(cwd, p), path_module=host)
    root = exchange_path(host.join(cwd, base), path_module=host)
    volume, tail = host.splitdrive(target)
    base_volume, base_tail = host.splitdrive(root)
    if host.normcase(volume) != host.normcase(base_volume):
        return None
    if host.sep != "/" and not all(host.isabs(q) for q in (target, root)):
        return None
    return posixpath.relpath(tail or "/", base_tail or "/")


# ---------------------------------------------------------------- 判定层：形态判定 / 平台语义 / 组装 / 逃逸


def _norm_posix_abs(s: str) -> str:
    """POSIX 绝对路径：`\\` 在这族里是文件名字符，折叠整段委托 `collapse_path`。"""
    return collapse_path(s)


def _norm_drive(s: str) -> str:
    q = s.replace("\\", "/")
    if _MSYS_DRIVE.match(q):  # /c:/a/b → c:/a/b
        q = q[1:]
    match = _DRIVE.match(q)
    if not match:  # pragma: no cover - family 已保证形态
        raise PathFormError(f"不是盘符绝对路径：{s!r}")
    drive, rest = match.group(1).upper(), match.group(2)
    if not rest.startswith("/"):
        raise PathFormError(f"盘符相对路径语义歧义：{s!r}")
    segs = _segments(rest)
    hit = _reserved_hit(segs)
    if hit:
        raise PathFormError(f"保留设备名 {hit!r}，不是文件路径：{s!r}")
    normed = collapse_path(rest)       # rest 已带前导 '/'（绝对形态：越界折到盘符根）
    return f"{drive}:/" if normed == "/" else f"{drive}:{normed}"


def _norm_unc(s: str) -> str:
    parts = s.replace("\\", "/")[2:].split("/")
    host = parts[0] if parts else ""
    share = parts[1] if len(parts) > 1 else ""
    if not host or not share:
        raise PathFormError(f"UNC 路径缺少 host/share：{s!r}")
    hit = _reserved_hit([host, share])
    if hit:
        raise PathFormError(f"保留设备名 {hit!r}，不是文件路径：{s!r}")
    tail = parts[2:]
    # share 是 UNC 的根：'..' 折到 share 即越界 —— 这条判定只在这里出现一次（不交给折叠层）。
    depth = 0
    for seg in tail:
        if seg in ("", "."):
            continue
        if seg == "..":
            if depth == 0:
                raise PathFormError(f"UNC 路径逃出其 share：{s!r}")
            depth -= 1
            continue
        if _is_reserved(seg):
            raise PathFormError(f"保留设备名 {seg!r}，不是文件路径：{s!r}")
        depth += 1
    # share 之下的余下部分是**相对形态**（前导空段属于分隔符，不是根），折叠交给 collapse_path。
    rest = collapse_path("/".join(tail).lstrip("/"))
    if rest == ".":
        rest = ""
    return f"//{host}/{share}" + ("/" + rest if rest else "")


def _from_file_url(s: str) -> str:
    parsed = urlsplit(s)
    if parsed.scheme.lower() != "file":  # pragma: no cover - family 已保证
        raise PathFormError(f"不是 file URL：{s!r}")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise PathFormError(f"file URL 不得带 query/fragment/userinfo：{s!r}")
    path = unquote(parsed.path)
    if not path:
        raise PathFormError(f"file URL 没有路径部分：{s!r}")
    host = parsed.netloc
    if host in ("", "localhost"):
        return canonical_path(path)
    return _norm_unc(f"//{host}{path}")


def _join_relative(base_canon: str, rel: str) -> str:
    """拼接到 canonical base 上：组装（`posixpath.join` 的「绝对 body 覆盖 base」）+ 折叠。

    折叠委托 `collapse_path`；**越界判定在这里唯一出现一次**（判定层），折叠层不知道 base。
    """
    windows_like = base_canon.startswith("//") or bool(_DRIVE.match(base_canon))
    body = rel.replace("\\", "/") if windows_like else rel
    joined = posixpath.join(base_canon, body)     # 组装：不折叠
    if base_canon.startswith("//") and joined.startswith("//"):
        # UNC 的 `//host/share` 是根：`//` 前缀不能被折叠吃掉（折叠层只认 '/' 分段）。
        # 余下部分按**绝对形态**折叠（折到根即停，与 POSIX normpath 的 `//` 语义一致），
        # 再把 `//` 前缀接回去。
        normed = "/" + collapse_path("/" + joined[2:])
    else:
        normed = collapse_path(joined)
    if base_canon.endswith("/"):
        inside = normed.startswith(base_canon)
    else:
        inside = normed == base_canon or normed.startswith(base_canon + "/")
    if not inside:
        raise PathFormError(f"相对路径逃出 base：{rel!r}（base={base_canon!r}）")
    return normed


def canonical_path(value: str | os.PathLike[str], *,
                   base: str | os.PathLike[str] | None = None) -> str:
    """路径词法 canonical（交换格式恒用 `/`，盘符大写，段字面大小写不动）。

    判据见规格 §4 判定表；本函数是那张表的唯一实现。相对路径必须有 `base`，
    且不得逃出 `base`——**没有 cwd 隐式回退**。
    """
    text = _text(value)
    if text is None:
        raise PathFormError(f"不是路径类型：{type(value).__name__}")
    if "\x00" in text:
        raise PathFormError("路径含 NUL 字节")
    s = text.strip()
    if not s:
        raise PathFormError("空路径")
    s, device_err = _strip_device_prefix(s)
    if device_err:
        raise PathFormError(f"{device_err}：{value!r}")
    fam = family(s)
    if fam == "invalid":
        raise PathFormError(f"不可接受的路径形式：{value!r}")
    if fam == "file-url":
        return _from_file_url(s)
    if fam == "unc":
        return _norm_unc(s)
    if fam == "windows-drive":
        return _norm_drive(s)
    if fam in ("posix", "wsl-mount"):
        return _norm_posix_abs(s)
    if fam == "relative":
        if base is None:
            raise PathFormError(f"相对路径缺少 base（不做 cwd 隐式回退）：{value!r}")
        base_canon = canonical_path(base)
        if family(base_canon) == "relative":  # pragma: no cover - 递归后不可能
            raise PathFormError(f"base 不是绝对路径：{base!r}")
        return _join_relative(base_canon, s)
    raise PathFormError(f"空路径：{value!r}")  # fam == "empty"


# ---------------------------------------------------------------- 文本 / 字节 I/O

# BOM 策略（规格 §3.1）：**一处决定**，不在调用点散落。
#   tolerate（默认）：`read_text` 未显式指定时剥 BOM。用户可编辑的文件（bootstrap.json /
#     WORKSPACE.md / *.custom.md）常被 Windows 记事本存成 UTF-8 with BOM，不该因此让工作区
#     被判成 unknown。
#   strict：保留首字符 `\ufeff`，只用于「必须字节一致」的判据；哈希类判据一律走 read_bytes，
#     不受本策略影响。
# 写入侧永不产生 BOM（`write_text` 固定 UTF-8 无 BOM），所以 BOM 只可能来自外部工具。
# 运行期读取（不在 import 期固化）：测试与本轮之外的调用方都能按需切换。
BOM_POLICY = "tolerate"
_BOM_POLICIES = ("tolerate", "strict")


def _strip_bom(bom: bool | None) -> bool:
    """解析 `read_text(bom=...)`：`True/False` 是显式覆盖，`None` 取模块级策略。"""
    if bom is not None:
        return bool(bom)
    if BOM_POLICY not in _BOM_POLICIES:
        raise ValueError(f"BOM_POLICY 只接受 'tolerate'/'strict'：{BOM_POLICY!r}")
    return BOM_POLICY == "tolerate"


def _newline_check(newline: str) -> str:
    if newline not in ("lf", "preserve"):
        raise ValueError(f"newline 只接受 'lf' / 'preserve'：{newline!r}")
    return newline


def read_bytes(path: str | os.PathLike[str]) -> bytes:
    with open(os.fspath(path), "rb") as fh:
        return fh.read()


def write_bytes(path: str | os.PathLike[str], data: bytes, *,
                atomic: bool = True) -> None:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"write_bytes 只接受 bytes：{type(data).__name__}")
    if atomic:
        atomic_write_bytes(path, bytes(data))
        return
    with open(os.fspath(path), "wb") as fh:
        fh.write(bytes(data))


def read_text(path: str | os.PathLike[str], *, bom: bool | None = None, errors: str = "strict",
              newline: str = "lf") -> str:
    """读 UTF-8 文本。`bom=None`（默认）取模块级 `BOM_POLICY`；`True/False` 是显式覆盖。

    `tolerate` 用 `utf-8-sig` 剥 BOM；`strict`（或显式 `bom=False`）把 `\\ufeff` 留给调用方。
    `newline="lf"`（默认）与写侧契约对称：磁盘 CRLF/CR 归一为 LF；`"preserve"` 原样。
    """
    _newline_check(newline)
    text = read_bytes(path).decode("utf-8-sig" if _strip_bom(bom) else "utf-8", errors)
    if newline == "lf":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def write_text(path: str | os.PathLike[str], text: str, *, atomic: bool = True,
               newline: str = "lf") -> None:
    """写 UTF-8 文本。`newline="lf"`（默认）先把 CRLF/CR 归一为 LF，磁盘恒为 LF。"""
    if not isinstance(text, str):
        raise TypeError(f"write_text 只接受 str：{type(text).__name__}")
    _newline_check(newline)
    if newline == "lf":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    data = text.encode("utf-8")
    if atomic:
        atomic_write_bytes(path, data)
        return
    with open(os.fspath(path), "wb") as fh:
        fh.write(data)


def append_text(path: str | os.PathLike[str], text: str, *, newline: str = "lf") -> None:
    """追加 UTF-8 文本（`O_APPEND` 单次续写，**不重写整个文件**）。

    为什么不能拿 `write_text` 代替：日志与账本（`gate.log`、`audit-*.jsonl`、
    `commits.jsonl`、委托账本）是**多进程并发追加**的；原子发布是「重建整文件」，
    会把别的进程刚写的行覆盖掉。`O_APPEND` 让内核把一次写挂到文件尾，语义与
    `open(path, "a", encoding="utf-8")` 对齐，但编码只在唯一实现里决定。
    """
    if not isinstance(text, str):
        raise TypeError(f"append_text 只接受 str：{type(text).__name__}")
    _newline_check(newline)
    if newline == "lf":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    data = text.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(os.fspath(path), flags, 0o666)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


# ---------------------------------------------------------------- 原子发布


def _transient(exc: BaseException) -> bool:
    """共享冲突类失败可重试：POSIX PermissionError / Win32 5,32,33。"""
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in (5, 32, 33)


def replace_retry(src: str | os.PathLike[str], dst: str | os.PathLike[str], *,
                  budget: float = 2.0) -> None:
    """原子替换 + 有界退避重试（Windows 上读侧持有句柄时 `os.replace` 会失败）。

    `budget` 是**墙钟总预算**：耗尽后把最后一次异常原样抛出，绝不退化成非原子写。
    """
    if budget < 0:
        raise ValueError(f"budget 不得为负：{budget}")
    deadline = time.monotonic() + budget
    delay = 0.01
    while True:
        try:
            _REPLACE(os.fspath(src), os.fspath(dst))
            return
        except OSError as exc:
            if not _transient(exc):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def _fsync_dir(directory: str) -> None:
    """目录 fsync：Windows 不支持，尽力而为（不得因此失败）。"""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - 个别文件系统不支持目录 fsync
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes, *,
                       mode: int | None = None, durable: bool = True) -> None:
    """同目录临时文件 → fsync → `replace_retry` → 目录 fsync。

    读者永远看不到半截文件；失败时清理临时文件并上抛，不留垃圾。

    两条「与 `open(..., "w")` 等价」的硬约束（否则原子发布会在暗处改变语义）：
    - **只读目标仍然写不动**：rename 换的是目录项，绕过文件自己的写保护；不先查，
      「只读的治理文件」就会变成一句空话（被静默覆盖）。
    - **既有文件的权限位随内容一起保留**：`mkstemp` 天生 0600，直接 rename 会把 0644
      的治理文件悄悄变成 0600。
    """
    target = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(target) or "."
    if os.path.exists(target) and not os.access(target, os.W_OK):
        raise PermissionError(errno.EACCES, "目标文件只读，拒绝原子覆盖", target)
    if mode is None and os.path.isfile(target):
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:  # pragma: no cover - 竞态下目标消失：按 mkstemp 默认处理
            mode = None
    fd, tmp = tempfile.mkstemp(dir=parent, prefix="." + os.path.basename(target) + ".",
                               suffix=".osnorm.tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        replace_retry(tmp, target)
        if durable:
            _fsync_dir(parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | os.PathLike[str], text: str, *, mode: int | None = None,
                      durable: bool = True) -> None:
    """原子写文本（原样，不做换行归一——换行契约属于 `write_text`，见 §3）。"""
    if not isinstance(text, str):
        raise TypeError(f"atomic_write_text 只接受 str：{type(text).__name__}")
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode, durable=durable)


# ---------------------------------------------------------------- 子进程 / stdio


def utf8_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """子进程 UTF-8 环境：`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（覆盖 base 同名键）。"""
    out: dict[str, str] = dict(base) if base else {}
    out["PYTHONUTF8"] = "1"
    out["PYTHONIOENCODING"] = "utf-8"
    return out


def run_text(argv: Sequence[str], *, cwd: str | os.PathLike[str] | None = None,
             env: Mapping[str, str] | None = None, input: str | None = None,
             timeout: float | None = None, check: bool = False,
             errors: str = "replace", **forbidden: object) -> subprocess.CompletedProcess:
    """跑子进程并**恒以文本返回**：`proc.stdout` / `proc.stderr` 永远是 str，不是 None。

    编码三键（`text` / `encoding` / `universal_newlines`）由本函数固定，调用方传即
    `TypeError`；同理不接受 `capture_output` / `stdout` / `stderr`（恒捕获）。
    env 合并顺序：`os.environ` → `utf8_env()` → 调用方 `env`（调用方优先）。
    需要流式/交互的子进程不在本函数范围内（调用方自行 Popen，并须进 R9 allowlist 登记）。
    """
    if forbidden:
        raise TypeError("run_text 固定了编码与捕获参数，不接受：" + "、".join(sorted(forbidden)))
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv 必须是参数序列，不是字符串")
    merged = dict(os.environ)
    merged.update(utf8_env())
    if env:
        merged.update({str(k): str(v) for k, v in dict(env).items()})
    return subprocess.run(list(argv), cwd=cwd, env=merged, input=input, timeout=timeout,
                          check=check, capture_output=True, text=True,
                          encoding="utf-8", errors=errors)


def read_stdin_text(*, encoding: str = "utf-8", errors: str = "strict") -> str:
    """读至 EOF。管道直接解码字节；TTY 沿用流的编码并替换无法解码的字符。

    BOM 原样交给调用方。
    """
    return _read_stdin_text(encoding=encoding, errors=errors)


def _read_stdin_text(buffer=None, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    """读取 stdin，或重放已用于定位候选模块的原始字节。"""
    stream = sys.stdin
    if stream.isatty():
        encoding = stream.encoding or encoding
        errors = "replace"
    if buffer is None:
        buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return stream.read()
    return buffer.read().decode(encoding, errors)


def _emit_stdio(stream, text: str) -> None:
    if stream is None:
        return
    buffer = getattr(stream, "buffer", None)
    if stream.isatty():
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and stream.errors != "replace":
            reconfigure(errors="replace")
    elif buffer is not None:
        # 与此前通过同一文本流写入的输出保持顺序。
        stream.flush()
        buffer.write(text.encode("utf-8", errors="replace"))
        buffer.flush()
        return
    stream.write(text)
    stream.flush()


def emit_stdout(text: str) -> None:
    """向 stdout 写入文本并刷新。换行由调用方提供。"""
    _emit_stdio(sys.stdout, text)


def emit_stderr(text: str) -> None:
    """向 stderr 写入文本并刷新。换行由调用方提供。"""
    _emit_stdio(sys.stderr, text)


def configure_stdio() -> None:
    """把 stdout/stderr 的**非 TTY** 分支钉成 UTF-8（TTY 分支只防崩、保留本机编码）。

    幂等：重复调用无害，不改任何模块级状态，**import 期不自动执行**。管道/重定向场景
    父进程统一按 UTF-8 解码子进程输出；子进程若按 Windows locale（cp936/cp1252）写管道，
    中文会被就地替换或解成乱码——这里就是那条分界。

    幂等落在**编码**上：已经是 UTF-8 的流一律不碰（连 `errors` 都不改）。缺了这条，
    「重复调用无害」只是「再 reconfigure 一次也一样」，对已经按 UTF-8 打开的流仍是多余的
    状态改动，也让 `scripts/*` 的旧私有实现在行为上与本函数不等价。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            is_tty = bool(stream.isatty())
        except (AttributeError, ValueError, OSError):
            is_tty = False
        try:
            if is_tty:
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 输出层不许抛（任何流实现都可能是残缺的）
            continue
