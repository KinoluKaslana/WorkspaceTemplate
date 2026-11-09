#!/usr/bin/env python3
"""tag 身份门（release_anchor.tag_identity）回归。

同一版本号不得对应两套**分发内容**：`v<policy>` 的 TEMPLATE_FILES blob 必须等于 HEAD。
运行：python3 -B tests/test_release_anchor_tag_identity.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
SCRIPT = SRC / ".github" / "scripts" / "release_anchor.py"


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def make_repo(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "AGENT_RULES.md").write_text("# rules <!-- id:R1 -->\n\n> **模板政策版本：`1.2.3`。**\n",
                                         encoding="utf-8")
    (root / "scripts" / "verify_rules.py").write_text(
        'TEMPLATE_FILES = ["AGENT_RULES.md"]\n', encoding="utf-8")
    git(root, "init", "-q", "-b", "dev")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")


def anchor(root: Path) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(SCRIPT), "--assert-tag-identity", "--root", str(root)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"raw": p.stdout + p.stderr}


class TagIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tagid-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_repo(self.root)

    def test_z1_tag_missing_is_warning(self):
        rc, info = anchor(self.root)
        self.assertEqual(rc, 0, info)
        self.assertEqual(info["verdict"], "tag-missing")

    def test_z2_identical_passes(self):
        git(self.root, "tag", "v1.2.3")
        rc, info = anchor(self.root)
        self.assertEqual(rc, 0, info)
        self.assertEqual(info["verdict"], "pass")

    def test_z3_same_version_different_content_fails(self):
        git(self.root, "tag", "v1.2.3")
        (self.root / "AGENT_RULES.md").write_text(
            "# rules <!-- id:R1 -->\n\n> **模板政策版本：`1.2.3`。**\n\n新增一行但没升版。\n", encoding="utf-8")
        git(self.root, "commit", "-aqm", "change without bump")
        rc, info = anchor(self.root)
        self.assertEqual(rc, 1, info)
        self.assertEqual(info["verdict"], "content-mismatch")
        self.assertIn("AGENT_RULES.md", info["differing"])

    def test_z4_undistributed_change_passes(self):
        git(self.root, "tag", "v1.2.3")
        (self.root / "ci.yml").write_text("on: push\n", encoding="utf-8")
        git(self.root, "add", "ci.yml")
        git(self.root, "commit", "-qm", "ci only")
        rc, info = anchor(self.root)
        self.assertEqual(rc, 0, info)
        self.assertEqual(info["verdict"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
