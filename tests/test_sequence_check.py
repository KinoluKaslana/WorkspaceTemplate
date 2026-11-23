"""`scripts/sequence_check.py` 的判据测试（S1–S8 + 实例侧 skip）。

每个 S 都带**红例**：违规夹具必须真的红，且点名到轮次/条目（否则等于假绿）。
最后一条跑真实树：有 `SEQUENCE.md` 就必须全绿，没有就必须显式 skip 且 rc=0
（这条把「判据文档」与「实现」钉在一起——文档改了实现跟不上，这里先红）。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import osnorm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEQ_REL = Path(".workspace") / "SEQUENCE.md"
LEDGER_REL = Path(".workspace") / "release_dev.json"

SUMMARY = "> 收录轮次 **R35–R44**。\n\n### 1. 一节\n\n- 条目。"

SEQ_TMPL = """# SEQUENCE — 夹具

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 备注 |
|---|---|---|---|---|
| 44 | `44.3.8.0` | `e3ec3a5` | R35–R44 全集 | 首条 |

## 2. 计划（planned）

### R45 · `45.3.8.0` · 机制轮
- **范围**：机制（C1）
- **依赖**：R44
- **判据**：S1 全绿
- **风险**：低

### R46 · `46.3.8.0` · 互校
- **范围**：`scripts/sequence_check.py`（C2、D1）
- **依赖**：R45
- **判据**：无
- **风险**：低
"""

WORKLIST_TMPL = """# WORKLIST（夹具）

