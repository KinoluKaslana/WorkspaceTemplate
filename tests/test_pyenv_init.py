#!/usr/bin/env python3
"""pyenv_init.py 回归（uv / 手动 venv / MCP 三模式）。

T1 uv dry-run：不建目录，报告 would_create
T2 uv apply：建出 .venv-<ver>，bootstrap.python.mode=uv、记录 uv 路径
T3 uv 缺失：exit 3 + 安装提示（不自动安装）
T4 mcp 缺参数：exit 2
T5 手动 venv apply：mode=local-venv
T6 已配置工作区 apply → exit 5，模式不被改写
T7 已配置 + --force → 才允许切换
运行：python3 -B tests/test_pyenv_init.py
"""
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/pyenv_init.py"
HAS_UV = shutil.which("uv") is not None


def mkws(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / ".workspace").mkdir(parents=True)
    (ws / "scripts").mkdir()
    shutil.copyfile(SRC / SCRIPT, ws / SCRIPT)
    # 布局口径在 workspace_preflight.py 里，两脚本必须一起分发（真实工作区里都是 TEMPLATE_FILES）
    shutil.copyfile(SRC / "scripts" / "workspace_preflight.py", ws / "scripts" / "workspace_preflight.py")
    (ws / ".workspace" / "bootstrap.json").write_text(
        json.dumps({"schema_version": 1, "workspace_status": "template",
                    "python": {"mode": "unconfigured"}}, ensure_ascii=False), encoding="utf-8")
    return ws


def run(ws: Path, *args, env=None):
    p = subprocess.run([sys.executable, "-B", SCRIPT, "--workspace", str(ws), "--json", *args],
                       cwd=ws, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **(env or {})})
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
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply")
        self.assertEqual(rc, 0, d)
        py = json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))["python"]
        self.assertEqual(py["mode"], "uv")
        self.assertTrue(Path(d["venv"]["path"]).exists())
        self.assertIn("uv", py["runtime"])

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
        self.assertEqual(json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))["python"]["mode"], "local-venv")


    def test_t6_refuse_overwrite_configured(self):
        boot = json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))
        boot["python"] = {"mode": "local-venv", "runtime": {"identifier": ".venv-312"}, "version": "3.12.1"}
        self.boot.write_text(json.dumps(boot), encoding="utf-8")
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply")
        self.assertEqual(rc, 5, d)
        self.assertEqual(d.get("error"), "already-configured")
        self.assertEqual(json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))["python"]["mode"], "local-venv")

    def test_t7_force_switches(self):
        boot = json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))
        boot["python"] = {"mode": "local-venv", "runtime": {"identifier": ".venv-312"}, "version": "3.12.1"}
        self.boot.write_text(json.dumps(boot), encoding="utf-8")
        if not HAS_UV:
            self.skipTest("uv 未安装")
        rc, d = run(self.ws, "--mode", "uv", "--python", "3.12", "--apply", "--force")
        self.assertEqual(rc, 0, d)
        self.assertEqual(json.loads(self.boot.read_text(encoding="utf-8", errors="replace"))["python"]["mode"], "uv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
