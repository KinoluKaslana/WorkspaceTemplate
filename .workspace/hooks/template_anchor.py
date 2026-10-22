#!/usr/bin/env python3
"""template_anchor.py — W4 完成声明门禁 CLI（纯 stdlib）。

子进程调 scripts/verify_rules.py --workspace <ws> --vs-template <clone> --json <tmp>
（timeout 60）。通过条件（三者同时）：verify_rules exit==0 且 顶层 clean==true 且
template_anchor.template_clean==true。clone 解析顺序：--template → env
WORKSPACE_TEMPLATE_CLONE → <ws>/.workspace/bootstrap.json 的 template.clone_path；
都取不到 → 输出「无法证明 clean（未配置模板克隆）」并 exit 1。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _emit(result: dict, pretty: bool) -> None:
    if pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ws = Path(args.workspace).resolve()

    clone = args.template
    if not clone:
        clone = os.environ.get("WORKSPACE_TEMPLATE_CLONE")
    if not clone:
        b = ws / ".workspace" / "bootstrap.json"
        if b.is_file():
            try:
                data = json.loads(b.read_text(encoding="utf-8"))
                clone = (data.get("template") or {}).get("clone_path")
            except Exception:
                clone = None
    if not clone or not str(clone).strip():
        _emit({"ok": False, "reason": "无法证明 clean（未配置模板克隆）"}, args.json)
        return 1
    clone_path = Path(str(clone)).expanduser().resolve()
    if not clone_path.is_dir():
        _emit({"ok": False, "reason": f"模板克隆目录不存在：{clone_path}"}, args.json)
        return 1

    vr = ws / "scripts" / "verify_rules.py"
    if not vr.is_file():
        _emit({"ok": False, "reason": f"verify_rules.py 不存在：{vr}"}, args.json)
        return 1

    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="vr-anchor-")
    os.close(fd)
    try:
        try:
            proc = subprocess.run(
                [sys.executable, "-B", str(vr), "--workspace", str(ws),
                 "--vs-template", str(clone_path), "--json", tmp_path],
                capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            _emit({"ok": False, "reason": "verify_rules 超时（60s）"}, args.json)
            return 1
        report: dict = {}
        try:
            parsed = json.loads(proc.stdout) if proc.stdout.strip() else {}
            if isinstance(parsed, dict):
                report = parsed
        except Exception:
            report = {}
        tpl = report.get("template_anchor") or {}
        clean = report.get("clean") is True
        template_clean = tpl.get("template_clean") is True
        template_head = tpl.get("template_head")
        ok = (proc.returncode == 0) and clean and template_clean
        if ok:
            _emit({"ok": True, "reason": "clean", "clean": True,
                   "template_clean": True, "template_head": template_head}, args.json)
            return 0
        reasons: list[str] = []
        if proc.returncode != 0:
            reasons.append(f"verify_rules exit={proc.returncode}")
        if not clean:
            reasons.append("clean=false")
        if not template_clean:
            reasons.append("template_clean=false")
        findings = report.get("findings") or []
        summary = "；".join(str(f)[:200] for f in findings[:3])
        if summary:
            reasons.append(f"findings: {summary}")
        _emit({
            "ok": False,
            "reason": "未通过： " + "；".join(reasons),
            "clean": clean,
            "template_clean": template_clean,
            "template_head": template_head,
            "findings": findings,
        }, args.json)
        return 1
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
