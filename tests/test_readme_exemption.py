#!/usr/bin/env python3
"""README 豁免的两种形态：被替换（REPLACED，info）与被删除（ABSENT，info）。

另外覆盖 `--vs-template` 的 `~` 展开：不展开会被解析成 <cwd>/~/... 而报 dir not found。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify_rules.py"


def _mkfile(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _git_init(repo: Path) -> None:
    """模板克隆必须是真 git 仓库：verify_rules 的信任锚要求有 .git。"""
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, capture_output=True)


def _fixture(tmp: Path) -> tuple[Path, Path]:
    """最小工作区 + 模板克隆：只含 3 个文件，其中两个 README 是豁免文件。"""
    ws, tpl = tmp / "ws", tmp / "tpl"
    for root in (ws, tpl):
        _mkfile(root / "AGENT_RULES.md", "# rules\n\n1. 必须做。 <!-- id:R1 -->\n")
        _mkfile(root / "README.md", "# readme\n")
        _mkfile(root / "README_EN.md", "# readme en\n")
    # verify_rules 需要条款注册表：R1 的登记文本必须与正文一致
    import hashlib
    reg = {}
    for name in ("AGENT_RULES.md", "README.md", "README_EN.md"):
        raw = (ws / name).read_bytes()
        reg[name] = {"file_sha256": hashlib.sha256(raw).hexdigest(), "clauses": {}}
    reg["AGENT_RULES.md"]["clauses"] = {"R1": "1. 必须做。"}
    _mkfile(ws / ".workspace" / "rule-clauses.json", json.dumps(reg, ensure_ascii=False))
    _git_init(tpl)
    return ws, tpl


def run(ws: Path, tpl: str, env=None):
    p = subprocess.run([sys.executable, "-B", str(VERIFY), "--workspace", str(ws),
                        "--vs-template", tpl],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"raw": p.stdout, "err": p.stderr}


class ReadmeExemption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.ws, self.tpl = _fixture(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_t1_replaced_readme_is_info_not_finding(self):
        (self.ws / "README.md").write_text("# 用户自己的首页\n", encoding="utf-8")
        rc, data = run(self.ws, str(self.tpl))
        self.assertEqual(data.get("findings"), [], data)
        self.assertTrue(any("REPLACED README.md" in i for i in data.get("info", [])), data)
        self.assertTrue(data.get("clean"), data)

    def test_t2_absent_readme_en_is_info_not_finding(self):
        """用户删掉英文版：不能算 MISSING（否则删不掉），但必须留痕在 info。"""
        (self.ws / "README_EN.md").unlink()
        rc, data = run(self.ws, str(self.tpl))
        self.assertEqual(data.get("findings"), [], data)
        self.assertTrue(any("ABSENT README_EN.md" in i for i in data.get("info", [])), data)
        self.assertTrue(data.get("clean"), data)

    def test_t3_missing_non_exempt_still_fails(self):
        """豁免只覆盖两个 README；别的文件缺失仍然是 finding（不能顺手放过）。"""
        (self.ws / "AGENT_RULES.md").unlink()
        rc, data = run(self.ws, str(self.tpl))
        self.assertEqual(rc, 1, data)
        self.assertTrue(any("MISSING AGENT_RULES.md" in f for f in data.get("findings", [])), data)

    def test_t4_tilde_expands(self):
        """`--vs-template ~/tpl` 必须展开成 $HOME/tpl，而不是 <cwd>/~/tpl。"""
        home = self.base / "home"
        home.mkdir()
        shutil.move(str(self.tpl), str(home / "tpl"))   # 已是 git 仓库，移动即可
        # `~` 的展开源随平台不同：POSIX 用 HOME，Windows 的 ntpath 用 USERPROFILE
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        rc, data = run(self.ws, "~/tpl", env=env)
        self.assertNotIn("error", data, data)
        self.assertTrue(data.get("clean"), data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

@unittest.skipUnless(shutil.which("git"), "未安装 git：同步用例需要真实仓库")
class SyncKeepsDeletedReadmeEn(unittest.TestCase):
    """用户删掉 README_EN.md 后，下一次模板同步不得把它装回来。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.ws = self.base / "ws"
        self.ws.mkdir()
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"],
                                 capture_output=True)
        if archive.returncode != 0:
            self.skipTest("git archive 失败（仓库不可用）")
        subprocess.run(["tar", "-x", "-C", str(self.ws)], input=archive.stdout, check=True)
        self.head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
        boot = {
            "workspace_status": "active",
            "python": {"mode": "local-venv", "runtime": {"identifier": ".venv-x"}},
            "git": {"status": "initialized"},
            "template": {"source_repo": "https://example.com/t.git",
                         "local_clone": str(ROOT),
                         "last_synced_commit": self.head},
        }
        (self.ws / ".workspace").mkdir(parents=True, exist_ok=True)
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps(boot, ensure_ascii=False, indent=2), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_t5_deleted_readme_en_not_reinstalled(self):
        if not (self.ws / "README_EN.md").exists():
            # 模板源本身就没有 README_EN（例如派生实例仓里跑本用例），无法验证「不复活」
            self.skipTest("模板源无 README_EN.md")
        (self.ws / "README.md").write_text("# 用户自己的首页\n", encoding="utf-8")  # 本地化
        (self.ws / "README_EN.md").unlink()
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "template_sync.py"),
                            "--workspace", str(self.ws), "--template", str(ROOT),
                            "--ref", self.head, "--apply"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertIn(r.returncode, (0,), r.stdout + r.stderr)
        self.assertFalse((self.ws / "README_EN.md").exists(), "README_EN.md 被装回来了")
        self.assertTrue((self.ws / "README.md").exists())
