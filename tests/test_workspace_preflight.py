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
                       cwd=ws, capture_output=True, text=True, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
