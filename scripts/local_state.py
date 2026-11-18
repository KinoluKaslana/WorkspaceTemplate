#!/usr/bin/env python3
"""本机状态按执行上下文分片（树内，不随仓库分发）。

状态根：`<workspace>/.workspace/.local/v1/<context_id>/runtime.json`
  context_id   = HMAC-SHA256(machine_material, "<workspace_id>|<realm>|<principal>")
  root_binding = HMAC-SHA256(context_id, realpath(workspace))

不变式：
- 只读判定（`read_status` / `read_state`）不创建、不修复任何文件；
- 只认精确的 `runtime.json`，同步冲突副本与别人的 context 目录一律不采信；
- 状态文件不含明文绝对路径；工作区移动 → `UNBOUND/root-mismatch`；
- 写入：新 context 写临时目录后整体 rename（读者看不到空目录），
  已有 context 写 `mkstemp + fsync + os.replace`，两者都 fsync 文件，父目录尽力 fsync；
- 锁在 OS 临时目录按 uid 分片，不进状态树（不会被同步到别的机器）；
- 路径链上任何一段是 symlink 都拒绝；`runtime.json` 用 O_NOFOLLOW 打开。

`mode` 的持久值与 `bootstrap.python.mode` 一致：`uv` / `local-venv` / `mcp`。
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator, Mapping

STATE_VERSION = "v1"
_RUNTIME_FILE = "runtime.json"
_REQUIRED_FIELDS = ("schema", "workspace_id", "context_id", "realm", "root_binding",
                    "mode", "venv_relpath", "version", "bound_at")
_VALID_MODES = frozenset({"uv", "local-venv", "mcp"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

R_NO_LOCAL = "no-local-state"
R_NO_SELF = "no-self-state"
R_ROOT_MISMATCH = "root-mismatch"
R_WORKSPACE_MISMATCH = "workspace-mismatch"
R_CONTEXT_MISMATCH = "context-mismatch"
R_REALM_MISMATCH = "realm-mismatch"
R_IDENTITY_UNAVAILABLE = "identity-unavailable"
R_BOUND = "ok"
R_BAD_JSON = "bad-json"
R_MISSING_FIELDS = "missing-fields"
R_UNKNOWN_SCHEMA = "unknown-schema"
R_MISSING_RUNTIME = "missing-runtime"
R_INVALID_FIELDS = "invalid-fields"
R_UNSAFE_PATH = "unsafe-path"


@dataclass(frozen=True)
class Identity:
    machine_material: bytes       # 只在内存中使用，绝不落盘
    principal: str                # POSIX uid / Windows SID
    realm: str                    # linux | windows | macos | wsl | container:<rt> | ci:<provider>


def _result(status: str, reason: str, cid: str = "", data: dict | None = None) -> dict:
    return {"status": status, "reason": reason, "context_id": cid, "data": data}


def context_id(workspace_id: str, ident: Identity) -> str:
    message = f"{workspace_id}|{ident.realm}|{ident.principal}".encode("utf-8")
    return hmac.new(ident.machine_material, message, hashlib.sha256).hexdigest()


def state_root(workspace: Path) -> Path:
    return Path(workspace) / ".workspace" / ".local" / STATE_VERSION


def context_dir(workspace: Path, workspace_id: str, ident: Identity, *,
                ephemeral: bool) -> Path:
    _require_bool(ephemeral, "ephemeral")
    suffix = "-eph" if ephemeral else ""
    return state_root(workspace) / f"{context_id(workspace_id, ident)}{suffix}"


def _require_bool(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be bool, got {type(value).__name__}")


def workspace_id_of(workspace: Path) -> str | None:
    """只读：bootstrap 里的 workspace_id（不创建）。"""
    try:
        data = json.loads((Path(workspace) / ".workspace" / "bootstrap.json")
                          .read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    wid = data.get("workspace_id")
    return wid if isinstance(wid, str) and _UUID_RE.match(wid) else None


def ensure_workspace_id(workspace: Path) -> str | None:
    """bootstrap 的 `workspace_id`：缺失则生成 UUID4 并原子写回（幂等、进程间串行）。

    为什么放 bootstrap：它是**工作区身份**，必须随工作区走（改路径不变、换机器不变），
    而 `.local` 里的东西是机器的。上下文分片、gate 结论都以它为输入之一。
    """
    boot = Path(workspace) / ".workspace" / "bootstrap.json"
    if not boot.is_file():
        return None

    def _read_id() -> str | None:
        try:
            data = json.loads(boot.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        wid = data.get("workspace_id")
        return wid if isinstance(wid, str) and _UUID_RE.match(wid) else None

    wid = _read_id()
    if wid:
        return wid
    lock_key = "bootstrap-" + hashlib.sha256(os.fsencode(os.path.realpath(os.fspath(workspace)))).hexdigest()[:16]
    with _locked(lock_key):
        wid = _read_id()
        if wid:
            return wid
        try:
            data = json.loads(boot.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        wid = str(uuid.uuid4())
        data["workspace_id"] = wid
        tmp = boot.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        _replace_with_retry(tmp, boot)
    return wid


def _root_binding(workspace: Path, cid: str) -> str:
    real = os.fsencode(os.path.realpath(os.fspath(workspace)))
    return hmac.new(cid.encode("ascii"), real, hashlib.sha256).hexdigest()


def _venv_relpath_problem(value: str) -> str | None:
    """返回拒绝理由；None 表示合法（无 drive、无 root、无 ..、非空）。"""
    if not isinstance(value, str):
        return "not-a-string"
    if not value or value != value.strip():
        return "empty-or-padded"
    if value in (".", ".."):
        return "dot"
    if ".." in Path(value).parts or ".." in PureWindowsPath(value).parts:
        return "parent-traversal"
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return "absolute"
    win = PureWindowsPath(value)
    if win.drive or win.root:
        return "windows-rooted"
    if value.startswith(("/", "\\")):
        return "rooted"
    return None


def _looks_absolute(value: str) -> bool:
    """字符串本身像绝对路径（POSIX / Windows 盘符 / UNC / ~ 家目录）。"""
    if not value:
        return False
    if value.startswith(("/", "\\", "~/", "~\\")):
        return True
    win = PureWindowsPath(value)
    if win.drive or win.root:
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _reject_abs_paths(obj: object, field: str) -> None:
    """递归检查可选字段：任何字符串都不得是明文绝对路径（R8）。"""
    if isinstance(obj, str):
        if _looks_absolute(obj):
            raise ValueError(f"{field} must not contain an absolute path: {obj!r}")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_abs_paths(k, f"{field}.<key>")      # 键也可能是路径字符串
            _reject_abs_paths(v, f"{field}.{k}")
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_abs_paths(v, f"{field}[{i}]")


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _chain_is_safe(workspace: Path, target: Path) -> bool:
    """workspace 之下的每一段都不能是 symlink；解析后仍须落在 workspace 实体内。

    工作区自身（或其祖先）是 symlink 时按 realpath 锚定：只要状态落在工作区实体内即接受。
    """
    try:
        rel = Path(target).relative_to(Path(workspace))
    except (ValueError, OSError):
        return False
    cur = Path(workspace)
    for part in rel.parts:
        cur = cur / part
        if _is_symlink(cur):
            return False
    try:
        Path(target).resolve().relative_to(Path(workspace).resolve())
    except (ValueError, OSError):
        return False
    return True


def _read_json_no_follow(path: Path) -> dict:
    """读 runtime.json：拒绝跟随 symlink，且容忍 Windows 上的瞬时独占。

    为什么需要重试（Windows 专属失败模式，与写侧 `_replace_with_retry` 同源）：
    `os.replace` 走 MoveFileEx(REPLACE_EXISTING) 期间，目标文件处于 delete-pending，
    此刻 `os.open(..., O_RDONLY)` 直接拿到 `PermissionError: [Errno 13]`（= WinError 5）。
    对读者就是「文件明明存在却读不到」。窗口是微秒级，指数退避即可收敛；
    POSIX 的 rename 没有这个窗口，所以这条路径在 Linux 上永远一次成功。
    只重试分享冲突：文件真的不存在（ENOENT）立刻抛，不白等。
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

    def _once() -> dict:
        fd = os.open(path, flags)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            return json.loads(fh.read())

    return _retry_on_sharing(_once, budget=_READ_BUDGET)


