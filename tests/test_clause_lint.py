"""clause_lint：条款属性门（meta ↔ 正文用词一致性）。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINT = ROOT / "scripts" / "clause_lint.py"
POLICY = ROOT / "AGENT_RULES.md"
META = ROOT / ".workspace" / "clauses.meta.json"

# 条款标记按格式拼出，避免本文件被条款注册表误认为又一份政策（CROSS-FILE-DUP）
def _mark(n: str) -> str:
    return "<!-- id:" + n + " -->"


N_BODY = (f"# 规则 {_mark('R1')}\n\n"
          f"## 0. 测试 {_mark('R2')}\n\n"
          f"1. 必须做甲。 {_mark('R3')}\n"
          f"2. 不得做乙。 {_mark('R4')}\n"
          f"3. 可以做丙。 {_mark('R5')}\n"
          f"4. 这是描述，不含义务。 {_mark('R6')}\n")

N_META = {
    "R1": {"severity": "INFO", "trigger": "policy-doc", "check": "", "reviewed": False},
    "R2": {"severity": "INFO", "trigger": "policy-doc", "check": "", "reviewed": False},
    "R3": {"severity": "MUST", "trigger": "rule-layering",
           "check": "python3 scripts/mark_rules.py check AGENT_RULES.md", "reviewed": True},
    "R4": {"severity": "MUST_NOT", "trigger": "rule-layering",
           "check": "python3 scripts/verify_rules.py --workspace .", "reviewed": True},
    "R5": {"severity": "MAY", "trigger": "rule-layering", "check": "", "reviewed": False},
    "R6": {"severity": "INFO", "trigger": "rule-layering", "check": "", "reviewed": False},
}


class ClauseLint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / ".workspace").mkdir()
        shutil.copy2(LINT, self.root / "scripts" / "clause_lint.py")
        self.write(N_BODY, N_META)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, body, meta):
        (self.root / "AGENT_RULES.md").write_text(body, encoding="utf-8")
        (self.root / ".workspace" / "clauses.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def run_lint(self, *args):
        return subprocess.run([sys.executable, str(self.root / "scripts" / "clause_lint.py"), *args],
                              capture_output=True, text=True)

    def test_t1_clean_baseline(self):
        p = self.run_lint()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("0 errors", p.stdout)

    def test_t2_missing_meta_is_error(self):
        meta = dict(N_META)
        del meta["R4"]
        self.write(N_BODY, meta)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E2", p.stdout)

    def test_t3_stale_meta_is_error(self):
        meta = dict(N_META)
        meta["R99"] = {"severity": "INFO", "trigger": "x", "check": "", "reviewed": False}
        self.write(N_BODY, meta)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E1", p.stdout)

    def test_t4_strong_without_check_is_error(self):
        meta = json.loads(json.dumps(N_META))
        meta["R3"]["check"] = ""
        self.write(N_BODY, meta)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E5", p.stdout)

    def test_t5_manual_on_machine_trigger_is_error(self):
        meta = json.loads(json.dumps(N_META))
        meta["R3"]["trigger"] = "rule-layering"
        meta["R3"]["check"] = "manual:懒得写命令"
        self.write(N_BODY, meta)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E8", p.stdout)

    def test_t6_unreviewed_strong_is_error(self):
        meta = json.loads(json.dumps(N_META))
        meta["R4"]["reviewed"] = False
        self.write(N_BODY, meta)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E9", p.stdout)

    def test_t7_modal_mismatch_is_error(self):
        body = N_BODY.replace("1. 必须做甲。", "1. 做甲。")
        self.write(body, N_META)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E7", p.stdout)

    def test_t8_vague_word_fails_by_default(self):
        body = N_BODY.replace("3. 可以做丙。", "3. 必要时可以做丙。")
        self.write(body, N_META)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1, p.stdout)      # findings>0 即失败
        self.assertIn("W2", p.stdout)
        self.assertEqual(self.run_lint("--lenient").returncode, 0)

    def test_t10_duplicate_id_is_error(self):
        body = N_BODY + "\n重复条款。 " + _mark("R3") + "\n"
        self.write(body, N_META)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E10", p.stdout)

    def test_t11_unmarked_tail_is_error(self):
        body = N_BODY + "\n文末多出来的一段义务。\n"
        self.write(body, N_META)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)
        self.assertIn("E11", p.stdout)

    def test_t12_vague_word_quoted_is_example(self):
        body = N_BODY.replace("4. 这是描述，不含义务。", "4. 可以用「必要时」当例子。")
        self.write(body, N_META)
        self.assertEqual(self.run_lint().returncode, 0)          # 引号内：不当义务
        body2 = body.replace("4. 可以用「必要时」当例子。", "4. 可以用「必要时」当例子，必要时照做。")
        self.write(body2, N_META)
        p = self.run_lint()
        self.assertEqual(p.returncode, 1)                        # 引号外：仍报 W2
        self.assertIn("W2", p.stdout)

    def test_t9_report_counts(self):
        p = self.run_lint("--report")
        data = json.loads(p.stdout)
        self.assertEqual(data["clauses"], 6)
        self.assertEqual(data["severity"]["MUST"], 1)
        self.assertEqual(data["check_kind"]["command"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
