#!/usr/bin/env python3
"""osnorm 装载器 —— hooks 侧使用 `scripts/osnorm.py` 的**唯一入口**。

为什么不 `import osnorm`：
- `python -I`（隔离模式）会把脚本目录移出 `sys.path`，hooks 目录里的裸 import 直接
  `ModuleNotFoundError`；哪怕不隔离，hooks 也可能被拷到没有 `scripts/` 的实例里。
- 裸 import 会写 `sys.modules`、按 `PYTHONPATH` 撞名，无法保证「代码 ↔ 实现」版本配对。

候选顺序（① 先行，② 只在 ① **不存在**时兜底）：
  ① `<hook 树>/../../scripts/osnorm.py` —— import 期确定；hooks 与 osnorm 同属一份
     `TEMPLATE_FILES`，① 保证版本配对（② 优先反而会在半同步实例里配错版本）。
  ② `<ws_root>/scripts/osnorm.py` —— 请求期（`ws_root` 来自 payload 解析之后的 cwd）；
     只兜半同步/旧 launcher 的实例。
  ③ 都不可用 → `OsnormUnavailable`：调用方必须 deny / advisory，**不得**当成「已通过」。
     （与 `gate_core._fallback_scope` 的功能兜底不同：这里是拒绝兜底。）

硬规则（三席 v2 评审 N1–N5）：
- **N1 版本钉死**：同一进程内只解析一次模块，记录指纹（路径 + size + mtime_ns + sha256）。
  后续请求若检测到文件变化 → `OsnormUnavailable("changed-after-load")`，绝不换版本继续跑。
- **N2 API 版本**：`exec_module` 之后、**任何 API 调用之前**校验 `OSNORM_API`；缺失或不等于
  `OSNORM_API_EXPECTED` → 按「不可用」处理。
- **N3 只接受普通文件**：候选路径 `is_symlink()` 或非 `os.path.isfile` → 拒绝。
  `__file__` 校验只用于防止 `sys.path` / `sys.modules` 串味，**不是安全边界**。
- **N4 隔离兼容**：本模块只用 stdlib 且不依赖脚本目录在 `sys.path` 里（`-I` 下也能 import
  自身以外的零依赖）；测试用 `-S -B` + `cwd=hookdir` 而不是 `-I`。
- **N5 A 损坏一律拒绝**：①（hook 自身树）存在但不可用（不可读 / 语法错 / API 缺失 /
  初始化抛异常）→ 拒绝，**不回退 ②**（回退会造成版本错配）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

# 装载器认的 osnorm 版本（N2）；与 `scripts/osnorm.py` 顶层的 `OSNORM_API` 配对。
OSNORM_API_EXPECTED = 1

# 装载器要求 osnorm 提供的 API（N2 的「所需 API 存在」）。
# `append_text` 是 R41 新增的并发追加原语：hooks 的日志/账本不能拿整文件重写代替。
REQUIRED_API = (
    "canonical_path",
    "family",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "append_text",
    "replace_retry",
    "run_text",
    "configure_stdio",
)

# 装载进内存时用的模块名（不写 `sys.modules`，只作为 module 对象的 `__name__`）。
_MODULE_NAME = "_ws_osnorm"


class OsnormUnavailable(RuntimeError):
    """osnorm 不可用。`reason` 是稳定的机器可判标记（测试与 deny 文案都认它）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# 进程级状态：只解析一次（N1）。`error` 一旦落定即**粘住**——不允许后续请求换个候选重试。
_STATE: dict = {"module": None, "fingerprint": None, "origin": None, "error": None}


def candidate_a() -> Path:
    """① hook 自身配套树：`.workspace/hooks/` → 仓根 `scripts/osnorm.py`。"""
    return Path(__file__).resolve().parents[2] / "scripts" / "osnorm.py"


def candidate_b(ws_root: str | os.PathLike[str] | None) -> Path | None:
    """② 请求期兜底：`<ws_root>/scripts/osnorm.py`；没有 `ws_root` 就没有候选。"""
    if ws_root is None:
        return None
    text = os.fspath(ws_root)
    if not text:
        return None
    return Path(text) / "scripts" / "osnorm.py"


def _present(path: Path) -> bool:
    """候选「存在」的判据：真文件/目录，**或** symlink（含断链）。
    存在但形态不对 → 交给 `_load_candidate` 报 `not-regular-file` / `symlink`，
    不把它当成「没这个候选」——那会把「坏形态」误报成「缺失」，也掩盖 N3 的判据。
    """
    return path.is_symlink() or os.path.exists(path)


def _read_candidate(path: Path) -> bytes:
    """读候选字节（单独成函数：不可读的故障注入点，见 T3）。"""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise OsnormUnavailable("unreadable", f"{path}: {type(exc).__name__}") from exc


def _fingerprint(path: Path, data: bytes) -> dict:
    st = path.stat()
    return {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest()}


