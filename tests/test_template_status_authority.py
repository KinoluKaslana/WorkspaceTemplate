#!/usr/bin/env python3
"""模板源 push 目标与 workspace_status 的权威约束。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from repo_identity import classify  # noqa: E402


def push_urls(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "--push", "--all", "origin"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def assert_template_status(test: unittest.TestCase, root: Path) -> None:
    boot = json.loads((root / ".workspace/bootstrap.json").read_text(encoding="utf-8"))
    source = (boot.get("template") or {}).get("source_repo")
    pushes = push_urls(root)
    if not pushes:
        test.skipTest("无 origin push remote，本约束不适用")
    if not any(classify(push, source, base=root) == "template" for push in pushes):
        test.skipTest("非模板正主（push 目标不是模板源），本约束不适用")
    test.assertEqual(
        boot.get("workspace_status"), "template",
        "本仓 push 到模板源；翻转 active 会静默关闭 README doc-fact 判据",
    )


class TemplateStatusAuthority(unittest.TestCase):
    SOURCE = "https://github.com/KinoluKaslana/WorkspaceTemplate"

    def fixture(self, push: str, status: str) -> Path:
        if shutil.which("git") is None:
            self.skipTest("本机无 git")
        tmp = tempfile.TemporaryDirectory(prefix="template-authority-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / ".workspace").mkdir()
        (root / ".workspace/bootstrap.json").write_text(json.dumps({
            "workspace_status": status, "template": {"source_repo": self.SOURCE},
        }), encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", push], check=True)
        return root

    def test_current_repository(self):
        assert_template_status(self, ROOT)

    def test_push_equals_source_template_is_green(self):
        assert_template_status(self, self.fixture(self.SOURCE + ".git", "template"))

    def test_push_equals_source_active_is_red(self):
        with self.assertRaisesRegex(AssertionError, "翻转 active"):
            assert_template_status(self, self.fixture("git@github.com:KinoluKaslana/WorkspaceTemplate.git", "active"))

    def test_push_differs_from_source_is_skipped(self):
        with self.assertRaises(unittest.SkipTest):
            assert_template_status(self, self.fixture("https://github.com/example/derived.git", "active"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
