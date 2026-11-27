#!/usr/bin/env python3
"""osnorm 性质测试（规格 §7 的 9 条，stdlib only）。

约定：
- **不 skip、不 expectedFailure**：每条性质在两种宿主上都跑；宿主相关的期望一律用
  「先测基线再比」而不是固定字面量（Windows 与 POSIX 的编码/路径形态不同）。
- 本文件本身也在 R1–R9 的扫描面里（`tests/`），所以读文件一律走 `osnorm.read_text`，
  不写 `encoding=` 关键字。
"""
from __future__ import annotations

import json
import os
import posixpath
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import osnorm  # noqa: E402

CASES = json.loads(osnorm.read_text(ROOT / "tests" / "test_osnorm_cases.json"))


def _canon(value, base=None):
    return osnorm.canonical_path(value, base=base)


class _FakeStream:
    """伪 TTY / 伪管道：只记录 reconfigure 收到的关键字，不碰真实 fd。"""

    def __init__(self, is_tty: bool, encoding: str) -> None:
        self._tty = is_tty
        self.encoding = encoding
        self.calls: list[dict] = []

    def isatty(self) -> bool:
        return self._tty

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))
        if "encoding" in kwargs:
            self.encoding = kwargs["encoding"]


class PropertyCase(unittest.TestCase):
    def setUp(self) -> None:
        # 短前缀：Windows 上 %TEMP% 本身就有 ~30 字符，用例路径要留够余量
        self._tmp = tempfile.TemporaryDirectory(prefix="osn")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ---------------------------------------------------------------- 性质 1：幂等


class IdempotenceTest(PropertyCase):
    """按金表三类分别断言（N3：有效/无效/上下文三种幂等形态不同）。"""

    def test_valid_rows_idempotent(self) -> None:
        self.assertGreater(len(CASES["valid"]), 0)
        for row in CASES["valid"]:
            once = _canon(row["input"])
            self.assertEqual(once, row["expected"], f"valid 首轮不一致：{row['input']!r}")
            self.assertEqual(_canon(once), once, f"valid 非幂等：{row['input']!r}")

    def test_invalid_rows_raise_twice_with_same_type(self) -> None:
        self.assertGreater(len(CASES["invalid"]), 0)
        for row in CASES["invalid"]:
            with self.assertRaises(osnorm.PathFormError, msg=f"首轮未抛：{row['input']!r}"):
                _canon(row["input"], row.get("base"))
            try:
                _canon(row["input"], row.get("base"))
            except osnorm.PathFormError as exc:
                self.assertIs(type(exc), osnorm.PathFormError)
            else:  # pragma: no cover - 只有回归才会到这
                self.fail(f"invalid 行第二轮没有抛：{row['input']!r}")

    def test_contextual_rows_idempotent_with_fixed_base(self) -> None:
        self.assertGreater(len(CASES["contextual"]), 0)
        for row in CASES["contextual"]:
            base = row["base"]
            once = _canon(row["input"], base)
            self.assertEqual(once, row["expected"], f"contextual 首轮不一致：{row['input']!r}")
            self.assertEqual(_canon(once, base), once, f"contextual 非幂等：{row['input']!r}")

    def test_spec_row_coverage(self) -> None:
        """规格 §4 判定表的每一行都必须在金表里有对应输入（缺行即红）。"""
        present = {row["input"] for key in ("valid", "invalid", "contextual")
                   for row in CASES[key]}
        self.assertGreaterEqual(len(CASES["spec_rows"]), 22)
        for spec_row in CASES["spec_rows"]:
            for value in spec_row["inputs"]:
                self.assertIn(value, present,
                              f"§4 表行 {spec_row['row']} 的输入未进金表：{value!r}")

    def test_row_family_matches_classifier(self) -> None:
        for key in ("valid", "invalid", "contextual"):
            for row in CASES[key]:
                self.assertEqual(osnorm.family(row["input"]), row["family"],
                                 f"{key} 行的 family 与 family() 不一致：{row['input']!r}")


