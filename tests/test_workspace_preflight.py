#!/usr/bin/env python3
"""workspace_preflight.py 回归。

P1 一致的工作区 → ok（无 mismatch）
P2 venv 记录缺失/不存在 → mismatch
P3 skills 目录与 REGISTRY 不一致 → mismatch
P4 TOOLS 无本机行 → warn（不阻塞）
P5 模板克隆 remote 与 source_repo 不一致 → mismatch
运行：python3 -B tests/test_workspace_preflight.py
"""
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/workspace_preflight.py"
HOST = socket.gethostname()


def run(ws: Path, *args):
    p = subprocess.run([sys.executable, "-B", SCRIPT, "--workspace", str(ws), "--json", *args],
                       cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout)
    except Exception:
        data = {"raw": p.stdout, "err": p.stderr}
    return p.returncode, data


def base_ws(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / ".workspace" / "skills" / "demo").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "skills").mkdir()
    (ws / "notes").mkdir()
    shutil.copyfile(SRC / "scripts" / "workspace_preflight.py", ws / "scripts" / "workspace_preflight.py")
    # 用桩代替真实 verify_rules，避免夹具依赖模板完整清单
    (ws / "scripts" / "verify_rules.py").write_text(
        'TEMPLATE_FILES = ["AGENT_RULES.md", ".workspace/skills/demo/SKILL.md"]\n', encoding="utf-8")
    (ws / ".workspace" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: \"Use when demo.\"\nversion: 1.0.0\n---\n", encoding="utf-8")
    (ws / "skills" / "REGISTRY.md").write_text(
        "| demo | x | `.workspace/skills/demo/SKILL.md` | workspace-skills |\n", encoding="utf-8")
    (ws / "notes" / "INDEX.md").write_text("# idx\n", encoding="utf-8")
    (ws / "TOOLS.md").write_text(f"| Workspace 根目录 | `{HOST}` `{ws}` |\n| **demo** | 已登记 |\n", encoding="utf-8")
    (ws / "AGENT_RULES.md").write_text("# rules\n", encoding="utf-8")
    boot = {
        "workspace_status": "active",
        "python": {"mode": "local-venv", "runtime": {"identifier": ".venv-999"}, "version": "9.9.9"},
        "template": {"source_repo": "https://example.com/t.git", "local_clone": str(tmp / "tpl")},
        "git": {"status": "initialized", "remote": ""},
    }
    (ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False, indent=1), encoding="utf-8")
    return ws



def _require_git():
    if shutil.which("git") is None:
        raise unittest.SkipTest("本机无 git，跳过依赖 git 的用例")


def _safe_symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest(f"本机不支持符号链接（{exc}）")

