#!/usr/bin/env python3
"""GIT_BIN 注入与可操作错误（真实反馈 问题 C）。

Windows 上 Python 子进程拉起 msys git 会以 0xC0000022 失败；以前只有裸 RuntimeError。
现在：`GIT_BIN` 环境变量可指定绝对路径，失败信息带可操作提示。
运行：python3 -B tests/test_git_bin.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GitBin(unittest.TestCase):
    def test_g1_default_is_plain_git(self):
        os.environ.pop("GIT_BIN", None)
        mod = load("ts_default", ROOT / "scripts" / "template_sync.py")
        self.assertEqual(mod.GIT_BIN, "git")

    def test_g2_env_override(self):
        old = os.environ.get("GIT_BIN")
        os.environ["GIT_BIN"] = "/opt/fake/git"
        try:
            mod = load("ts_override", ROOT / "scripts" / "template_sync.py")
            self.assertEqual(mod.GIT_BIN, "/opt/fake/git")
        finally:
            if old is None:
                os.environ.pop("GIT_BIN", None)
            else:
                os.environ["GIT_BIN"] = old

    def test_g3_bad_git_bin_error_is_actionable(self):
        os.environ["GIT_BIN"] = "/nonexistent/git-binary"
        try:
            mod = load("ts_bad", ROOT / "scripts" / "template_sync.py")
            with self.assertRaises(RuntimeError) as ctx:
                mod.git(Path("."), "status")
            self.assertIn("GIT_BIN", str(ctx.exception))
        finally:
            os.environ.pop("GIT_BIN", None)

    def test_g4_verify_rules_trust_failure_mentions_git_bin(self):
        if not shutil.which("git"):
            self.skipTest("未安装 git")
        tmp = tempfile.TemporaryDirectory(prefix="trust-")
        self.addCleanup(tmp.cleanup)
        clone = Path(tmp.name) / "clone"
        clone.mkdir()
        subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
        os.environ["GIT_BIN"] = "/nonexistent/git-binary"
        try:
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_rules.py"),
                                "--workspace", str(ROOT), "--vs-template", str(clone)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
            self.assertIn("GIT_BIN", r.stdout + r.stderr)
        finally:
            os.environ.pop("GIT_BIN", None)

    def test_g5_trust_none_compares_but_never_clean(self):
        """显式降级：仍做字节比对（不再是整段跳过），但结论恒不 clean。"""
        if not shutil.which("git"):
            self.skipTest("未安装 git")
        tmp = tempfile.TemporaryDirectory(prefix="trust-none-")
        self.addCleanup(tmp.cleanup)
        clone = Path(tmp.name) / "clone"
        clone.mkdir()
        subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
        # 放一份与工作区不同的 AGENT_RULES.md：证明降级模式下字节比对**仍然执行**
        (clone / "AGENT_RULES.md").write_text("different\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_rules.py"),
                            "--workspace", str(ROOT), "--vs-template", str(clone), "--trust=none"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        out = r.stdout + r.stderr
        self.assertIn("UNANCHORED", out)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("\"clean\": false", out)
        # 仍然跑过逐文件比对（克隆里的 AGENT_RULES.md 与工作区不同 → TAMPERED）
        self.assertIn("[vs-template] TAMPERED AGENT_RULES.md", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