# ---------------------------------------------------------------- 变换层：collapse_path


class CollapsePathTest(PropertyCase):
    """§2 契约：金表逐条 + 幂等 + 不抛 + 两条「三份规则不一致」的语义钉子。

    这一层是**纯词法**：只认 '/'，不认 '\\'，不判越界，不查文件系统。判定层的平台语义
    （盘符、UNC share 边界）由下面 `canonical_path` 的两条用例钉住。
    """

    def test_gold_table_rows(self) -> None:
        self.assertGreaterEqual(len(CASES["collapse"]), 13, "§2 金表必须逐条落表")
        for row in CASES["collapse"]:
            self.assertEqual(osnorm.collapse_path(row["input"]), row["expected"],
                             f"折叠金表不符：{row['input']!r}")

    def test_idempotent(self) -> None:
        values = [row["input"] for row in CASES["collapse"]]
        values += ["a/../..", "../..", "a//b//", "/a/b/../../..", "//host/share/../x",
                   "C:/a/../../x", "a\\..\\b", ".hidden", "a/./../b"]
        for value in values:
            once = osnorm.collapse_path(value)
            self.assertEqual(osnorm.collapse_path(once), once, f"非幂等：{value!r}")

    def test_never_raises_and_always_str(self) -> None:
        """任意字符串都返回 str（含 NUL、超长、纯 '..'、非 ASCII）——折叠层不判错。"""
        values = ["", ".", "..", "../..", "/", "//", "///", "\x00", "a\x00b",
                  "..\\..\\..", "/..", "/../..", "a/" * 5000, ".." + "/.." * 2000,
                  "中文/路径/../名字", "\ud7ff/../x", "/" + "a" * 10000]
        for value in values:
            got = osnorm.collapse_path(value)
            self.assertIsInstance(got, str, f"折叠必须返回 str：{value[:20]!r}")

    def test_backslash_is_an_ordinary_character(self) -> None:
        """`\\` 不是分隔符：折叠层不碰它（Windows 语义只属于判定层）。"""
        self.assertEqual(osnorm.collapse_path("a\\..\\b"), "a\\..\\b")
        self.assertEqual(osnorm.collapse_path("./a\\b/"), "a\\b")

    def test_no_boundary_check(self) -> None:
        """不判越界：相对形态保留前导 '..'（这是错误留给判定层的边界）。"""
        self.assertEqual(osnorm.collapse_path("../x"), "../x")
        self.assertEqual(osnorm.collapse_path("../../x"), "../../x")
        self.assertEqual(osnorm.collapse_path("/../x"), "/x")

    # ---- 冻结要求 ②：两处「三份折叠规则不一致」的语义，各钉一条 ----

    def test_drive_family_still_folds_silently_to_root(self) -> None:
        """盘符族：越界 `..` 静默折到盘符根（原语义，保持；不改成抛错）。"""
        self.assertEqual(_canon("C:/a/../../x"), "C:/x")
        self.assertNotIn("..", _canon("C:/a/../../x"))

    def test_unc_family_still_raises_at_share_boundary(self) -> None:
        """UNC 族：`..` 折到 share 即逃出共享 → 抛 PathFormError（原语义，保持）。"""
        with self.assertRaises(osnorm.PathFormError):
            _canon("//h/s/../x")
        self.assertEqual(_canon("//h/s/a/../x"), "//h/s/x")


# ---------------------------------------------------------------- 性质 2：族收敛 / 不误合并