def _fsync_dir(path: Path) -> None:
    """父目录 fsync：POSIX 上让 rename 落盘；Windows 不支持目录句柄，忽略。"""
    if os.name == "nt":                                   # pragma: no cover - Windows
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# 并发发布的重试预算：Windows 上一次 os.replace 失败几乎都是「读侧正持有句柄」的
# 短窗口（毫秒级），2s 足够收敛；超出就说明是真越权，必须抛。
_REPLACE_BUDGET = 2.0
_REPLACE_FIRST_DELAY = 0.01
_REPLACE_MAX_DELAY = 0.2
# 读侧用同一套预算：读写两侧对称，谁先耗尽谁抛（不允许一侧无限等另一侧）。
_READ_BUDGET = _REPLACE_BUDGET
# Windows: ERROR_ACCESS_DENIED(5) / ERROR_SHARING_VIOLATION(32) / ERROR_LOCK_VIOLATION(33)
_WIN_SHARING_ERRORS = frozenset({5, 32, 33})
_SHARING_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})


def _is_share_conflict(exc: OSError) -> bool:
    """这个 OSError 是否属于「别人持有句柄」的可重试冲突。"""
    if getattr(exc, "winerror", None) in _WIN_SHARING_ERRORS:
        return True
    return exc.errno in _SHARING_ERRNOS


