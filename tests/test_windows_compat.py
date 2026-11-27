#!/usr/bin/env python3
"""Windows 兼容性回归（在 Linux 上可跑：用 Windows 风格**输入**与注入的失败模式，不看 `os.name`）。

为什么需要这一套：3.8.0 在 windows-latest 与真机（Win11 + Python 3.14）上红，而 ubuntu-latest
全绿。Windows 独有的四类失败在这里各有一个**不依赖平台**的可执行判据：

W1 路径族：`repo_identity` 对已解析的本地路径一律前置 `/`，Windows 上产出 `/C:/a/b`
   （既带前导斜杠又保留盘符）。判据：`_canonical_local` 按族归一（盘符大写、保留、UNC 双斜杠），
   并且 `/C:/a/b` 这类畸形形态必须被拒绝，而不是被当成合法 POSIX 绝对路径。
W2 tag 身份：`release_anchor.tag_identity` 用 `rev-parse <ref>^{commit}` 判存在性。peel 失败与
   「ref 不存在」在退出码/输出上完全同形 → 误报 `tag-missing`。判据：改用 `show-ref --verify`
   且**永不**发 `^{...}`；git 自身出错必须报 `git-failed`，不许降级成 `tag-missing`。
W3 上次同步点：`template_sync` 同样用 `cat-file -e <rev>^{commit}` 探测，失败即把用户的
   README_EN 删除当成缺失装回来。判据：`_commit_exists` 只发 `rev-parse --verify --quiet`。
W4 并发替换：`os.replace` 在 Windows 上遇到「读侧持有句柄」会抛 `PermissionError(WinError 5)`。
   判据：分享冲突按指数退避重试且总时长 ≤2s、真越权立刻抛、重试耗尽必须抛（不允许非原子写兜底）。
W5 编码：子进程按 locale（cp1252/cp936）写管道 → 父进程按 UTF-8 解码得到 `?`/乱码或直接
   `UnicodeDecodeError`。判据：`_utf8_stdio()` 把管道编码钉成 UTF-8，控制台保留本机编码；
   端到端在 `PYTHONIOENCODING=cp1252` 下跑真实脚本，输出必须是合法 UTF-8 且中文完好。
W6 并发读：`os.replace` 进行中目标文件处于 delete-pending，读侧 `os.open` 同样拿到
   `PermissionError`（「文件存在却读不到」）。判据：读路径与写路径共用同一套分享冲突重试
   （同一预算、同一分类器），ENOENT 这类真缺失立刻抛，读路径仍然一个字节都不写。
"""
import importlib.util
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import osnorm  # noqa: E402

from repo_identity import _canonical_local, convert_format, repair  # noqa: E402
import local_state as ls                                           # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


anchor = _load("release_anchor_w32", ".github/scripts/release_anchor.py")
sync = _load("template_sync_w32", "scripts/template_sync.py")
release = _load("release_w32", "scripts/release.py")


# ── W1 路径族 ────────────────────────────────────────────────────────────────

