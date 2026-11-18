#!/usr/bin/env python3
"""P0: pyenv_init 只允许派生工作区翻转 workspace_status。"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYENV = ROOT / "scripts" / "pyenv_init.py"
POLICY = ROOT / "scripts" / "policy_facts.py"
SOURCE = "https://github.com/KinoluKaslana/WorkspaceTemplate"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def canonical_local_path(p: Path) -> str:
    """本地绝对路径的 canonical 形式：正斜杠 + 盘符大写 + 路径大小写保持不变。

    与 `scripts/repo_identity.py::_canonical_local` 是**同一个**规范形。为什么不是
    `str(Path)`：原生写法在 Windows 上是 `C:\\a\\b`、在 POSIX 上是 `/a/b`，同一条路径
    两个字符串，跨平台/跨工具比较不成立；正斜杠是唯一两边都成立的形态。
    旧用例拿 `str(existing)` 当期望值，等于假设「产品会回显原生分隔符」——在 Windows
    上就是那条 `'C:/…' != 'C:\\…'`。
    """
    text = p.as_posix()
    drive = p.drive
    return (drive.upper() + text[len(drive):]) if drive else text


def make_workspace(base: Path, origin: str | None) -> Path:
    ws = base / "ws"
    (ws / ".workspace").mkdir(parents=True)
    boot = {
        "schema_version": 1,
        "workspace_status": "template",
        "python": {"mode": "unconfigured", "sentinel": "legacy"},
        "skills": {"sentinel": [1, 2]},
        "notes": {"sentinel": {"x": True}},
        "git": {"status": "unconfigured"},
        "readme": {"status": "undecided"},
        "template": {"source_repo": SOURCE, "sentinel": "keep"},
        "workspace_id": "11111111-1111-4111-8111-111111111111",
    }
    (ws / ".workspace" / "bootstrap.json").write_text(
        json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    git(ws.parent, "init", "-q", str(ws))
    if origin is not None:
        git(ws, "remote", "add", "origin", origin)
    return ws


def apply_mcp(ws: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(PYENV), "--workspace", str(ws), "--mode", "mcp",
         "--mcp-server", "test", "--mcp-tool", "python", "--apply", "--json"],
        capture_output=True, text=True, encoding="utf-8")


@unittest.skipUnless(shutil.which("git"), "需要 git 判定模板正主")
class TemplateStatusAuthority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pyenv-authority-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_t_a_template_origin_stays_template(self):
        ws = make_workspace(self.base, SOURCE + ".git/")
        result = apply_mcp(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
        self.assertEqual(boot["workspace_status"], "template")
        self.assertTrue(out.get("template_protected"), out)
        self.assertIn("hint", out)

    def test_t_a_malformed_scheme_origin_stays_template(self):
        ws = make_workspace(self.base, "https:///github.com/KinoluKaslana/WorkspaceTemplate.git")
        before = (ws / ".workspace/bootstrap.json").read_bytes()
        result = apply_mcp(ws)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((ws / ".workspace/bootstrap.json").read_bytes(), before)

    def test_t_a_repo_identity_whitelist(self):
        spec = importlib.util.spec_from_file_location("pyenv_identity", PYENV)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        cases = {
            "": None, "   ": None, "/": None, "file:///": None,
            ".": None, "..": None, "./x": None, r"C:\work\repo": None,
            "C:/work/repo": None, "nosuch://h/o/r": None, "https:///x/y.git": None,
            SOURCE + ".git": "github.com/KinoluKaslana/WorkspaceTemplate",
            "HTTPS://GITHUB.COM/KinoluKaslana/WorkspaceTemplate.GIT":
                "github.com/KinoluKaslana/WorkspaceTemplate",
            SOURCE + ".git/": "github.com/KinoluKaslana/WorkspaceTemplate",
            "https://github.com:443/KinoluKaslana/WorkspaceTemplate.git":
                "github.com/KinoluKaslana/WorkspaceTemplate",
            "http://github.com:80/o/r.git": "github.com/o/r",
            "ssh://github.com:22/o/r.git": "github.com/o/r",
            "git://github.com:9418/o/r.git": "github.com/o/r",
            "ssh://git@github.com/KinoluKaslana/WorkspaceTemplate.git":
                "github.com/KinoluKaslana/WorkspaceTemplate",
            "git@github.com:KinoluKaslana/WorkspaceTemplate.git":
                "github.com/KinoluKaslana/WorkspaceTemplate",
            "git@github.com:someone/derived.git": "github.com/someone/derived",
            "file:///tmp/x.git": "/tmp/x",
            "file://localhost/tmp/x.git": "/tmp/x",
            "file://localhost:22/tmp/x.git": None,
        }
        existing = self.base.resolve()
        canonical = canonical_local_path(existing)
        cases[str(existing)] = canonical      # 原生写法输入 → canonical
        cases[canonical] = canonical          # 已 canonical → 幂等
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(module._repo_identity(value), expected)

    def test_t_a_fail_closed_origins_and_default_port_template_stay_template(self):
        origins = ("/", ".", r"C:\work\repo", "nosuch://h/o/r")
        for origin in origins:
            with self.subTest(origin=origin), tempfile.TemporaryDirectory(
                    prefix="pyenv-e2e-", dir=self.base) as case:
                ws = make_workspace(Path(case), origin)
                result = apply_mcp(ws)
                self.assertNotEqual(result.returncode, 0)
                out = json.loads(result.stdout)
                boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
                self.assertEqual(boot["workspace_status"], "template")
                self.assertIn("重跑", out.get("hint", ""))

    def test_t_a_nonexistent_scheme_less_origins_stay_template(self):
        for origin in ("not a real url", "./relative"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory(
                    prefix="pyenv-origin-", dir=self.base) as case:
                ws = make_workspace(Path(case), origin)
                result = apply_mcp(ws)
                self.assertNotEqual(result.returncode, 0)
                out = json.loads(result.stdout)
                boot = json.loads(
                    (ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
                self.assertEqual(boot["workspace_status"], "template")
                self.assertIn("无法归一化", out.get("hint", ""))

    def test_t_a_ssh_template_origin_stays_template(self):
        ws = make_workspace(self.base, "ssh://git@github.com/KinoluKaslana/WorkspaceTemplate.git")
        result = apply_mcp(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
        self.assertEqual(boot["workspace_status"], "template")

    def test_t_a_file_uri_and_path_are_equivalent(self):
        source = self.base / "template.git"
        source.mkdir()
        ws = make_workspace(self.base, str(source))
        boot_path = ws / ".workspace/bootstrap.json"
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        boot["template"]["source_repo"] = source.as_uri()
        boot_path.write_text(json.dumps(boot), encoding="utf-8")
        result = apply_mcp(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(boot_path.read_text(encoding="utf-8"))["workspace_status"], "template")

    def test_t_b_missing_origin_stays_template(self):
        ws = make_workspace(self.base, None)
        result = apply_mcp(ws)
        self.assertNotEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
        self.assertEqual(boot["workspace_status"], "template")
        self.assertIn("origin", out.get("hint", ""))

    def test_t_b_other_origin_becomes_active(self):
        ws = make_workspace(self.base, "https://example.invalid/owner/derived.git")
        result = apply_mcp(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
        self.assertEqual(boot["workspace_status"], "active")

    def test_t_b_local_path_origins_become_active(self):
        for as_file_uri in (False, True):
            with self.subTest(as_file_uri=as_file_uri), tempfile.TemporaryDirectory(
                    prefix="pyenv-local-origin-", dir=self.base) as case:
                case_root = Path(case)
                local_origin = case_root / "local.git"
                local_origin.mkdir()
                origin = local_origin.as_uri() if as_file_uri else str(local_origin)
                ws = make_workspace(case_root, origin)
                result = apply_mcp(ws)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                out = json.loads(result.stdout)
                boot = json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
                self.assertEqual(boot["workspace_status"], "active")
                self.assertEqual(out.get("local_state", {}).get("status"), "BOUND")
                self.assertNotIn("hint", out)

    def test_t_c_only_python_workspace_id_status_may_change(self):
        ws = make_workspace(self.base, "git@example.invalid:owner/derived.git")
        path = ws / ".workspace/bootstrap.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        result = apply_mcp(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = json.loads(path.read_text(encoding="utf-8"))
        for key in before.keys() | after.keys():
            if key not in {"python", "workspace_id", "workspace_status"}:
                self.assertEqual(after.get(key), before.get(key), key)

    def test_t_d_policy_facts_negative_control_is_driven_by_status(self):
        fixture = self.base / "policy"
        fixture.mkdir()
        for name in ("AGENT_RULES.md", "CHANGELOG.md", "README.md", "README_EN.md"):
            shutil.copy2(ROOT / name, fixture / name)
        shutil.copytree(ROOT / "docs", fixture / "docs")
        shutil.copytree(ROOT / "scripts", fixture / "scripts")
        shutil.copytree(ROOT / ".workspace" / "skills", fixture / ".workspace" / "skills")
        shutil.copy2(ROOT / ".workspace" / "bootstrap.json", fixture / ".workspace" / "bootstrap.json")
        plugin = fixture / ".hermes/plugins/workspace-gate"
        plugin.mkdir(parents=True)
        shutil.copy2(ROOT / ".hermes/plugins/workspace-gate/plugin.yaml", plugin / "plugin.yaml")
        spec = importlib.util.spec_from_file_location("policy_facts_t_d", POLICY)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        readme = fixture / "README.md"
        text, n = re.subn(r"policy-v\d+\.\d+\.\d+", "policy-v0.0.1",
                          readme.read_text(encoding="utf-8"), count=1)
        self.assertEqual(n, 1, "README.md 政策徽章定位失败（徽章版本不该写死）")
        readme.write_text(text, encoding="utf-8")
        boot_path = fixture / ".workspace/bootstrap.json"
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        boot["workspace_status"] = "active"
        boot_path.write_text(json.dumps(boot), encoding="utf-8")
        self.assertFalse(any("README.md" in finding for finding in module.check(fixture)))
        boot["workspace_status"] = "template"
        boot_path.write_text(json.dumps(boot), encoding="utf-8")
        self.assertTrue(any("README.md" in finding for finding in module.check(fixture)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