class FamilyConvergenceTest(PropertyCase):
    def test_equivalent_forms_converge(self) -> None:
        drive = ["C:\\a\\b", "C:/a/b", "c:/a/b", "/c:/a/b", "\\\\?\\C:\\a\\b",
                 "//?/C:/a/b", "file:///C:/a/b"]
        values = {_canon(v) for v in drive}
        self.assertEqual(values, {"C:/a/b"}, f"盘符族未收敛：{values}")

        unc = ["\\\\host\\share\\x", "//host/share/x", "\\\\?\\UNC\\host\\share\\x",
               "file://host/share/x"]
        values = {_canon(v) for v in unc}
        self.assertEqual(values, {"//host/share/x"}, f"UNC 族未收敛：{values}")

    def test_distinct_families_never_merged(self) -> None:
        pairs = [
            ("/mnt/c/Users", "C:/Users"),                 # wsl 挂载 ≠ 盘符
            ("//wsl.localhost/Ubuntu/x", "//host/share/x"),  # wsl UNC ≠ 普通 UNC
            ("/x", "//host/share/x"),                     # posix ≠ UNC
            ("C:/A", "C:/a"),                             # 大小写不折叠（D1）
            ("/mnt/c", "//mnt/c"),
        ]
        for left, right in pairs:
            self.assertNotEqual(_canon(left), _canon(right),
                                f"异族被误合并：{left!r} / {right!r}")


# ---------------------------------------------------------------- 性质 3：无 cwd 隐式回退


class NoCwdFallbackTest(PropertyCase):
    def test_posix_relative_matches_path_as_posix(self) -> None:
        """§4 末行：posix 相对 + base 必须与 `Path(...).as_posix()` 逐字一致（键口径）。

        带 `.`/`..` 的输入与 `posixpath.normpath` 对账（`pathlib` 不做词法折叠）。
        """
        base = "/r"
        for rel in ["a/b", "./a", "x/y/z", "a-b/c_d"]:
            self.assertEqual(_canon(rel, base), (Path(base) / rel).as_posix(),
                             f"与 Path.as_posix() 不一致：{rel!r}")
        for rel in ["sub/../b", "a/./b", "a/b/../c"]:
            # 用 posixpath（不是 os.path）：契约与宿主无关，Windows 上 ntpath 会换成反斜杠
            expected = posixpath.normpath((Path(base) / rel).as_posix())
            self.assertEqual(_canon(rel, base), expected, f"词法折叠不一致：{rel!r}")

    def test_relative_without_base_always_raises(self) -> None:
        for value in ["a/b", "x", "./a", ".", "..", "a/../b"]:
            with self.assertRaises(osnorm.PathFormError, msg=f"无 base 却成功：{value!r}"):
                _canon(value)

    def test_base_result_is_cwd_independent(self) -> None:
        before = os.getcwd()
        first = _canon("a/b", base="/r")
        try:
            os.chdir(self.tmp)
            second = _canon("a/b", base="/r")
        finally:
            os.chdir(before)
        self.assertEqual(first, second)
        self.assertEqual(first, "/r/a/b")


# ---------------------------------------------------------------- 性质 4：换行契约


class NewlineContractTest(PropertyCase):
    def test_write_text_defaults_to_lf(self) -> None:
        target = self.tmp / "nl.txt"
        osnorm.write_text(target, "a\r\nb\rc\nd")
        self.assertEqual(osnorm.read_bytes(target), b"a\nb\nc\nd")

    def test_write_text_preserve_keeps_bytes(self) -> None:
        target = self.tmp / "raw.txt"
        osnorm.write_text(target, "a\r\nb", newline="preserve")
        self.assertEqual(osnorm.read_bytes(target), b"a\r\nb")

    def test_read_text_lf_normalizes(self) -> None:
        target = self.tmp / "read.txt"
        osnorm.write_bytes(target, b"a\r\nb\rc")
        self.assertEqual(osnorm.read_text(target), "a\nb\nc")
        self.assertEqual(osnorm.read_text(target, newline="preserve"), "a\r\nb\rc")

    def test_invalid_newline_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            osnorm.write_text(self.tmp / "x.txt", "a", newline="crlf")
        with self.assertRaises(ValueError):
            osnorm.read_text(self.tmp / "x.txt", newline="crlf")


# ---------------------------------------------------------------- 性质 5：BOM


