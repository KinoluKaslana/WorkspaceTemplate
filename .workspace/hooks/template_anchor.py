#!/usr/bin/env python3
"""template_anchor.py — W4 完成声明门禁 CLI（纯 stdlib）。

子进程调 scripts/verify_rules.py --workspace <ws> --vs-template <clone> --json <tmp>
（timeout 60）。通过条件（三者同时）：verify_rules exit==0 且 顶层 clean==true 且
template_anchor.template_clean==true。clone 解析顺序：--template → env
WORKSPACE_TEMPLATE_CLONE → <ws>/.workspace/bootstrap.json 的 template.local_clone（旧键 clone_path 兼容）；
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
from typing import Any

import osnorm_loader as _osnorm_loader


def _utf8_stdio() -> None:
    """stdout/stderr 输出编码钉成 UTF-8（管道与重定向场景）——单一实现是 osnorm。

    为什么必须改**编码**而不只是 `errors="replace"`：父进程（测试、CI、release.py）
    统一按 UTF-8 解码子进程输出；子进程若按 Windows locale（cp1252/cp936）写管道，
    中文要么被就地替换成 `?`，要么被父进程解成乱码，断言里的中文直接对不上。
    console 分支保持本机编码（只防崩）：真控制台上 reconfigure 成 UTF-8 会让
    Windows 控制台按本地代码页误渲染，那里不需要为了管道语义牺牲显示。
    """
    module = _osnorm_loader.bootstrap_module()
    if module is None:
        return  # 不可用时只输出 ASCII 的 ok:false 包（见 main），与 locale 无关
    try:
        module.configure_stdio()
    except Exception:                                                   # noqa: BLE001 - 输出层不许抛
        pass


_utf8_stdio()


def _osn(ws_root: str | None = None) -> Any:
    """经装载器取 osnorm（**禁止裸 import**）。"""
    return _osnorm_loader.load(ws_root)



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

    try:
        _osn(str(ws))
    except _osnorm_loader.OsnormUnavailable as exc:
        # 独立 CLI 入口：不可用就明确报错退出（调用方按 exit!=0 当「未证明 clean」）。
        _emit({"ok": False,
               "reason": f"osnorm 不可用（{exc.reason}）：{exc.detail}"}, args.json)
        return 2

    clone = args.template
    if not clone:
        clone = os.environ.get("WORKSPACE_TEMPLATE_CLONE")
    if not clone:
        b = ws / ".workspace" / "bootstrap.json"
        if b.is_file():
            try:
                # bom=True：原 `read_text(encoding="utf-8-sig")` 剥 BOM 的语义原样保留。
                data = json.loads(_osn(str(ws)).read_text(b, bom=True))
                _tpl = data.get("template") or {}
                clone = _tpl.get("local_clone") or _tpl.get("clone_path")
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
            # 恒捕获 + UTF-8 由 osnorm.run_text 固定；退出码与 stdout 判据不变。
            proc = _osn(str(ws)).run_text(
                [sys.executable, "-B", str(vr), "--workspace", str(ws),
                 "--vs-template", str(clone_path), "--json", tmp_path],
                errors="replace", timeout=60)
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
