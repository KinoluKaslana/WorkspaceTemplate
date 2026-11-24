#!/usr/bin/env python3
"""方案 C 外部锚回归：账本进 `TEMPLATE_FILES` 后的两条哈希证明（真跑脚本，不 mock）。

背景：`tests/osnorm_allowlist.json` 的 `count` 与 `baseline` 同处一个可编辑文件，
`audit()` 的 exact + baseline 只能在**文件内部**自洽——「真加一处违规、`count`+1、
`baseline`+1」能一起改到自洽，账本本身没有独立锚点。方案 C 把账本登记进
`TEMPLATE_FILES`，由 `scripts/verify_rules.py` 给出与该文件独立的外部锚：

- T-c1：在已清零的账本恢复一处 R4 豁免且**不重建注册表** → `verify_rules --workspace .`
  报 `[file] HASH-DIFF tests/osnorm_allowlist.json`。
- T-c2：伪造模板副本 + 实例侧账本放宽 → `verify_rules --workspace <实例> --vs-template <副本>`
  对该文件报 `HASH-DIFF`（派生实例自行放宽在跨实例对账里暴露）。

两条都跑真实子进程，不 mock、不调 `verify_rules` 内部函数。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import osnorm  # noqa: E402

VERIFY_RULES = SCRIPTS / "verify_rules.py"
ALLOWLIST_REL = "tests/osnorm_allowlist.json"
IGNORE = shutil.ignore_patterns(".venv", ".git", "__pycache__", "*.pyc", ".pytest_cache")


def run_verify(workspace: Path, *extra: str) -> tuple[int, dict]:
    """跑 verify_rules 并解析其 JSON 报告，返回 (exit_code, report)。"""
    proc = osnorm.run_text([sys.executable, "-B", str(VERIFY_RULES),
                            "--workspace", str(workspace), *extra],
                           cwd=str(ROOT), timeout=300)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - 诊断用
        raise AssertionError(f"verify_rules 输出不是 JSON：{exc}\n"
                             f"stdout={proc.stdout[:800]}\nstderr={proc.stderr[:800]}") from exc
    return proc.returncode, report


def restore_r4_exemption(text: str) -> str:
    """模拟为一处复发的 reconfigure 同时放宽 entries 和 baseline。"""
    doc = json.loads(text)
    file = ".workspace/hooks/hook.py"
    doc["baseline"].setdefault(file, {})["R4"] = 1
    doc["entries"].append({
        "file": file, "rule": "R4", "kind": "call", "target": ".reconfigure",
        "line_kind": "call", "count": 1, "reason": "外部锚回归用条目", "expires": "R47",
    })
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def git(path: Path, *args: str) -> None:
    proc = osnorm.run_text(["git", "-C", str(path), *args], cwd=str(path), timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{proc.stderr[:400]}")


class TestAllowlistAnchorT1(unittest.TestCase):
    """T-c1：同仓抬高账本必须留 HASH-DIFF（注册表未重建 → 文件级外部锚报警）。"""

    def test_t_c1_bump_without_registry_rebuild_is_red(self) -> None:
        with tempfile.TemporaryDirectory(prefix="osnorm-anchor-") as tmp:
            workspace = Path(tmp) / "workspace"
            shutil.copytree(ROOT, workspace, ignore=IGNORE)
            target = workspace / ALLOWLIST_REL
            original = osnorm.read_bytes(target)
            rc0, report0 = run_verify(workspace)
            self.assertEqual(0, rc0, msg=f"起点不 clean，无法归因：{report0.get('findings')}")

            relaxed = restore_r4_exemption(original.decode("utf-8"))
            osnorm.write_bytes(target, relaxed.encode("utf-8"))
            rc, report = run_verify(workspace)
            findings = report["findings"]
            self.assertEqual(1, rc, msg=f"抬高账本后应红：{findings}")
            self.assertFalse(report["clean"])
            self.assertTrue(any(f.startswith(f"[file] HASH-DIFF {ALLOWLIST_REL}") for f in findings),
                            msg=f"缺少该文件的 HASH-DIFF：{findings}")

            osnorm.write_bytes(target, original)
            rc_after, report_after = run_verify(workspace)
            self.assertEqual(0, rc_after, msg=f"还原后应回绿：{report_after.get('findings')}")


class TestAllowlistAnchorT2(unittest.TestCase):
    """T-c2：派生实例自行放宽账本，必须被 --vs-template 的跨实例对账暴露。"""

    def test_t_c2_instance_relaxation_exposed_by_vs_template(self) -> None:
        with tempfile.TemporaryDirectory(prefix="osnorm-anchor-") as tmp:
            tpl = Path(tmp) / "template"
            inst = Path(tmp) / "instance"
            shutil.copytree(ROOT, tpl, ignore=IGNORE)
            shutil.copytree(ROOT, inst, ignore=IGNORE)
            git(tpl, "init")
            git(tpl, "add", "-A")
            git(tpl, "-c", "user.name=anchor", "-c", "user.email=anchor@example.invalid",
                "commit", "-m", "template-copy")

            # 起点：实例与模板副本逐字节一致 → 必须 clean，否则后续红无法归因到账本
            rc0, report0 = run_verify(inst, "--vs-template", str(tpl))
            self.assertEqual(0, rc0, msg=f"未改动的实例副本应 clean：{report0.get('findings')}")

            target = inst / ALLOWLIST_REL
            relaxed = restore_r4_exemption(osnorm.read_text(target))
            osnorm.write_bytes(target, relaxed.encode("utf-8"))

            rc, report = run_verify(inst, "--vs-template", str(tpl))
            findings = report["findings"]
            self.assertEqual(1, rc, msg=f"实例放宽账本后应红：{findings}")
            self.assertTrue(any(f.startswith(f"[file] HASH-DIFF {ALLOWLIST_REL}") for f in findings),
                            msg=f"缺少该文件的 HASH-DIFF：{findings}")
            self.assertTrue(any(f.startswith(f"[vs-template] TAMPERED {ALLOWLIST_REL}") for f in findings),
                            msg=f"缺少与模板副本的字节差异：{findings}")
            # 模板副本自身保持 clean：红来自实例侧，不是参照侧被改
            rc_tpl, report_tpl = run_verify(tpl, "--vs-template", str(tpl))
            self.assertEqual(0, rc_tpl, msg=f"模板副本应保持 clean：{report_tpl.get('findings')}")


if __name__ == "__main__":
    unittest.main()