def _retry_on_sharing(op, *, budget: float = _REPLACE_BUDGET,
                      clock=time.monotonic, sleep=time.sleep):
    """在分享冲突上指数退避重试 `op()`；非冲突立刻抛，重试耗尽也抛。

    读写两侧共用：写侧是 `os.replace`（MoveFileEx），读侧是 `os.open`（delete-pending
    期间的 open 同样拿 WinError 5）。语义不变——重试的是同一个原子操作，绝不改成
    非原子写或「先删再建」。
    """
    delay = _REPLACE_FIRST_DELAY
    deadline = clock() + budget
    while True:
        try:
            return op()
        except OSError as exc:
            if not _is_share_conflict(exc) or clock() >= deadline:
                raise
            sleep(min(delay, max(0.0, deadline - clock())))
            delay = min(delay * 2, _REPLACE_MAX_DELAY)


def _replace_with_retry(src: str | os.PathLike[str], dst: str | os.PathLike[str],
                        *, budget: float = _REPLACE_BUDGET,
                        clock=time.monotonic, sleep=time.sleep) -> None:
    """原子替换 + 指数退避重试（总等待 ≤ budget，默认 2s）；重试耗尽才抛。

    为什么需要（Windows 专属失败模式）：`os.replace` 走 MoveFileEx(REPLACE_EXISTING)，
    目标文件只要被**任何**进程打开过（哪怕只读、哪怕不是本进程），就会
    `PermissionError: [WinError 5] Access is denied`。本模块的读者（`read_state`）
    打开 `runtime.json` 的窗口是微秒级，重试即可收敛；POSIX 的 rename 无此限制，
    所以这条路径在 Linux 上永远一次成功，行为不变。
    只在**分享冲突**上重试：真越权（只读文件、目录无权限）立刻抛，不白等 2 秒。
    绝不改成非原子写：重试的是同一个 `os.replace`，语义仍是原子发布。
    """
    _retry_on_sharing(lambda: os.replace(src, dst), budget=budget, clock=clock, sleep=sleep)


