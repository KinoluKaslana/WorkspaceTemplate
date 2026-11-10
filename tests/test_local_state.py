#!/usr/bin/env python3
"""树内按 context 分片的本机状态（`scripts/local_state.py`）契约测试。

19 例覆盖：未绑定 / 别人的 context / 绑定与判读 / 根漂移 / 字段篡改 / 损坏 JSON /
身份不可得 / 绝对路径拒绝 / 不落明文路径 / 并发读不撕裂 / 冲突副本不采信 /
ephemeral 开关 / seed 覆盖 / 首次绑定原子发布 / symlink 逃逸拒绝 / principal 按 realm /
空 seed 非法 / venv_relpath 类型 / context 目录无多余文件。

运行：python3 -B tests/test_local_state.py
"""
import json
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


def ident(material=b"machine-A", principal="1000", realm="linux") -> "ls.Identity":
    return ls.Identity(machine_material=material, principal=principal, realm=realm)


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
        ls.bind(self.ws, WSID, a, "venv", ".venv-312", "3.12.7", ephemeral=False)
        ls.bind(self.ws, WSID, b, "venv", ".venv-312", "3.12.7", ephemeral=False)
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

    def test_03_bind_then_bound(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "uv", ".venv-314", "3.14.7", ephemeral=False)
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "BOUND")

    def test_04_root_mismatch_after_move(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        moved = self.tmp / "ws-moved"
        shutil.move(str(self.ws), str(moved))
        res = ls.read_status(moved, WSID, i, ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("UNBOUND", "root-mismatch"))

    # ---- 损坏与篡改 -----------------------------------------------------
    def test_05_workspace_mismatch(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        f = ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        data["workspace_id"] = "deadbeef-dead-dead-dead-deaddeadbeef"
        f.write_text(json.dumps(data), encoding="utf-8")
        res = ls.read_status(self.ws, WSID, i, ephemeral=False)
        self.assertEqual(res["status"], "CORRUPT")
        self.assertIn("workspace", res["reason"])

    def test_06_corrupt_json(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        (ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json").write_text(
            "{not json", encoding="utf-8")
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "CORRUPT")

    def test_07_identity_unavailable(self):
        res = ls.read_status(self.ws, WSID, None, ephemeral=False)
        self.assertEqual(res["status"], "IDENTITY_UNAVAILABLE")
        self.assertFalse((self.ws / ".workspace" / ".local").exists())

    # ---- 输入校验 -------------------------------------------------------
    def test_08_absolute_venv_rejected(self):
        for bad in ("/abs/.venv-312", "C:\\abs\\.venv-312"):
            with self.assertRaises(ValueError, msg=bad):
                ls.bind(self.ws, WSID, ident(), "venv", bad, "3.12.7", ephemeral=False)

    def test_18_venv_relpath_type_rejected_before_mkdir(self):
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "venv", Path(".venv-312"), "3.12.7", ephemeral=False)
        self.assertFalse((self.ws / ".workspace" / ".local").exists(), "类型错误不得留下目录")

    def test_09_no_plaintext_paths(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        text = (ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json").read_text(
            encoding="utf-8")
        for bad in (str(self.ws), str(self.ws.resolve()), str(Path.home())):
            self.assertNotIn(bad, text)

    # ---- 并发与写入完整性 -----------------------------------------------
    def test_10_concurrent_read_never_sees_partial_json(self):
        i = ident()
        stop = threading.Event()
        errors = []

        def reader():
            f = ls.context_dir(self.ws, WSID, i, ephemeral=False) / "runtime.json"
            while not stop.is_set():
                if f.exists():
                    try:
                        json.loads(f.read_text(encoding="utf-8"))
                    except Exception as exc:      # noqa: BLE001
                        errors.append(f"{type(exc).__name__}: {exc}")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            for n in range(20):
                ls.bind(self.ws, WSID, i, "venv", f".venv-3{n % 3}12", "3.12.7", ephemeral=False)
                time.sleep(0.002)
        finally:
            stop.set()
            t.join(timeout=2)
        self.assertFalse(errors, f"并发读到半截 JSON：{errors[:3]}")

    def test_14_first_bind_never_exposes_empty_context(self):
        for round_no in range(5):
            ws = self.tmp / f"ws{round_no}"
            (ws / ".workspace").mkdir(parents=True)
            i = ident(material=f"machine-{round_no}".encode())
            cid = ls.context_id(WSID, i)
            stop = threading.Event()
            bad = []

            def reader():
                d = ls.state_root(ws) / cid
                while not stop.is_set():
                    if d.is_dir() and not (d / "runtime.json").is_file():
                        bad.append("empty-context-visible")

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            try:
                time.sleep(0.001)
                ls.bind(ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
                time.sleep(0.001)
            finally:
                stop.set()
                t.join(timeout=2)
            self.assertFalse(bad, f"首次绑定暴露了空 context 目录：{bad[:3]}")

    def test_11_conflict_copies_ignored(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        bad = {"schema": "v1", "workspace_id": "other", "context_id": "zzz"}
        (d / "runtime (conflicted copy).json").write_text(json.dumps(bad), encoding="utf-8")
        (d / "runtime.sync-conflict-20260101.json").write_text(json.dumps(bad), encoding="utf-8")
        self.assertEqual(ls.read_status(self.ws, WSID, i, ephemeral=False)["status"], "BOUND")

    def test_19_context_dir_has_no_extra_files(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["runtime.json"],
                         "锁与临时文件不得留在 context 目录")

    # ---- 身份 -----------------------------------------------------------
    def test_12_ephemeral_flag(self):
        self.assertIs(ls.ephemeral({"WORKSPACE_CONTEXT_EPHEMERAL": "1"}), True)
        self.assertIs(ls.ephemeral({}), False)
        self.assertIs(ls.ephemeral({"WORKSPACE_CONTEXT_EPHEMERAL": "0"}), False)
        i = ident()
        a = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        b = ls.context_dir(self.ws, WSID, i, ephemeral=True)
        self.assertEqual(b.name, a.name + "-eph")

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

    def test_17_empty_seed_is_invalid(self):
        info = {"machine_id": b"m", "uid": "1000", "realm": "linux"}
        self.assertIsNone(ls.resolve_identity({"WORKSPACE_CONTEXT_SEED": ""}, info))

    # ---- 路径安全 -------------------------------------------------------
    def test_15_symlink_escape_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.ws / ".workspace" / ".local").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            ls.bind(self.ws, WSID, ident(), "venv", ".venv-312", "3.12.7", ephemeral=False)
        self.assertEqual(list(outside.iterdir()), [], "不得通过 symlink 写工作区外")

    @unittest.skipIf(os.name == "nt", "POSIX 权限模型下才有的 symlink 语义")
    def test_20_symlinked_runtime_file_not_followed(self):
        i = ident()
        ls.bind(self.ws, WSID, i, "venv", ".venv-312", "3.12.7", ephemeral=False)
        d = ls.context_dir(self.ws, WSID, i, ephemeral=False)
        outside_file = self.tmp / "outside.json"
        outside_file.write_text(json.dumps({"schema": "v1"}), encoding="utf-8")
        (d / "runtime.json").unlink()
        (d / "runtime.json").symlink_to(outside_file)
        res = ls.read_status(self.ws, WSID, i, ephemeral=False)
        self.assertEqual((res["status"], res["reason"]), ("CORRUPT", "unsafe-path"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
