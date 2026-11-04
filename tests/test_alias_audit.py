#!/usr/bin/env python3
"""别名审计回归（docs_alias_audit.py）。

A1 空别名表 → ok（无债务、无死指针）
A2 probe-only 别名 → 非 strict 通过但计债务；strict 失败
A3 死指针（heading 不存在）→ 失败
A4 指纹漂移 → 失败
A5 --restore 用最新快照覆盖回去
运行：python3 -B tests/test_alias_audit.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent


def build_ws(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "tests").mkdir()
    for f in ("docs_index.py", "docs_hit_report.py", "docs_alias_audit.py"):
        shutil.copyfile(SRC / "scripts" / f, ws / "scripts" / f)
    shutil.copyfile(SRC / "tests" / "docs_probe.json", ws / "tests" / "docs_probe.json")
    (ws / "docs" / "a.md").write_text(
        "# A\n\n## 1. 拒绝码\n\nno-read、no-session、unresolved-target。\n\n"
        "## 2. 审计事件\n\ngate-deny、unobserved、gate-intent。\n\n"
        "## 3. 活体拒绝腿\n\ntools: Write 的结构性无读定义。\n", encoding="utf-8")
    # 判据集替换成 2 条稳定题，避免依赖真实文档
    probe = {"schema": 1, "cases": [
        {"id": "p1", "title": "拒绝码", "question": "有哪些拒绝码", "expect": ["no-read", "no-session"]},
        {"id": "p2", "title": "审计事件", "question": "有哪些审计事件", "expect": ["gate-deny", "unobserved"]},
    ]}
    (ws / "tests" / "docs_probe.json").write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, "-B", "scripts/docs_index.py", "build"], cwd=ws, capture_output=True)
    return ws


def wp(ws: Path, aliases):
    (ws / "docs" / ".aliases.json").write_text(
        json.dumps({"schema": 1, "aliases": aliases}, ensure_ascii=False), encoding="utf-8")


def audit(ws: Path, *args):
    p = subprocess.run([sys.executable, "-B", "scripts/docs_alias_audit.py", *args],
                       cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


class AliasAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aliasaudit-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = build_ws(Path(self.tmp.name))

    def test_a1_empty_ok(self):
        wp(self.ws, [])
        rc, out = audit(self.ws)
        self.assertEqual(rc, 0, out)

    def test_a2_probe_only_debt(self):
        wp(self.ws, [{"phrases": ["拒绝码"], "path": "docs/a.md", "heading": "1. 拒绝码",
                      "source": "probe-only", "boost": 20}])
        rc, out = audit(self.ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("DEBT", out)
        rc2, out2 = audit(self.ws, "--strict")
        self.assertEqual(rc2, 1, out2)

    def test_a3_dead_pointer(self):
        wp(self.ws, [{"phrases": ["x"], "path": "docs/a.md", "heading": "不存在的节",
                      "source": "user-query", "miss_id": "m1"}])
        rc, out = audit(self.ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("DEAD", out)

    def test_a4_fingerprint_drift(self):
        wp(self.ws, [{"phrases": ["拒绝码"], "path": "docs/a.md", "heading": "1. 拒绝码",
                      "source": "probe-only", "sha256": "c1:deadbeef"}])
        rc, out = audit(self.ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("DRIFT", out)

    def test_a5_restore(self):
        snap = self.ws / "docs" / ".aliases.bak.20260101000000.json"
        snap.write_text(json.dumps({"schema": 1, "aliases": []}), encoding="utf-8")
        wp(self.ws, [{"phrases": ["z"], "path": "docs/a.md", "heading": "9. 不存在",
                      "source": "probe-only"}])
        rc, out = audit(self.ws, "--restore")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads((self.ws / "docs" / ".aliases.json").read_text())["aliases"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