def _write_fd_synced(target: Path, payload: str) -> None:
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())


@contextmanager
def _locked(cid: str) -> Iterator[None]:
    uid = getattr(os, "getuid", lambda: 0)()
    lock_dir = Path(tempfile.gettempdir()) / f"workspace-template-locks-{uid}"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_dir / f"{cid}.lock"
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":                                # pragma: no cover - Windows
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":                            # pragma: no cover - Windows
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def read_state(workspace: Path, workspace_id: str, ident: Identity | None, *,
               ephemeral: bool) -> dict:
    """只读判定，成功时 `data` 带经校验的 payload；绝不写盘。"""
    _require_bool(ephemeral, "ephemeral")
    if ident is None:
        return _result("IDENTITY_UNAVAILABLE", R_IDENTITY_UNAVAILABLE)

    cid = context_id(workspace_id, ident)
    local_root = Path(workspace) / ".workspace" / ".local"
    if not local_root.exists():
        return _result("UNBOUND", R_NO_LOCAL, cid)
    if not state_root(workspace).exists():
        return _result("UNBOUND", R_NO_SELF, cid)

    d = context_dir(workspace, workspace_id, ident, ephemeral=ephemeral)
    if not d.is_dir():
        return _result("UNBOUND", R_NO_SELF, cid)
    if not _chain_is_safe(workspace, d):
        return _result("CORRUPT", R_UNSAFE_PATH, cid)

    runtime = d / _RUNTIME_FILE
    if not runtime.is_file():
        return _result("CORRUPT", R_MISSING_RUNTIME, cid)
    if _is_symlink(runtime):
        return _result("CORRUPT", R_UNSAFE_PATH, cid)
    try:
        data = _read_json_no_follow(runtime)
    except (OSError, ValueError):
        return _result("CORRUPT", R_BAD_JSON, cid)
    if not isinstance(data, dict):
        return _result("CORRUPT", R_BAD_JSON, cid)
    if data.get("schema") != STATE_VERSION:
        return _result("CORRUPT", R_UNKNOWN_SCHEMA, cid)
    if any(f not in data for f in _REQUIRED_FIELDS):
        return _result("CORRUPT", R_MISSING_FIELDS, cid)
    if data["workspace_id"] != workspace_id:
        return _result("CORRUPT", R_WORKSPACE_MISMATCH, cid)
    if data["context_id"] != cid:
        return _result("CORRUPT", R_CONTEXT_MISMATCH, cid)
    if data["realm"] != ident.realm:
        return _result("CORRUPT", R_REALM_MISMATCH, cid)

    binding = data["root_binding"]
    if not (isinstance(binding, str) and _HEX64.match(binding)):
        return _result("CORRUPT", R_INVALID_FIELDS, cid)
    if not hmac.compare_digest(binding, _root_binding(workspace, cid)):
        return _result("UNBOUND", R_ROOT_MISMATCH, cid)

    venv = data["venv_relpath"]
    if not (isinstance(data["mode"], str) and data["mode"] in _VALID_MODES
            and isinstance(data["version"], str) and isinstance(data["bound_at"], str)):
        return _result("CORRUPT", R_INVALID_FIELDS, cid)
    if venv is not None and _venv_relpath_problem(venv) is not None:
        return _result("CORRUPT", R_INVALID_FIELDS, cid)

    return _result("BOUND", R_BOUND, cid, data)


def read_status(workspace: Path, workspace_id: str, ident: Identity | None, *,
                ephemeral: bool) -> dict:
    """兼容视图：只留 status / reason / context_id。"""
    res = read_state(workspace, workspace_id, ident, ephemeral=ephemeral)
    return {k: res[k] for k in ("status", "reason", "context_id")}