class WindowsPathFamily(unittest.TestCase):
    def test_drive_letter_survives_canonicalisation(self):
        """Windows 上 `Path.as_posix()` 给 `C:/a/b`：归一后必须还是 `C:/a/b`。"""
        self.assertEqual(_canonical_local("C:/Users/kinolu/AppData/Local/Temp/identity-relative"),
                         "C:/Users/kinolu/AppData/Local/Temp/identity-relative")

    def test_lowercase_drive_uppercased_and_backslashes_folded(self):
        self.assertEqual(_canonical_local(r"c:\Work\Repo.git"), "C:/Work/Repo")
        self.assertEqual(_canonical_local("d:/x/y/"), "D:/x/y")

    def test_unc_share_keeps_double_slash(self):
        """`\\\\host\\share\\x` 与 `\\\\wsl.localhost\\...` 是两段前缀，折叠成 `/host/share` 就丢了语义。"""
        self.assertEqual(_canonical_local(r"\\host\share\x\y"), "//host/share/x/y")
        self.assertEqual(_canonical_local("//wsl.localhost/Ubuntu/home/k/x"),
                         "//wsl.localhost/Ubuntu/home/k/x")

    def test_leading_slash_drive_is_rejected_not_mangled(self):
        """畸形形态 `/C:/x`（旧实现在 Windows 上的产物）必须 None，不许当成 POSIX 绝对路径。"""
        self.assertIsNone(_canonical_local("/C:/x/y"))
        self.assertIsNone(convert_format("/C:/work/repo"))
        self.assertIsNone(_canonical_local("C:/"))

    def test_device_namespace_prefix_rejected(self):
        self.assertIsNone(_canonical_local("//?/C:/x"))
        self.assertIsNone(_canonical_local("//./C:/x"))

    def test_posix_absolute_unchanged(self):
        """Linux 侧行为不变：POSIX 绝对路径逐字节相同。"""
        self.assertEqual(_canonical_local("/tmp/x.git"), "/tmp/x")
        self.assertEqual(convert_format("/tmp/x.git"), "/tmp/x")

    def test_base_relative_matches_path_as_posix(self):
        """`repair(相对路径, base=…)` 必须等于 `Path.resolve().as_posix()` 本身。

        这是 Windows 上唯一会真正跑到的分支（测试用 `repair("real/repo", base=tmp)` 断言它），
        也是 `/C:/…` 畸形值的产地：旧实现丢了盘符。
        """
        with tempfile.TemporaryDirectory(prefix="w32-identity-") as tmp:
            root = Path(tmp)
            (root / "real/repo").mkdir(parents=True)
            expected = (root / "real/repo").resolve().as_posix()
            self.assertEqual(repair("real/repo", base=root), expected)
            self.assertTrue(expected.startswith("/") or expected[1:3] == ":/")


# ── W2 tag 身份 ──────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return osnorm.run_text(["git", "-C", str(root), *args], errors="replace")


