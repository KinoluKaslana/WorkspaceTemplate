#!/usr/bin/env python3
"""osnorm 装载器契约 T1–T4（R41 规格 §5 + 三席 v2 的 N1–N6）。

判据分工：
  T1 隔离树（无 osnorm）→ 写前事件必须输出**完整 deny 包络**、rc=0、恰一个 JSON，
     且 reason 里带具体失败分支标记（证明真的走到 osnorm-unavailable，而不是别的 deny）。
  T2 正常树 → ① 先于 ②；加载到的 `__file__` 落在配套根下；指纹四元齐全；
     同一进程内文件被改 → `changed-after-load`（N1，绝不换版本继续跑）。
  T3 故障矩阵 → 不存在 / 不可读 / 语法错 / API 缺失 / 初始化抛异常（+ API 版本不匹配、
     ① 损坏不回退 ②）一律 deny 且 rc=0。
  T4 `TEMPLATE_FILES` 顺序 → `scripts/osnorm.py` 必须先于所有 `.workspace/hooks/*`。

隔离方式（N4）：**不用 `-I`**（它会把脚本目录移出 `sys.path`，hooks 的
`import gate_core/wtarget/cc_transcript` 会先崩，测到的就不是 osnorm 分支了）。
这里统一 `-S -B` + `cwd=<hookdir>` + 清空 `PYTHONPATH`。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".workspace" / "hooks"
SCRIPTS = ROOT / "scripts"
PY = sys.executable

REASON_MARKER = "[workspace-gate:osnorm-unavailable]"


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "GATE_CLIENT")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _copy_hooks(dst: Path) -> Path:
    dst.mkdir(parents=True)
    for path in sorted(HOOKS.glob("*.py")):
        shutil.copy2(path, dst / path.name)
    return dst


def _make_ws(base: Path, hooks_root: Path | None = None) -> tuple[Path, Path]:
    """建一个最小工作区：`.workspace/hooks/`（全部 hook 模块）+ `.workspace/bootstrap.json`。

    `hooks_root` 给定时 hooks 落 `<hooks_root>/.workspace/hooks`（用于把 ① 与 ② 分开：
    ① 取 `<hookdir>/../../scripts`，② 取 payload cwd 的 `<ws_root>/scripts`）。
    """
    ws = base / "ws"
    if hooks_root is None:
        hooks = _copy_hooks(ws / ".workspace" / "hooks")
    else:
        hooks = _copy_hooks(hooks_root / ".workspace" / "hooks")
    (ws / ".workspace").mkdir(parents=True, exist_ok=True)
    (ws / ".workspace" / "bootstrap.json").write_text(
        json.dumps({"workspace_status": "active"}), encoding="utf-8")
    return ws, hooks


def _put_osnorm(ws: Path, body: bytes | None = None, *, where: Path | None = None) -> Path:
    target = (where or ws) / "scripts" / "osnorm.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body if body is not None else (SCRIPTS / "osnorm.py").read_bytes())
    return target


def _payload(ws: Path) -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(ws / "notes.txt")},
        "cwd": str(ws),
        "session_id": "loader-fixture",
        "model": "claude-sonnet-4",
    })


def _run_hook(hooks: Path, ws: Path, event: str = "pre_write") -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-S", "-B", str(hooks / "hook.py"), event],
                          input=_payload(ws), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(hooks), env=_clean_env(), timeout=180)


def _run_probe(hookdir: Path, ws: Path, code: str) -> subprocess.CompletedProcess:
    """在隔离树里跑一段探针（`-S -B -c`，参数 = hookdir, ws）。"""
    return subprocess.run([PY, "-S", "-B", "-c", code, str(hookdir), str(ws)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(hookdir), env=_clean_env(), timeout=180)


def _icacls(*args: str) -> subprocess.CompletedProcess:
    """调 `icacls`（Windows）。输出是本地化编码，按字节收，失败时再解码。"""
    exe = shutil.which("icacls") or str(
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe")
    return subprocess.run([exe, *args], capture_output=True, timeout=120)


def _icacls_text(proc: subprocess.CompletedProcess) -> str:
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    return raw.decode("mbcs" if os.name == "nt" else "utf-8", "replace").strip()


def _deny_read_nt(path: Path):
    """Windows：用 icacls 造真不可读，返回 `(恢复函数, 机制名)`。

    只拒 `(RD)`（FILE_READ_DATA），**不**用 `(R)`：`(R)` 把 READ_CONTROL 一并拒掉，
    `icacls /remove:d` 自己也会被拒（真机实测 rc=5），DACL 再也修不回来。
    `(RD)` 下 `stat`/`os.path.exists` 仍可用，只有读内容抛 PermissionError——这正是
    osnorm 装载器 `unreadable` 分支要覆盖的形态。
    """
    account = os.environ.get("USERNAME", "")
    if os.environ.get("USERDOMAIN"):
        account = f"{os.environ['USERDOMAIN']}\\{account}"
    if not account:
        raise AssertionError("USERNAME 不可用，无法构造 icacls 主体")
    proc = _icacls(str(path), "/deny", f"{account}:(RD)")
    if proc.returncode != 0:
        raise AssertionError(f"icacls /deny 失败（rc={proc.returncode}）：{_icacls_text(proc)}")

    def restore() -> str | None:
        done = _icacls(str(path), "/remove:d", account)
        return None if done.returncode == 0 else (
            f"icacls /remove:d rc={done.returncode}：{_icacls_text(done)}")
    return restore, "icacls 未能生效"


def _deny_read_posix(path: Path):
    """POSIX：`chmod 000`，返回 `(恢复函数, 机制名)`；恢复回原 mode。"""
    mode = path.stat().st_mode & 0o777
    path.chmod(0)

    def restore() -> str | None:
        try:
            path.chmod(mode)
        except OSError as exc:
            return f"chmod {mode:o} 失败：{exc}"
        return None
    return restore, "chmod 000 未能生效"


class _EnvelopeAssertions(unittest.TestCase):
    def assert_deny_envelope(self, proc: subprocess.CompletedProcess, token: str) -> dict:
        """完整包络三字段 + 具体分支标记（N6：不只断 rc，也不只断「有 deny」）。"""
        self.assertEqual(proc.returncode, 0, f"rc 必须为 0：{proc.stderr}")
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"stdout 必须恰一个 JSON：{proc.stdout!r}")
        out = json.loads(lines[0])
        self.assertEqual(set(out), {"hookSpecificOutput"}, f"顶层多/少字段：{out}")
        hso = out["hookSpecificOutput"]
        self.assertEqual(set(hso), {"hookEventName", "permissionDecision",
                                    "permissionDecisionReason"}, f"包络三字段不符：{hso}")
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn(REASON_MARKER, hso["permissionDecisionReason"])
        self.assertIn(token, hso["permissionDecisionReason"],
                      f"失败分支标记缺失（应含 {token}）：{hso['permissionDecisionReason']}")
        return out


class T1IsolatedTree(_EnvelopeAssertions):
    """无 osnorm 的隔离树：写前事件必须 fail-closed 到合法 deny 包络。"""

    def test_no_osnorm_anywhere_denies_with_full_envelope(self):
        with tempfile.TemporaryDirectory(prefix="r41-t1-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            self.assertFalse((ws / "scripts" / "osnorm.py").exists())
            proc = _run_hook(hooks, ws)
            out = self.assert_deny_envelope(proc, "candidates-absent")
            # 纯 ASCII：deny 包络里的中文必须是 \u 转义（字节层不依赖任何 locale）
            raw = proc.stdout.encode("utf-8")
            self.assertTrue(raw.isascii(), f"deny 输出必须纯 ASCII：{raw[:200]!r}")
            self.assertIn("osnorm", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_lifecycle_event_is_advisory_never_deny(self):
        """生命周期事件只有 advisory（`hook.py:735` 既有约定）：不 deny、不请求续轮。"""
        with tempfile.TemporaryDirectory(prefix="r41-t1b-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            proc = _run_hook(hooks, ws, "session_start")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, proc.stdout)
            out = json.loads(lines[0])
            self.assertNotIn("hookSpecificOutput", out)
            self.assertIn(REASON_MARKER, out.get("systemMessage", ""))


class T2NormalTree(_EnvelopeAssertions):
    """正常树：① 先行、`__file__` 落配套根、指纹齐全、N1 版本钉死。"""

    def test_repo_loader_loads_companion_osnorm(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import osnorm_loader
        finally:
            sys.path.pop(0)
        module = osnorm_loader.load(str(ROOT))
        self.assertEqual(Path(module.__file__).resolve(), (SCRIPTS / "osnorm.py").resolve())
        self.assertEqual(module.OSNORM_API, 1)
        state = osnorm_loader.state()
        self.assertEqual(state["origin"], "A")
        fp = state["fingerprint"]
        self.assertEqual(set(fp), {"path", "size", "mtime_ns", "sha256"})
        self.assertEqual(fp["sha256"],
                         hashlib.sha256((SCRIPTS / "osnorm.py").read_bytes()).hexdigest())

    def test_candidate_a_wins_over_b(self):
        """① 存在时 ② 永远不被采纳（② 放一个语法错的假模块，被选中就会炸）。"""
        with tempfile.TemporaryDirectory(prefix="r41-t2a-") as tmp:
            base = Path(tmp)
            ws, _ = _make_ws(base)
            tools = base / "tools"
            hooks = _copy_hooks(tools / ".workspace" / "hooks")   # ① 树（独立）
            (tools / ".workspace").mkdir(parents=True, exist_ok=True)
            a_path = _put_osnorm(ws, where=tools)             # ① = <tools>/scripts/osnorm.py
            _put_osnorm(ws, b"def broken(:\n")                # ② 坏的（被选中就会炸）
            code = ("import json,sys\nsys.path.insert(0,sys.argv[1])\n"
                    "import osnorm_loader as L\n"
                    "m=L.load(sys.argv[2])\n"
                    "print(json.dumps({'file': str(m.__file__), 'origin': L.state()['origin']}))\n")
            proc = _run_probe(hooks, ws, code)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            got = json.loads(proc.stdout)
            self.assertEqual(got["origin"], "A")
            self.assertEqual(Path(got["file"]).resolve(), a_path.resolve())

    def test_candidate_b_is_used_when_a_is_absent(self):
        """① 不存在 → ② `<ws_root>/scripts/osnorm.py`（请求期解析）被采纳。"""
        with tempfile.TemporaryDirectory(prefix="r41-t2b-") as tmp:
            base = Path(tmp)
            ws, hooks = _make_ws(base, hooks_root=base / "tools")
            b_path = _put_osnorm(ws)
            self.assertFalse((base / "tools" / "scripts" / "osnorm.py").exists())
            code = ("import json,sys\nsys.path.insert(0,sys.argv[1])\n"
                    "import osnorm_loader as L\n"
                    "m=L.load(sys.argv[2])\n"
                    "print(json.dumps({'file': str(m.__file__), 'origin': L.state()['origin']}))\n")
            proc = _run_probe(hooks, ws, code)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            got = json.loads(proc.stdout)
            self.assertEqual(got["origin"], "B")
            self.assertEqual(Path(got["file"]).resolve(), b_path.resolve())
            self.assertTrue(str(Path(got["file"]).resolve()).startswith(str(ws.resolve())))

    def test_n1_version_is_pinned_after_load(self):
        """同一进程内文件被改 → `changed-after-load`，不换版本继续跑。"""
        with tempfile.TemporaryDirectory(prefix="r41-t2c-") as tmp:
            base = Path(tmp)
            ws, hooks = _make_ws(base, hooks_root=base / "tools")
            _put_osnorm(base / "tools")
            code = ("import json,sys\nsys.path.insert(0,sys.argv[1])\n"
                    "import osnorm_loader as L\n"
                    "first=L.load(sys.argv[2])\n"
                    "import os\n"
                    "target=os.path.join(os.path.dirname(os.path.dirname(sys.argv[1])),"
                    " 'scripts', 'osnorm.py')\n"
                    "with open(target, 'a', encoding='utf-8') as fh:\n"
                    "    fh.write('\\n# tampered\\n')\n"
                    "out={'first': str(first.__file__)}\n"
                    "try:\n"
                    "    L.load(sys.argv[2])\n"
                    "except L.OsnormUnavailable as exc:\n"
                    "    out['reason']=exc.reason\n"
                    "print(json.dumps(out))\n")
            proc = _run_probe(hooks, ws, code)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["reason"], "changed-after-load")


class T3FaultMatrix(_EnvelopeAssertions):
    """五类故障 + 两条加强项：每一类都必须 deny、rc=0、标记精确。"""

    def _deny_for(self, body: bytes | None, token: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="r41-t3-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            if body is not None:
                _put_osnorm(ws, body)
            return self.assert_deny_envelope(_run_hook(hooks, ws), token)

    def test_absent(self):
        self._deny_for(None, "candidates-absent")

    def test_syntax_error(self):
        self._deny_for(b"def broken(:\n    pass\n", "init-error")
        # 具体到 SyntaxError 这一分支（不是一个泛化的 init-error 就够）
        with tempfile.TemporaryDirectory(prefix="r41-t3s-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            _put_osnorm(ws, b"def broken(:\n    pass\n")
            self.assertIn("SyntaxError",
                          json.loads(_run_hook(hooks, ws).stdout)["hookSpecificOutput"]
                          ["permissionDecisionReason"])

    def test_api_missing(self):
        # 只有版本常量、没有任何 API → api-missing，且列出缺的具体名字
        self._deny_for(b"OSNORM_API = 1\n", "api-missing")
        with tempfile.TemporaryDirectory(prefix="r41-t3a-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            _put_osnorm(ws, b"OSNORM_API = 1\n")
            reason = json.loads(_run_hook(hooks, ws).stdout)["hookSpecificOutput"][
                "permissionDecisionReason"]
            self.assertIn("run_text", reason)

    def test_init_error(self):
        self._deny_for(b"raise RuntimeError('boom')\n", "init-error")
        with tempfile.TemporaryDirectory(prefix="r41-t3i-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            _put_osnorm(ws, b"raise RuntimeError('boom')\n")
            self.assertIn("RuntimeError",
                          json.loads(_run_hook(hooks, ws).stdout)["hookSpecificOutput"][
                              "permissionDecisionReason"])

    def test_api_version_mismatch(self):
        """N2：`OSNORM_API` 不是 1 → 按不可用处理（不是「加载成功就凑合跑」）。"""
        self._deny_for(b"OSNORM_API = 2\n", "api-version")

    def test_unreadable(self):
        """不可读 → deny，**真子进程**断言，两条平台路径都必须真拦住读取。

        - POSIX：`chmod 000`。
        - Windows：`icacls <file> /deny <user>:(RD)`（只拒 FILE_READ_DATA；理由见
          `_deny_read_nt`）。
        施加后先由本进程读一次：预期 OSError；若读得到 → 用例失败（`icacls 未能生效`）。
        不再退回「进程内 patch `_read_candidate`」：被测 hook 是**子进程**启动的，
        进程内补丁根本打不到它，那样的绿是假证据（R41b 真机 JSONDecodeError 的来源）。
        """
        with tempfile.TemporaryDirectory(prefix="r41b-t3u-") as tmp:
            ws, hooks = _make_ws(Path(tmp))
            path = _put_osnorm(ws)
            restore, mechanism = (
                _deny_read_nt(path) if os.name == "nt" else _deny_read_posix(path))
            try:
                enforced = False
                try:
                    path.read_bytes()
                except OSError:
                    enforced = True
                self.assertTrue(enforced, f"{mechanism}：{path} 仍可被本进程读取")
                self.assert_deny_envelope(_run_hook(hooks, ws), "unreadable")
            finally:
                error = restore()
                if error is not None:
                    exc = sys.exc_info()[1]
                    if exc is not None:
                        exc.add_note(f"不可读 ACL 恢复失败，需手工清理：{error}")
                    else:
                        self.fail(f"不可读 ACL 恢复失败，需手工清理：{error}")

    def test_broken_a_never_falls_back_to_b(self):
        """N5：① 存在但初始化抛异常 → 拒绝，**不回退 ②**（回退会造成版本错配）。"""
        with tempfile.TemporaryDirectory(prefix="r41-t3n-") as tmp:
            base = Path(tmp)
            ws, hooks = _make_ws(base, hooks_root=base / "tools")
            _put_osnorm(base / "tools", b"raise RuntimeError('boom-a')\n")   # ① 坏
            _put_osnorm(ws)                                                   # ② 好
            proc = _run_hook(hooks, ws)
            out = self.assert_deny_envelope(proc, "init-error")
            self.assertIn("boom-a", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_n3_rejects_symlink_and_non_regular_file(self):
        """N3：候选不是普通文件 → 拒绝（`__file__` 校验只防串味，不是安全边界）。"""
        with tempfile.TemporaryDirectory(prefix="r41-t3f-") as tmp:
            base = Path(tmp)
            ws, hooks = _make_ws(base, hooks_root=base / "tools")
            # 目录（真跑，跨平台稳定）
            (base / "tools" / "scripts").mkdir(parents=True)
            (base / "tools" / "scripts" / "osnorm.py").mkdir()
            self.assert_deny_envelope(_run_hook(hooks, ws), "not-regular-file")

        with tempfile.TemporaryDirectory(prefix="r41-t3l-") as tmp:
            base = Path(tmp)
            ws, hooks = _make_ws(base, hooks_root=base / "tools")
            target = _put_osnorm(ws)
            link = base / "tools" / "scripts" / "osnorm.py"
            link.parent.mkdir(parents=True)
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                # Windows 默认没有 SeCreateSymbolicLinkPrivilege：用 mock 钉同一行分支。
                sys.path.insert(0, str(HOOKS))
                try:
                    import osnorm_loader
                finally:
                    sys.path.pop(0)
                from unittest import mock
                with mock.patch.object(Path, "is_symlink", lambda self: True):
                    with self.assertRaises(osnorm_loader.OsnormUnavailable) as ctx:
                        osnorm_loader._load_candidate(target)
                self.assertEqual(ctx.exception.reason, "symlink")
                return
            self.assert_deny_envelope(_run_hook(hooks, ws), "symlink")


class T4TemplateOrder(unittest.TestCase):
    """T4：`TEMPLATE_FILES` 里 osnorm 必须先于所有 hooks（同步是逐文件 copy2）。"""

    def test_osnorm_precedes_every_hook_file(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import verify_rules
        finally:
            sys.path.pop(0)
        files = list(verify_rules.TEMPLATE_FILES)
        self.assertEqual(len(files), len(set(files)), "TEMPLATE_FILES 不得有重复项")
        osnorm_idx = files.index("scripts/osnorm.py")
        hook_idx = [i for i, rel in enumerate(files) if rel.startswith(".workspace/hooks/")]
        self.assertTrue(hook_idx, "TEMPLATE_FILES 里没有任何 hooks 文件")
        self.assertLess(osnorm_idx, min(hook_idx),
                        "scripts/osnorm.py 必须排在所有 .workspace/hooks/* 之前")
        self.assertLess(files.index(".workspace/hooks/osnorm_loader.py"),
                        files.index(".workspace/hooks/hook.py"),
                        "装载器必须先于使用它的 hook.py 下发")


if __name__ == "__main__":
    unittest.main()
