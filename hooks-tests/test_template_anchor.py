#!/usr/bin/env python3
"""W4 完成声明门禁（进程级）：template_anchor 四种情况 exit 码。

运行：python3 -B hooks-tests/test_template_anchor.py（任意 cwd 可跑，纯 stdlib）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
ANCHOR = HOOKS / "template_anchor.py"
PY = sys.executable

sys.path.insert(0, str(ROOT / "scripts"))
import verify_rules  # noqa: E402


class TemplateAnchor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w4-anchor-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / "ws"
        self.ws.mkdir(parents=True)
        self.clone = self.base / "clone"
        self.clone.mkdir(parents=True)

    def _copy_template_files(self, dst: Path):
        for rel in verify_rules.TEMPLATE_FILES:
            src = ROOT / rel
            if not src.is_file():
                continue
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())

    def _init_git(self, repo: Path):
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            r = subprocess.run(["git", "-C", str(repo), *args],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def _git_commit_all(self, repo: Path):
        subprocess.run(["git", "-C", str(repo), "add", "-A"], capture_output=True)
        r = subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_anchor(self, template=None, env_extra=None):
        cmd = [PY, "-B", str(ANCHOR), "--workspace", str(self.ws), "--json"]
        if template:
            cmd += ["--template", str(template)]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("WORKSPACE_TEMPLATE_CLONE", None)
        if env_extra:
            env.update(env_extra)
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
        out = {}
        try:
            out = json.loads(p.stdout) if p.stdout.strip() else {}
        except Exception:
            out = {}
        return p.returncode, out

    def test_clean_exit0(self):
        self._copy_template_files(self.ws)
        self._copy_template_files(self.clone)
        self._init_git(self.clone)
        self._git_commit_all(self.clone)
        rc, out = self.run_anchor(template=self.clone)
        self.assertEqual(rc, 0, out)

    def test_modified_rule_exit1(self):
        self._copy_template_files(self.ws)
        self._copy_template_files(self.clone)
        self._init_git(self.clone)
        self._git_commit_all(self.clone)
        (self.clone / "AGENT_RULES.md").write_bytes(
            (self.clone / "AGENT_RULES.md").read_bytes() + b"\n<!-- tamper -->\n")
        rc, out = self.run_anchor(template=self.clone)
        self.assertEqual(rc, 1)
        self.assertFalse(out.get("ok"))

    def test_dirty_non_compared_file_exit1(self):
        self._copy_template_files(self.ws)
        self._copy_template_files(self.clone)
        self._init_git(self.clone)
        self._git_commit_all(self.clone)
        # 只新增非受比文件（未提交 → 脏），受比文件干净 → template_clean:false
        (self.clone / "scratch.txt").write_text("x\n")
        rc, out = self.run_anchor(template=self.clone)
        self.assertEqual(rc, 1)
        self.assertTrue(out.get("clean"), "受比文件干净时 clean 应仍为 true（验证门禁键）")
        self.assertFalse(out.get("template_clean"), "非受比脏文件仍应令 template_clean=false")

    def test_no_clone_exit1(self):
        self._copy_template_files(self.ws)
        rc, out = self.run_anchor(template=None)
        self.assertEqual(rc, 1)
        self.assertIn("无法证明", out.get("reason", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
