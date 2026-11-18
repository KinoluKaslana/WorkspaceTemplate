#!/usr/bin/env python3
"""pyenv_init.py 回归（uv / 手动 venv / MCP 三模式）。

T1 uv dry-run：不建目录，报告 would_create
T2 uv apply：建出 .venv-<ver>，本机 runtime.json 记 mode=uv（bootstrap 不再存）
T3 uv 缺失：exit 3 + 安装提示（不自动安装）
T4 mcp 缺参数：exit 2
T5 手动 venv apply：mode=local-venv
T6 已配置工作区 apply → exit 5，模式不被改写
T7 已配置 + --force → 才允许切换
运行：python3 -B tests/test_pyenv_init.py
"""
import io
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/pyenv_init.py"
HAS_UV = shutil.which("uv") is not None


def mkws(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / ".workspace").mkdir(parents=True)
    (ws / "scripts").mkdir()
    shutil.copyfile(SRC / SCRIPT, ws / SCRIPT)
    shutil.copyfile(SRC / "scripts" / "repo_identity.py", ws / "scripts" / "repo_identity.py")
    # 布局口径在 workspace_preflight.py 里，两脚本必须一起分发（真实工作区里都是 TEMPLATE_FILES）
    shutil.copyfile(SRC / "scripts" / "workspace_preflight.py", ws / "scripts" / "workspace_preflight.py")
    # 运行时模式写本机状态：local_state.py 必须一起分发
    shutil.copyfile(SRC / "scripts" / "local_state.py", ws / "scripts" / "local_state.py")
    (ws / ".workspace" / "bootstrap.json").write_text(
        json.dumps({"schema_version": 1, "workspace_status": "template",
                    "python": {"mode": "unconfigured"},
                    "template": {"source_repo": "https://example.invalid/template"}},
                   ensure_ascii=False), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin",
                    "https://example.invalid/derived/repo.git"], check=True, capture_output=True)
    return ws


def local_runtime(ws: Path) -> dict:
    """读本机状态里的 runtime.json（缺陷时可返回 {}）。"""
    for p in (ws / ".workspace" / ".local").rglob("runtime.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def run(ws: Path, *args, env=None, cwd=None):
    p = subprocess.run([sys.executable, "-B", str(ws / SCRIPT),
                        "--workspace", str(ws), "--json", *args],
                       cwd=cwd or ws, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "UV_CACHE_DIR": str(ws / ".uv-cache"), **(env or {})})
    try:
        data = json.loads(p.stdout)
    except Exception:
        data = {"raw": p.stdout[-400:], "err": p.stderr[-400:]}
    return p.returncode, data


class PyenvInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pyenv-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = mkws(Path(self.tmp.name))
        self.boot = self.ws / ".workspace" / "bootstrap.json"

    def test_t1_uv_dry_run(self):
        if not HAS_UV:
            self.skipTest("uv 未安装")
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12")
        self.assertEqual(rc, 0, d)
        self.assertTrue(d["venv"].get("would_create"))
        self.assertFalse(Path(d["venv"]["path"]).exists())
        self.assertEqual(json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))["python"]["mode"], "unconfigured")

    def test_t2_uv_apply(self):
        if not HAS_UV:
            self.skipTest("uv 未安装")
        subprocess.run(
            ["git", "-C", str(self.ws), "remote", "set-url", "origin",
             "https://example.com/someone/derived.git"],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply")
        self.assertEqual(rc, 0, d)
        rt = local_runtime(self.ws)
        self.assertEqual(rt.get("mode"), "uv")
        self.assertTrue(Path(d["venv"]["path"]).exists())
        self.assertEqual((rt.get("runtime") or {}).get("identifier"), Path(d["venv"]["path"]).name)
        boot = json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))
        self.assertNotIn("python", boot, "bootstrap 不得再存运行时模式")
        self.assertEqual(boot.get("workspace_status"), "active")
        self.assertRegex(boot.get("workspace_id", ""), r"^[0-9a-f-]{36}$")

    def test_t3_uv_missing(self):
        empty = Path(self.tmp.name) / "emptybin"
        empty.mkdir()
        rc, d = run(self.ws, "--mode", "uv", env={"PATH": str(empty)})
        self.assertEqual(rc, 3, d)
        self.assertEqual(d.get("error"), "uv-missing")
        self.assertIn("uv", json.dumps(d, ensure_ascii=False))

    def test_t4_mcp_requires_args(self):
        rc, d = run(self.ws, "--mode", "mcp")
        self.assertEqual(rc, 2, d)
        self.assertEqual(d.get("error"), "mcp-args-required")

    def test_t5_venv_apply(self):
        rc, d = run(self.ws, "--mode", "venv", "--apply")
        self.assertEqual(rc, 0, d)
        self.assertEqual(local_runtime(self.ws).get("mode"), "local-venv")


    def test_t8_dry_run_does_not_touch_bootstrap(self):
        before = self.boot.read_bytes()
        rc, d = run(self.ws, "--mode", "mcp", "--mcp-server", "s", "--mcp-tool", "t")
        self.assertEqual(rc, 0, d)
        self.assertEqual(self.boot.read_bytes(), before, "dry-run 改了 bootstrap")

    def test_t9_persist_mode_never_writes_bootstrap(self):
        sys.path.insert(0, str(self.ws / "scripts"))
        import pyenv_init as pi
        before = self.boot.read_bytes()
        res = pi.persist_mode(self.ws, "mcp", None, None,
                              {"mcp_server": "s", "mcp_tool": "t"},
                              {"workspace_file_access": None}, "mcp（测试）")
        self.assertEqual(res.get("status"), "BOUND", res)
        boot = json.loads(self.boot.read_text(encoding="utf-8"))
        # 旧工作区里既有的 python 节必须原样保留（既不删也不写新模式）
        self.assertEqual(boot.get("python"), json.loads(before).get("python"),
                         "persist_mode 动了 bootstrap.python")
        # 允许它生成 workspace_id（工作区身份），但不得顺手动别的字段
        self.assertIn("workspace_id", boot)
        self.assertEqual(boot.get("workspace_status"), json.loads(before)["workspace_status"])

    def _run_inproc(self, pi, argv):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = pi.main(argv)
        finally:
            sys.stdout = old
        return rc, buf.getvalue()

    def test_t10_identity_unavailable_creates_no_venv(self):
        sys.path.insert(0, str(self.ws / "scripts"))
        import pyenv_init as pi

        class FakeLS:
            @staticmethod
            def resolve_identity(env, info):
                return None

            @staticmethod
            def detect_platform_info():
                return {}

        orig = pi._local_state
        pi._local_state = lambda: FakeLS
        try:
            rc, out = self._run_inproc(pi, ["--workspace", str(self.ws), "--mode", "venv",
                                            "--apply", "--json"])
        finally:
            pi._local_state = orig
        self.assertEqual(rc, 4, out)
        self.assertIn("identity-unavailable", out)
        self.assertEqual([p.name for p in self.ws.glob(".venv-*")], [],
                         "身份不可得却先建了 venv（半套副作用）")

    def test_t11_mark_failure_returns_4_then_recovers_without_force(self):
        sys.path.insert(0, str(self.ws / "scripts"))
        import pyenv_init as pi
        real = pi._mark_active

        def boom(_boot_p, _activate, _hint):
            raise OSError("disk full")

        pi._mark_active = boom
        try:
            rc, out = self._run_inproc(pi, ["--workspace", str(self.ws), "--mode", "venv",
                                            "--apply", "--json"])
        finally:
            pi._mark_active = real
        self.assertEqual(rc, 4, out)
        self.assertIn("mark-active-failed", out)
        self.assertEqual(local_runtime(self.ws).get("mode"), "local-venv",
                         "本机状态应已写入（只差共享状态翻转）")
        rc2, out2 = self._run_inproc(pi, ["--workspace", str(self.ws), "--mode", "venv",
                                          "--apply", "--json"])
        self.assertEqual(rc2, 0, out2)
        boot = json.loads(self.boot.read_text(encoding="utf-8"))
        self.assertEqual(boot.get("workspace_status"), "active")
        self.assertNotIn("python", boot)

    def test_t12_legacy_bootstrap_only_is_not_half_commit(self):
        """只有旧 bootstrap.python.mode 的老工作区，不得被「半提交恢复」误吃。"""
        boot = json.loads(self.boot.read_text(encoding="utf-8"))
        boot["python"] = {"mode": "local-venv", "runtime": {"identifier": ".venv-312"},
                          "version": "3.12.1"}
        boot["workspace_status"] = "template"
        self.boot.write_text(json.dumps(boot), encoding="utf-8")
        before = self.boot.read_bytes()
        rc, d = run(self.ws, "--mode", "venv", "--apply", "--json")
        self.assertEqual(rc, 5, d)
        self.assertEqual(d.get("error"), "already-configured")
        self.assertEqual(self.boot.read_bytes(), before,
                         "旧工作区被误改：状态被翻转或唯一 mode 记录被删")
        self.assertFalse((self.ws / ".workspace" / ".local").exists(),
                         "旧工作区不该凭空出现本机绑定")

    def test_t13_unknown_python_is_rejected_not_silently_ignored(self):
        """`--python` 必须真的选解释器：找不到就报错，不得静默退回当前解释器。"""
        rc, d = run(self.ws, "--mode", "venv", "--python", "9.99", "--apply", "--json")
        self.assertEqual(rc, 2, d)
        self.assertEqual(d.get("error"), "python-not-found")
        self.assertEqual([p.name for p in self.ws.glob(".venv-*")], [], "失败仍建了 venv")

    def test_t13a_python_version_spellings_are_normalized_for_both_modes(self):
        sys.path.insert(0, str(self.ws / "scripts"))
        import pyenv_init as pi
        forms = ("3.12", "python3.12", "Python 3.12", "  python  3.12  ")
        self.assertEqual({pi.venv_dir_name(value) for value in forms}, {".venv-312"})
        self.assertEqual(pi.venv_dir_name("3.12.4"), ".venv-3124")
        if HAS_UV:
            for value in forms[:3]:
                rc, data = run(self.ws, "--mode", "uv", "--python", value)
                self.assertEqual(rc, 0, data)
                self.assertEqual(data["identifier"], ".venv-312")
                self.assertIn("(Python 3.12)", data["tools_snippet"])
        with mock.patch.object(pi, "_resolve_base_python", return_value=sys.executable):
            for value in forms[:3]:
                rc, output = self._run_inproc(
                    pi, ["--workspace", str(self.ws), "--mode", "venv", "--python", value,
                         "--json"])
                self.assertEqual(rc, 0, output)
                self.assertEqual(json.loads(output)["identifier"], ".venv-312")

    def test_t13b_invalid_python_versions_are_clear_errors(self):
        sys.path.insert(0, str(self.ws / "scripts"))
        import pyenv_init as pi
        for value in ("abc", "3", "python"):
            with self.assertRaisesRegex(ValueError, "非法 Python 版本"):
                pi._norm_python_version(value)
            for mode in ("uv", "venv"):
                rc, data = run(self.ws, "--mode", mode, "--python", value)
                self.assertEqual(rc, 2, data)
                self.assertEqual(data.get("error"), "invalid-python-version")

    def test_t14_capabilities_are_not_fabricated(self):
        rc, d = run(self.ws, "--mode", "venv", "--apply", "--json")
        self.assertEqual(rc, 0, d)
        rt = local_runtime(self.ws)
        caps = rt.get("capabilities") or {}
        self.assertIsNone(caps.get("network_access"), "未探测就断言有网络")
        self.assertEqual(caps.get("basis"), "structural")

    def test_t15_relative_origin_is_independent_of_process_cwd(self):
        (self.ws / "localrepo").mkdir()
        subprocess.run(["git", "-C", str(self.ws), "remote", "set-url", "origin", "localrepo"],
                       check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        args = ("--mode", "mcp", "--mcp-server", "s", "--mcp-tool", "t", "--apply")
        rc_ws, d_ws = run(self.ws, *args, cwd=self.ws)
        self.assertEqual(rc_ws, 0, d_ws)
        self.assertEqual(d_ws.get("workspace_status"), "active")
        self.assertEqual(d_ws.get("local_state", {}).get("status"), "BOUND")

        other = mkws(Path(self.tmp.name) / "other")
        (other / "localrepo").mkdir()
        subprocess.run(["git", "-C", str(other), "remote", "set-url", "origin", "localrepo"],
                       check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        rc_tmp, d_tmp = run(other, *args, cwd=Path(tempfile.gettempdir()))
        self.assertEqual(rc_tmp, 0, d_tmp)
        self.assertEqual(d_tmp.get("workspace_status"), "active")
        self.assertEqual(d_tmp.get("local_state", {}).get("status"), "BOUND")

    def test_t6_refuse_overwrite_configured(self):
        rc0, d0 = run(self.ws, "--mode", "venv", "--apply")
        self.assertEqual(rc0, 0, d0)
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply")
        self.assertEqual(rc, 5, d)
        self.assertEqual(d.get("error"), "already-configured")
        self.assertEqual(local_runtime(self.ws).get("mode"), "local-venv")

    def test_t7_force_switches(self):
        rc0, d0 = run(self.ws, "--mode", "venv", "--apply")
        self.assertEqual(rc0, 0, d0)
        if not HAS_UV:
            self.skipTest("uv 未安装")
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply", "--force")
        self.assertEqual(rc, 0, d)
        self.assertEqual(local_runtime(self.ws).get("mode"), "uv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
