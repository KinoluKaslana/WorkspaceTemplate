#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁目标判定：PowerShell 变量/环境变量展开 + 「已接管」工作区（真实反馈 B1/B2）。

B1：`$dest = Join-Path $env:TEMP 'x'; Remove-Item -Recurse -Force $dest` 以前判 unresolved → deny。
B2：临时克隆自带 `.workspace/bootstrap.json`（`workspace_status: template`），以前被当成受管工作区，
    删它要先读它的 AGENT_RULES.md；现在只有「锚定工作区内」或「已接管工作区」才受管。

运行：python3 -B hooks-tests/test_gate_targets.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import gate_core as core  # noqa: E402
import wtarget  # noqa: E402


class PwshTarget(unittest.TestCase):
    def test_p1_join_path_env_temp_resolves(self):
        env_tmp = tempfile.mkdtemp(prefix="ps-temp-")
        self.addCleanup(shutil.rmtree, env_tmp, True)
        old = os.environ.get("TEMP")
        os.environ["TEMP"] = env_tmp
        self.addCleanup(lambda: os.environ.__setitem__("TEMP", old) if old is not None
                        else os.environ.pop("TEMP", None))
        cmd = ("$dest = Join-Path $env:TEMP 'WorkspaceTemplate-sync'\n"
               "Remove-Item -Recurse -Force $dest")
        res = wtarget.classify_pwsh(cmd, str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["kind"], "write", res)
        self.assertEqual(res["targets"], [os.path.normpath(os.path.join(env_tmp, "WorkspaceTemplate-sync"))])

    def test_p2_literal_assignment_resolves(self):
        cmd = "$d = 'C:\\tmp\\ws-sync'\nRemove-Item -Recurse -Force $d"
        res = wtarget.classify_pwsh(cmd, str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["kind"], "write", res)
        self.assertEqual(res["targets"], [os.path.normpath("C:\\tmp\\ws-sync")])

    def test_p3_unknown_variable_still_unresolved(self):
        """不可展开的变量仍 fail-closed，不能因为这次修复放宽成放行。"""
        res = wtarget.classify_pwsh("Remove-Item -Recurse -Force $unknownDest", str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["kind"], "unresolved", res)


class TakenOverWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gate-targets-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        # 锚定工作区（已接管）
        self.ws = base / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        (self.ws / core.BOOTSTRAP_REL).write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "local-venv"}}), encoding="utf-8")

        # 模板克隆（未接管）
        self.clone = base / "temp" / "WorkspaceTemplate-sync"
        (self.clone / ".workspace").mkdir(parents=True)
        (self.clone / core.BOOTSTRAP_REL).write_text(
            json.dumps({"workspace_status": "template", "python": {"mode": "unconfigured"}}), encoding="utf-8")

        # 另一个已接管工作区
        self.other = base / "other-ws"
        (self.other / ".workspace").mkdir(parents=True)
        (self.other / core.BOOTSTRAP_REL).write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "uv"}}), encoding="utf-8")

    def sid(self):
        return "t-" + self._testMethodName


    def test_p4_env_mutation_makes_command_unresolved(self):
        """命令内改写 $env:TEMP 时，静态展开会与实际目标分叉 → 必须仍 unresolved。"""
        cmd = "$env:TEMP = '<ws>\\victim'\nRemove-Item -Recurse -Force $env:TEMP"
        res = wtarget.classify_pwsh(cmd, str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["kind"], "unresolved", res)

    def test_p5_move_source_is_a_target_too(self):
        """Move-Item 的源目录被移走同样具破坏性。"""
        res = wtarget.classify_pwsh("Move-Item -Path 'C:\\a\\src' -Destination 'C:\\b\\dst'",
                                    str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["kind"], "write", res)
        self.assertIn(os.path.normpath("C:\\a\\src"), res["targets"])
        self.assertIn(os.path.normpath("C:\\b\\dst"), res["targets"])

    def test_p6_exclude_patterns_are_not_targets(self):
        res = wtarget.classify_pwsh("Remove-Item -Recurse -Force 'C:\\a\\dir' -Exclude *.log",
                                    str(ROOT), ["AGENT_RULES.md"])
        self.assertEqual(res["targets"], [os.path.normpath("C:\\a\\dir")], res)

    def test_t1_template_clone_is_not_managed(self):
        res = core.evaluate_pre_write(str(self.ws), "dsh", self.sid(), [str(self.clone / "x.txt")])
        self.assertEqual(res["action"], "allow", res)

    def test_t2_other_taken_over_workspace_still_denied(self):
        res = core.evaluate_pre_write(str(self.ws), "dsh", self.sid(), [str(self.other / "x.txt")])
        self.assertEqual(res["action"], "deny", res)
        self.assertIn("AGENT_RULES.md", res["reason"])

    def test_t3_inside_anchor_still_denied(self):
        res = core.evaluate_pre_write(str(self.ws), "dsh", self.sid(), [str(self.ws / "AGENT_RULES.md")])
        self.assertEqual(res["action"], "deny", res)

    def test_t4_nested_template_clone_inside_anchor_still_managed(self):
        nested = self.ws / "vendor" / "clone"
        (nested / ".workspace").mkdir(parents=True)
        (nested / core.BOOTSTRAP_REL).write_text(
            json.dumps({"workspace_status": "template"}), encoding="utf-8")
        res = core.evaluate_pre_write(str(self.ws), "dsh", self.sid(), [str(nested / "x.txt")])
        self.assertEqual(res["action"], "deny", res)

    def test_t5_anchor_resolved_from_subdir_cwd(self):
        """cwd 是工作区子目录时，锚定工作区仍是根（不是子目录）。"""
        sub = self.ws / "notes" / "deep"
        sub.mkdir(parents=True)
        res = core.evaluate_pre_write(str(sub), "dsh", self.sid(), [str(self.ws / "AGENT_RULES.md")])
        self.assertEqual(res["action"], "deny", res)

    def test_t6_symlink_outside_pointing_inside_is_managed(self):
        """区外软链解析后指回区内 → 必须按区内守门（反向逃逸）。"""
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = outside / "link-to-gate"
        try:
            os.symlink(self.ws / "AGENT_RULES.md", link)
        except (OSError, NotImplementedError):
            self.skipTest("平台不支持 symlink")
        res = core.evaluate_pre_write(str(self.ws), "dsh", self.sid(), [str(link)])
        self.assertEqual(res["action"], "deny", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