@unittest.skipUnless(shutil.which("git"), "未安装 git：tag 身份用例需要真实仓库")
class TagIdentityWithoutPeeling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="w32-tagid-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir(parents=True)
        osnorm.write_text(self.root / "AGENT_RULES.md", "# rules <!-- id:R1 -->\n\n> **模板政策版本：`1.2.3`。**\n", newline="lf")
        osnorm.write_text(self.root / "scripts" / "verify_rules.py", 'TEMPLATE_FILES = ["AGENT_RULES.md"]\n', newline="lf")
        _git(self.root, "init", "-q", "-b", "dev")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")
        self.real_run = subprocess.run

    def _peel_fails_run(self, argv, *a, **kw):
        """模拟 Windows：任何带 `^{...}` 的调用失败，其余全部透传。"""
        if "^{" in " ".join(str(x) for x in argv):
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: ambiguous argument")
        return self.real_run(argv, *a, **kw)

    def test_tag_lookup_never_uses_peel_syntax(self):
        """tag 存在时，即使 peel 语法不可用，判决也必须是 pass（旧实现给 tag-missing）。"""
        _git(self.root, "tag", "v1.2.3")
        seen: list[str] = []

        def spy(argv, *a, **kw):
            seen.append(" ".join(str(x) for x in argv))
            return self._peel_fails_run(argv, *a, **kw)

        with mock.patch.object(subprocess, "run", spy):
            ok, info = anchor.tag_identity(self.root)
        self.assertTrue(ok, info)
        self.assertEqual(info["verdict"], "pass", info)
        self.assertFalse([c for c in seen if "^{" in c], f"仍在用 peel 语法：{seen}")

    def test_peel_failure_would_misreport_tag_missing(self):
        """自证：桩确实复现了 Windows 的失败形态（否则上面的用例是空跑）。"""
        _git(self.root, "tag", "v1.2.3")
        proc = self._peel_fails_run(
            ["git", "-C", str(self.root), "rev-parse", "refs/tags/v1.2.3^{commit}"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_git_error_is_not_reported_as_tag_missing(self):
        """`show-ref` 自身报错（128）≠ ref 不存在（1）：必须 git-failed，不许 tag-missing。"""
        _git(self.root, "tag", "v1.2.3")

        def failing(argv, *a, **kw):
            if "show-ref" in [str(x) for x in argv]:
                return subprocess.CompletedProcess(argv, 128, stdout="",
                                                   stderr="fatal: not a git repository")
            return self.real_run(argv, *a, **kw)

        with mock.patch.object(subprocess, "run", failing):
            ok, info = anchor.tag_identity(self.root)
        self.assertFalse(ok, info)
        self.assertEqual(info["verdict"], "git-failed", info)
        self.assertIn("not a git repository", info["error"])

    def test_absent_tag_is_still_tag_missing(self):
        """回归保护：真·没有 tag 时仍按 warning 处理（rc=0, tag-missing）。"""
        ok, info = anchor.tag_identity(self.root)
        self.assertTrue(ok, info)
        self.assertEqual(info["verdict"], "tag-missing", info)


# ── W3 上次同步点探测 ────────────────────────────────────────────────────────

class SyncPrevCommitProbe(unittest.TestCase):
    def test_probe_never_uses_peel_syntax(self):
        calls: list[list[str]] = []

        def runner(argv, **kw):
            calls.append(list(argv))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self.assertTrue(sync._commit_exists(Path("."), "deadbeef", runner=runner))
        joined = " ".join(calls[0])
        self.assertNotIn("^{", joined)
        self.assertIn("rev-parse", joined)
        self.assertIn("--verify", joined)
        self.assertIn("--quiet", joined)
        self.assertIn("deadbeef", calls[0])
        self.assertNotIn("cat-file", joined)

    def test_probe_false_when_rev_missing(self):
        self.assertFalse(sync._commit_exists(
            Path("."), "deadbeef",
            runner=lambda argv, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="")))

    def test_probe_false_when_git_unusable(self):
        """git 不可执行时必须返回 False（fail-closed 由上层决定），不许把异常漏出去。"""
        def boom(argv, **kw):
            raise RuntimeError("git 无法执行")

        self.assertFalse(sync._commit_exists(Path("."), "deadbeef", runner=boom))


# ── W4 并发替换 ──────────────────────────────────────────────────────────────

class ReplaceRetryOnSharingViolation(unittest.TestCase):
    def test_retries_share_conflict_then_succeeds(self):
        calls = {"n": 0}
        sleeps: list[float] = []

        with tempfile.TemporaryDirectory(prefix="w32-replace-") as tmp:
            dst = Path(tmp) / "dst.txt"

            def flaky(src, dst_path):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise PermissionError(13, "Access is denied")   # errno EACCES == WinError 5 同类
                osnorm.write_text(Path(dst_path), "ok", newline="lf")

            with mock.patch.object(ls.os, "replace", flaky):
                ls._replace_with_retry("src", dst, sleep=sleeps.append)
            # 夹具必须落在临时目录：旧版直接写相对路径 "dst"，会在**仓库根**留下一个
            # 2 字节的垃圾文件（内容 "ok"），跑一次测试就把工作树弄脏。
            self.assertEqual(osnorm.read_text(dst, bom=False, newline="preserve"), "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(sleeps), 2)
        self.assertLess(sleeps[1], sleeps[0] * 2 + 0.001)   # 指数递增
        self.assertGreater(sleeps[1], sleeps[0])            # 且确实变大

    def test_non_conflict_error_raises_immediately(self):
        calls = {"n": 0}

        def missing(src, dst):
            calls["n"] += 1
            raise FileNotFoundError(2, "No such file")

        with mock.patch.object(ls.os, "replace", missing):
            with self.assertRaises(FileNotFoundError):
                ls._replace_with_retry("src", "dst", sleep=lambda _s: None)
        self.assertEqual(calls["n"], 1, "非分享冲突不许重试（真越权必须立刻抛）")

    def test_budget_is_bounded_and_error_surfaces(self):
        now = [0.0]
        sleeps: list[float] = []
        calls = {"n": 0}

        def always(src, dst):
            calls["n"] += 1
            raise PermissionError(13, "Access is denied")

        def clock():
            return now[0]

        def sleep(sec):
            sleeps.append(sec)
            now[0] += sec

        with mock.patch.object(ls.os, "replace", always):
            with self.assertRaises(PermissionError):
                ls._replace_with_retry("src", "dst", budget=2.0, clock=clock, sleep=sleep)
        self.assertLessEqual(sum(sleeps), 2.0 + 1e-9, "重试总时长必须 ≤2s")
        self.assertGreaterEqual(calls["n"], 2, "必须真的重试过")

    def test_share_conflict_classification(self):
        win5 = OSError(22, "x")
        win5.winerror = 5
        win32 = OSError(22, "x")
        win32.winerror = 32
        other_win = OSError(22, "x")
        other_win.winerror = 3
        self.assertTrue(ls._is_share_conflict(win5))
        self.assertTrue(ls._is_share_conflict(win32))
        self.assertTrue(ls._is_share_conflict(PermissionError(13, "denied")))
        self.assertFalse(ls._is_share_conflict(other_win))
        self.assertFalse(ls._is_share_conflict(FileNotFoundError(2, "gone")))

    def test_bind_uses_retry_path(self):
        """`bind` 的两条写盘路径都必须走重试（而不是裸 os.replace）。"""
        src = osnorm.read_text(ROOT / "scripts" / "local_state.py", bom=False, newline="preserve")
        self.assertIn("_replace_with_retry(tmp_path, d / _RUNTIME_FILE)", src)
        self.assertIn("_replace_with_retry(tmp_dir, d)", src)
        self.assertNotIn("os.replace(tmp_path", src)
        self.assertNotIn("os.replace(tmp_dir", src)


# ── W6 并发读（delete-pending 期间的 open） ──────────────────────────────────

class ReadRetryOnSharingViolation(unittest.TestCase):
    def test_reader_tolerates_transient_sharing_error(self):
        """读侧必须吸收瞬时独占：`os.replace` 期间 open 拿 EACCES，重试后成功。

        这是真机上的失败形态（Windows 11 + Python 3.14，并发读 runtime.json）：
        `PermissionError: [Errno 13] Permission denied: '...\\runtime.json'`，
        而文件其实一直在。读侧重试与写侧 `os.replace` 重试是同一机制。
        """
        calls = {"n": 0}
        real = ls.os.open

        def flaky(path, flags, mode=0o777):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(13, "Access is denied")
            return real(path, flags, mode)

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "runtime.json"
            osnorm.write_text(f, '{"schema": 1}', newline="lf")
            with mock.patch.object(ls.os, "open", flaky):
                self.assertEqual(ls._read_json_no_follow(f), {"schema": 1})
            self.assertEqual(osnorm.read_text(f, bom=False, newline="preserve"), '{"schema": 1}',
                             "读路径重试不得写盘")
        self.assertEqual(calls["n"], 3, "必须真的重试到成功")

    def test_reader_does_not_retry_missing_file(self):
        """ENOENT 不是分享冲突：真缺失要立刻抛，不许白等 2 秒。"""
        calls = {"n": 0}
        real = ls.os.open

        def counting(path, flags, mode=0o777):
            calls["n"] += 1
            return real(path, flags, mode)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ls.os, "open", counting):
                with self.assertRaises(FileNotFoundError):
                    ls._read_json_no_follow(Path(tmp) / "nope.json")
        self.assertEqual(calls["n"], 1)

    def test_read_and_write_share_one_retry_helper(self):
        """读侧与写侧必须是**同一个**重试实现（同一预算/分类器），不能各写一套。"""
        src = osnorm.read_text(ROOT / "scripts" / "local_state.py", bom=False, newline="preserve")
        self.assertIn("return _retry_on_sharing(_once, budget=_READ_BUDGET)", src)
        self.assertIn("_retry_on_sharing(lambda: os.replace(src, dst)", src)
        self.assertEqual(ls._READ_BUDGET, ls._REPLACE_BUDGET, "读写两侧预算必须对称")

    def test_bind_verification_read_happens_under_lock(self):
        """`bind` 的收尾校验读必须在锁内：锁外读会撞上兄弟进程的 delete-pending 窗口。

        锁外读的失败形态是多进程并发写时偶发 `p.exitcode == 1`（读到 CORRUPT 或撞上
        PermissionError），与「并发读」同源，靠加读侧重试只是兜底。
        """
        src = osnorm.read_text(ROOT / "scripts" / "local_state.py", bom=False, newline="preserve")
        head, _, tail = src.partition("with _locked(cid):")
        self.assertIn("return read_state(workspace, workspace_id, ident, ephemeral=ephemeral)",
                      tail.split("_SID_RE")[0],
                      "收尾校验读必须留在锁内")


# ── W7 写盘行尾：治理文件必须钉死 LF ─────────────────────────────────────────

class GovernanceWritersPinLf(unittest.TestCase):
    def test_every_repo_text_writer_pins_lf(self):
        """所有写仓库文本产物的 `write_text` 必须显式 `newline="\\n"`。

        Windows 的 text 模式写会把 `\\n` 翻成 `\\r\\n`。而治理文件的**字节**就是判据：
        读证据是字节级全等（gate_core 模块头「不做哈希前归一化」），注册表是 file_sha256。
        产品自己把 AGENT_RULES.md 写成 CRLF，就等于把该工作区里的守门永久判成 deny
        （真机失败形态：`release.py --apply` / `mark_rules.py registry` 跑过之后，
        `test_dsh_wiring::test_d2` 与 `test_pwsh_writes::test_after_full_read_write_is_allowed`
        一律 `deny`）。`.gitattributes eol=lf` 只管 git 检出，管不到我们自己的写盘。
        """
        offenders = []
        for pattern in ("scripts/*.py", ".github/scripts/*.py"):
            for path in sorted(ROOT.glob(pattern)):
                tree = ast.parse(osnorm.read_text(path, bom=False, newline="preserve"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = (getattr(node.func, "attr", None)
                            or getattr(node.func, "id", None))
                    if name == "write_text" and not any(
                            k.arg == "newline" for k in node.keywords):
                        offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [], f"这些写盘会按平台翻译行尾（Windows→CRLF）：{offenders}")


# ── W5 编码 ──────────────────────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, encoding: str, tty: bool = False):
        self.encoding = encoding
        self._tty = tty
        self.calls: list[dict] = []

    def isatty(self) -> bool:
        return self._tty

    def reconfigure(self, **kw) -> None:
        self.calls.append(kw)
        if "encoding" in kw:
            self.encoding = kw["encoding"]


class Utf8StdioGuard(unittest.TestCase):
    def _run_guard(self, out: _FakeStream, err: _FakeStream) -> None:
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            release._utf8_stdio()

    def test_pipe_reconfigured_to_utf8(self):
        out, err = _FakeStream("cp1252"), _FakeStream("gbk")
        self._run_guard(out, err)
        self.assertEqual(out.encoding.lower(), "utf-8")
        self.assertEqual(err.encoding.lower(), "utf-8")
        for stream in (out, err):
            self.assertEqual(stream.calls[-1].get("errors"), "replace")

    def test_console_keeps_local_encoding(self):
        """真控制台只加 errors=replace：reconfigure 成 UTF-8 会让 Windows 控制台按本地代码页误渲染。"""
        out, err = _FakeStream("cp936", tty=True), _FakeStream("cp936", tty=True)
        self._run_guard(out, err)
        self.assertEqual(out.encoding, "cp936")
        self.assertEqual(out.calls, [{"errors": "replace"}])

    def test_already_utf8_is_noop(self):
        out, err = _FakeStream("UTF-8"), _FakeStream("utf8")
        self._run_guard(out, err)
        self.assertEqual(out.calls, [])
        self.assertEqual(err.calls, [])

    def test_script_writes_utf8_even_under_cp1252_locale(self):
        """端到端：`PYTHONIOENCODING=cp1252` 下真实脚本的中文输出仍是合法 UTF-8。

        没有这个守卫时，子进程会按 cp1252 写管道 → 中文先崩（UnicodeEncodeError）或被父进程
        解成乱码/`?`，这正是 Windows CI 与真机上那批 `UnicodeDecodeError: 'gbk' codec ...` 的产地。
        """
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        missing = Path(tempfile.gettempdir()) / "w32-no-such-workspace-dir"
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "gate_status.py"),
                               "--require", "cc", "--workspace", str(missing)],
                              capture_output=True, env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        text = proc.stderr.decode("utf-8")          # 严格解码：不是合法 UTF-8 就红
        self.assertIn("不是目录", text)
        # gate_status 对 workspace 做 `.resolve()`：Windows CI 的 %TEMP% 是 8.3 短名
        # （RUNNER~1），回显会是长名。两边都按 resolve 后比较（R53）。
        self.assertIn(str(missing.resolve()), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