def bind(workspace: Path, workspace_id: str, ident: Identity, mode: str,
         venv_relpath: str | None, version: str, *, ephemeral: bool,
         runtime: dict | None = None, capabilities: dict | None = None,
         decision_source: str | None = None) -> dict:
    _require_bool(ephemeral, "ephemeral")
    if ident is None:
        return _result("IDENTITY_UNAVAILABLE", R_IDENTITY_UNAVAILABLE)
    if not isinstance(mode, str) or mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")
    if venv_relpath is not None:
        problem = _venv_relpath_problem(venv_relpath)
        if problem is not None:
            raise ValueError(f"venv_relpath must be a workspace-relative path, "
                             f"got {venv_relpath!r} ({problem})")

    cid = context_id(workspace_id, ident)
    data = {
        "schema": STATE_VERSION,
        "workspace_id": workspace_id,
        "context_id": cid,
        "realm": ident.realm,
        "root_binding": _root_binding(workspace, cid),
        "mode": mode,
        "venv_relpath": venv_relpath,
        "version": version,
        "bound_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # 可选扩展：运行时标识、能力声明、决策来源（都不含明文绝对路径）
    if runtime is not None:
        if not isinstance(runtime, dict):
            raise ValueError("runtime must be dict|None")
        _reject_abs_paths(runtime, "runtime")
        data["runtime"] = runtime
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            raise ValueError("capabilities must be dict|None")
        _reject_abs_paths(capabilities, "capabilities")
        data["capabilities"] = capabilities
    if decision_source is not None:
        if not isinstance(decision_source, str):
            raise ValueError("decision_source must be str|None")
        _reject_abs_paths(decision_source, "decision_source")
        data["decision_source"] = decision_source
    # 统一校验**完整 payload**（含必填字段与字典键）：不只查三个可选入口
    _reject_abs_paths(data, "state")
    payload = json.dumps(data, sort_keys=True)
    d = context_dir(workspace, workspace_id, ident, ephemeral=ephemeral)
    parent = d.parent
    # 先查链路再建目录：否则 mkdir 会沿着 symlink 在工作区外建目录
    if not _chain_is_safe(workspace, parent):
        raise ValueError(f"refusing to write through symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    if not _chain_is_safe(workspace, parent):
        raise ValueError(f"refusing to write through symlink: {parent}")
    if _is_symlink(d) or (d.exists() and not _chain_is_safe(workspace, d)):
        raise ValueError(f"refusing to write through symlink: {d}")

    with _locked(cid):
        if not d.exists():
            # 原子发布：同级临时目录写完 + fsync，再整体 rename
            tmp_dir = Path(tempfile.mkdtemp(dir=str(parent), prefix=".publish-"))
            try:
                _write_fd_synced(tmp_dir / _RUNTIME_FILE, payload)
                _fsync_dir(tmp_dir)
                _replace_with_retry(tmp_dir, d)
                _fsync_dir(parent)
            except BaseException:
                for child in tmp_dir.glob("*"):
                    try:
                        child.unlink()
                    except OSError:
                        pass
                try:
                    tmp_dir.rmdir()
                except OSError:
                    pass
                raise
        else:
            fd, tmp_path = tempfile.mkstemp(dir=str(d), prefix=".runtime.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                _replace_with_retry(tmp_path, d / _RUNTIME_FILE)
                _fsync_dir(d)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        # 收尾校验读留在锁内：锁外读会与本进程/兄弟进程刚发布的 os.replace 重叠，
        # Windows 上那一段是 delete-pending → read_state 直接拿 PermissionError 退化成
        # CORRUPT（多进程并发写时偶发一次，与「并发读」同源）。锁内读既避开该窗口，
        # 又保证读到的是本次发布的结果。
        return read_state(workspace, workspace_id, ident, ephemeral=ephemeral)


_SID_RE = re.compile(r"\bS-\d+(?:-\d+){1,15}\b")


def _windows_sid(runner=None) -> str | None:
    """取当前用户的 SID（Windows）。取不到返回 None，绝不回退成用户名。"""
    import subprocess
    run = runner or (lambda: subprocess.run(["whoami", "/user"], capture_output=True,
                                            text=True, encoding="utf-8",
                                            errors="replace", timeout=5))
    try:
        proc = run()
    except Exception:                                  # noqa: BLE001 - 采集失败不抛
        return None
    out = getattr(proc, "stdout", "") or ""
    match = _SID_RE.search(out)
    return match.group(0) if match else None


_UNSET = object()
_MATERIAL_CACHE: object = _UNSET


def _machine_material() -> bytes | None:
    """机器身份原料（不含用户）：Linux machine-id / Windows MachineGuid / macOS IOPlatformUUID。

    只读、带进程内缓存；取不到返回 None。hooks 每次工具调用都会取，所以这里不做子进程。
    """
    global _MATERIAL_CACHE
    if _MATERIAL_CACHE is not _UNSET:
        return _MATERIAL_CACHE                      # type: ignore[return-value]
    material: bytes | None = None
    if os.name == "nt":                             # pragma: no cover - Windows
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                material = str(guid).encode("utf-8")
        except OSError:
            material = None
    else:
        import platform as _platform
        if _platform.system().lower() == "darwin":      # pragma: no cover - macOS
            try:
                import subprocess
                out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5).stdout
                match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
                if match:
                    material = match.group(1).encode("utf-8")
            except (OSError, ValueError, subprocess.SubprocessError):
                material = None
        else:
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    text = Path(candidate).read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if text:
                    material = text.encode("utf-8")
                    break
    _MATERIAL_CACHE = material
    return material


def _local_scope_seed(env: Mapping[str, str] | None = None) -> bytes | None:
    """身份原料取不到时的兜底：本机持久随机种子。

    存放位置在用户状态目录（`$XDG_STATE_HOME` 或 `~/.local/state`），**不在工作区里**，
    因此不会被仓库同步带走。两台都取不到 machine-id 的机器各自得到不同 scope，
    不会退化成同一个 `unknown` 桶。

    并发安全：用 `<seed>.lock`（O_EXCL）串行化「读→创建→回读」，先写临时文件再
    `os.replace` 原子发布，最后**回读**最终内容，所有进程收敛到同一个种子；
    空种子文件（崩溃残留）会被重建；拿不到锁也会回读，不会自己造一个新种子。
    """
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME")
    if not base and os.name == "nt":                         # pragma: no cover - Windows
        base = env.get("LOCALAPPDATA")
    base = base or str(Path.home() / ".local" / "state")
    path = Path(base) / "workspace-template" / "scope-seed"

    def _read() -> bytes | None:
        try:
            data = path.read_bytes().strip()
        except OSError:
            return None
        return data or None

    existing = _read()
    if existing:
        return existing
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _read()

    lock = path.with_name(path.name + ".lock")
    acquired = False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # 陈旧锁回收：持锁者崩溃（10s 未释放）且种子仍未生成 → 删锁重试一次
            try:
                if time.time() - lock.stat().st_mtime > 10.0 and _read() is None:
                    lock.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.01)
            continue
        except OSError:
            break
        os.close(fd)
        acquired = True
        break
    if not acquired:
        # 拿不到锁：只回读，绝不自己造种子（否则串行化就白做了）
        return _read()
    try:
        data = _read()
        if data:
            return data
        seed = os.urandom(16).hex().encode("ascii")
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(seed)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, path)
            _fsync_dir(path.parent)
        except OSError:
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return _read()
    finally:
        if acquired:
            try:
                os.unlink(lock)
            except OSError:
                pass


