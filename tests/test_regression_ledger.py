#!/usr/bin/env python3
"""回归台账机械化：把 `REGRESSION-BACKLOG.md` 的条目钉成断言。

台账来源：`~/.cache/seat-logs/REGRESSION-BACKLOG.md`（分桶 A = D4→G5→G6 历史遗留，
分桶 B = 本轮修复引入）。本文件只做一件事：**每条断言上方写台账 ID**，以后再改造，
删掉哪条修复就会红。

写法约定
--------
1. 优先走**生产入口**：`scripts/policy_facts.py --check`（子进程）、
   `scripts/pyenv_init.py --apply`（子进程）、`scripts/repo_identity.repair/classify`
   （这两个就是该模块的对外入口，没有第二层包装）。
2. 每条变异都在**临时副本**上做（`tempfile.TemporaryDirectory` + 复制必要文件），
   真实仓库一个字节都不动。`--check` 跑的是副本里的 `scripts/policy_facts.py`——
   该脚本用 `__file__` 推 ROOT，跑副本才会检查副本。
3. 每条变异都断言 **returncode + 关键 finding 文本**，且副本基线先自证是绿的
   （`setUpClass` 里断言），否则「绿」没有意义。
4. `accepted` 类条目不判是**设计决定**，不是漏测：G5-02 / G5-03 用正向断言证明
   「现在确实不判」，防止有人偷偷把开放词表判据加回来。

运行：`python3 -m unittest tests.test_regression_ledger -v`（整套 < 30s）
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PLUGIN_YAML = Path(".hermes/plugins/workspace-gate/plugin.yaml")

sys.path.insert(0, str(SCRIPTS))
from repo_identity import classify, repair  # noqa: E402

_spec = importlib.util.spec_from_file_location("_policy_facts_ledger", SCRIPTS / "policy_facts.py")
assert _spec is not None and _spec.loader is not None
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)

# 副本必须齐的权威源/判据面：缺一个，`--check` 的基线就不再是绿的。
_DOC_FILES = ("AGENT_RULES.md", "CHANGELOG.md", "README.md", "README_EN.md",
              "CONTRIBUTING.md", "SECURITY.md", "UPGRADE.md")


def build_fixture(base: Path) -> Path:
    """造一个与真实仓储**同构且绿**的最小副本（只复制判据面需要的文件）。"""
    base.mkdir(parents=True)
    for name in _DOC_FILES:
        shutil.copy2(ROOT / name, base / name)
    (base / "docs").mkdir()
    for path in sorted((ROOT / "docs").glob("*.md")):
        shutil.copy2(path, base / "docs" / path.name)
    # scripts 计数与 `_load_verify_rules` 都读这个目录，必须整份搬（只搬 .py）。
    (base / "scripts").mkdir()
    for path in sorted(SCRIPTS.glob("*.py")):
        shutil.copy2(path, base / "scripts" / path.name)
    for path in sorted((ROOT / ".workspace/skills").glob("*/SKILL.md")):
        dst = base / ".workspace" / "skills" / path.parent.name
        dst.mkdir(parents=True)
        shutil.copy2(path, dst / "SKILL.md")
    (base / ".workspace").mkdir(exist_ok=True)
    shutil.copy2(ROOT / ".workspace/bootstrap.json", base / ".workspace/bootstrap.json")
    shutil.copy2(ROOT / ".workspace/releases.json", base / ".workspace/releases.json")
    (base / PLUGIN_YAML).parent.mkdir(parents=True)
    shutil.copy2(ROOT / PLUGIN_YAML, base / PLUGIN_YAML)
    return base


class PolicyFactsLedger(unittest.TestCase):
    """分桶 A 的 11 条 + 分桶 B 里由 `scripts/policy_facts.py` 承担的条目。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="regression-ledger-")
        cls.base = build_fixture(Path(cls._tmp.name) / "base")
        cls.cur = pf.policy_version(cls.base)                 # 政策头（如 3.7.0）
        cls.plugin = pf.plugin_version(cls.base)              # plugin.yaml:2
        cls.scripts = pf.scripts_count(cls.base)
        cls.ledger = pf.changelog_versions(cls.base) or ()
        # 台账里的历史版本：取 CHANGELOG 第二条（当前 3.6.1），不写死版本号。
        cls.hist = cls.ledger[1] if len(cls.ledger) > 1 else ""
        cls.lo, cls.hi = (int(x) for x in pf.clause_range(cls.base)[1:].split("..R"))
        # 基线自证：副本绿才算「变异 → 红」有效（否则红可能来自复制不全）。
        cls.base_findings = pf.check(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.base_findings, [], "副本基线不干净，本用例无意义")
        for attr in ("cur", "hist"):
            self.assertTrue(getattr(self, attr), f"{attr} 取不到值，台账用例前提失效")
        self.assertNotIn("9.9.9", self.ledger, "9.9.9 成了历史版本，臆造版本用例失效")

    # ── 工具 ────────────────────────────────────────────────────────────────
    def case(self) -> Path:
        """基线的一份私有副本（变异只发生在它身上）。"""
        tmp = tempfile.TemporaryDirectory(prefix="regression-case-")
        self.addCleanup(tmp.cleanup)
        dst = Path(tmp.name) / "repo"
        shutil.copytree(self.base, dst)
        return dst

    @staticmethod
    def append(root: Path, rel: str, *lines: str) -> None:
        path = root / rel
        text = path.read_text(encoding="utf-8").rstrip("\n")
        path.write_text(text + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def replace(root: Path, rel: str, old: str, new: str) -> None:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{rel} 里找不到 {old!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def check(self, root: Path) -> tuple[int, str]:
        """生产入口：副本里的 `--check`（不用 `check()`，CLI 才是门禁口径）。"""
        proc = subprocess.run(
            [sys.executable, "-B", str(root / "scripts" / "policy_facts.py"), "--check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout + proc.stderr

    def assert_red(self, root: Path, needle: str) -> str:
        rc, out = self.check(root)
        self.assertNotEqual(rc, 0, f"--check 期望红却绿（{needle}）：\n{out}")
        self.assertIn(needle, out, f"红了但 finding 不含「{needle}」：\n{out}")
        return out

    def assert_green(self, root: Path) -> str:
        rc, out = self.check(root)
        self.assertEqual(rc, 0, f"--check 期望绿却红：\n{out}")
        self.assertIn("policy facts: ok", out)
        return out

    def assert_entries_agree(self, root: Path, needle: str) -> None:
        """G6-06/07/R18-05 的口径：CLI 与测试侧 `check()` 对同一变异**同判**。"""
        rc, out = self.check(root)
        cli = [line[len("FAIL "):] for line in out.splitlines() if line.startswith("FAIL ")]
        module = [finding.splitlines()[0] for finding in pf.check(root)]
        self.assertNotEqual(rc, 0, f"--check 侧绿了（入口分叉）：\n{out}")
        self.assertTrue(module, "`check()` 侧绿了（入口分叉）——两入口判据不一致")
        self.assertEqual(cli, module, f"两入口 findings 不一致：cli={cli} module={module}")
        self.assertTrue(any(needle in finding for finding in module),
                        f"两入口都红了但没报「{needle}」：{module}")

    # ── 分桶 A：D4 → G5 → G6 ───────────────────────────────────────────────
    def test_d4_01_badge_version_drift_is_red(self):
        """# D4-01 README 徽章 `policy-vX.Y.Z` 漂一位必须红（2026-11-02 真实事故）。

        徽章是生成块内容，权威源是 `AGENT_RULES.md:4`；手改一位 = 块内容与渲染不符。
        """
        case = self.case()
        self.replace(case, "README.md", f"policy-v{self.cur}", f"policy-v{self.hist}")
        self.assert_red(case, "生成块内容与权威源不一致")
        # D4-01 的另一半：英文 README 的徽章同样受管（只钉一个会漏掉另一半）。
        case = self.case()
        self.replace(case, "README_EN.md", f"policy-v{self.cur}", f"policy-v{self.hist}")
        self.assert_red(case, "README_EN.md")

    def test_d4_02_history_narrative_is_not_a_prose_assertion(self):
        """# D4-02 历史叙述句（`在 3.6.1 发布时…`）不得被判成「与政策头不一致」。

        当前契约分两半，必须一起钉住，否则任一半被「修」都会静默：
        ① 沿革引用**按登记制**——行尾带 `<!-- history:x.y.z -->`（值 ∈ CHANGELOG 账本）
           即绿；旧 D4 的开放词表散文正则是误报源，已整段删除；
        ② 同一句**不带**标记 = 未登记 → 红（②「未登记即错」），而不是 D4 的散文判据。
        """
        # ① 带标记：
        case = self.case()
        self.append(case, "docs/policy-language.md",
                    f"在 {self.hist} 发布时…我们收窄了判据。<!-- history:{self.hist} -->")
        self.assert_green(case)
        # ② 不带标记（登记契约的另一面）：
        case = self.case()
        self.append(case, "docs/policy-language.md", f"在 {self.hist} 发布时…我们收窄了判据。")
        self.assert_red(case, f"历史政策版本 {self.hist} 未登记")

    def test_g5_01_prose_current_version_assertion_is_red(self):
        """# G5-01 正文断言 `当前政策版本：3.6.1`（政策头 3.7.0）必须红。"""
        case = self.case()
        self.append(case, "docs/policy-language.md", f"当前政策版本：{self.hist}")
        self.assert_red(case, f"历史政策版本 {self.hist} 未登记")

    def test_g5_02_label_false_positive_stays_green(self):
        """# G5-02 标签判据误报已删：字段名 + 无关 semver 必须绿（accepted，正向钉住）。

        当前**不判**这一行是设计决定：旧实现用开放词表（`政策版本|policy version`）补洞，
        词表内误报字段名、词表外照样漏（G5-03）。词表已删，纯散文归 N2 人工层。
        本用例证明「确实不判」——有人偷偷加回词表判据就会红。
        """
        case = self.case()
        self.append(case, "docs/policy-language.md",
                    '工具版本 9.9.9；"当前政策版本"只是字段名')
        self.assert_green(case)

    def test_g5_03_plain_prose_baseline_not_judged(self):
        """# G5-03 `本模板现行治理基线为 9.9.9。` 归 N2，机械判据**不判**（accepted）。

        这是**显式接受**的漏判：没有结构标记的自然语言散文断言不进机械面（N2 人工评审）。
        正向断言「现在不判」，防止有人为了补它把开放词表判据加回来（那会重新引入 G5-02）。
        """
        case = self.case()
        self.append(case, "docs/policy-language.md", "本模板现行治理基线为 9.9.9。")
        self.assert_green(case)

    def test_g6_01_stale_clause_range_is_red(self):
        """# G6-01 陈旧范围 `R1..R130` 必须红；# R13-02 只跑 `--check`（不跑测试套件）即红。"""
        case = self.case()
        self.append(case, "docs/architecture.md", f"条款范围见 R{self.lo}..R{self.hi - 6}。")
        # R13-02：这里**只**跑了 `--check` 子进程，没有跑 unittest 套件——单入口假绿已修。
        self.assert_red(case, "未登记正文写条款范围")

    def test_s6_template_architecture_generated_range_drift_is_red(self):
        """# S6 模板生成块的 R1..R136 手改成 R1..R130 必须红。"""
        case = self.case()
        want = f"R{self.lo}..R{self.hi}"
        self.replace(case, "docs/architecture.md", want, f"R{self.lo}..R{self.hi - 6}")
        self.assert_red(case, "生成块内容与权威源不一致")

    def test_s7_template_current_policy_version_outside_block_is_red(self):
        """# S7 模板文档正文漏出当前政策版本必须红。"""
        case = self.case()
        self.append(case, "docs/tools.md", f"本仓政策 {self.cur}")
        self.assert_red(case, f"生成块之外的当前政策版本 {self.cur}")

    def test_active_instance_user_docs_do_not_inherit_template_doc_facts(self):
        """合法实例的 docs/README 是用户文档；撞模板账本或自有版本均必须绿。"""
        for rel, line in (("docs/tools.md", "本应用版本 2.0.0 已发布。"),
                          ("docs/tools.md", "本应用版本 7.7.7 已发布。"),
                          ("README.md", "本应用版本 2.0.0 已发布。")):
            with self.subTest(rel=rel, line=line):
                case = self.case()
                boot = case / ".workspace/bootstrap.json"
                data = json.loads(boot.read_text(encoding="utf-8"))
                data["workspace_status"] = "active"
                boot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
                self.append(case, rel, line)
                self.assert_green(case)

    def test_g6_02_non_canonical_clause_separators_are_red(self):
        """# G6-02 非 `..` 连接符（`R1–R136` / `R1~R136` / `R1 至 R136`）必须红。"""
        for sep in ("–", "~", " 至 "):
            with self.subTest(sep=sep):
                case = self.case()
                self.append(case, "docs/architecture.md",
                            f"条款范围见 R{self.lo}{sep}R{self.hi}。")
                self.assert_red(case, "连接符不合法")

    def test_g6_03_subset_clause_range_stays_green(self):
        """# G6-03 子集引用（`R2–R8`）不得被判成政策范围——必须绿。"""
        case = self.case()
        self.append(case, "docs/architecture.md",
                    f"子集 R{self.lo + 1}–R{self.lo + 7} 属功能引用，不判值。")
        self.assert_green(case)

    def test_g6_04_out_of_bounds_clause_range_is_red(self):
        """# G6-04 越界被当子集放过（`R0..R136`、`R1..R137`）必须红。"""
        case = self.case()
        self.append(case, "docs/architecture.md", f"条款范围见 R{self.lo - 1}..R{self.hi}。")
        self.assert_red(case, "越界")
        case = self.case()
        self.append(case, "docs/architecture.md", f"条款范围见 R{self.lo}..R{self.hi + 1}。")
        self.assert_red(case, "越界")

    def test_g6_05_glued_and_chained_clause_range_is_red(self):
        """# G6-05 粘连/链式（`R2..R8foo`、`R2..R8..R999`）必须红。"""
        for form in ("R2..R8foo", "R2..R8..R999"):
            with self.subTest(form=form):
                case = self.case()
                self.append(case, "docs/architecture.md", f"条款范围见 {form}。")
                self.assert_red(case, "形态不合规")

    def test_g6_06_changelog_heading_form_agrees_across_entries(self):
        """# G6-06 `## 3.6.1beta` 时两入口必须同判；# R12-03 右边界不得把它读成 3.6.1。"""
        case = self.case()
        self.append(case, "CHANGELOG.md", f"## {self.hist}beta")
        self.assert_entries_agree(case, "条目标题格式不符")
        # R12-03：格式不符的标题**不进账本**——不能被无右边界正则收成 3.6.1。
        self.assertEqual(pf.changelog_versions(case).count(self.hist), 1,
                         "`## 3.6.1beta` 被读成了 3.6.1（CHANGELOG 条目正则右边界丢了）")

    def test_g6_07_heading_form_agrees_across_entries(self):
        """# G6-07 `# 当前政策版本 9.9.9` 标题形态两入口必须同判；# R18-05 同一条。"""
        case = self.case()
        self.append(case, "docs/policy-language.md", "# 当前政策版本 9.9.9")
        self.assert_entries_agree(case, "标题写旧政策版本 9.9.9")

    # ── 分桶 B：script 侧修复 ──────────────────────────────────────────────
    def test_r12_01_history_marker_does_not_exempt_the_line(self):
        """# R12-01 `<!-- history:3.6.1 -->` 不得让同行其它版本被跳过。"""
        case = self.case()
        self.append(case, "docs/policy-language.md",
                    f"<!-- history:{self.hist} --> 当前政策版本 9.9.9")
        self.assert_red(case, "历史标记行上的未登记版本 9.9.9")

    def test_r12_02_history_marker_cannot_register_current_version(self):
        """# R12-02 `<!-- history:<当前版本> -->` 不得成为块外写当前版本的后门。"""
        case = self.case()
        self.append(case, "docs/policy-language.md",
                    f"沿革见 CHANGELOG <!-- history:{self.cur} -->。")
        self.assert_red(case, "登记了**当前**政策版本")

    def test_r13_01_duplicate_fact_inside_generated_block_area_is_red(self):
        """# R13-01 生成块外的派生事实副本（插件版本 / scripts 计数）必须红。

        判据是**形状**不是值：写对的冗余值也红（冗余本身就是漂移源）。
        这里故意用**真实值**（`plugin.yaml:2` 的版本、`scripts/*.py` 的实际个数）。
        """
        case = self.case()
        self.append(case, "README.md", f"插件 v{self.plugin} 已发布。")
        self.assert_red(case, f"派生事实副本「插件 v{self.plugin}」")
        for form in (f"本仓共 {self.scripts} 个 scripts。", f"本仓 scripts（{self.scripts} 个）"):
            with self.subTest(form=form):
                case = self.case()
                self.append(case, "docs/tools.md", form)
                self.assert_red(case, "派生事实副本")

    def test_r13_01_ledger_wording_form_is_red(self):
        """# R13-01 `scripts N 个` / `脚本 N 个`：块外红、生成块内绿。"""
        for noun in ("scripts", "脚本"):
            with self.subTest(noun=noun, area="outside"):
                case = self.case()
                self.append(case, "docs/tools.md", f"工具层 {noun} {self.scripts} 个，全部随模板下发。")
                self.assert_red(case, "派生事实副本")
            with self.subTest(noun=noun, area="generated"):
                case = self.case()
                path = case / "docs/tools.md"
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + f"\n{pf.BEGIN}\n工具层 {noun} {self.scripts} 个。R2–R8。\n{pf.END}\n",
                    encoding="utf-8",
                )
                self.assertEqual(pf.dup_fact_offenders(case), [],
                                 "生成块内的 scripts 形态或无关 R2–R8 被误伤")


class RepoIdentityLedger(unittest.TestCase):
    """分桶 B 里由 `scripts/repo_identity.py`（`repair` / `classify`）承担的条目。"""

    SOURCE = "https://github.com/KinoluKaslana/WorkspaceTemplate"

    def test_fail_closed_literals(self):
        """# R17-01 `C:\\work\\repo`；# R17-02 未知 scheme；# R17-04 `/`、`file:///`。"""
        # R17-01 POSIX 上不得把 Windows 盘符读成 scp-like（`c/\\work\\repo`）。
        self.assertIsNone(repair(r"C:\work\repo"))
        self.assertIsNone(repair("C:/work/repo"))
        # R17-02 白名单是闭式集合：未知 scheme 不得被接受。
        self.assertIsNone(repair("nosuch://h/o/r"))
        # R17-04 `/` 与 `file:///` 必须 None（曾归一成空串 → 误激活）。
        self.assertIsNone(repair("/"))
        self.assertIsNone(repair("file:///"))

    def test_default_port_and_host_tail_dot_fold_into_template(self):
        """# R17-03 默认端口 `:443` 折叠；# R18-03 主机名尾点 `github.com.` 归一。"""
        # R17-03
        self.assertEqual(classify(self.SOURCE.replace("github.com/", "github.com:443/"),
                                  self.SOURCE), "template")
        # R18-03
        self.assertEqual(classify(self.SOURCE.replace("github.com/", "github.com./"),
                                  self.SOURCE), "template")

    def test_encoded_traversal_and_separators_are_rejected(self):
        """# R18-01 `%2e%2e` 点段穿透；# R19-01 NFKC 后新生转义；# R19-02 编码斜杠。"""
        # R18-01
        self.assertIsNone(repair("https://github.com/o/%2e%2e/r.git"))
        self.assertIsNone(repair("https://github.com/o/%252e%252e/r.git"))
        # R19-01：`%2ｆ%`（全角 f）在 NFKC 之后才长出合法转义，必须重新解码再判。
        self.assertIsNone(repair("https://github.com/o/%2ｆ%/r"))
        # R19-02：编码斜杠 `%2f` 会把两个 segment 合并，必须拒绝。
        self.assertIsNone(repair("https://github.com/o%2fr/r"))

    def test_oversized_and_malformed_authorities_are_rejected(self):
        """# R18-02 超长 host（5000 字符）必须拒绝（有长度上限，不靠下游偶然失败）。"""
        self.assertIsNone(repair("https://" + "a" * 5000 + "/o/r"))

    def test_relative_paths_are_not_derived_identity(self):
        """# R19-03 `relative/foo` / `a/b` / `foo.git` 不得被当派生身份。

        在空的临时 cwd 里跑：断言的是「相对路径不构成派生身份」，不是「本机恰好没有该目录」。
        """
        with tempfile.TemporaryDirectory(prefix="identity-empty-") as tmp, chdir(tmp):
            for value in ("relative/foo", "a/b", "foo.git"):
                with self.subTest(value=value):
                    self.assertIsNone(repair(value))
                    self.assertIsNone(classify(value, self.SOURCE))


@unittest.skipUnless(shutil.which("git"), "需要 git 才能构造 origin 场景")
class PyenvActivationLedger(unittest.TestCase):
    """分桶 B 里由 `scripts/pyenv_init.py --apply` 承担的条目（模板正主保护）。"""

    def setUp(self):
        try:
            self.source = json.loads((ROOT / ".workspace/bootstrap.json")
                                    .read_text(encoding="utf-8"))["template"]["source_repo"]
        except Exception as exc:                                    # noqa: BLE001
            self.skipTest(f"读不到模板源仓库地址：{exc}")
        self._tmp = tempfile.TemporaryDirectory(prefix="pyenv-ledger-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def workspace(self, origin: str | None) -> Path:
        # 每次调用都要一个新目录：同一用例内会构造多个 origin 场景。
        ws = Path(tempfile.mkdtemp(prefix="ws-", dir=self.base))
        (ws / ".workspace").mkdir(parents=True)
        boot = {"schema_version": 1, "workspace_status": "template",
                "python": {"mode": "unconfigured"}, "template": {"source_repo": self.source}}
        boot_path = ws / ".workspace/bootstrap.json"
        boot_path.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        if origin is not None:
            subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", origin],
                           check=True)
        return ws

    @staticmethod
    def apply(ws: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "pyenv_init.py"), "--workspace", str(ws),
             "--mode", "mcp", "--mcp-server", "test", "--mcp-tool", "python",
             "--apply", "--json"],
            capture_output=True, text=True, encoding="utf-8")

    def status(self, ws: Path) -> str:
        return json.loads((ws / ".workspace/bootstrap.json").read_text(encoding="utf-8")
                          )["workspace_status"]

    def test_r15_01_template_clone_stays_template(self):
        """# R15-01 模板正主跑 `--apply` 后 `workspace_status` 必须仍是 `template`。

        翻转会静默关掉 README 判据（`policy_facts.is_template` 是 N5 豁免的开关）。
        """
        ws = self.workspace(self.source + ".git/")
        before = (ws / ".workspace/bootstrap.json").read_bytes()
        result = self.apply(ws)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status(ws), "template")
        self.assertTrue(json.loads(result.stdout).get("template_protected"), result.stdout)
        self.assertNotEqual((ws / ".workspace/bootstrap.json").read_bytes(), before,
                            "--apply 什么都没写，本用例测不到翻转判据")

    def test_r15_02_git_without_origin_stays_template(self):
        """# R15-02 无 origin 的 git 工作区必须 fail-closed（rc≠0、bootstrap 字节不变）。"""
        ws = self.workspace(None)
        before = (ws / ".workspace/bootstrap.json").read_bytes()
        result = self.apply(ws)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.status(ws), "template")
        self.assertEqual((ws / ".workspace/bootstrap.json").read_bytes(), before)
        self.assertIn("origin", json.loads(result.stdout).get("hint", ""))

    def test_r16_01_missing_host_origin_is_not_derived(self):
        """# R16-01 `https:///x/y.git`（缺 host）不得被当合法派生身份。"""
        ws = self.workspace("https:///x/y.git")
        before = (ws / ".workspace/bootstrap.json").read_bytes()
        result = self.apply(ws)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.status(ws), "template")
        self.assertEqual((ws / ".workspace/bootstrap.json").read_bytes(), before)

    def test_r16_02_non_url_origins_are_not_derived(self):
        """# R16-02 `not a real url` / `./relative` 不得按 cwd 解析成路径。"""
        for origin in ("not a real url", "./relative"):
            with self.subTest(origin=origin):
                ws = self.workspace(origin)
                result = self.apply(ws)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.status(ws), "template")
                self.assertIn("无法归一化", json.loads(result.stdout).get("hint", ""))

    def test_r18_04_malformed_origin_fails_before_persisting(self):
        """# R18-04 `https://[bad/o/r` 必须 rc≠0、bootstrap 字节不变、不生成 `.local`。

        关键在顺序：异常若发生在持久化之后，会留下「半写状态」（身份已改、状态没翻）。
        """
        ws = self.workspace("https://[bad/o/r")
        boot_path = ws / ".workspace/bootstrap.json"
        before = boot_path.read_bytes()
        result = self.apply(ws)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("repo-identity-unavailable", result.stdout)
        self.assertEqual(boot_path.read_bytes(), before, "半写：bootstrap 被改过")
        self.assertFalse((ws / ".workspace/.local").exists(),
                         "半写：持久化先于身份判定，留下了本机状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