| 项 | 内容 |
|---|---|
| C1 | 机制 |
| C2 | 互校脚本 |
| D1 | 文档 |
"""


def ledger_entry(version="44.3.8.0", summary=SUMMARY, round_no=44):
    return {"version": version, "round": round_no, "date": "2026-11-23",
            "summary": summary, "commit": None, "tag": None, "files_sha256": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        for name in ("sequence_check.py", "osnorm.py"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / ".workspace").mkdir()
        self.write_all()

    def tearDown(self):
        self.tmp.cleanup()

    # ── 夹具 ────────────────────────────────────────────────────────────────
    def write_all(self, *, seq: str | None = SEQ_TMPL, summary: str = SUMMARY,
                  ledger: list[dict] | None = None, worklist: str | None = WORKLIST_TMPL):
        if seq is not None:
            osnorm.write_text(self.root / SEQ_REL, seq, newline="lf")
        else:
            (self.root / SEQ_REL).unlink(missing_ok=True)
        if ledger is None:
            ledger = [ledger_entry(summary=summary)]
        osnorm.write_text(self.root / LEDGER_REL, json.dumps(ledger, ensure_ascii=False, indent=2),
                          newline="lf")
        osnorm.write_text(self.root / "CHANGELOG_DEV.md",
                          f"# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n{summary}\n", newline="lf")
        if worklist is not None:
            osnorm.write_text(self.root / "WORKLIST.md", worklist, newline="lf")
        else:
            (self.root / "WORKLIST.md").unlink(missing_ok=True)

    def write_ledger(self, entries):
        osnorm.write_text(self.root / LEDGER_REL, json.dumps(entries, ensure_ascii=False, indent=2),
                          newline="lf")

    def run_script(self, *args):
        return osnorm.run_text([sys.executable, "-B", str(self.root / "scripts" / "sequence_check.py"),
                                "--root", str(self.root), *args], cwd=self.root, errors="replace")

    def status(self, proc):
        """把 stdout 解析成 {S1: PASS, ...}（人读输出每行 `S1 PASS …`）。"""
        out = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in {f"S{i}" for i in range(1, 9)}:
                out[parts[0]] = parts[1]
        return out


class Green(Base):
    def test_all_pass(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        st = self.status(p)
        self.assertEqual(st, {f"S{i}": "PASS" for i in range(1, 9)}, p.stdout)
        self.assertIn("→ OK", p.stdout)

    def test_json_shape(self):
        p = self.run_script("--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(data["ok"])
        self.assertFalse(data["skipped"])
        self.assertEqual([c["id"] for c in data["checks"]],
                         ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"])
        self.assertEqual([r["round"] for r in data["rounds"]], [44, 45, 46])
        self.assertEqual(data["ledger_entries"], 1)
        self.assertTrue(data["worklist"].endswith("WORKLIST.md"))


class Instance(Base):
    def test_missing_sequence_is_skip_rc0(self):
        self.write_all(seq=None)
        for extra in ((), ("--json",)):
            p = self.run_script(*extra)
            self.assertEqual(p.returncode, 0, "实例侧缺 SEQUENCE.md 必须 rc=0：" + p.stdout + p.stderr)
        p = self.run_script("--json")
        data = json.loads(p.stdout)
        self.assertTrue(data["skipped"])
        self.assertIn(".workspace/SEQUENCE.md", data["reason"])
        self.assertIn("SKIP", self.run_script().stdout)

    def test_missing_everything_but_sequence_still_reports(self):
        """只有计划侧、没有账本：不崩，红在 S3（缺账本）。"""
        (self.root / LEDGER_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("不存在", p.stdout)


class S1Rounds(Base):
    def test_duplicate_round(self):
        self.write_all(seq=SEQ_TMPL.replace("### R46 · `46.3.8.0`", "### R45 · `45.3.8.0`"))
        p = self.run_script()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.status(p)["S1"], "FAIL")
        self.assertIn("R45 重复出现", p.stdout)

    def test_not_increasing(self):
        self.write_all(seq=SEQ_TMPL.replace("### R45 · `45.3.8.0`", "### R43 · `43.3.8.0`"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S1"], "FAIL")
        self.assertIn("未严格递增", p.stdout)

    def test_unparseable(self):
        self.write_all(seq="# SEQUENCE\n\n没有任何轮次\n")
        p = self.run_script()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.status(p)["S1"], "FAIL")
        self.assertIn("0 个轮次", p.stdout)


class S2VersionRound(Base):
    def test_first_segment_mismatch(self):
        self.write_all(seq=SEQ_TMPL.replace("### R46 · `46.3.8.0`", "### R46 · `45.3.8.0`"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S2"], "FAIL")
        self.assertIn("R46", p.stdout)
        self.assertIn("≠ 轮次 46", p.stdout)

    def test_not_four_segments(self):
        self.write_all(seq=SEQ_TMPL.replace("`46.3.8.0` · 互校", "`46.3.8` · 互校"))
        p = self.run_script()
        # 三段版本不能被当成「本轮不带版本」放过（fail-closed）
        self.assertEqual(self.status(p)["S2"], "FAIL", p.stdout)
        self.assertIn("不是合法 NN.X.Y.Z", p.stdout)


class S3Ledger(Base):
    def test_landed_without_ledger_names_round(self):
        self.write_all(seq=SEQ_TMPL.replace("- **依赖**：R45\n- **判据**：无",
                                            "- **依赖**：R45\n- **land_status**：landed\n- **判据**：无"))
        p = self.run_script()
        self.assertEqual(p.returncode, 1)
        st = self.status(p)
        self.assertEqual(st["S3"], "FAIL")
        self.assertEqual(st["S6"], "FAIL")       # 计数也跟着红（landed 2 ≠ 账本 1）
        self.assertIn("[R46]", p.stdout)
        self.assertIn("没有 round 46 的条目", p.stdout)

    def test_ledger_extra_round(self):
        self.write_all(ledger=[ledger_entry(), ledger_entry("45.3.8.0", round_no=45)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("账本多出", p.stdout)

    def test_landed_no_ledger_needs_commit(self):
        seq = SEQ_TMPL.replace("| 44 | `44.3.8.0` | `e3ec3a5` |", "| 44 | （并入上文） | （无） |")
        self.write_all(seq=seq)
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("landed-no-ledger 但提交列不是提交号", p.stdout)

    def test_landed_no_ledger_with_commit_ok(self):
        seq = SEQ_TMPL.replace("| 44 | `44.3.8.0` | `e3ec3a5` |", "| 44 | （并入上文） | `e3ec3a5` |")
        self.write_all(seq=seq, ledger=[])
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "PASS", p.stdout)
        self.assertEqual(self.status(p)["S6"], "PASS", p.stdout)   # landed 0 == 账本 0


class S4Deps(Base):
    def test_inverted(self):
        self.write_all(seq=SEQ_TMPL.replace("- **依赖**：R44", "- **依赖**：R46"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S4"], "FAIL")
        self.assertIn("[R45]", p.stdout)
        self.assertIn("依赖倒置", p.stdout)

    def test_dangling(self):
        self.write_all(seq=SEQ_TMPL.replace("- **依赖**：R44", "- **依赖**：R99"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S4"], "FAIL")
        self.assertIn("悬空", p.stdout)


class S5Worklist(Base):
    def test_missing_worklist_is_skip_not_red(self):
        self.write_all(worklist=None)
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S5"], "SKIP")
        self.assertIn("WORKLIST.md 未找到", p.stdout)
        self.assertIn(str(self.root), p.stdout)          # 注明找过哪些路径

    def test_dangling_worklist_ref(self):
        self.write_all(worklist=WORKLIST_TMPL.replace("| C2 | 互校脚本 |\n", ""))
        p = self.run_script()
        self.assertEqual(self.status(p)["S5"], "FAIL")
        self.assertIn("[R46]", p.stdout)
        self.assertIn("C2", p.stdout)


class S7Prose(Base):
    def test_one_char_drift(self):
        # 只让账本漂移一个字符，CHANGELOG_DEV.md 保持原文 —— 这正是 R46 之前真实发生过的那次
        self.write_all()
        self.write_ledger([ledger_entry(summary=SUMMARY.replace("R35–R44", "R35–R45"))])
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL")
        self.assertIn("prose 漂移", p.stdout)
        self.assertIn("CHANGELOG_DEV.md", p.stdout)
        self.assertIn("R35–R45", p.stdout)               # 账本侧
        self.assertIn("R35–R44", p.stdout)               # 段落侧

    def test_missing_section(self):
        self.write_all(ledger=[ledger_entry(version="44.3.8.0")])
        osnorm.write_text(self.root / "CHANGELOG_DEV.md", "# CHANGELOG_DEV\n\n## 43.3.8.0（x）\n\n无\n",
                          newline="lf")
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL")
        self.assertIn("没有 `## 44.3.8.0`", p.stdout)


class S8Immutability(Base):
    def test_landed_version_tampered(self):
        # 计划侧把已落地版本改掉、账本仍是事实侧的旧值 → S8 必须判「已落地版本不可变」
        self.write_all(seq=SEQ_TMPL.replace("| 44 | `44.3.8.0` |", "| 44 | `44.3.9.0` |"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("已落地版本被改", p.stdout)

    def test_ledger_not_increasing(self):
        self.write_all(ledger=[ledger_entry(), ledger_entry("44.3.8.0", round_no=44)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("账本版本未严格递增", p.stdout)

    def test_ledger_round_field_mismatch(self):
        self.write_all(ledger=[dict(ledger_entry(), round=45)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("round 字段", p.stdout)


class RealTree(unittest.TestCase):
    """真实树上的契约：有 SEQUENCE.md 必须全绿；没有必须显式 skip + rc=0。"""

    def test_real_tree(self):
        p = osnorm.run_text([sys.executable, "-B", str(ROOT / "scripts" / "sequence_check.py")],
                            cwd=ROOT, errors="replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        if (ROOT / SEQ_REL).is_file():
            # S1–S8 里只有 S5 可能 SKIP（WORKLIST.md 是工作区级文件，裸克隆/临时副本里可能没有）
            for i in range(1, 9):
                self.assertTrue(f"S{i} PASS" in p.stdout or (i == 5 and f"S{i} SKIP" in p.stdout),
                                f"S{i} 在真实树上既非 PASS 也非合法 SKIP：\n{p.stdout}")
            self.assertIn("→ OK", p.stdout)
        else:
            self.assertIn("SKIP", p.stdout)


if __name__ == "__main__":
    unittest.main()
