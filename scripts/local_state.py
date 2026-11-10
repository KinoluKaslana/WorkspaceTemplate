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

import hashlib
import hmac
import json
import os
import re
import tempfile
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
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        return json.loads(fh.read())


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
         venv_relpath: str | None, version: str, *, ephemeral: bool) -> dict:
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
    payload = json.dumps(data, sort_keys=True)

    with _locked(cid):
        if not d.exists():
            # 原子发布：同级临时目录写完 + fsync，再整体 rename
            tmp_dir = Path(tempfile.mkdtemp(dir=str(parent), prefix=".publish-"))
            try:
                _write_fd_synced(tmp_dir / _RUNTIME_FILE, payload)
                _fsync_dir(tmp_dir)
                os.replace(tmp_dir, d)
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
                os.replace(tmp_path, d / _RUNTIME_FILE)
                _fsync_dir(d)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

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


def detect_platform_info(env: Mapping[str, str] | None = None) -> dict:
    """采集本机身份原料（只读、best-effort，不抛异常）。调用方决定缺项时怎么处理。"""
    env = os.environ if env is None else env
    info: dict[str, Any] = {"machine_id": None, "uid": None, "sid": None, "realm": None}

    import platform as _platform
    system = _platform.system().lower()
    if os.name == "nt":                                    # pragma: no cover - Windows
        info["realm"] = "windows"
        # 身份用 SID，不用用户名：用户名可改、可重名，SID 才稳定
        info["sid"] = _windows_sid()
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                info["machine_id"] = str(guid).encode("utf-8")
        except OSError:
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
                                     capture_output=True, text=True, timeout=5).stdout
                match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
                if match:
                    info["machine_id"] = match.group(1).encode("utf-8")
            except (OSError, ValueError):
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