def machine_scope(env: Mapping[str, str] | None = None) -> str:
    """机器级作用域键（12 位 hex），给 hook 运行期状态分片用。

    不含 principal：`.hook-state/` 本身就在某个具体工作区副本里，跨机器隔离才是目的。
    取不到机器身份 → 本机持久种子派生；两者都拿不到才是 `"unknown"`，
    **永不回退到共享根目录**（否则又变回跨机混用）。
    """
    env = os.environ if env is None else env
    seed = env.get("WORKSPACE_CONTEXT_SEED")
    material = seed.encode("utf-8") if seed else _machine_material()
    if material:
        return hmac.new(material, b"hook-scope", hashlib.sha256).hexdigest()[:12]
    local = _local_scope_seed(env)
    if local:
        return hmac.new(local, b"hook-scope-local", hashlib.sha256).hexdigest()[:12]
    return "unknown"


def detect_platform_info(env: Mapping[str, str] | None = None) -> dict:
    """采集本机身份原料（只读、best-effort，不抛异常）。调用方决定缺项时怎么处理。"""
    env = os.environ if env is None else env
    info: dict[str, Any] = {"machine_id": None, "uid": None, "sid": None, "realm": None}

    import platform as _platform
    system = _platform.system().lower()
    # 平台族**只认这一个真值源**（`platform.system()`），不再叠加 `os.name == "nt"`：
    # 两个真值源会在「需要模拟别的平台」时打架——旧实现在 Windows 上即使把
    # platform.system() 换成 Darwin 也照走 windows 分支，于是 ioreg 超时那条契约
    # 在 Windows 上根本无法被用例覆盖（期望值只能硬编码成宿主平台，等于没测）。
    # 真机上二者一致（Windows 的 platform.system() 恒为 "Windows"），行为不变。
    if system == "windows":                                # pragma: no cover - Windows
        info["realm"] = "windows"
        # 身份用 SID，不用用户名：用户名可改、可重名，SID 才稳定
        info["sid"] = _windows_sid()
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                info["machine_id"] = str(guid).encode("utf-8")
        except (ImportError, OSError):                     # 非 Windows 模拟该分支时无 winreg
            info["machine_id"] = None
    else:
        try:
            info["uid"] = str(os.getuid())
        except AttributeError:                              # pragma: no cover
            info["uid"] = None
        if system == "darwin":                                  # pragma: no cover - macOS
            info["realm"] = "macos"
            # macOS 的身份原料就是 IOPlatformUUID，不走 Linux 的 machine-id 文件
            try:
                import subprocess
                out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5).stdout
                match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
                if match:
                    info["machine_id"] = match.group(1).encode("utf-8")
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        else:
            info["realm"] = "wsl" if "microsoft" in _platform.release().lower() else "linux"
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    text = Path(candidate).read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if text:
                    info["machine_id"] = text.encode("utf-8")
                    break

    if env.get("WORKSPACE_CONTEXT_SEED"):
        info["machine_id"] = env["WORKSPACE_CONTEXT_SEED"].encode("utf-8")
    if env.get("WORKSPACE_REALM"):
        info["realm"] = env["WORKSPACE_REALM"]
    return info


def resolve_identity(env: Mapping[str, str],
                     platform_info: Mapping[str, Any]) -> Identity | None:
    """纯函数：显式 seed 优先；空 seed 视为配置错误（返回 None），不静默回退。"""
    if "WORKSPACE_CONTEXT_SEED" in env:
        seed = env.get("WORKSPACE_CONTEXT_SEED") or ""
        if not seed.strip():
            return None
        material = seed.encode("utf-8")
    else:
        mid = platform_info.get("machine_id")
        if not mid:
            return None
        material = mid.encode("utf-8") if isinstance(mid, str) else bytes(mid)

    realm = platform_info.get("realm")
    if not realm:
        return None
    realm = str(realm)
    if realm == "windows":
        principal = platform_info.get("sid") or platform_info.get("uid")
    else:
        principal = platform_info.get("uid") or platform_info.get("sid")
    if not principal:
        return None
    return Identity(machine_material=material, principal=str(principal), realm=realm)


def ephemeral(env: Mapping[str, str]) -> bool:
    return env.get("WORKSPACE_CONTEXT_EPHEMERAL") == "1"
