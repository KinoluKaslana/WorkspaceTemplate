"""`scripts/sequence_check.py` 的判据测试（S1–S11 + git 历史锚 + 远端锚 + 实例侧 skip）。

每个 S 都带**红例**：违规夹具必须真的红，且点名到轮次/条目（否则等于假绿）。
R46c 起额外钉住三件**抗绕过**的事：成对删除（§1 行 + 账本条目一起删）、账本 summary 与
CHANGELOG 同步改写、landed 缺席/非法 land_status——这三条只有在**git 历史锚**存在时才判得出来，
所以夹具一律建真 git 仓库（不是 mock）。

R46d 补的六条缺口各自有对抗测试（注释里标 A–F）：
  A 无 SEQUENCE.md 时 S10 仍执行（dev 必在 / main 必不在 / 其它分支 WARN）
  B `--worklist` 显式传入不存在或不可读的路径 → S5 红（不传且无候选 = 声明过的 SKIP）
  C `legacy` 只允许白名单轮次（LEGACY_ROUNDS = R44/R45）
  D `verified_by` 出现白名单外席位名 → 红
  E 未提交条目（`commit: null`）→ S7 独立状态 `DEGRADED`（摘要与 --json 都机器可读，不算 FAIL）
  F 历史轮次集合 = 历史账本轮次 ∪ 历史 SEQUENCE 曾标 landed / landed-no-ledger 的轮次
S11（远端锚）另有三条：`origin/x` 是祖先 → PASS；不是超集 → 红；无远端 → WARN + 「仅有本地锚」。

R46e 再修的三席九条（A–I，与 R46d 的 A–F 同名不同义）：
  A S10 的模板身份只由 git 锚判定，改 `bootstrap.workspace_status` 关不掉它
  B dev 分支删 `SEQUENCE.md` = FAIL（S10/S11 无论 skip 与否都执行）
  C 已提交历史版本之间是**全字段**严格前缀比较（summary 改写 + 普通 commit = 红）
  D `landed-no-ledger` 也必须有三席确认
  E §2 的 `verified_by: [...]` 中英文方括号/全角逗号/空格都解析
  F `--json` 在任何结局（含 S10 FAIL / SKIP）都只输出一个 JSON 对象
  G SEQUENCE §4.1 的「能挡/不能挡」清单与实跑能力一致（机械断言在 DocsCapabilityList）
  H 落地即入账：首次标 landed 的提交必须已含该轮账本条目
  I §0 的 `TEMPLATE_FILES` 口径与 `verify_rules.TEMPLATE_FILES` 不再分叉

最后一条跑真实树：有 `SEQUENCE.md` 就必须全绿，没有就必须显式 skip 且 rc=0
（这条把「判据文档」与「实现」钉在一起——文档改了实现跟不上，这里先红）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import osnorm  # noqa: E402
import sequence_check as sc  # noqa: E402 - 单测解析器/单条判据（不跑子进程）

ROOT = Path(__file__).resolve().parents[1]
SEQ_REL = Path(".workspace") / "SEQUENCE.md"
LEDGER_REL = Path(".workspace") / "release_dev.json"
TL_REL = Path(".workspace") / "bootstrap.json"

SUMMARY = "> 收录轮次 **R35–R44**。\n\n### 1. 一节\n\n- 条目。"

SEQ_TMPL = """# SEQUENCE — 夹具

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 确认（verified_by / verdict） | 备注 |
|---|---|---|---|---|---|
| 44 | `44.3.8.0` | `{commit}` | R35–R44 全集 | `legacy`（机制建立前） | 首条 |

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

