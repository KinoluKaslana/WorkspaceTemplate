#!/usr/bin/env python3
"""local_state.py v2 — 按两席签字条件修订。

v1 → v2 的改动（全部来自签字轮）：
1. 锁文件移出 context 目录，放 OS 临时目录（按 context_id 命名）——状态树里不再有多余文件；
2. 首次绑定**原子发布整个 context 目录**：先在同级临时目录写好 runtime.json，再 rename，
   消除"目录已存在但 runtime.json 还没写"的窗口（SOL 探针实测的 CORRUPT 误报）；
3. symlink 防护覆盖每个路径组件，并用 O_NOFOLLOW 打开 runtime.json（SOL 探针实测的逃逸写）；
4. `resolve_identity`：principal 按 realm 选（windows→sid，其他→uid）；空 seed 视为非法 → None；
5. `venv_relpath` 先做类型校验再建目录；`mode` 同样先校验；
6. `.local` 存在但版本根缺失 → `no-self-state`（契约明确，不再由实现偶然决定）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Iterator, Mapping

STATE_VERSION = "v1"
_RUNTIME_FILE = "runtime.json"
_REQUIRED_FIELDS = ("schema", "workspace_id", "context_id", "realm", "root_binding",
                    "mode", "venv_relpath", "version", "bound_at")
_VALID_MODES = frozenset({"uv", "venv", "mcp"})

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


def _result(status: str, reason: str, cid: str = "") -> dict:
    return {"status": status, "reason": reason, "context_id": cid}


def context_id(workspace_id: str, ident: Identity) -> str:
    message = f"{workspace_id}|{ident.realm}|{ident.principal}".encode("utf-8")
    return hmac.new(ident.machine_material, message, hashlib.sha256).hexdigest()


def state_root(workspace: Path) -> Path:
    return Path(workspace) / ".workspace" / ".local" / STATE_VERSION


def context_dir(workspace: Path, workspace_id: str, ident: Identity, *,
                ephemeral: bool) -> Path:
    suffix = "-eph" if ephemeral else ""
    return state_root(workspace) / f"{context_id(workspace_id, ident)}{suffix}"


def _root_binding(workspace: Path, cid: str) -> str:
    real = os.fsencode(os.path.realpath(os.fspath(workspace)))
    return hmac.new(cid.encode("ascii"), real, hashlib.sha256).hexdigest()


def _is_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _chain_is_safe(workspace: Path, target: Path) -> bool:
    """从 workspace 到 target（含）之间任何一段都不能是 symlink。"""
    try:
        ws = Path(workspace).resolve()
        rel = Path(target).relative_to(Path(workspace))
    except (ValueError, OSError):
        return False
    cur = Path(workspace)
    for part in rel.parts:
        cur = cur / part
        try:
            if cur.is_symlink():
                return False
        except OSError:
            return False
    # target 本身可能是新建的：不存在也算安全
    try:
        target.resolve().relative_to(ws)
    except (ValueError, OSError):
        return False
    return True


def _read_json_no_follow(path: Path) -> dict:
    flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            return json.loads(fh.read())
    finally:
        pass


@contextmanager
def _locked(cid: str) -> Iterator[None]:
    """锁放在 OS 临时目录：它不是工作区状态，不能被同步。"""
    lock_dir = Path(tempfile.gettempdir()) / "workspace-template-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{cid}.lock"
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":                                    # pragma: no cover - Windows
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":                                # pragma: no cover - Windows
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def read_status(workspace: Path, workspace_id: str, ident: Identity | None, *,
                ephemeral: bool) -> dict:
    """只读：绝不创建、绝不修复。"""
    if ident is None:
        return _result("IDENTITY_UNAVAILABLE", R_IDENTITY_UNAVAILABLE)

    cid = context_id(workspace_id, ident)
    local_root = Path(workspace) / ".workspace" / ".local"
    if not local_root.exists():
        return _result("UNBOUND", R_NO_LOCAL, cid)
    if not state_root(workspace).exists():
        # .local 在、版本根不在：半初始化，按契约判 no-self-state
        return _result("UNBOUND", R_NO_SELF, cid)

    d = context_dir(workspace, workspace_id, ident, ephemeral=ephemeral)
    if not d.is_dir():
        return _result("UNBOUND", R_NO_SELF, cid)
    if not _chain_is_safe(workspace, d):
        return _result("CORRUPT", R_UNSAFE_PATH, cid)

    runtime = d / _RUNTIME_FILE
    if not runtime.is_file():
        return _result("CORRUPT", R_MISSING_RUNTIME, cid)
    if runtime.is_symlink():
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
    if not (isinstance(binding, str) and len(binding) == 64
            and all(c in "0123456789abcdef" for c in binding)):
        return _result("CORRUPT", R_INVALID_FIELDS, cid)
    if not hmac.compare_digest(binding, _root_binding(workspace, cid)):
        return _result("UNBOUND", R_ROOT_MISMATCH, cid)

    venv = data["venv_relpath"]
    if not (isinstance(data["mode"], str) and data["mode"] in _VALID_MODES
            and (venv is None or (isinstance(venv, str) and not _is_absolute_path(venv)))
            and isinstance(data["version"], str) and isinstance(data["bound_at"], str)):
        return _result("CORRUPT", R_INVALID_FIELDS, cid)

    return _result("BOUND", R_BOUND, cid)


def bind(workspace: Path, workspace_id: str, ident: Identity, mode: str,
         venv_relpath: str | None, version: str, *, ephemeral: bool) -> dict:
    if ident is None:
        return _result("IDENTITY_UNAVAILABLE", R_IDENTITY_UNAVAILABLE)
    if venv_relpath is not None:
        if not isinstance(venv_relpath, str):
            raise ValueError(f"venv_relpath must be str|None, got {type(venv_relpath).__name__}")
        if _is_absolute_path(venv_relpath):
            raise ValueError(f"venv_relpath must be relative, got {venv_relpath!r}")
    if not isinstance(mode, str) or mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")

    cid = context_id(workspace_id, ident)
    d = context_dir(workspace, workspace_id, ident, ephemeral=ephemeral)
    parent = d.parent
    # 先查链路再建目录：否则 mkdir 会沿着 symlink 在工作区外建目录
    if not _chain_is_safe(workspace, parent):
        raise ValueError(f"refusing to write through symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    if not _chain_is_safe(workspace, parent):
        raise ValueError(f"refusing to write through symlink: {parent}")

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
            # 原子发布：先在同级临时目录写完，再整体 rename，读者永远看不到"空 context 目录"
            tmp_dir = Path(tempfile.mkdtemp(dir=str(parent), prefix=".publish-"))
            try:
                (tmp_dir / _RUNTIME_FILE).write_text(payload, encoding="utf-8")
                os.replace(tmp_dir, d)
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
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    return read_status(workspace, workspace_id, ident, ephemeral=ephemeral)


def resolve_identity(env: Mapping[str, str],
                     platform_info: Mapping[str, str]) -> Identity | None:
    """纯函数：显式 seed 优先；空 seed 视为配置错误（返回 None），不静默回退。"""
    if "WORKSPACE_CONTEXT_SEED" in env:
        seed = env.get("WORKSPACE_CONTEXT_SEED") or ""
        if not seed:
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
    # principal 按 realm 选：Windows 用 SID，其余用 uid
    if realm == "windows":
        principal = platform_info.get("sid") or platform_info.get("uid")
    else:
        principal = platform_info.get("uid") or platform_info.get("sid")
    if not principal:
        return None
    return Identity(machine_material=material, principal=str(principal), realm=realm)


def ephemeral(env: Mapping[str, str]) -> bool:
    return env.get("WORKSPACE_CONTEXT_EPHEMERAL") == "1"
