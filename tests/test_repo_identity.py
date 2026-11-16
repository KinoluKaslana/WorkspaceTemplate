#!/usr/bin/env python3
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from repo_identity import classify, convert_format, normalize_encoding, repair  # noqa: E402

SOURCE = "https://github.com/KinoluKaslana/WorkspaceTemplate"


class RepoIdentityPipeline(unittest.TestCase):
    def test_encoding_equivalence_and_traversal(self):
        for value in (
            "https://github.com/o/%2e%2e/r.git",
            "https://github.com/o/%252e%252e/r.git",
            "https://github.com/o/../r.git",
        ):
            with self.subTest(value=value):
                self.assertIsNone(repair(value))
        self.assertEqual(repair("ｈｔｔｐｓ://ＧＩＴＨＵＢ．ＣＯＭ/o/r.git"), "github.com/o/r")
        self.assertEqual(repair("https://github.com/Ｋｉｎｏｌｕ/r.git"), "github.com/Kinolu/r")

    def test_encoding_fixed_point_and_encoded_separators(self):
        traversal = ("%2ｆ%", "%252e%252e", "％２ｅ％２ｅ")
        separators = ("%2f", "%5c", "%2F%2E%2E%2F")
        for encoded in traversal + separators:
            with self.subTest(encoded=encoded):
                self.assertIsNone(repair(f"https://github.com/o/{encoded}/r"))

    def test_relative_paths_require_existing_workspace_target(self):
        for value in ("relative/foo", "a/b", "foo.git"):
            with self.subTest(value=value):
                self.assertIsNone(repair(value))
        with tempfile.TemporaryDirectory(prefix="identity-relative-") as tmp:
            root = Path(tmp)
            (root / "real/repo").mkdir(parents=True)
            self.assertEqual(repair("real/repo", base=root),
                             (root / "real/repo").as_posix())

    def test_relative_paths_require_base_and_cannot_escape(self):
        with tempfile.TemporaryDirectory(prefix="identity-base-") as tmp:
            root = Path(tmp) / "ws"
            outside = Path(tmp) / "outside"
            (root / "localrepo").mkdir(parents=True)
            outside.mkdir()
            self.assertIsNone(repair("localrepo"))                         # T-b
            self.assertIsNone(repair("../outside", base=root))             # T-c
            (root / "outside-link").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(repair("outside-link", base=root))           # T-d

    def test_idempotence(self):
        values = (SOURCE, "git@github.com:o/r.git", "file:///tmp/x.git", "/tmp/x.git", "bad")
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(repair(repair(value)), repair(value))

    def test_eight_remote_equivalents(self):
        expected = "github.com/KinoluKaslana/WorkspaceTemplate"
        values = (
            SOURCE, "HTTPS://GITHUB.COM/KinoluKaslana/WorkspaceTemplate",
            SOURCE.replace("github.com/", "github.com:443/"), SOURCE + "/",
            SOURCE + ".git", "ssh://git@github.com/KinoluKaslana/WorkspaceTemplate.git",
            "git@github.com:KinoluKaslana/WorkspaceTemplate.git",
            "git://github.com:9418/KinoluKaslana/WorkspaceTemplate.git",
        )
        self.assertEqual({repair(value) for value in values}, {expected})
        self.assertTrue(all(classify(value, SOURCE) == "template" for value in values))

    def test_rejections_and_limits(self):
        bad = (
            "", "   ", "/", "file:///", ".", "..", "./x", r"C:\work\repo",
            r"\\srv\share", "nosuch://h/o/r", "https:///x/y.git", "https://[bad/o/r",
            "https://" + "a" * 256 + "/o/r", "https://h/" + "a" * 513,
            "https://h/o/" + "a" * 256, "https://h/o\nr", "https://h/o/%00r",
            "https://h/o/%2e%2e/r", "https://h/o/%252e%252e/r",
        )
        for value in bad:
            with self.subTest(value=value):
                self.assertIsNone(repair(value))

    def test_stages_and_host_tail_dot(self):
        normalized = normalize_encoding(" HTTPS://github.com./o/r.git ")
        self.assertEqual(normalized, "https://github.com./o/r.git")
        self.assertEqual(convert_format(normalized), "github.com/o/r")
        self.assertEqual(classify("https://example.invalid/o/r", SOURCE), "derived")
        self.assertEqual(classify("file:///tmp/x", SOURCE), "derived")
        self.assertEqual(classify("/tmp/x", SOURCE), "derived")


@unittest.skipUnless(shutil.which("git"), "requires git")
class ActivationAtomicity(unittest.TestCase):
    def _workspace(self, origin):
        tmp = tempfile.TemporaryDirectory(prefix="identity-atomic-")
        self.addCleanup(tmp.cleanup)
        ws = Path(tmp.name) / "ws"
        (ws / ".workspace").mkdir(parents=True)
        boot = {"schema_version": 1, "workspace_status": "template",
                "python": {"mode": "unconfigured"}, "template": {"source_repo": SOURCE}}
        (ws / ".workspace/bootstrap.json").write_text(json.dumps(boot), encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", origin], check=True)
        return ws

    def test_malformed_apply_writes_nothing(self):
        ws = self._workspace("https://[bad/o/r")
        boot = ws / ".workspace/bootstrap.json"
        before = boot.read_bytes()
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/pyenv_init.py"), "--workspace", str(ws),
             "--mode", "mcp", "--mcp-server", "test", "--mcp-tool", "python", "--apply", "--json"],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(boot.read_bytes(), before)
        self.assertFalse((ws / ".workspace/.local").exists())
        self.assertIn("repo-identity-unavailable", result.stdout)

    def test_template_and_derived_activation(self):
        for origin, active in ((SOURCE, False), ("https://example.invalid/o/r", True)):
            with self.subTest(origin=origin):
                ws = self._workspace(origin)
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "scripts/pyenv_init.py"), "--workspace", str(ws),
                     "--mode", "mcp", "--mcp-server", "test", "--mcp-tool", "python", "--apply", "--json"],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                boot = json.loads((ws / ".workspace/bootstrap.json").read_text())
                self.assertEqual(boot["workspace_status"] == "active", active)
                if not active:
                    self.assertIn("remote set-url", json.loads(result.stdout)["hint"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
