#!/usr/bin/env python3
"""树内按 context 分片的本机状态（`scripts/local_state.py`）契约测试。

覆盖：未绑定 / 别人的 context / 绑定与判读 / 根漂移 / 字段篡改 / 损坏 JSON / 身份不可得 /
venv 路径契约 / 不落明文路径 / 并发读不撕裂 / 冲突副本不采信 / ephemeral 开关 /
seed 覆盖 / 首次绑定原子发布 / symlink 逃逸拒绝 / principal 按 realm / 空 seed 非法 /
context 目录无多余文件 / mode 词表 / HMAC 固定向量 / 首次绑定 fsync / 多进程写 / 工作区别名。

运行：python3 -B tests/test_local_state.py
"""
import json
import hashlib
import hmac
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import local_state as ls  # noqa: E402

WSID = "11111111-2222-3333-4444-555555555555"


def _safe_symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """建符号链接；本机没有该权限（非管理员且未开开发者模式的 Windows）时跳过用例。

    跳过的是**夹具能力**（SeCreateSymbolicLinkPrivilege），不是被测属性：
    `_chain_is_safe` 的拒绝逻辑在 Windows 上同样必须成立，只是这台机器造不出样本。
    """
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:      # pragma: no cover - 平台相关
        raise unittest.SkipTest(f"本机不支持创建符号链接（{exc}）")


def ident(material=b"machine-A", principal="1000", realm="linux") -> "ls.Identity":
    return ls.Identity(machine_material=material, principal=principal, realm=realm)


def _bind_worker(ws: str, version: str) -> None:
    import local_state as _ls
    _ls.bind(Path(ws), WSID, _ls.Identity(b"machine-A", "1000", "linux"),
             "local-venv", ".venv-312", version, ephemeral=False)


def _snapshot(root: Path) -> dict:
    """目录树快照（相对路径 → (是否目录, 大小, mtime_ns)），用于证明只读路径不写盘。"""
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.stat()
        out[str(p.relative_to(root))] = (p.is_dir(), st.st_size, st.st_mtime_ns)
    return out


def _scope_worker(home: str) -> str:
    import local_state as _ls
    _ls._MATERIAL_CACHE = None
    return _ls.machine_scope({"XDG_STATE_HOME": home})


def _wid_worker(bootdir: str) -> str | None:
    from pathlib import Path as _P
    import local_state as _ls
    return _ls.ensure_workspace_id(_P(bootdir))


class LocalStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ls-test-"))
        self.ws = self.tmp / "ws"
        (self.ws / ".workspace").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 基础判读 -------------------------------------------------------
    def test_01_unbound_creates_nothing(self):
        res = ls.read_status(self.ws, WSID, ident(), ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("UNBOUND", "no-local-state"))
        self.assertFalse((self.ws / ".workspace" / ".local").exists())

    def test_02_other_context_is_not_mine(self):
        a, b, c = (ident(material=f"machine-{x}".encode()) for x in ("A", "B", "C"))
        ls.bind(self.ws, WSID, a, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        ls.bind(self.ws, WSID, b, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        root = ls.state_root(self.ws)

        def snapshot():
            return {p.name: (p / "runtime.json").read_bytes()
                    for p in sorted(root.iterdir())
                    if p.is_dir() and (p / "runtime.json").is_file()}

        before = snapshot()
        self.assertEqual(ls.read_status(self.ws, WSID, a, ephemeral=False)["status"], "BOUND")
        res = ls.read_status(self.ws, WSID, c, ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("UNBOUND", "no-self-state"))
        self.assertEqual(snapshot(), before, "读别人的状态不得修改对方文件")

    def test_03_bind_then_bound_and_payload(self):
        i = ident()
        res = ls.bind(self.ws, WSID, i, "uv", ".venv-314", "3.14.7", ephemeral=False)
        self.assertEqual(res["status"], "BOUND")
        self.assertEqual(res["data"]["mode"], "uv")
        self.assertEqual(res["data"]["venv_relpath"], ".venv-314")
        self.assertEqual(res["data"]["version"], "3.14.7")
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "BOUND")

    def test_04_root_mismatch_after_move(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        moved = self.tmp / "ws-moved"
        shutil.move(str(self.ws), str(moved))
        res = ls.read_status(moved, WSID, i, ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("UNBOUND", "root-mismatch"))

    # ---- 损坏与篡改 -----------------------------------------------------
    def test_05_workspace_mismatch(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        f = ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        data["workspace_id"] = "deadbeef-dead-dead-dead-deaddeadbeef"
        f.write_text(json.dumps(data), encoding="utf-8")
        res = ls.read_status(self.ws, WSID, i, ephemeral=False)
        self.assertEqual(res["status"], "CORRUPT")
        self.assertIn("workspace", res["reason"])

    def test_06_corrupt_json(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        (ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json").write_text(
            "{not json", encoding="utf-8")
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "CORRUPT")

    def test_07_identity_unavailable(self):
        res = ls.read_status(self.ws, WSID, None, ephemeral=False)
        self.assertEqual(res["status"], "IDENTITY_UNAVAILABLE")
        self.assertFalse((self.ws / ".workspace" / ".local").exists())

    # ---- 输入校验 -------------------------------------------------------
    def test_08_venv_path_contract(self):
        bad = ["/abs/.venv-312", "C:\\abs\\.venv-312", "\\rooted", "C:drive-relative",
               "../outside", "a/../../b", "", " .venv-312"]
        for value in bad:
            with self.assertRaises(ValueError, msg=value):
                ls.bind(self.ws, WSID, ident(), "local-venv", value, "3.12.7", ephemeral=False)
        self.assertFalse((self.ws / ".workspace" / ".local").exists())

    def test_18_venv_relpath_type_rejected_before_mkdir(self):
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", Path(".venv-312"), "3.12.7",
                    ephemeral=False)
        self.assertFalse((self.ws / ".workspace" / ".local").exists(), "类型错误不得留下目录")

    def test_22_mode_vocabulary(self):
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "venv", ".venv-312", "3.12.7", ephemeral=False)
        for mode in ("uv", "local-venv", "mcp"):
            self.assertEqual(
                ls.bind(self.ws, WSID, ident(), mode, ".venv-312", "3.12.7",
                        ephemeral=False)["data"]["mode"], mode)

    def test_09_no_plaintext_paths(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        text = (ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json").read_text(
            encoding="utf-8")
        for bad in (str(self.ws), str(self.ws.resolve()), str(Path.home())):
            self.assertNotIn(bad, text)

    # ---- 并发与写入完整性 -----------------------------------------------
    def test_10_concurrent_read_never_sees_partial_json(self):
        """并发读：每次拿到的都必须是**完整** payload。

        读走产品自己的读路径（`_read_json_no_follow`），不裸用 `read_text()`：
        Windows 上 `os.replace` 的瞬间目标文件处于 delete-pending，裸 `open()`
        拿到 `PermissionError: [Errno 13]`（文件明明存在却读不到）—— 那是产品读路径
        必须吸收的**瞬时独占**，不是「读到半截」。判据不变且更强：从第一次成功读
        之后，任何异常或非完整 payload 都算撕裂。
        """
        i = ident()
        stop = threading.Event()
        errors = []
        reads = [0]

        def reader():
            f = ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json"
            while not stop.is_set():
                try:
                    data = ls._read_json_no_follow(f)
                except FileNotFoundError:
                    continue      # 首次发布前还没有文件；发布是原子的，不存在半截形态
                except Exception as exc:      # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                if not isinstance(data, dict) or data.get("schema") != ls.STATE_VERSION:
                    errors.append(f"partial-json: {str(data)[:80]}")
                reads[0] += 1

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            for n in range(20):
                ls.bind(self.ws, WSID, i, "local-venv", f".venv-3{n % 3}12", "3.12.7",
                        ephemeral=False)
                time.sleep(0.002)
        finally:
            stop.set()
            t.join(timeout=2)
        self.assertFalse(errors, f"并发读到半截 JSON：{errors[:3]}")
        self.assertGreater(reads[0], 0, "读线程一次都没读到，用例无效")

    def test_14_first_bind_never_exposes_empty_context(self):
        for round_no in range(5):
            ws = self.tmp / f"ws{round_no}"
            (ws / ".workspace").mkdir(parents=True)
            i = ident(material=f"machine-{round_no}".encode())
            cid = ls.context_id(WSID, i)
            stop = threading.Event()
            bad = []
            seen = [0]

            def reader():
                d = ls.state_root(ws) / cid
                while not stop.is_set():
                    if d.is_dir():
                        seen[0] += 1
                        if not (d / "runtime.json").is_file():
                            bad.append("empty-context-visible")

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            try:
                time.sleep(0.001)
                ls.bind(ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
                time.sleep(0.001)
            finally:
                stop.set()
                t.join(timeout=2)
            self.assertFalse(bad, f"首次绑定暴露了空 context 目录：{bad[:3]}")
            self.assertGreater(seen[0], 0, "探针没观察到发布后的目录，用例无效")

    def test_24_first_bind_fsyncs_file_and_dirs(self):
        calls = [0]
        dirs: list[str] = []
        real_fsync = os.fsync
        real_dir = ls._fsync_dir

        def counting(fd):
            calls[0] += 1
            return real_fsync(fd)

        def recording(path):
            dirs.append(str(path))
            return real_dir(path)

        os.fsync = counting
        ls._fsync_dir = recording
        try:
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        finally:
            os.fsync = real_fsync
            ls._fsync_dir = real_dir
        self.assertGreaterEqual(calls[0], 1, "首次发布必须 fsync 文件")
        self.assertGreaterEqual(len(dirs), 2, f"首次发布必须 fsync 临时目录与父目录：{dirs}")
        cid = ls.context_id(WSID, ident())
        self.assertTrue(any(cid not in d for d in dirs), "必须有对发布父目录的 fsync")
        self.assertTrue(any(".publish-" in d for d in dirs), "必须有对临时目录的 fsync")

    def test_25_multiprocess_writers_leave_valid_state(self):
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_bind_worker, args=(str(self.ws), f"3.12.{n}"))
                 for n in range(3)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            self.assertEqual(p.exitcode, 0)
        res = ls.read_state(self.ws, WSID, ident(), ephemeral=False)
        self.assertEqual(res["status"], "BOUND")
        self.assertIn(res["data"]["version"], {"3.12.0", "3.12.1", "3.12.2"})

    def test_11_conflict_copies_ignored(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        bad = {"schema": "v1", "workspace_id": "other", "context_id": "zzz"}
        (d / "runtime (conflicted copy).json").write_text(json.dumps(bad), encoding="utf-8")
        (d / "runtime.sync-conflict-20260101.json").write_text(json.dumps(bad), encoding="utf-8")
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "BOUND")

    def test_19_context_dir_has_no_extra_files(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["runtime.json"],
                         "锁与临时文件不得留在 context 目录")

    def test_29_read_only_never_writes_any_branch(self):
        """所有判读分支（含全部 CORRUPT/UNBOUND）都必须一个字节都不写。"""
        i = ident()
        cid = ls.context_id(WSID, i)
        d = ls.state_root(self.ws) / cid

        def prepare_bad(kind: str) -> Path:
            ws = Path(tempfile.mkdtemp(dir=str(self.tmp))) / "ws"
            (ws / ".workspace").mkdir(parents=True)
            ls.bind(ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
            f = ls.state_root(ws) / cid / "runtime.json"
            data = json.loads(f.read_text(encoding="utf-8"))
            if kind == "bad-json":
                f.write_text("{not json", encoding="utf-8")
            elif kind == "unknown-schema":
                data["schema"] = "v2"
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "missing-fields":
                data.pop("version")
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "invalid-fields":
                data["mode"] = "bogus"
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "workspace-mismatch":
                data["workspace_id"] = "deadbeef-dead-dead-dead-deaddeadbeef"
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "context-mismatch":
                data["context_id"] = "0" * 64
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "realm-mismatch":
                data["realm"] = "windows"
                f.write_text(json.dumps(data), encoding="utf-8")
            elif kind == "missing-runtime":
                f.unlink()
            return ws

        expectations = {
            "bad-json": "bad-json",
            "unknown-schema": "unknown-schema",
            "missing-fields": "missing-fields",
            "invalid-fields": "invalid-fields",
            "workspace-mismatch": "workspace-mismatch",
            "context-mismatch": "context-mismatch",
            "realm-mismatch": "realm-mismatch",
            "missing-runtime": "missing-runtime",
        }
        for kind, reason in expectations.items():
            with self.subTest(kind=kind):
                ws = prepare_bad(kind)
                before = _snapshot(ws / ".workspace")
                res = ls.read_state(ws, WSID, i, ephemeral=False)
                ls.read_status(ws, WSID, i, ephemeral=False)
                after = _snapshot(ws / ".workspace")
                self.assertEqual(res["status"], "CORRUPT", kind)
                self.assertEqual(res["reason"], reason, kind)
                self.assertEqual(before, after, f"{kind}：只读判定写了盘")

        # UNBOUND：完全未绑定
        ws0 = Path(tempfile.mkdtemp(dir=str(self.tmp))) / "ws0"
        (ws0 / ".workspace").mkdir(parents=True)
        before = _snapshot(ws0 / ".workspace")
        self.assertEqual(ls.read_status(ws0, WSID, i, ephemeral=False)["reason"],
                         "no-local-state")
        self.assertEqual(before, _snapshot(ws0 / ".workspace"), "no-local-state 写了盘")

        # UNBOUND：别人的 context（先为自己的 context 绑定，才可能是 no-self-state）
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        other = ident(material=b"machine-Z")
        before = _snapshot(self.ws / ".workspace")
        res = ls.read_status(self.ws, WSID, other, ephemeral=False)
        self.assertEqual(res["reason"], "no-self-state")
        self.assertEqual(before, _snapshot(self.ws / ".workspace"), "no-self-state 写了盘")

        # UNBOUND：工作区被移动
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        moved = self.tmp / "moved-ws"
        shutil.move(str(self.ws), str(moved))
        before = _snapshot(moved / ".workspace")
        res = ls.read_status(moved, WSID, i, ephemeral=False)
        self.assertEqual(res["reason"], "root-mismatch")
        self.assertEqual(before, _snapshot(moved / ".workspace"), "root-mismatch 写了盘")

    # ---- 身份 -----------------------------------------------------------
    def test_12_ephemeral_flag(self):
        self.assertIs(ls.ephemeral({"WORKSPACE_CONTEXT_EPHEMERAL": "1"}), True)
        self.assertIs(ls.ephemeral({}), False)
        self.assertIs(ls.ephemeral({"WORKSPACE_CONTEXT_EPHEMERAL": "0"}), False)
        i = ident()
        a = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        b = ls.context_dir(self.ws, WSID, i, ephemeral=True)
        self.assertEqual(b.name, a.name + "-eph")
        with self.assertRaises(TypeError):
            ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral="false")

    def test_13_seed_override(self):
        info = {"machine_id": b"machine-A", "uid": "1000", "realm": "linux"}
        i1 = ls.resolve_identity({"WORKSPACE_CONTEXT_SEED": "ci-run-42"}, info)
        i2 = ls.resolve_identity({"WORKSPACE_CONTEXT_SEED": "ci-run-43"}, info)
        self.assertIsNotNone(i1)
        self.assertIsNotNone(i2)
        self.assertNotEqual(ls.context_id(WSID, i1), ls.context_id(WSID, i2))

    def test_16_principal_by_realm(self):
        win = {"machine_id": b"m", "uid": "1000", "sid": "S-1-5-21", "realm": "windows"}
        i = ls.resolve_identity({}, win)
        self.assertIsNotNone(i)
        self.assertEqual(i.principal, "S-1-5-21")
        posix = {"machine_id": b"m", "uid": "1000", "sid": "S-1-5-21", "realm": "linux"}
        i2 = ls.resolve_identity({}, posix)
        self.assertIsNotNone(i2)
        self.assertEqual(i2.principal, "1000")

    def test_28_windows_sid_parsed_not_username(self):
        class Fake:
            stdout = ("USER INFORMATION\n------------\n\n"
                      "User Name   SID\nkinolu      S-1-5-21-1111-2222-3333-1001\n")

        self.assertEqual(ls._windows_sid(lambda: Fake()),
                         "S-1-5-21-1111-2222-3333-1001")
        class Bad:
            stdout = "whoami: no user information"

        self.assertIsNone(ls._windows_sid(lambda: Bad()))
        self.assertIsNone(ls._windows_sid(lambda: (_ for _ in ()).throw(OSError("nope"))))

    def test_17_empty_seed_is_invalid(self):
        info = {"machine_id": b"m", "uid": "1000", "realm": "linux"}
        self.assertIsNone(ls.resolve_identity({"WORKSPACE_CONTEXT_SEED": ""}, info))

    def test_23_hmac_fixed_vectors(self):
        """HMAC 公式里**跨平台可复现**的那一半用固定向量钉死，路径绑定只钉口径。

        `context_id` 的输入全是字面量（workspace_id / material / principal / realm），
        任何平台都必须给出同一个值 —— 那是真正的固定向量。
        `_root_binding` 的输入含 `os.path.realpath(workspace)`：Windows 上会多出盘符、
        分隔符变反斜杠（`/tmp/x` → `C:\\tmp\\x`），**不存在跨平台相同的常量**。旧用例
        把 Linux 上的哈希写死（b0a4543e…），等于把「Linux 机器身份」当成判据 —— 在
        Windows 上必然红。改成可判定断言：长度/字符集、同输入同值、不同路径与不同
        cid 必须不同、同一目录的不同写法（`a` 与 `a/.`）必须同值、且等于按文档口径
        独立算出的 realpath-byte HMAC。
        """
        i = ident()
        self.assertEqual(ls.context_id(WSID, i),
                         "0f60a5ad61a652706f636371a446785f2d3960f5e63186f1c6f335365a220a59")
        # realm 是公式的一部分：同 material/principal、不同 realm 必须得到不同 context_id
        win = ident(realm="windows")
        self.assertNotEqual(ls.context_id(WSID, i), ls.context_id(WSID, win))
        self.assertEqual(ls.context_id(WSID, win),
                         "393e753d11f05c8c9b9fc05f3de769076aee8146aaf3476f5b0d18f9896b3d7a")

        cid = ls.context_id(WSID, i)
        a = self.tmp / "bind-a"
        b = self.tmp / "bind-b"
        a.mkdir()
        b.mkdir()
        binding = ls._root_binding(a, cid)
        self.assertRegex(binding, r"^[0-9a-f]{64}$", "root_binding 必须是 64 位小写 hex")
        self.assertEqual(binding, ls._root_binding(a, cid), "同输入必须同值（同平台可复现）")
        self.assertEqual(binding, ls._root_binding(Path(str(a) + os.sep + "."), cid),
                         "同一目录的不同写法必须绑到同一个值（realpath 归一）")
        self.assertNotEqual(binding, ls._root_binding(b, cid), "不同工作区路径必须不同值")
        self.assertNotEqual(binding, ls._root_binding(a, ls.context_id(WSID, win)),
                            "不同 context 必须不同值")
        # 口径复算：cid 作 key、realpath(workspace) 的**字节流**作消息（不是 str(path) 原样）
        self.assertEqual(binding,
                         hmac.new(cid.encode("ascii"),
                                  os.fsencode(os.path.realpath(os.fspath(a))),
                                  hashlib.sha256).hexdigest(),
                         "root_binding 口径必须是 realpath 字节流，不是原生字符串")

    def test_30_machine_scope_falls_back_to_local_seed(self):
        """取不到 machine-id 时用本机持久种子：两台机器（不同状态目录）必须得到不同 scope。"""
        old = ls._MATERIAL_CACHE
        ls._MATERIAL_CACHE = None
        try:
            env1 = {"XDG_STATE_HOME": str(self.tmp / "st1")}
            a = ls.machine_scope(env1)
            b = ls.machine_scope(env1)
            env2 = {"XDG_STATE_HOME": str(self.tmp / "st2")}
            c = ls.machine_scope(env2)
        finally:
            ls._MATERIAL_CACHE = old
        self.assertNotEqual(a, "unknown")
        self.assertEqual(a, b, "同一台机器的 scope 必须稳定")
        self.assertNotEqual(a, c, "两台机器不得共用 same scope")
        seed_file = self.tmp / "st1" / "workspace-template" / "scope-seed"
        self.assertTrue(seed_file.is_file())
        if os.name != "nt":
            self.assertEqual(seed_file.stat().st_mode & 0o777, 0o600)

    def test_31_empty_scope_seed_is_repaired(self):
        """崩溃留下的空种子文件必须被重建，不能永久退化成 unknown。"""
        old = ls._MATERIAL_CACHE
        ls._MATERIAL_CACHE = None
        try:
            home = self.tmp / "st-empty"
            seed_dir = home / "workspace-template"
            seed_dir.mkdir(parents=True)
            (seed_dir / "scope-seed").write_bytes(b"")
            scope = ls.machine_scope({"XDG_STATE_HOME": str(home)})
            self.assertNotEqual(scope, "unknown", "空种子文件没有被修复")
            self.assertTrue((seed_dir / "scope-seed").read_bytes().strip())
        finally:
            ls._MATERIAL_CACHE = old

    def test_32_concurrent_scope_seed_creation_converges(self):
        """多个进程同时首次创建种子：必须拿到同一个 scope，且不出现 unknown。"""
        home = str(self.tmp / "st-race")
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(4) as pool:
            scopes = pool.map(_scope_worker, [home] * 4)
        self.assertNotIn("unknown", scopes)
        self.assertEqual(len(set(scopes)), 1, f"并发首次创建得到不同 scope：{scopes}")

    def test_33_no_seed_created_without_lock(self):
        """锁被占（或陈旧未回收）时只能回读，绝不自己造种子。"""
        old = ls._MATERIAL_CACHE
        ls._MATERIAL_CACHE = None
        try:
            home = self.tmp / "st-locked"
            seed_dir = home / "workspace-template"
            seed_dir.mkdir(parents=True)
            lock = seed_dir / "scope-seed.lock"
            lock.write_bytes(b"")
            scope = ls.machine_scope({"XDG_STATE_HOME": str(home)})
            self.assertEqual(scope, "unknown", "没拿到锁却造了种子")
            self.assertFalse((seed_dir / "scope-seed").exists(), "没拿到锁却写了种子文件")
        finally:
            ls._MATERIAL_CACHE = old

    def test_34_detect_platform_info_never_raises_on_timeout(self):
        """Darwin 的 ioreg 超时不得穿透（契约是「不抛异常」）。

        平台族由 `platform.system()` **单点**决定（产品侧已收敛成唯一真值源），所以
        任何宿主都能真实走一遍 Darwin 分支；旧用例在 Windows 上必红的原因不是
        「期望写错」这么简单——产品当时还以 `os.name == "nt"` 为第二真值源，patch 掉
        `platform.system()` 也不生效。现在期望值 `macos` 是真能判定的。
        """
        import platform as _platform
        import subprocess as _subprocess
        old_sys, old_run = _platform.system, _subprocess.run

        def boom(*_a, **_k):
            raise _subprocess.TimeoutExpired(cmd="ioreg", timeout=5)

        _platform.system = lambda: "Darwin"
        _subprocess.run = boom
        try:
            info = ls.detect_platform_info({})
        finally:
            _platform.system = old_sys
            _subprocess.run = old_run
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get("realm"), "macos")
        self.assertIsNone(info.get("machine_id"), "ioreg 超时后不得编造 machine_id")

    def test_35_stale_lock_is_reclaimed(self):
        """持锁进程崩溃残留的 .lock（>10s）必须被回收，否则永远 unknown。"""
        old = ls._MATERIAL_CACHE
        ls._MATERIAL_CACHE = None
        try:
            home = self.tmp / "st-stale"
            seed_dir = home / "workspace-template"
            seed_dir.mkdir(parents=True)
            lock = seed_dir / "scope-seed.lock"
            lock.write_bytes(b"")
            old_ts = __import__("time").time() - 20
            os.utime(lock, (old_ts, old_ts))
            scope = ls.machine_scope({"XDG_STATE_HOME": str(home)})
            self.assertNotEqual(scope, "unknown", "陈旧锁没有被回收")
            self.assertTrue((seed_dir / "scope-seed").read_bytes().strip())
            self.assertFalse(lock.exists(), "回收后应删掉锁文件")
        finally:
            ls._MATERIAL_CACHE = old

    def test_36_workspace_id_created_once_and_readonly_variant(self):
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.write_text(json.dumps({"workspace_status": "template"}), encoding="utf-8")
        self.assertIsNone(ls.workspace_id_of(self.ws), "只读取值不应凭空造 id")
        self.assertNotIn("workspace_id",
                         json.loads(boot.read_text(encoding="utf-8")), "只读取值写了盘")
        wid = ls.ensure_workspace_id(self.ws)
        self.assertRegex(wid or "", r"^[0-9a-f-]{36}$")
        self.assertEqual(ls.ensure_workspace_id(self.ws), wid, "ensure 不幂等")
        self.assertEqual(ls.workspace_id_of(self.ws), wid)
        data = json.loads(boot.read_text(encoding="utf-8"))
        self.assertEqual(data["workspace_id"], wid)
        self.assertEqual(data["workspace_status"], "template", "不得顺手改别的字段")

    def test_38_concurrent_workspace_id_converges(self):
        """多进程首次 ensure：所有返回值必须一致，且等于落盘值。"""
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.write_text(json.dumps({"workspace_status": "template", "sentinel": 1}), encoding="utf-8")
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(6) as pool:
            ids = pool.map(_wid_worker, [str(self.ws)] * 6)
        self.assertTrue(all(ids), f"有进程拿不到 id：{ids}")
        self.assertEqual(len(set(ids)), 1, f"并发首次 ensure 产生多个 id：{set(ids)}")
        data = json.loads(boot.read_text(encoding="utf-8"))
        self.assertEqual(data["workspace_id"], ids[0])
        self.assertEqual(data["sentinel"], 1, "不得改动其他字段")

    def test_39_optional_fields_reject_absolute_paths(self):
        """R8：本机状态不得存明文绝对路径——可选字段也要拦。"""
        bad_runtime = ({"identifier": "/abs/python"}, {"nested": {"p": "C:\\abs\\py"}},
                       {"u": "~/venv"})
        for payload in bad_runtime:
            with self.assertRaises(ValueError, msg=str(payload)):
                ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                        ephemeral=False, runtime=payload)
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                    ephemeral=False, capabilities={"diag": "\\\\srv\\share"})
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                    ephemeral=False, decision_source="/home/a/choice.txt")
        # 字典**键**也是字符串：不能靠「值安全」蒙混
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                    ephemeral=False, runtime={"/abs/key": "safe"})
        # Windows 风格 ~\ 也要识别
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                    ephemeral=False, runtime={"p": "~\\secret"})
        # 必填字段同样不得是绝对路径（version 就是一条真实可乘之机）
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "/home/u/python",
                    ephemeral=False)
        self.assertFalse((self.ws / ".workspace" / ".local").exists(), "校验失败不得落盘")
        ok = ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                     ephemeral=False, runtime={"identifier": ".venv-312", "uv_version": "0.13"},
                     decision_source="local-venv（用户选择）")
        self.assertEqual(ok["status"], "BOUND")

    def test_40_ensure_workspace_id_is_serialised(self):
        """两个线程同时首次 ensure：必须收敛到同一个 id（去掉锁会稳定失败）。

        用「慢 uuid」把生成窗口撑开：没有锁时两个线程都会在读到空 id 后各自生成，
        返回值必然不同；有锁时后到者拿到的是前者已落盘的值。
        """
        import threading
        boot = self.ws / ".workspace" / "bootstrap.json"
        boot.write_text(json.dumps({"workspace_status": "template"}), encoding="utf-8")
        real_uuid4 = ls.uuid.uuid4
        counter = [0]

        def slow_uuid4():
            time.sleep(0.05)
            counter[0] += 1
            return real_uuid4()

        results: list[str | None] = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            results.append(ls.ensure_workspace_id(self.ws))

        ls.uuid.uuid4 = slow_uuid4
        try:
            ts = [threading.Thread(target=worker) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=10)
        finally:
            ls.uuid.uuid4 = real_uuid4
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1, f"并发首次 ensure 返回了不同 id：{results}")
        self.assertEqual(json.loads(boot.read_text(encoding="utf-8"))["workspace_id"], results[0])
        self.assertEqual(counter[0], 1, "锁没有起作用：uuid 被生成了不止一次")

    def test_41_written_state_has_no_absolute_strings(self):
        """不变量不是「几个字段被拒」，而是「落盘的 JSON 里任何字符串都不像绝对路径」。"""
        res = ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7",
                      ephemeral=False, runtime={"identifier": ".venv-312", "uv_version": "0.13"},
                      capabilities={"workspace_file_access": True},
                      decision_source="local-venv（用户选择）")
        self.assertEqual(res["status"], "BOUND")
        path = ls.context_dir(self.ws, WSID, ident(), ephemeral=False) / "runtime.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        offenders: list[str] = []

        def walk(node, where: str) -> None:
            if isinstance(node, str):
                if ls._looks_absolute(node):
                    offenders.append(f"{where}={node!r}")
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(k, f"{where}.<key>")
                    walk(v, f"{where}.{k}")
                return
            if isinstance(node, (list, tuple)):
                for i, v in enumerate(node):
                    walk(v, f"{where}[{i}]")

        walk(data, "$")
        self.assertEqual(offenders, [], f"状态文件含绝对路径字符串：{offenders}")

    # ---- 路径安全 -------------------------------------------------------
    def test_15_symlink_escape_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        _safe_symlink(self.ws / ".workspace" / ".local", outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        self.assertEqual(list(outside.iterdir()), [], "不得通过 symlink 写工作区外")

    @unittest.skipIf(os.name == "nt", "POSIX 权限模型下才有的 symlink 语义")
    def test_21_symlinked_context_dir_refused(self):
        outside = self.tmp / "outside-ctx"
        outside.mkdir()
        root = ls.state_root(self.ws)
        root.mkdir(parents=True, exist_ok=True)
        i = ident()
        (root / ls.context_id(WSID, i)).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        self.assertEqual(list(outside.iterdir()), [], "不得借 context 目录 symlink 写出工作区")

    @unittest.skipIf(os.name == "nt", "POSIX 权限模型下才有的 symlink 语义")
    def test_20_symlinked_runtime_file_not_followed(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        outside_file = self.tmp / "outside.json"
        outside_file.write_text(json.dumps({"schema": "v1"}), encoding="utf-8")
        (d / "runtime.json").unlink()
        (d / "runtime.json").symlink_to(outside_file)
        res = ls.read_status(self.ws, WSID, i, ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("CORRUPT", "unsafe-path"))

    @unittest.skipIf(os.name == "nt", "POSIX 权限模型下才有的 symlink 语义")
    def test_27_bind_through_alias_writes_inside_real_workspace(self):
        real = self.ws
        alias = self.tmp / "alias-bind"
        alias.symlink_to(real, target_is_directory=True)
        res = ls.bind(alias, WSID, ident(), "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        self.assertEqual(res["status"], "BOUND")
        written = (real / ".workspace" / ".local" / ls.STATE_VERSION
                   / ls.context_id(WSID, ident()) / "runtime.json")
        self.assertTrue(written.is_file(), "经别名 bind 也必须落在工作区实体内")

    @unittest.skipIf(os.name == "nt", "POSIX 权限模型下才有的 symlink 语义")
    def test_26_workspace_alias_binds_inside_real_workspace(self):
        real = self.ws
        ls.bind(real, WSID, ident(), "local-venv", ".venv-312", "3.12.7", ephemeral=False)
        alias = self.tmp / "alias"
        alias.symlink_to(real, target_is_directory=True)
        res = ls.read_status(alias, WSID, ident(), ephemeral=False)
        self.assertEqual(res["status"], "BOUND")
        written = (real / ".workspace" / ".local" / ls.STATE_VERSION
                   / ls.context_id(WSID, ident()) / "runtime.json")
        self.assertTrue(written.is_file(), "状态必须落在工作区实体内")
        self.assertFalse((self.tmp / ".workspace").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