class BomContractTest(PropertyCase):
    def test_bom_strip_and_keep(self) -> None:
        target = self.tmp / "bom.txt"
        osnorm.write_bytes(target, b"\xef\xbb\xbfhello")
        self.assertEqual(osnorm.read_text(target, bom=True), "hello")
        self.assertEqual(osnorm.read_text(target, bom=False), "\ufeffhello")

    def test_bom_true_on_plain_utf8_is_transparent(self) -> None:
        target = self.tmp / "plain.txt"
        osnorm.write_text(target, "hello")
        self.assertEqual(osnorm.read_text(target, bom=True), "hello")

    # ---- §3.1 模块级策略：一处决定，不在调用点散落 -------------------------------------

    def _with_policy(self, value, fn):
        saved = osnorm.BOM_POLICY
        osnorm.BOM_POLICY = value
        try:
            return fn()
        finally:
            osnorm.BOM_POLICY = saved

    def test_default_policy_tolerates_bom(self) -> None:
        """默认 tolerate：不传 bom 也剥 BOM（记事本写的 bootstrap.json 不再被判 unknown）。"""
        target = self.tmp / "bom.txt"
        osnorm.write_bytes(target, b"\xef\xbb\xbfhello")
        self.assertEqual(osnorm.read_text(target), "hello")

    def test_strict_policy_keeps_bom(self) -> None:
        target = self.tmp / "bom.txt"
        osnorm.write_bytes(target, b"\xef\xbb\xbfhello")
        self.assertEqual(self._with_policy("strict", lambda: osnorm.read_text(target)),
                         "\ufeffhello")

    def test_explicit_flag_overrides_policy(self) -> None:
        target = self.tmp / "bom.txt"
        osnorm.write_bytes(target, b"\xef\xbb\xbfhello")
        self.assertEqual(self._with_policy("strict", lambda: osnorm.read_text(target, bom=True)),
                         "hello")
        self.assertEqual(self._with_policy("tolerate", lambda: osnorm.read_text(target, bom=False)),
                         "\ufeffhello")

    def test_unknown_policy_raises(self) -> None:
        target = self.tmp / "bom.txt"
        osnorm.write_bytes(target, b"hello")
        with self.assertRaises(ValueError):
            self._with_policy("lax", lambda: osnorm.read_text(target))


# ---------------------------------------------------------------- 性质 6：解码