# F 的夹具：§1 里有一条 **landed-no-ledger** 的 R45（正是真实树的形态：
# 机制轮不占版本号，只验「提交存在」）。删掉这一行 + 把 R46 依赖改成 R44，
# 两边的「当前文件」就互相自洽了，只有历史锚看得见。
SEQ_TMPL_45 = """# SEQUENCE — 夹具（R45 = landed-no-ledger）

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 确认（verified_by / verdict） | 备注 |
|---|---|---|---|---|---|
| 44 | `44.3.8.0` | `@SHA@` | R35–R44 全集 | `legacy`（机制建立前） | 首条 |
| 45 | （并入 44 条） | `@SHA@` | 机制轮 | `legacy`（机制建立前） | 机制轮不占版本号 |

## 2. 计划（planned）

### R46 · `46.3.8.0` · 互校
- **范围**：`scripts/sequence_check.py`（C2、D1）
- **依赖**：@DEP@
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

# 三席齐全的一条 confirmation（S9 的绿形态）
CONFIRM_OK = ("verified_by=[SOL, GPT-SOL-6, PRO]；verdict=pass；verified_at=2026-11-23")

# write_all(seq=...) 的哨兵：TEMPLATE = 用模板；None = 删掉 SEQUENCE.md
TEMPLATE = object()


def ledger_entry(version="44.3.8.0", summary=SUMMARY, round_no=44, commit=None):
    return {"version": version, "round": round_no, "date": "2026-11-23",
            "summary": summary, "commit": commit, "tag": None, "files_sha256": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / "scripts").mkdir()
        for name in ("sequence_check.py", "osnorm.py"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / ".workspace").mkdir()
        # 夹具默认是 **template 工作区**（S10 才有 dev/main 边界可判）
        osnorm.write_text(self.root / TL_REL,
                          json.dumps({"schema_version": 1, "workspace_status": "template"}),
                          newline="lf")
        self.origin_tmp = tempfile.TemporaryDirectory()
        self.origin = Path(self.origin_tmp.name) / "origin.git"
        self.sha = self.restart_history(keep_files=False)
        # 三阶段落账，刻意让「计划首次标 landed」与「账本条目」同批（H 的历史锚）：
        #   D 事实侧先行：CHANGELOG + WORKLIST 入历史（此时还没有计划文件 / 账本）
        #   L 账本入历史：条目 commit 指向 D（D 里确有 `## 44.3.8.0` 段落，S7 有锚）
        #   C 计划首版：§1 首次标 R44 landed —— 该提交的账本已含 round 44（H 通过）
        osnorm.write_text(self.root / "CHANGELOG_DEV.md",
                          f"# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n{SUMMARY}\n",
                          newline="lf")
        osnorm.write_text(self.root / "WORKLIST.md", WORKLIST_TMPL, newline="lf")
        self.commit_all("facts")
        self.plan_sha = self.head()
        self.write_ledger([ledger_entry(commit=self.plan_sha)])
        self.commit_all("ledger")
        self.write_all(ledger=[ledger_entry(commit=self.plan_sha)])
        self.commit_all("plan")
        self.add_origin()

    def tearDown(self):
        self.tmp.cleanup()
        self.origin_tmp.cleanup()

    # ── git 夹具 ────────────────────────────────────────────────────────────
    def git(self, *args):
        return osnorm.run_text(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
                               cwd=self.root, errors="replace")

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def commit_all(self, msg="fixture"):
        self.git("add", "-A")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                 "commit", "-q", "-m", msg, "--allow-empty")

    def restart_history(self, *, keep_files=True):
        """重建 git 历史（可选先把事实文件删掉），返回新的 HEAD sha。"""
        shutil.rmtree(self.root / ".git", ignore_errors=True)
        if not keep_files:
            for rel in (SEQ_REL, LEDGER_REL, Path("CHANGELOG_DEV.md"), Path("WORKLIST.md")):
                (self.root / rel).unlink(missing_ok=True)
        self.git("init", "-q", "-b", "dev")
        self.commit_all("init")
        return self.head()

    def add_origin(self, name="origin"):
        """建一个本地 bare 远端并把 dev 推上去（离线、真实 ref，不是 mock）。

        `origin/<分支>` 是 S11 唯一的输入；用真远端而不是手写 `refs/remotes/...`，
        是为了让「推过 / 没推过 / 推过之后又改写」三种状态都真的产生。
        """
        if not self.origin.exists():
            self.git("init", "-q", "--bare", "-b", "dev", str(self.origin))
        self.git("remote", "remove", name)
        self.git("remote", "add", name, str(self.origin))
        self.git("push", "-q", name, "dev")

    def drop_origin(self, name="origin"):
        self.git("remote", "remove", name)

    # ── 夹具 ────────────────────────────────────────────────────────────────
    def write_all(self, *, seq=TEMPLATE, summary: str = SUMMARY,
                  ledger: list[dict] | None = None, worklist: str | None = WORKLIST_TMPL,
                  commit: str | None = None):
        if seq is TEMPLATE:
            text = SEQ_TMPL.format(commit=commit or self.sha)
        elif seq is None:
            text = None
        else:
            text = seq.replace("{commit}", commit or self.sha)
        if text is not None:
            osnorm.write_text(self.root / SEQ_REL, text, newline="lf")
        else:
            (self.root / SEQ_REL).unlink(missing_ok=True)
        if ledger is None:
            ledger = [ledger_entry(summary=summary, commit=commit or self.plan_sha)]
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

    def write_changelog(self, body):
        osnorm.write_text(self.root / "CHANGELOG_DEV.md", body, newline="lf")

    def run_script(self, *args):
        return osnorm.run_text([sys.executable, "-B", str(self.root / "scripts" / "sequence_check.py"),
                                "--root", str(self.root), *args], cwd=self.root, errors="replace")

    def status(self, proc):
        """把 stdout 解析成 {S1: PASS, ...}（人读输出每行 `S1 PASS …`）。"""
        out = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in {f"S{i}" for i in range(1, 12)}:
                out[parts[0]] = parts[1]
        return out


class Green(Base):
    def test_all_pass(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        st = self.status(p)
        self.assertEqual(st, {f"S{i}": "PASS" for i in range(1, 12)}, p.stdout)
        self.assertIn("→ OK", p.stdout)
        # 历史锚生效的证据：真在 git 里比对，而不是「当前文件自证」
        self.assertIn("git 锚", p.stdout)
        # 远端锚生效的证据：摘要必须写「本地 + 远端锚」而不是只有本地
        self.assertIn("锚 本地 + 远端锚", p.stdout)

    def test_json_shape(self):
        p = self.run_script("--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(data["ok"])
        self.assertFalse(data["skipped"])
        self.assertEqual([c["id"] for c in data["checks"]],
                         ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"])
        self.assertEqual([r["round"] for r in data["rounds"]], [44, 45, 46])
        self.assertEqual(data["ledger_entries"], 1)
        self.assertEqual(data["branch"], "dev")
        self.assertTrue(data["worklist"].endswith("WORKLIST.md"))
        self.assertEqual(data["degraded"], [])
        self.assertFalse(data["anchor"]["only_local"])
        self.assertEqual(data["anchor"]["remote_ref"], "origin/dev")


class Instance(Base):
    """实例侧 = 非模板仓（git 历史里从未出现 _DEV 文件）：缺计划文件才是合法 SKIP。"""

    def _make_instance(self):
        # 重建一个没有 _DEV 文件历史的仓（restart_history 会删掉事实文件再 init）
        self.sha = self.restart_history(keep_files=False)
        self.drop_origin()                          # 新历史与旧 origin/dev 无关：别让 S11 判红
        (self.root / LEDGER_REL).unlink(missing_ok=True)

    def test_missing_sequence_is_skip_rc0(self):
        self._make_instance()
        for extra in ((), ("--json",)):
            p = self.run_script(*extra)
            self.assertEqual(p.returncode, 0, "实例侧缺 SEQUENCE.md 必须 rc=0：" + p.stdout + p.stderr)
        p = self.run_script("--json")
        data = json.loads(p.stdout)
        self.assertTrue(data["skipped"])
        self.assertIn(".workspace/SEQUENCE.md", data["reason"])
        self.assertIn("S10", json.dumps(data, ensure_ascii=False))   # S10 不随计划侧一起跳过
        self.assertEqual(data["counts"]["SKIP"], 9)                  # S1–S9 声明式 SKIP
        self.assertEqual(data["counts"]["FAIL"], 0)
        self.assertIn("SKIP", self.run_script().stdout)

    def test_missing_everything_but_sequence_still_reports(self):
        """只有计划侧、没有账本：不崩，红在 S3（缺账本）。"""
        (self.root / LEDGER_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("不存在", p.stdout)


class PlanFileDeleted(Base):
    """B：dev 分支删计划文件 = FAIL，且 S10/S11 无论 skip 与否都要执行。"""

    def test_dev_without_sequence_is_fail_even_with_empty_ledger(self):
        self.write_ledger([])                       # 账本写 [] 也关不掉判据
        (self.root / SEQ_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        st = self.status(p)
        self.assertEqual(st["S1"], "FAIL", p.stdout)
        self.assertIn("dev 分支缺", p.stdout)
        self.assertIn("删掉计划文件不能把 S1–S9", p.stdout)
        # S10/S11 照常执行（不是整条 skip）
        self.assertEqual(st["S10"], "PASS", p.stdout)
        self.assertEqual(st["S11"], "PASS", p.stdout)
        self.assertIn("S10 PASS", p.stdout)
        self.assertIn("S11 PASS", p.stdout)

    def test_dev_without_sequence_json_is_single_object(self):
        (self.root / SEQ_REL).unlink()
        p = self.run_script("--json")
        self.assertEqual(p.returncode, 1, p.stdout)
        data = json.loads(p.stdout)                  # 不是人读文本
        self.assertFalse(data["ok"])
        self.assertTrue(data["skipped"])
        self.assertEqual(next(c for c in data["checks"] if c["id"] == "S1")["status"], "FAIL")
        self.assertEqual(data["counts"]["FAIL"], 1)


class S1Rounds(Base):
    def test_duplicate_round(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "### R46 · `46.3.8.0`", "### R45 · `45.3.8.0`"))
        p = self.run_script()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.status(p)["S1"], "FAIL")
        self.assertIn("R45 重复出现", p.stdout)

    def test_not_increasing(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "### R45 · `45.3.8.0`", "### R43 · `43.3.8.0`"))
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
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "### R46 · `46.3.8.0`", "### R46 · `45.3.8.0`"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S2"], "FAIL")
        self.assertIn("R46", p.stdout)
        self.assertIn("≠ 轮次 46", p.stdout)

    def test_not_four_segments(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "`46.3.8.0` · 互校", "`46.3.8` · 互校"))
        p = self.run_script()
        # 三段版本不能被当成「本轮不带版本」放过（fail-closed）
        self.assertEqual(self.status(p)["S2"], "FAIL", p.stdout)
        self.assertIn("不是合法 NN.X.Y.Z", p.stdout)


class S3Ledger(Base):
    def test_landed_without_ledger_names_round(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "- **依赖**：R45\n- **判据**：无",
            "- **依赖**：R45\n- **land_status**：landed\n- **判据**：无"))
        p = self.run_script()
        self.assertEqual(p.returncode, 1)
        st = self.status(p)
        self.assertEqual(st["S3"], "FAIL")
        self.assertEqual(st["S6"], "FAIL")       # 计数也跟着红（landed 2 ≠ 账本 1）
        self.assertIn("[R46]", p.stdout)
        self.assertIn("没有 round 46 的条目", p.stdout)

    def test_ledger_extra_round(self):
        self.write_all(ledger=[ledger_entry(commit=self.plan_sha),
                               ledger_entry("45.3.8.0", round_no=45, commit=self.plan_sha)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("账本多出", p.stdout)

    def test_landed_no_ledger_needs_commit(self):
        seq = SEQ_TMPL.format(commit=self.sha).replace(
            "| 44 | `44.3.8.0` | `" + self.sha + "` |", "| 44 | （并入上文） | （无） |")
        self.write_all(seq=seq)
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("landed-no-ledger 但提交列不是提交号", p.stdout)

    def test_landed_no_ledger_with_real_commit_ok(self):
        """机制轮（不占版本号）只要提交号**真在 git 里**、且从来不进账本，就不该红。"""
        self.sha = self.restart_history(keep_files=False)
        seq = SEQ_TMPL.format(commit=self.sha).replace(
            "| 44 | `44.3.8.0` | `" + self.sha + "` |", "| 44 | （并入上文） | `" + self.sha + "` |")
        self.write_all(seq=seq, ledger=[])
        self.commit_all()
        p = self.run_script()
        st = self.status(p)
        self.assertEqual(st["S3"], "PASS", p.stdout)
        self.assertEqual(st["S6"], "PASS", p.stdout)   # landed 0 == 账本 0
        self.assertEqual(st["S8"], "PASS", p.stdout)

    def test_landed_no_ledger_fake_commit_is_red(self):
        """提交号只验正则会放过编造的 sha；S3 必须走 git cat-file。"""
        seq = SEQ_TMPL.format(commit=self.sha).replace(
            "| 44 | `44.3.8.0` | `" + self.sha + "` |", "| 44 | （并入上文） | `deadbeef` |")
        self.write_all(seq=seq)
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL", p.stdout)
        self.assertIn("不在本仓 git 对象库", p.stdout)

    def test_invalid_land_status_is_red(self):
        """`land_status: done` 这类非法取值不许按 planned 静默放过。"""
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "- **依赖**：R45\n- **判据**：无",
            "- **依赖**：R45\n- **land_status**：done\n- **判据**：无"))
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S3"], "FAIL")
        self.assertIn("land_status 取值非法", p.stdout)
        self.assertIn("done", p.stdout)

    def test_landed_row_but_planned_in_section2_is_red(self):
        """§1 有落地行、§2 却写 planned：两边必须一致。"""
        twin = SEQ_TMPL.format(commit=self.sha) + (
            "\n### R44 · `44.3.8.0` · 重写块\n- **land_status**：planned\n")
        self.write_all(seq=twin)
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "FAIL", p.stdout)
        self.assertIn("§2 声明 land_status=planned", p.stdout)

    def test_paired_delete_is_red_by_history_anchor(self):
        """① 同时删 §1 landed 行 + 账本条目：当前两边「一致」，只有 git 历史看见少了。"""
        seq = "\n".join(line for line in SEQ_TMPL.format(commit=self.sha).splitlines()
                        if not line.startswith("| 44 |"))
        self.write_all(seq=seq + "\n", ledger=[])
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        st = self.status(p)
        self.assertEqual(st["S3"], "FAIL", p.stdout)
        self.assertIn("曾在账本里", p.stdout)
        self.assertIn("账本 append-only", p.stdout)
        self.assertEqual(st["S6"], "FAIL", p.stdout)      # 历史下界：账本 <1 / landed <1
        self.assertEqual(st["S8"], "FAIL", p.stdout)      # 历史条目被删除
        self.assertIn("账本历史条目被删除", p.stdout)


class S3HistoryRoundSet(Base):
    """F：历史轮次集合必须是「历史账本轮次 ∪ 历史 SEQUENCE 曾落地轮次」。

    R45 这类机制轮**不进账本**（landed-no-ledger），所以只看账本历史完全看不见它被删掉。
    """

    def _write45(self, *, dep="R45", drop45=False):
        text = SEQ_TMPL_45.replace("@SHA@", self.plan_sha).replace("@DEP@", dep)
        if drop45:
            text = "\n".join(line for line in text.splitlines() if not line.startswith("| 45 |"))
        self.write_all(seq=text + "\n")

    def test_baseline_green(self):
        self._write45()
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "PASS", p.stdout)

    def test_delete_landed_no_ledger_row_and_redirect_dep_is_red(self):
        """删 §1 的 R45 行 + 把 R46 依赖从 R45 改 R44 → 当前文件自洽，历史锚必须判红。"""
        self._write45()
        self.commit_all("has R45")
        self._write45(dep="R44", drop45=True)
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S3"], "FAIL", p.stdout)
        self.assertIn("R45 曾在 SEQUENCE 标 landed/landed-no-ledger", p.stdout)
        self.assertIn("落地不可撤销/降级", p.stdout)
        # 当前文件之间是自洽的：依赖没悬空、计数也没变 —— 红只可能来自历史锚
        self.assertEqual(self.status(p)["S4"], "PASS", p.stdout)


class S4Deps(Base):
    def test_inverted(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace("- **依赖**：R44", "- **依赖**：R46"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S4"], "FAIL")
        self.assertIn("[R45]", p.stdout)
        self.assertIn("依赖倒置", p.stdout)

    def test_dangling(self):
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace("- **依赖**：R44", "- **依赖**：R99"))
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
        self.assertIn("声明为豁免", p.stdout)
        self.assertIn(str(self.root), p.stdout)          # 注明找过哪些路径

    def test_explicit_worklist_path_typo_is_red(self):
        """B：显式 `--worklist` 指错不许退化成 SKIP（否则拼错就等于关掉 S5）。"""
        p = self.run_script("--worklist", "WORKLIST.md.typo")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S5"], "FAIL")
        self.assertIn("不存在或不可读", p.stdout)
        self.assertIn("拼错不许退化成 SKIP", p.stdout)

    def test_explicit_worklist_absolute_missing_is_red(self):
        """B：绝对路径形式（`/tmp/does-not-exist`）同样必须红。"""
        p = self.run_script("--worklist", "/tmp/does-not-exist-r46d")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S5"], "FAIL")
        self.assertIn("/tmp/does-not-exist-r46d", p.stdout)

    @unittest.skipIf(os.name == "nt", "Windows 没有 POSIX 权限位：用目录名覆盖「解析不到」那一路")
    def test_explicit_worklist_unreadable_is_red(self):
        """B：文件存在但读不了（chmod 000）→ 红，不是 SKIP、也不是 IO 崩溃 rc=2。"""
        target = self.root / "WORKLIST.md"
        os.chmod(target, 0o000)
        try:
            p = self.run_script("--worklist", str(target))
        finally:
            os.chmod(target, 0o644)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.status(p)["S5"], "FAIL")
        self.assertIn("不可读", p.stdout)

    def test_explicit_worklist_ok(self):
        p = self.run_script("--worklist", str(self.root / "WORKLIST.md"))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S5"], "PASS")

    def test_dangling_worklist_ref(self):
        self.write_all(worklist=WORKLIST_TMPL.replace("| C2 | 互校脚本 |\n", ""))
        p = self.run_script()
        self.assertEqual(self.status(p)["S5"], "FAIL")
        self.assertIn("[R46]", p.stdout)
        self.assertIn("C2", p.stdout)


class S7Prose(Base):
    def test_one_char_drift(self):
        # 只让账本漂移一个字符，CHANGELOG_DEV.md 保持原文 —— 这正是 R46 之前真实发生过的那次
        self.write_ledger([ledger_entry(summary=SUMMARY.replace("R35–R44", "R35–R45"),
                                        commit=self.plan_sha)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL")
        self.assertIn("prose 漂移", p.stdout)
        self.assertIn("CHANGELOG_DEV.md", p.stdout)
        self.assertIn("R35–R45", p.stdout)               # 账本侧
        self.assertIn("R35–R44", p.stdout)               # 段落侧

    def test_missing_section(self):
        self.write_changelog("# CHANGELOG_DEV\n\n## 43.3.8.0（x）\n\n无\n")
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL")
        self.assertIn("没有 `## 44.3.8.0`", p.stdout)

    def test_summary_rewrite_with_synced_changelog_is_red(self):
        """② 账本 summary 与 CHANGELOG **一起**改：工作区腿一致，但 git 锚腿不一致。"""
        new = SUMMARY.replace("R35–R44", "R35–R45")
        self.write_ledger([ledger_entry(summary=new, commit=self.plan_sha)])
        self.write_changelog(f"# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n{new}\n")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S7"], "FAIL", p.stdout)
        self.assertIn("git 锚", p.stdout)
        self.assertEqual(self.status(p)["S8"], "FAIL", p.stdout)   # 历史条目被改写（第二道锚）

    def test_committed_entry_uses_its_commit_as_anchor(self):
        """条目带真提交号时，锚必须取 `git show <commit>:CHANGELOG_DEV.md`。"""
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "PASS", p.stdout)
        self.assertIn(f"{self.plan_sha[:7]}:CHANGELOG_DEV.md", p.stdout)

    def test_fake_entry_commit_is_red(self):
        self.write_ledger([dict(ledger_entry(), commit="deadbeef")])
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL", p.stdout)
        self.assertIn("不在本仓 git 对象库", p.stdout)
        self.assertEqual(self.status(p)["S8"], "FAIL", p.stdout)

    def test_middle_entry_drift_is_checked_too(self):
        """S7 逐条查，不是只看末条：中间那条漂移一个字符也要红。"""
        summary45 = "> 收录轮次 **R45**。\n\n- 新条目。"
        self.sha = self.restart_history(keep_files=False)
        self.write_all(ledger=[])
        self.commit_all("plan")
        c1 = self.head()
        self.write_ledger([ledger_entry(commit=c1),
                           ledger_entry("45.3.8.0", summary45, round_no=45, commit=c1)])
        self.write_changelog("# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n" + SUMMARY +
                             "\n\n## 45.3.8.0（2026-11-24）\n\n" + summary45 + "\n")
        self.commit_all("two entries")
        drift = [ledger_entry(summary=SUMMARY.replace("R35–R44", "R35–R99"), commit=c1),
                 ledger_entry("45.3.8.0", summary45, round_no=45, commit=c1)]
        self.write_ledger(drift)
        p = self.run_script()
        self.assertEqual(self.status(p)["S7"], "FAIL", p.stdout)
        self.assertIn("44.3.8.0", p.stdout)

    def test_uncommitted_entry_is_degraded_and_rc0(self):
        """E：未提交条目（commit: null）→ 独立状态 DEGRADED，不算 FAIL，但必须明示。

        夹具刻意让**当前账本与历史账本唯一版本逐字相同**（都是 commit: null），
        这样 S8 不红、rc=0 —— 证明 DEGRADED 本身不会把门禁判失败，同时它绝不被当成 PASS。
        """
        self.sha = self.restart_history(keep_files=False)
        self.plan_sha = self.sha
        self.write_all(ledger=[ledger_entry(commit=None)])   # 账本条目 commit=None
        self.commit_all("uncommitted ledger")
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)          # DEGRADED 不计 FAIL
        self.assertEqual(self.status(p)["S7"], "DEGRADED", p.stdout)   # 但也不是 PASS
        self.assertIn("DEGRADED(S7)", p.stdout)              # 人读摘要计数
        self.assertIn("无 git 锚", p.stdout)                  # 原因写清楚
        data = json.loads(self.run_script("--json").stdout)
        self.assertEqual(data["degraded"], ["S7"])           # 机器可读
        self.assertEqual(data["counts"]["DEGRADED"], 1)
        self.assertEqual(next(c for c in data["checks"] if c["id"] == "S7")["status"], "DEGRADED")
        self.assertTrue(data["ok"])


class S8Immutability(Base):
    def test_landed_version_tampered(self):
        # 计划侧把已落地版本改掉、账本仍是事实侧的旧值 → S8 必须判「已落地版本不可变」
        self.write_all(seq=SEQ_TMPL.format(commit=self.sha).replace(
            "| 44 | `44.3.8.0` |", "| 44 | `44.3.9.0` |"))
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("已落地版本被改", p.stdout)

    def test_ledger_not_increasing(self):
        self.write_all(ledger=[ledger_entry(commit=self.plan_sha),
                               ledger_entry("44.3.8.0", round_no=44, commit=self.plan_sha)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("账本版本未严格递增", p.stdout)

    def test_ledger_round_field_mismatch(self):
        self.write_all(ledger=[dict(ledger_entry(commit=self.plan_sha), round=45)])
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL")
        self.assertIn("round 字段", p.stdout)

    def test_history_entry_rewrite_is_red(self):
        """改写已提交条目的结构性事实（date）→ 与历史版本不一致。"""
        self.write_ledger([dict(ledger_entry(commit=self.plan_sha), date="2026-11-20")])
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "FAIL", p.stdout)
        self.assertIn("账本历史条目被改写", p.stdout)

    def test_committed_summary_rewrite_with_synced_changelog_is_red(self):
        """C：改旧 R44 的 ledger summary + 同步改 CHANGELOG → **普通 commit**（不 amend）仍须红。

        R46d 的 `strict=False` 把已提交历史版本之间的 summary 差异降级成 prose_note，
        于是这条路径 rc=0 / S8 PASS。现在已提交条目之间也是全字段前缀比较。
        """
        new = SUMMARY.replace("R35–R44", "R35–R45")
        self.write_ledger([ledger_entry(summary=new, commit=self.plan_sha)])
        self.write_changelog(f"# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n{new}\n")
        self.commit_all("prose rewrite without amend")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S8"], "FAIL", p.stdout)
        self.assertIn("账本历史条目被改写", p.stdout)
        self.assertIn("append-only 覆盖全部字段", p.stdout)

    def test_no_git_is_red_not_silently_green(self):
        """没有 git = 锚不可用：不许退化成「当前文件自证」的绿。"""
        shutil.rmtree(self.root / ".git")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S8"], "FAIL", p.stdout)
        self.assertIn("git 锚不可用", p.stdout)
        self.assertEqual(self.status(p)["S10"], "DEGRADED", p.stdout)

    def test_committed_append_is_green(self):
        """正常追加（新轮次已提交）→ 历史前缀一致，S8 不该红。"""
        summary45 = "> 收录轮次 **R45**。\n\n- 新条目。"
        self.write_ledger([ledger_entry(commit=self.plan_sha),
                           ledger_entry("45.3.8.0", summary45, round_no=45, commit=self.plan_sha)])
        self.commit_all("append")
        p = self.run_script()
        self.assertEqual(self.status(p)["S8"], "PASS", p.stdout)


class S9Seats(Base):
    def _landed_with(self, confirm: str, note: str = "三席齐", round_no: int = 44):
        seq = SEQ_TMPL.format(commit=self.sha)
        if round_no == 44:
            seq = seq.replace("| `legacy`（机制建立前） | 首条 |", f"| {confirm} | {note} |")
        else:
            seq = seq.replace("| 44 | `44.3.8.0` |", f"| {round_no} | `{round_no}.3.8.0` |")
            seq = seq.replace("| `legacy`（机制建立前） | 首条 |", f"| {confirm} | {note} |")
        self.write_all(seq=seq)

    def test_three_seats_pass(self):
        self._landed_with(CONFIRM_OK)
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "PASS", p.stdout)
        self.assertIn("三席确认 1 轮齐全", p.stdout)

    def test_fullwidth_bracket_seats_are_green(self):
        """E：`verified_by：【SOL、GPT-SOL-6、PRO】`（全角括号/顿号）曾被解析成空名单误红。"""
        self._landed_with("verified_by：【SOL、GPT-SOL-6、PRO】；verdict=pass；verified_at=2026-11-23")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "PASS", p.stdout)
        self.assertIn("三席确认 1 轮齐全", p.stdout)

    def test_landed_no_ledger_without_seats_is_red(self):
        """D：§1 插一行 landed-no-ledger（真 sha、无三席字段）不再能跳过 S9。"""
        row44 = ("| 44 | `44.3.8.0` | `{sha}` | R35–R44 全集 | "
                 "`legacy`（机制建立前） | 首条 |").format(sha=self.sha)
        row47 = ("| 47 | （并入 44 条） | `{sha}` | 机制轮 | （无） | 想蒙混 |").format(sha=self.sha)
        seq = SEQ_TMPL.format(commit=self.sha).replace(row44, row44 + "\n" + row47)
        self.write_all(seq=seq)
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("没有任何 verified_by", p.stdout)

    def test_landed_no_ledger_with_legacy_note_stays_green(self):
        """D 的对照：R45 是白名单 legacy 轮次且写了 note → 仍豁免（不误伤机制轮）。"""
        text = SEQ_TMPL_45.replace("@SHA@", self.plan_sha).replace("@DEP@", "R45")
        self.write_all(seq=text)
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "PASS", p.stdout)


    def test_only_two_seats_is_red(self):
        """⑤ landed 但 verified_by 只有两席。"""
        self._landed_with("verified_by=[SOL, GPT-SOL-6]；verdict=pass；verified_at=2026-11-23")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("三席不全", p.stdout)
        self.assertIn("PRO", p.stdout)

    def test_unknown_seat_is_red(self):
        """D：白名单外的席位名（INTRUDER）不许被当成「多写了一个」放过。"""
        self._landed_with("verified_by=[SOL, GPT-SOL-6, PRO, INTRUDER]；verdict=pass；"
                          "verified_at=2026-11-23")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("未知席位", p.stdout)
        self.assertIn("INTRUDER", p.stdout)

    def test_legacy_on_new_round_is_red(self):
        """C：新轮次（R46，不在 LEGACY_ROUNDS 里）自称 legacy → 红。

        `legacy` 是「机制建立前」的豁免，白名单写死在脚本里；否则任何新轮次写一个
        legacy 就绕掉了三席确认。
        """
        row44 = ("| 44 | `44.3.8.0` | `{sha}` | R35–R44 全集 | "
                 "`legacy`（机制建立前） | 首条 |").format(sha=self.sha)
        row46 = ("| 46 | `46.3.8.0` | `{sha}` | 新轮次 | "
                 "`legacy`（自称机制前） | 想蒙混 |").format(sha=self.sha)
        seq = """# SEQUENCE — 夹具

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 确认（verified_by / verdict） | 备注 |
|---|---|---|---|---|---|
{r44}
{r46}
""".format(r44=row44, r46=row46)
        self.write_all(seq=seq, ledger=[ledger_entry(round_no=44, commit=self.plan_sha),
                                        ledger_entry("46.3.8.0", round_no=46, commit=self.plan_sha)])
        self.write_changelog("# CHANGELOG_DEV\n\n## 44.3.8.0（2026-11-23）\n\n" + SUMMARY +
                             "\n\n## 46.3.8.0（2026-11-24）\n\n" + SUMMARY + "\n")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("不在 legacy 白名单", p.stdout)
        self.assertIn("[44, 45]", p.stdout)                  # 白名单原文要打印出来

    def test_bad_verdict_is_red(self):
        self._landed_with("verified_by=[SOL, GPT-SOL-6, PRO]；verdict=no；verified_at=2026-11-23")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("verdict 取值非法", p.stdout)

    def test_pass_with_notes_needs_notes(self):
        self._landed_with("verified_by=[SOL, GPT-SOL-6, PRO]；verdict=pass-with-notes；"
                          "verified_at=2026-11-23", note="")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("notes 为空", p.stdout)

    def test_verified_at_after_ledger_date_is_red(self):
        """先记账后确认 = 账本记录了自己没验过的事。"""
        self._landed_with("verified_by=[SOL, GPT-SOL-6, PRO]；verdict=pass；verified_at=2026-12-01")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("先记账后确认", p.stdout)

    def test_legacy_without_note_is_red(self):
        self._landed_with("`legacy`", note="")
        p = self.run_script()
        self.assertEqual(self.status(p)["S9"], "FAIL", p.stdout)
        self.assertIn("没有写原因", p.stdout)


class SeatsParsing(unittest.TestCase):
    """E：§2 的 `verified_by: [...]` 必须解析出三个席位名（多种写法都合法）。"""

    def test_parse_seats_variants(self):
        for raw in ("[SOL, GPT-SOL-6, PRO]", "【SOL、GPT-SOL-6、PRO】",
                    "[SOL,GPT-SOL-6,PRO]", " SOL ， GPT-SOL-6 、 PRO "):
            self.assertEqual(sc._parse_seats(raw), ["SOL", "GPT-SOL-6", "PRO"], raw)

    def test_section2_bracket_fields_parse_and_pass_s9(self):
        text = """# SEQUENCE — 夹具