class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="preflight-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = base_ws(self.root)

    def test_p1_clean(self):
        _require_git()
        venv = self.ws / ".venv-999" / "bin"
        venv.mkdir(parents=True)
        _safe_symlink(venv / "python", sys.executable)
        tpl = self.root / "tpl"
        subprocess.run(["git", "init", "-q", str(tpl)], check=True)
        subprocess.run(["git", "-C", str(tpl), "remote", "add", "origin", "https://example.com/t.git"], check=True)
        rc, data = run(self.ws)
        self.assertEqual(data.get("mismatch"), 0, data)
        self.assertEqual(rc, 0, data)

    def test_p2_missing_venv(self):
        rc, data = run(self.ws)
        self.assertEqual(rc, 1, data)
        self.assertIn("python", {i["id"] for i in data["items"] if i["status"] == "mismatch"})

    def test_p3_skills_mismatch(self):
        (self.ws / ".workspace" / "skills" / "extra").mkdir()
        (self.ws / ".workspace" / "skills" / "extra" / "SKILL.md").write_text(
            "---\nname: extra\n---\n", encoding="utf-8")
        rc, data = run(self.ws)
        self.assertEqual(rc, 1, data)
        self.assertIn("skills", {i["id"] for i in data["items"] if i["status"] == "mismatch"})

    def test_p4_no_host_line_warns(self):
        (self.ws / "TOOLS.md").write_text("| 其它主机 | `/home/x/ws` |\n", encoding="utf-8")
        rc, data = run(self.ws)
        st = {i["id"]: i["status"] for i in data["items"]}
        self.assertEqual(st["workspace_root"], "warn")
        self.assertEqual(rc, 1)      # venv 仍缺失 → 整体 mismatch

    def test_p5_template_remote_mismatch(self):
        _require_git()
        venv = self.ws / ".venv-999" / "bin"
        venv.mkdir(parents=True)
        _safe_symlink(venv / "python", sys.executable)
        tpl = self.root / "tpl"
        subprocess.run(["git", "init", "-q", str(tpl)], check=True)
        subprocess.run(["git", "-C", str(tpl), "remote", "add", "origin", "https://evil.example/x.git"], check=True)
        rc, data = run(self.ws)
        st = {i["id"]: i["status"] for i in data["items"]}
        self.assertEqual(st["template_clone"], "mismatch")


    @unittest.skipIf(os.name == "nt", "POSIX 上用 shell 桩模拟 .exe 解释器")
    def test_p6_venv_layout_scripts_python_exe(self):
        """Windows 布局（Scripts/python.exe）不能因为找不到 bin/python 就判 mismatch。"""
        scripts = self.ws / ".venv-999" / "Scripts"
        scripts.mkdir(parents=True)
        stub = scripts / "python.exe"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        rc, data = run(self.ws)
        py = [i for i in data["items"] if i["id"] == "python"][0]
        self.assertNotEqual(py["status"], "mismatch", py)
        self.assertIn("Scripts/python.exe", str(py))


    def _layout_only(self):
        import subprocess as sp
        p = sp.run([sys.executable, "-B", SCRIPT, "--workspace", str(self.ws), "--layout-only"],
                   cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode, json.loads(p.stdout)

    def test_p20_readme_undecided_after_git_init_warns(self):
        """建仓后必须有过 README 决策；只 warn 不 mismatch（它不阻塞，但必须被看见）。"""
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["git"] = {"status": "initialized", "remote": "", "default_branch": "main"}
        boot.pop("readme", None)
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = run(self.ws)
        item = [i for i in data["items"] if i["id"] == "readme"][0]
        self.assertEqual(item["status"], "warn", item)
        self.assertIn("--record-readme", item["action"])

    def test_p21_readme_bad_status_is_mismatch(self):
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["readme"] = {"status": "whatever"}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = run(self.ws)
        self.assertEqual(rc, 1, data)
        self.assertIn("readme", {i["id"] for i in data["items"] if i["status"] == "mismatch"})

    def test_p22_record_readme_writes_and_verifies(self):
        """机械入口：--record-readme 写 bootstrap.readme，随后 preflight 该项转 ok。"""
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["git"] = {"status": "initialized", "remote": "", "default_branch": "main"}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        import subprocess as sp
        p = sp.run([sys.executable, "-B", SCRIPT, "--workspace", str(self.ws), "--record-readme", "provided"],
                   cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, p.stderr)
        saved = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))["readme"]
        self.assertEqual(saved["status"], "generated-provided")
        self.assertTrue(saved["decided_at"])
        rc, data = run(self.ws)
        item = [i for i in data["items"] if i["id"] == "readme"][0]
        self.assertEqual(item["status"], "ok", item)

    def test_p23_record_readme_rejects_bad_mode(self):
        import subprocess as sp
        p = sp.run([sys.executable, "-B", SCRIPT, "--workspace", str(self.ws), "--record-readme", "auto-magic"],
                   cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_p16_bootstrap_with_bom(self):
        """Windows 的 PowerShell/记事本会写 UTF-8 BOM；带 BOM 的 bootstrap.json 也必须能读。

        真机探针里踩到过：`Set-Content -Encoding UTF8` 写出 BOM，旧版 `json.loads(text)` 直接
        `JSONDecodeError: Unexpected UTF-8 BOM`，preflight 连报告都出不来。
        """
        boot = self.ws / ".workspace" / "bootstrap.json"
        raw = boot.read_bytes()
        boot.write_bytes(b"\xef\xbb\xbf" + raw)
        rc, data = run(self.ws)
        self.assertNotIn("raw", data, data)              # 能解析出报告，而不是 JSONDecodeError
        self.assertIsInstance(data.get("items"), list)

    def test_p17_mcp_never_probes_workspace_interpreter(self):
        """mcp 模式即使工作区根目录里恰好有 bin/python，也不该去执行它（更不该报出它的版本）。"""
        stub_dir = self.ws / "bin"
        stub_dir.mkdir(parents=True)
        stub = stub_dir / "python"
        stub.write_text(f'#!/bin/sh\necho 9.9.9\n', encoding="utf-8")
        stub.chmod(0o755)
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "mcp", "runtime": {"mcp_server": "x", "mcp_tool": "y", "identifier": None}}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = self._layout_only()
        self.assertEqual(rc, 0, data)
        self.assertEqual(data["layout"], "n/a")
        self.assertIsNone(data["interpreter"])
        self.assertIsNone(data["version"])          # 没有执行根目录那个桩

    def test_p18_empty_identifier_no_false_positive(self):
        """空 identifier 不能回退到工作区根目录：那里的 bin/python 不属于本工作区登记的 venv。"""
        stub_dir = self.ws / "bin"
        stub_dir.mkdir(parents=True)
        stub = stub_dir / "python"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "local-venv", "runtime": {"identifier": ""}}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = self._layout_only()
        self.assertEqual(rc, 3, data)
        self.assertEqual(data["layout"], "none")
        self.assertIn("identifier", data["note"])

    def test_p19_uv_reports_tool_availability(self):
        """uv 模式除了布局，还要报 uv 工具是否可用（否则 exit 0 会被当成"uv 环境健康"）。"""
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "uv", "runtime": {"identifier": ".venv-999"}}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        d = self.ws / ".venv-999" / "bin"
        d.mkdir(parents=True)
        stub = d / "python"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        rc, data = self._layout_only()
        self.assertEqual(rc, 0, data)
        self.assertIn("uv", data)
        self.assertIn("available", data["uv"])

    def test_p13_layout_only_mcp_not_applicable(self):
        """MCP 模式没有本地 venv：布局不适用，exit 0（不能报 3 让自动化当故障）。"""
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "mcp", "runtime": {"mcp_server": "x", "mcp_tool": "y"}}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = self._layout_only()
        self.assertEqual(rc, 0, data)
        self.assertEqual(data["mode"], "mcp")
        self.assertEqual(data["layout"], "n/a")
        self.assertFalse(data["applicable"])

    def test_p14_layout_only_unconfigured(self):
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "unconfigured"}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        rc, data = self._layout_only()
        self.assertEqual(rc, 3, data)
        self.assertFalse(data["applicable"])
        self.assertIn("未配置", data["note"])

    def test_p15_layout_only_uv_mode_uses_same_rules(self):
        """uv 模式照样是本地 venv，布局规则与 local-venv 相同。"""
        boot = json.loads((self.ws / ".workspace" / "bootstrap.json").read_text(encoding="utf-8"))
        boot["python"] = {"mode": "uv", "runtime": {"identifier": ".venv-999"}}
        (self.ws / ".workspace" / "bootstrap.json").write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
        d = self.ws / ".venv-999" / "bin"
        d.mkdir(parents=True)
        stub = d / "python"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        rc, data = self._layout_only()
        self.assertEqual(rc, 0, data)
        self.assertEqual(data["mode"], "uv")
        self.assertTrue(data["applicable"])
        self.assertEqual(data["layout"], "posix")

    def test_p10_layout_only_windows(self):
        """--layout-only：只读诊断，Windows 布局要给 layout=windows 且 exit 0。"""
        scripts = self.ws / ".venv-999" / "Scripts"
        scripts.mkdir(parents=True)
        stub = scripts / "python.exe"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        p = subprocess.run([sys.executable, "-B", SCRIPT, "--workspace", str(self.ws), "--layout-only"],
                           cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["layout"], "windows")
        self.assertTrue(data["interpreter"].endswith("python.exe"), data)
        self.assertEqual(data["source"], "probed-on-host")

    def test_p11_layout_only_missing(self):
        """--layout-only：找不到解释器时 layout=none、exit 3，且不伪造版本。"""
        p = subprocess.run([sys.executable, "-B", SCRIPT, "--workspace", str(self.ws), "--layout-only"],
                           cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["layout"], "none")
        self.assertIsNone(data["interpreter"])
        self.assertIsNone(data["version"])