def _load_candidate(path: Path) -> tuple[object, dict]:
    """按 §2.2 装载并校验一个候选；任何一步失败都抛 `OsnormUnavailable`。

    校验顺序是判据的一部分：普通文件（N3）→ 读字节 → 指纹 → exec_module →
    `__file__` 比对 → `OSNORM_API`（N2）→ 必需 API。**API 调用一律在校验之后**。
    """
    # N3：只接受普通文件。注意：这条与 `__file__` 校验都**不是安全边界**，
    # 只用于防止「串味」（误装到 `sys.path` 里的同名模块 / 半同步残留）。
    if path.is_symlink():
        raise OsnormUnavailable("symlink", str(path))
    if not os.path.isfile(path):
        raise OsnormUnavailable("not-regular-file", str(path))

    data = _read_candidate(path)
    fingerprint = _fingerprint(path, data)

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, str(path))
    if spec is None or spec.loader is None:  # pragma: no cover - 形态已被 isfile 排除
        raise OsnormUnavailable("no-loader", str(path))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - 语法错/初始化抛异常/SystemExit 一律算不可用
        raise OsnormUnavailable("init-error", f"{type(exc).__name__}: {exc}") from exc

    try:
        loaded = Path(module.__file__).resolve()
    except (AttributeError, OSError, TypeError) as exc:  # pragma: no cover - 极端形态
        raise OsnormUnavailable("file-mismatch", f"__file__ 不可解析：{exc}") from exc
    if loaded != path.resolve():
        raise OsnormUnavailable("file-mismatch", f"{loaded} != {path.resolve()}")

    # N2：API 版本必须在这里校验——在**任何** API 调用之前。
    api = getattr(module, "OSNORM_API", None)
    if api != OSNORM_API_EXPECTED:
        raise OsnormUnavailable("api-version", f"OSNORM_API={api!r}（需要 {OSNORM_API_EXPECTED}）")
    missing = [name for name in REQUIRED_API if not callable(getattr(module, name, None))]
    if missing:
        raise OsnormUnavailable("api-missing", "缺：" + ", ".join(missing))

    return module, fingerprint


def _install(module: object, fingerprint: dict, origin: str) -> object:
    _STATE.update(module=module, fingerprint=fingerprint, origin=origin, error=None)
    return module


def _bootstrap() -> None:
    """import 期只处理 ①：存在就装/拒，**不存在则留待请求期试 ②**（N5 的反面）。"""
    path = candidate_a()
    if not _present(path):
        return  # ① 不存在：请求期再试 ②
    try:
        module, fingerprint = _load_candidate(path)
    except OsnormUnavailable as exc:
        _STATE["error"] = exc  # ① 存在但不可用 → 粘住错误，绝不回退 ②（N5）
        return
    _install(module, fingerprint, "A")


def _verify_unchanged() -> None:
    """N1：同一进程内版本钉死。内容变了（或换了路径身份）→ 拒绝，不换版本继续跑。"""
    fingerprint = _STATE["fingerprint"]
    path = Path(fingerprint["path"])
    if path.is_symlink():
        raise OsnormUnavailable("changed-after-load", f"{path} 变成 symlink")
    try:
        st = os.stat(path)
    except OSError as exc:
        raise OsnormUnavailable("changed-after-load", f"{path}: {type(exc).__name__}") from exc
    if st.st_size == fingerprint["size"] and st.st_mtime_ns == fingerprint["mtime_ns"]:
        return
    # 只有「size 或 mtime 变了」才重算哈希：内容相同则刷新元数据，不同才拒绝。
    data = _read_candidate(path)
    if hashlib.sha256(data).hexdigest() != fingerprint["sha256"]:
        raise OsnormUnavailable("changed-after-load", f"{path} 内容已变化")
    fingerprint["size"] = st.st_size
    fingerprint["mtime_ns"] = st.st_mtime_ns


def load(ws_root: str | os.PathLike[str] | None = None):
    """返回**已校验**的 osnorm 模块；不可用抛 `OsnormUnavailable`。

    - 已装载：只做 N1 指纹复核（每个请求一次，开销是 `stat`）。
    - 未装载且 ① 不存在：用 `ws_root` 试 ②；成功即钉死，失败即粘住。
    - ① 在 import 期已判不可用：直接抛该错误（不回退 ②）。
    """
    module = _STATE["module"]
    if module is not None:
        _verify_unchanged()
        return module
    if _STATE["error"] is not None:
        raise _STATE["error"]

    path = candidate_b(ws_root)
    if path is None or not _present(path):
        error = OsnormUnavailable("candidates-absent", str(path or candidate_a()))
        _STATE["error"] = error
        raise error
    try:
        module, fingerprint = _load_candidate(path)
    except OsnormUnavailable as exc:
        _STATE["error"] = exc
        raise
    return _install(module, fingerprint, "B")


def bootstrap_module():
    """import 期 ① 的结果（**绝不触发 ②**）；供 stdio 重配这类无 `ws_root` 的早期调用。"""
    return _STATE["module"]


def state() -> dict:
    """诊断快照（指纹/来源/错误）；只读副本，测试与审计用。"""
    return {"origin": _STATE["origin"], "fingerprint": dict(_STATE["fingerprint"] or {}),
            "error": None if _STATE["error"] is None else {
                "reason": _STATE["error"].reason, "detail": _STATE["error"].detail}}


def _reset_for_tests() -> None:  # pragma: no cover - 仅测试夹具
    _STATE.update(module=None, fingerprint=None, origin=None, error=None)
    _bootstrap()


_bootstrap()