## 2. 计划（planned）

### R47 · `47.3.8.0` · 新轮
- **范围**：x
- **依赖**：R44
- **land_status**：landed
- **verified_by**：[SOL, GPT-SOL-6, PRO]
- **verdict**：pass
- **verified_at**：2026-11-23
"""
        rounds = sc.parse_sequence(text)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].verified_by, ["SOL", "GPT-SOL-6", "PRO"])
        status, detail, findings = sc.check_s9(rounds, [])
        self.assertEqual(status, "PASS", detail + repr(findings))


class LandedImpliesLedger(Base):
    """H：某轮首次在历史 SEQUENCE 里标成 landed 的提交，账本必须同时已含该轮。"""

    def test_flip_to_landed_without_ledger_is_red(self):
        seq = SEQ_TMPL.format(commit=self.sha).replace(
            "- **依赖**：R45\n- **判据**：无",
            "- **依赖**：R45\n- **land_status**：landed\n"
            "- **verified_by**：[SOL, GPT-SOL-6, PRO]\n- **verdict**：pass\n"
            "- **verified_at**：2026-11-23\n- **判据**：无")
        self.write_all(seq=seq)
        self.commit_all("flip R46 to landed without ledger")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S3"], "FAIL", p.stdout)
        self.assertIn("落地必须与入账同批", p.stdout)

    def test_first_landed_with_ledger_in_same_commit_passes(self):
        """H 的对照：fixture 的 R44 首次落地提交已含账本条目 → S3 不因 H 误红。"""
        p = self.run_script()
        self.assertEqual(self.status(p)["S3"], "PASS", p.stdout)
        self.assertIn("落地即入账 1 轮同批", p.stdout)


class JsonEverywhere(Base):
    """F：`--json` 在任何结局都只输出一个 JSON 对象（含 S10 FAIL / 计划侧 SKIP）。"""

    def test_json_on_s10_fail_path(self):
        self.git("checkout", "-q", "-b", "main")
        (self.root / SEQ_REL).unlink()              # 计划侧缺失，走曾经的「人读文本」分支
        p = self.run_script("--json")
        self.assertEqual(p.returncode, 1, p.stdout)
        data = json.loads(p.stdout)                  # 不是人读文本
        self.assertFalse(data["ok"])
        self.assertTrue(data["skipped"])
        for key in ("ok", "checks", "counts", "degraded", "warned", "skipped", "skipped_checks"):
            self.assertIn(key, data)
        s10 = next(c for c in data["checks"] if c["id"] == "S10")
        self.assertEqual(s10["status"], "FAIL")
        self.assertEqual(data["counts"]["FAIL"], 1)
        self.assertEqual(data["counts"]["SKIP"], 8)          # S2–S9；S1 在 main 上是 WARN


class S10Branch(Base):
    def test_dev_branch_with_both_files_pass(self):
        p = self.run_script()
        self.assertEqual(self.status(p)["S10"], "PASS", p.stdout)

    def test_main_branch_with_dev_files_is_red(self):
        """⑥ main 分支上留 CHANGELOG_DEV.md。"""
        self.git("checkout", "-q", "-b", "main")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S10"], "FAIL", p.stdout)
        self.assertIn("main 分支上存在 _DEV 文件", p.stdout)
        self.assertIn("CHANGELOG_DEV.md", p.stdout)

    def test_main_branch_without_sequence_still_checks_s10(self):
        """A：删掉计划侧不能成为绕过 S10 的办法（main 的合法态正是没有 SEQUENCE.md）。"""
        self.git("checkout", "-q", "-b", "main")
        (self.root / SEQ_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("S10", p.stdout)
        self.assertEqual(self.status(p)["S10"], "FAIL", p.stdout)

    def test_main_branch_without_sequence_and_without_dev_files_is_green(self):
        """A：main + 无 SEQUENCE.md + 无 _DEV 文件 = 合法发布态，rc=0。"""
        self.git("checkout", "-q", "-b", "main")
        for rel in (SEQ_REL, Path("CHANGELOG_DEV.md"), LEDGER_REL):
            (self.root / rel).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("S10 PASS", p.stdout)

    def test_clean_main_is_green(self):
        self.git("checkout", "-q", "-b", "main")
        for rel in (SEQ_REL, Path("CHANGELOG_DEV.md"), LEDGER_REL):
            (self.root / rel).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("S10 PASS", p.stdout)

    def test_dev_branch_without_sequence_but_missing_dev_file_is_red(self):
        """A：dev 上缺 SEQUENCE.md 仍要判「两文件必须在」——缺账本就红。"""
        (self.root / SEQ_REL).unlink()
        (self.root / LEDGER_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S10"], "FAIL", p.stdout)
        self.assertIn("dev 分支缺 _DEV 文件", p.stdout)

    def test_other_branch_warns_but_does_not_red(self):
        self.git("checkout", "-q", "-b", "feature/x")
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S10"], "WARN", p.stdout)
        self.assertIn("WARN(S10", p.stdout)

    def test_other_branch_without_sequence_warns(self):
        """A：其它分支即使没有 SEQUENCE.md 也只 WARN（不判红、也不静默 skip）。"""
        self.git("checkout", "-q", "-b", "feature/y")
        (self.root / SEQ_REL).unlink()
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S10"], "WARN", p.stdout)

    def test_dev_branch_missing_file_is_red(self):
        (self.root / LEDGER_REL).unlink()
        p = self.run_script()
        self.assertEqual(self.status(p)["S10"], "FAIL", p.stdout)
        self.assertIn("dev 分支缺 _DEV 文件", p.stdout)

    def test_bootstrap_json_cannot_disable_s10(self):
        """A：把 bootstrap.workspace_status 改成 active 也关不掉 S10（身份只看 git 锚）。"""
        self.git("checkout", "-q", "-b", "main")
        osnorm.write_text(self.root / TL_REL,
                          json.dumps({"schema_version": 1, "workspace_status": "active"}),
                          newline="lf")
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S10"], "FAIL", p.stdout)
        self.assertIn("main 分支上存在 _DEV 文件", p.stdout)

    def test_bootstrap_json_active_on_dev_still_judges(self):
        """A：JSON 同样不能把 S10 变成「不判」——dev 上两文件齐 → PASS 仍然判。"""
        osnorm.write_text(self.root / TL_REL,
                          json.dumps({"schema_version": 1, "workspace_status": "active"}),
                          newline="lf")
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S10"], "PASS", p.stdout)

    def test_non_template_repo_warns_even_with_template_status(self):
        """A：非模板仓（历史里没有 _DEV 文件）只 WARN；JSON 写 template 也变不成模板仓。"""
        self.sha = self.restart_history(keep_files=False)   # 历史里没有 _DEV 文件
        self.drop_origin()
        osnorm.write_text(self.root / TL_REL,
                          json.dumps({"schema_version": 1, "workspace_status": "template"}),
                          newline="lf")
        self.commit_all("non-template")
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S10"], "WARN", p.stdout)
        self.assertIn("非模板仓", p.stdout)


class S11RemoteAnchor(Base):
    def test_origin_ancestor_is_pass(self):
        p = self.run_script()
        self.assertEqual(self.status(p)["S11"], "PASS", p.stdout)
        self.assertIn("本地 + 远端锚", p.stdout)

    def test_no_origin_warns_and_says_only_local(self):
        """⑨ 没有 origin：只 WARN，且摘要必须明示「仅有本地锚」。"""
        self.drop_origin()
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(p)["S11"], "WARN", p.stdout)
        self.assertIn("仅有本地锚", p.stdout)
        self.assertIn("锚 仅有本地锚", p.stdout)            # 摘要行也要写
        data = json.loads(self.run_script("--json").stdout)
        self.assertTrue(data["anchor"]["only_local"])
        self.assertEqual(data["warned"].count("S11"), 1)

    def test_rewritten_local_history_is_red(self):
        """⑧ 本地历史被改写（origin 仍指着改写前的提交）→ 不是超集 → 红。"""
        before = self.head()
        self.git("-c", "user.name=x", "-c", "user.email=x@y", "commit", "-q", "--amend",
                 "-m", "rewritten")
        self.assertNotEqual(before, self.head())
        p = self.run_script()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.status(p)["S11"], "FAIL", p.stdout)
        self.assertIn("本地历史不是 origin/dev 的超集", p.stdout)
        data = json.loads(self.run_script("--json").stdout)
        self.assertIn("S11", data["counts"] and [c["id"] for c in data["checks"]
                                                if c["status"] == "FAIL"])

    def test_unpushed_branch_warns(self):
        """尚未推送的分支（origin/<branch> 不存在）→ WARN，不是红。"""
        self.git("checkout", "-q", "-b", "dev-next")
        p = self.run_script()
        self.assertEqual(self.status(p)["S11"], "WARN", p.stdout)
        self.assertIn("origin/dev-next", p.stdout)
        self.assertIn("仅有本地锚", p.stdout)


class DocsCapabilityList(unittest.TestCase):
    """G：SEQUENCE.md §4.1 的「能挡 / 不能挡」清单必须与机械判据的实际能力一致。

    这条是**文档口径的机械断言**，不是实现断言：R46d 写了「能挡三文本同步改写」，
    而 PRO 实跑「同步改写后普通提交」全绿 —— 过度承诺本身就是缺陷（读者会据此少设防）。
    清单里的每一项都对应一个真有构造测试的 S 判据；「不能挡」必须点名主体边界。
    """

    def _block(self) -> str:
        if not (ROOT / SEQ_REL).is_file():
            self.skipTest("当前树没有 SEQUENCE.md（实例侧/发布夹具）：文档口径不归这里管")
        text = osnorm.read_text(ROOT / SEQ_REL)
        start = text.index("### 4.1")
        end = text.index("\n## ", start) if "\n## " in text[start:] else len(text)
        return text[start:end]

    def test_can_block_list_is_grounded(self):
        block = self._block()
        for item in ("能挡", "误删", "误改已提交的历史条目", "成对删除", "依赖倒置",
                     "landed 与账本脱钩", "计划文件缺失"):
            self.assertIn(item, block)

    def test_cannot_block_list_names_the_boundary(self):
        block = self._block()
        for item in ("不能挡", "追加伪造的轮次", "伪造三席确认", "重写整条可达历史",
                     "本地改写而远端同谋"):
            self.assertIn(item, block)
        self.assertIn("AGENT_RULES.md §12 R100", block)      # 主体边界必须指向政策条款
        self.assertIn("仓内判据防漂移", block)
        self.assertIn("不防主动伪造", block)


class DocsTemplateFilesClaim(unittest.TestCase):
    """I：§0 的「在 / 不在 TEMPLATE_FILES」口径必须与 verify_rules.TEMPLATE_FILES 实际内容一致。"""

    def test_dev_file_claim_matches_template_files(self):
        import verify_rules
        if not (ROOT / SEQ_REL).is_file():
            self.skipTest("当前树没有 SEQUENCE.md（实例侧/发布夹具）：无 §0 边界段可对")
        text = osnorm.read_text(ROOT / SEQ_REL)
        start = text.index("**边界**")
        para = text[start:text.index("\n\n", start)]
        # 两份 _DEV 文件确实在清单里（:58 / :112 一类的事实由这里现场取，不写死数字）
        for rel in ("CHANGELOG_DEV.md", ".workspace/release_dev.json"):
            self.assertIn(rel, verify_rules.TEMPLATE_FILES, rel)
            self.assertIn(rel.split("/")[-1], para, rel)
        # 文档口径：在清单内、但只存在于 dev 分支（旧措辞「不进 TEMPLATE_FILES」= 分叉）
        self.assertIn("在 `TEMPLATE_FILES` 清单内", para)
        self.assertNotIn("不进 `TEMPLATE_FILES`", para)
        self.assertIn("只存在于 dev 分支", para)


class RealTree(unittest.TestCase):
    """真实树上的契约：有 SEQUENCE.md 就必须全绿；没有必须显式 skip + rc=0。"""

    def test_real_tree(self):
        p = osnorm.run_text([sys.executable, "-B", str(ROOT / "scripts" / "sequence_check.py")],
                            cwd=ROOT, errors="replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        if (ROOT / SEQ_REL).is_file():
            # S5 可声明式 SKIP（WORKLIST.md 是工作区级文件）；S7 在账本末条未提交时是
            # DEGRADED（显式降级，不是绿）；S10 在非 dev/main 分支上 WARN；
            # S11 在 origin/<当前分支> 未推送时 WARN（「仅有本地锚」）。
            allowed = {5: ("PASS", "SKIP"), 7: ("PASS", "DEGRADED"), 10: ("PASS", "WARN"),
                       11: ("PASS", "WARN")}
            for i in range(1, 12):
                ok = any(f"S{i} {s}" in p.stdout for s in allowed.get(i, ("PASS",)))
                self.assertTrue(ok, f"S{i} 在真实树上不是在允许的状态里：\n{p.stdout}")
            self.assertIn("→ OK", p.stdout)
        else:
            self.assertIn("SKIP", p.stdout)


if __name__ == "__main__":
    unittest.main()