def _load(name):
    sys.path.insert(0, str(SRC / "scripts"))
    try:
        import importlib
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


class VenvLayout(unittest.TestCase):
    """venv 解释器位置是平台事实：POSIX=bin/python，Windows=Scripts/python.exe。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="venv-")
        self.addCleanup(self.tmp.cleanup)
        self.venv = Path(self.tmp.name) / ".venv-314"

    def _touch(self, *parts):
        f = self.venv.joinpath(*parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
        return f

    def test_p7_posix_layout(self):
        f = self._touch("bin", "python")
        for mod in ("workspace_preflight", "pyenv_init"):
            self.assertEqual(_load(mod).venv_interpreter(self.venv), f, mod)

    def test_p8_windows_layout(self):
        f = self._touch("Scripts", "python.exe")
        for mod in ("workspace_preflight", "pyenv_init"):
            self.assertEqual(_load(mod).venv_interpreter(self.venv), f, mod)

    def test_p12_single_source(self):
        """布局口径只有一处：pyenv_init 必须是 workspace_preflight 的同一个函数对象。"""
        pf = _load("workspace_preflight")
        init = _load("pyenv_init")
        self.assertIs(init.venv_interpreter, pf.venv_interpreter)
        self.assertEqual(init.VENV_INTERPRETERS, pf.VENV_INTERPRETERS)

    def test_p9_no_interpreter(self):
        (self.venv / "lib").mkdir(parents=True)
        for mod in ("workspace_preflight", "pyenv_init"):
            self.assertIsNone(_load(mod).venv_interpreter(self.venv), mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