class DecodeContractTest(PropertyCase):
    def test_illegal_utf8_raises_in_strict(self) -> None:
        target = self.tmp / "bad.bin"
        osnorm.write_bytes(target, b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            osnorm.read_text(target)
        self.assertEqual(osnorm.read_text(target, errors="ignore"), "")
        self.assertEqual(osnorm.read_text(target, errors="replace"), "\ufffd")

    def test_nul_byte_is_valid_utf8(self) -> None:
        """GPTSOL6 M6：`b"a\\x00"` 是合法 UTF-8，不能当非法样本。"""
        target = self.tmp / "nul.bin"
        osnorm.write_bytes(target, b"a\x00")
        self.assertEqual(osnorm.read_text(target), "a\x00")

    def test_utf16_bytes_are_not_guessed(self) -> None:
        target = self.tmp / "utf16.bin"
        osnorm.write_bytes(target, "中文".encode("utf-16"))
        with self.assertRaises(UnicodeDecodeError):
            osnorm.read_text(target)


# ---------------------------------------------------------------- 性质 7：原子写并发 / 重试


WRITER = """import sys
sys.path.insert(0, sys.argv[1])
import osnorm
target, mode, rounds = sys.argv[2], sys.argv[3], int(sys.argv[4])
# 载荷在子进程里按 mode 现拼，**不走上 argv**：Windows 的 CreateProcess 命令行上限
# 约 32K 字符，64K 载荷塞进 argv 会直接 [WinError 206]（不是临时路径太长）。
payload = (mode * 4000 + "\\n") * 16
for _ in range(rounds):
    osnorm.write_text(target, payload)
"""

PAYLOAD_MODES = ("A", "B")


def payload_for(mode: str) -> str:
    return (mode * 4000 + "\n") * 16


class AtomicWriteTest(PropertyCase):
    def test_atomic_write_text_keeps_bytes_and_mode(self) -> None:
        """`atomic_write_text` 是低层原语：**不做**换行归一（契约只属于 `write_text`）。"""
        target = self.tmp / "atomic_raw.txt"
        osnorm.atomic_write_text(target, "a\r\nb")
        self.assertEqual(osnorm.read_bytes(target), b"a\r\nb")
        locked = self.tmp / "mode.txt"
        osnorm.atomic_write_text(locked, "x", mode=0o600)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(locked).st_mode), 0o600)

    def test_write_bytes_roundtrip(self) -> None:
        target = self.tmp / "bytes.bin"
        osnorm.write_bytes(target, b"\x00\x01\xff")
        self.assertEqual(osnorm.read_bytes(target), b"\x00\x01\xff")
        plain = self.tmp / "plain.bin"
        osnorm.write_bytes(plain, b"xy", atomic=False)
        self.assertEqual(osnorm.read_bytes(plain), b"xy")
        with self.assertRaises(TypeError):
            osnorm.write_bytes(self.tmp / "nope.bin", "not-bytes")

    def test_concurrent_readers_see_only_complete_payloads(self) -> None:
        worker = self.tmp / "w.py"
        osnorm.write_text(worker, WRITER)
        target = self.tmp / "s.txt"
        payloads = {payload_for(mode) for mode in PAYLOAD_MODES}
        osnorm.write_text(target, payload_for(PAYLOAD_MODES[0]))
        procs = []
        try:
            for index in range(8):
                mode = PAYLOAD_MODES[index % 2]
                # argv 里只放路径/mode/轮数（都 ≤ 十几个字符）：Windows 命令行长度上限
                # 与文件名长度限制都不再参与，载荷由子进程自己拼。
                procs.append(subprocess.Popen(
                    [sys.executable, "-B", str(worker), str(SCRIPTS), str(target),
                     mode, "50"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
            seen = set()
            deadline = time.monotonic() + 60
            reads = 0
            concurrent_reads = 0
            share_misses = 0
            # 子进程跑完后再补读到 100 次，保证「读者只见完整载荷」不是空断言
            while time.monotonic() < deadline:
                # Windows 的普通读句柄阻止 replace。每次读前留出无句柄的窗口，
                # 避免紧循环让写进程耗尽共享冲突重试预算。
                if os.name == "nt":
                    time.sleep(0.001)
                running = any(p.poll() is None for p in procs)
                if reads >= 100 and not running:
                    break
                try:
                    text = osnorm.read_text(target)
                except OSError as exc:
                    # Windows：写侧正处在 os.replace 的 delete-pending 窗口时，读侧 open 会
                    # 短暂失败。那是共享冲突，不是撕裂读——跳过这一次，不计入 seen。
                    if os.name != "nt" or not isinstance(exc, (PermissionError, FileNotFoundError)):
                        raise
                    share_misses += 1
                    continue
                seen.add(text)
                reads += 1
                if running:
                    concurrent_reads += 1
            for proc in procs:
                _, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
            seen.add(osnorm.read_text(target))
        finally:
            for proc in procs:
                if proc.poll() is None:  # pragma: no cover - 超时兜底
                    proc.kill()
                    proc.wait(timeout=30)
                proc.stderr.close()
        self.assertGreaterEqual(reads, 100, "读取次数不足，断言没有覆盖到并发窗口")
        self.assertGreater(concurrent_reads, 0, "写进程运行期间没有成功读取")
        self.assertTrue(seen <= payloads,
                        f"读者读取了非预期载荷（长度：{sorted(map(len, seen - payloads))}；"
                        f"共享冲突 {share_misses} 次）")
        leftovers = [p.name for p in self.tmp.iterdir() if p.name.endswith(".osnorm.tmp")]
        self.assertEqual(leftovers, [], f"临时文件未清理：{leftovers}")

    def test_replace_retry_backs_off_then_succeeds(self) -> None:
        src, dst = self.tmp / "a", self.tmp / "b"
        osnorm.write_text(src, "x")
        calls = {"n": 0}
        real = osnorm._REPLACE

        def flaky(source, target):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(13, "sharing violation")
            return real(source, target)

        osnorm._REPLACE = flaky
        try:
            started = time.monotonic()
            osnorm.replace_retry(src, dst, budget=5.0)
            elapsed = time.monotonic() - started
        finally:
            osnorm._REPLACE = real
        self.assertEqual(calls["n"], 3, "重试次数不符（应在两次失败后成功）")
        self.assertGreater(elapsed, 0.0, "退避没有真的等待")
        self.assertEqual(osnorm.read_text(dst), "x")

    def test_replace_retry_exhausts_budget(self) -> None:
        src, dst = self.tmp / "c", self.tmp / "d"
        osnorm.write_text(src, "y")
        real = osnorm._REPLACE

        def always_fail(source, target):
            raise PermissionError(13, "sharing violation")

        osnorm._REPLACE = always_fail
        try:
            with self.assertRaises(PermissionError):
                osnorm.replace_retry(src, dst, budget=0.05)
        finally:
            osnorm._REPLACE = real
        self.assertFalse(dst.exists(), "耗尽预算后不得落出半成品目标文件")
        self.assertTrue(src.exists(), "耗尽预算后源文件必须仍在（不得退化成非原子写）")

    def test_non_transient_error_is_not_retried(self) -> None:
        real = osnorm._REPLACE
        calls = {"n": 0}

        def broken(source, target):
            calls["n"] += 1
            raise OSError(2, "no such file")

        osnorm._REPLACE = broken
        try:
            with self.assertRaises(OSError):
                osnorm.replace_retry(self.tmp / "e", self.tmp / "f", budget=5.0)
        finally:
            osnorm._REPLACE = real
        self.assertEqual(calls["n"], 1, "非共享冲突类错误不得重试")


# ---------------------------------------------------------------- 性质 8：子进程解码


class SubprocessDecodeTest(PropertyCase):
    def test_gbk_console_setting_is_overridden(self) -> None:
        """父进程环境是 GBK（模拟 Windows cp936 控制台）时，子进程仍必须产出正确 str。"""
        old = os.environ.get("PYTHONIOENCODING")
        os.environ["PYTHONIOENCODING"] = "gbk"
        try:
            proc = osnorm.run_text([sys.executable, "-B", "-c",
                                    "import sys; sys.stdout.write('中文')"])
        finally:
            if old is None:
                os.environ.pop("PYTHONIOENCODING", None)
            else:
                os.environ["PYTHONIOENCODING"] = old
        self.assertIsInstance(proc.stdout, str, "stdout 必须是 str，不能是 None")
        self.assertIsInstance(proc.stderr, str)
        self.assertEqual(proc.stdout, "中文")

    def test_stdout_is_str_even_on_failure(self) -> None:
        proc = osnorm.run_text([sys.executable, "-B", "-c",
                                "import sys; print('out'); sys.exit(3)"])
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.strip(), "out")

        with self.assertRaises(subprocess.CalledProcessError) as caught:
            osnorm.run_text([sys.executable, "-B", "-c", "import sys; sys.exit(4)"],
                            check=True)
        self.assertIsInstance(caught.exception.stdout, str)

    def test_encoding_keys_are_refused(self) -> None:
        for kwargs in ({"text": True}, {"encoding": "utf-8"},
                       {"universal_newlines": True}, {"capture_output": True},
                       {"stdout": subprocess.PIPE}):
            with self.assertRaises(TypeError, msg=f"未拒绝 {kwargs}"):
                osnorm.run_text([sys.executable, "-B", "-c", "pass"], **kwargs)

    def test_caller_env_wins_over_utf8_defaults(self) -> None:
        proc = osnorm.run_text([sys.executable, "-B", "-c",
                                "import os; print(os.environ['OSNORM_PROBE'])"],
                               env={"OSNORM_PROBE": "caller"})
        self.assertEqual(proc.stdout.strip(), "caller")


# ---------------------------------------------------------------- 性质 9：stdio


STDIO_CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
import osnorm
before = sys.stdout.encoding
tty = sys.stdout.isatty()
osnorm.configure_stdio()
sys.stdout.write("%s|%s|%s" % (tty, before, sys.stdout.encoding))
"""


class StdioTest(PropertyCase):
    def _run_child(self, env_extra=None):
        env = {"PYTHONIOENCODING": "gbk"}
        if env_extra:
            env.update(env_extra)
        return osnorm.run_text([sys.executable, "-B", "-c", STDIO_CHILD, str(SCRIPTS)],
                               env=env)

    def test_non_tty_branch_forces_utf8(self) -> None:
        proc = self._run_child()
        tty, before, after = proc.stdout.strip().split("|")
        self.assertEqual(tty, "False", "管道场景不应被判成 TTY")
        self.assertEqual(after, "utf-8", "非 TTY 分支必须 reconfigure 成 UTF-8")
        self.assertNotEqual(before, "utf-8", "前提不成立：子进程起始编码已是 UTF-8")

    def test_fake_stream_branches(self) -> None:
        real_out, real_err = sys.stdout, sys.stderr
        tty_stream = _FakeStream(True, "cp936")
        pipe_stream = _FakeStream(False, "cp936")
        try:
            sys.stdout, sys.stderr = tty_stream, pipe_stream
            osnorm.configure_stdio()
            osnorm.configure_stdio()  # 幂等
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        self.assertEqual(tty_stream.calls[0], {"errors": "replace"},
                         "TTY 分支不得改 encoding（保留本机编码）")
        self.assertEqual(tty_stream.encoding, "cp936")
        self.assertEqual(pipe_stream.calls[0], {"encoding": "utf-8", "errors": "replace"})
        self.assertEqual(pipe_stream.encoding, "utf-8")

    def test_tty_branch_keeps_local_encoding_on_posix(self) -> None:
        """真 TTY 分支：用 pty 起子进程，断言 encoding 前后一致（不硬编码 locale）。"""
        if os.name != "posix":  # Windows 无 pty；TTY 分支已由伪流覆盖
            return
        master, slave = os.openpty()
        try:
            proc = subprocess.Popen(
                [sys.executable, "-B", "-c", STDIO_CHILD, str(SCRIPTS)],
                stdin=slave, stdout=slave, stderr=slave,
                env=dict(os.environ, PYTHONIOENCODING="gbk"))
            os.close(slave)
            slave = -1
            chunks = b""
            deadline = time.monotonic() + 30
            while proc.poll() is None and time.monotonic() < deadline:
                try:
                    chunks += os.read(master, 4096)
                except OSError:  # pragma: no cover - 从端已关
                    break
            proc.wait(timeout=30)
            try:
                chunks += os.read(master, 4096)
            except OSError:  # pragma: no cover
                pass
        finally:
            os.close(master)
            if slave != -1:
                os.close(slave)
        text = chunks.decode("utf-8", "replace")
        self.assertIn("True|", text, f"pty 子进程未被判成 TTY：{text!r}")
        _, before, after = text.strip().split("|")
        self.assertEqual(after, before, "TTY 分支不得改 encoding")

    def test_import_has_no_side_effect(self) -> None:
        """import 期不得改 stdio（`configure_stdio` 只能被显式调用）。"""
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "import osnorm;"
                "print(sys.stdout.encoding)")
        proc = osnorm.run_text([sys.executable, "-B", "-c", code, str(SCRIPTS)],
                               env={"PYTHONIOENCODING": "gbk"})
        self.assertEqual(proc.stdout.strip(), "gbk", "import 期出现了 stdio 副作用")


if __name__ == "__main__":
    unittest.main()
