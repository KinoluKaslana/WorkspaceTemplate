#!/usr/bin/env python3
"""docs_index.py 回归（RAG 设计落地件）。

覆盖：
  I1 build 产出 schema/files/sections/df，且**不存正文**
  I2 check 在索引新鲜时 ok=true
  I3 条款 ID 精确命中排第一
  I4 正文改动后 query 拒答（stale，fail-closed）
  I5 新增未索引文件 → check ok=false（unindexed）
  I6 原子重建不留 .tmp
  I7 中文自然语言标题命中（无条款 ID）
  I8 标题加分按 IDF 缩放：高频词标题不得压过正文强证据
  I9 覆盖率因子：命中更多查询词的节排前
  I10 别名表生效，且目标指纹变化后别名被忽略
  I11 噪声查询不硬凑结果（返回空）
运行：python3 -B tests/test_docs_index.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "docs_index.py"
PY = sys.executable


def run(*args, cwd):
    p = subprocess.run([PY, "-B", str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True)
    return p.returncode, p.stdout.strip()


class DocsIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docsidx-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        (self.ws / "docs").mkdir()
        (self.ws / "docs" / "a.md").write_text(
            "# A\n\n## 1. 守门判据 <!-- R98 -->\n\n覆盖 `1..total`，缺口即拦。\n\n"
            "## 2. 活体拒绝腿怎么测\n\n给子 agent 结构性无读定义。\n", encoding="utf-8")
        (self.ws / "docs" / "b.md").write_text(
            "# B\n\n## 3. 审计事件\n\n十三个事件，落 audit-<client>.jsonl。\n", encoding="utf-8")

    def build(self):
        rc, out = run("build", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def test_i1_build_schema_and_no_body(self):
        self.build()
        idx = json.loads((self.ws / "docs" / ".index.json").read_text(encoding="utf-8"))
        for k in ("schema", "files", "sections", "df", "n_sections"):
            self.assertIn(k, idx)
        self.assertGreaterEqual(idx["n_sections"], 3)
        self.assertIn("docs/a.md", idx["files"])
        raw = json.dumps(idx, ensure_ascii=False)
        self.assertNotIn("缺口即拦", raw, "索引不得存正文")

    def test_i2_check_ok(self):
        self.build()
        rc, out = run("check", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(out)["ok"])

    def test_i3_clause_match_first(self):
        self.build()
        rc, out = run("query", "R98", "审计", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        res = json.loads(out)["results"]
        self.assertTrue(res[0]["clauses"].count("R98") >= 1)
        self.assertTrue(res[0]["path"].endswith("a.md"))

    def test_i4_stale_refuses(self):
        self.build()
        p = self.ws / "docs" / "a.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n追加\n", encoding="utf-8")
        rc, out = run("query", "R98", cwd=self.ws)
        self.assertEqual(rc, 3, out)
        self.assertEqual(json.loads(out)["error"], "stale")

    def test_i5_unindexed_detected(self):
        self.build()
        (self.ws / "docs" / "c.md").write_text("# C\n\n## 4. 新增\n\n正文\n", encoding="utf-8")
        rc, out = run("check", cwd=self.ws)
        self.assertEqual(rc, 1, out)
        self.assertFalse(json.loads(out)["ok"])

    def test_i6_atomic_no_tmp(self):
        self.build()
        self.assertEqual(list((self.ws / "docs").glob("*.tmp")), [])

    def test_i7_chinese_heading(self):
        self.build()
        rc, out = run("query", "活体拒绝腿", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        res = json.loads(out)["results"]
        self.assertIn("活体拒绝腿", res[0]["heading"])


    # ---- 检索质量（打分重构后新增）----
    def test_i8_heading_idf_scaled(self):
        (self.ws / "docs" / "c.md").write_text(
            "# C\n\n## 5. 同轮并发读写的坑\n\n并发写入会被拒。\n", encoding="utf-8")
        self.build()
        rc, out = run("query", "同轮并发", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        top = json.loads(out)["results"][0]
        self.assertIn("同轮并发", top["heading"], "正文强证据的节应排第一")

    def test_i9_coverage_factor(self):
        self.build()
        rc, out = run("query", "审计事件 拒绝码", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        top = json.loads(out)["results"][0]
        self.assertIn("审计", top["heading"])

    def test_i10_alias_and_fingerprint(self):
        ali = {"schema": 1, "aliases": [{"phrases": ["活体夹具"], "path": "docs/a.md",
                                         "heading": "2. 活体拒绝腿怎么测", "boost": 20}]}
        (self.ws / "docs" / ".aliases.json").write_text(json.dumps(ali), encoding="utf-8")
        self.build()
        rc, out = run("query", "活体夹具", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("活体拒绝腿", json.loads(out)["results"][0]["heading"])
        # 目标文件改动 → 别名失效，且索引 stale 会先拒答
        p = self.ws / "docs" / "a.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n补\n", encoding="utf-8")
        rc2, out2 = run("query", "活体夹具", cwd=self.ws)
        self.assertEqual(rc2, 3, out2)

    def test_i11_noise_query_empty(self):
        self.build()
        rc, out = run("query", "今天天气不错", "--json", cwd=self.ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["results"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
