#!/usr/bin/env python3
"""版本三元组一致性：政策版本 ↔ CHANGELOG 顶层版本 ↔ bootstrap 记录版本。

对应条款 R2（规范性变更必须更新版本并记录到 CHANGELOG）。只判「版本是否自洽」，
不判「该升 PATCH / MINOR / MAJOR」——后者仍需人。
用法：python3 scripts/version_check.py [--json]
退出码：0 一致；1 不一致；2 用法/IO。
"""
import argparse
import json
import re
import sys
from pathlib import Path


def _utf8_stdio() -> None:
    """stdout/stderr 输出编码钉成 UTF-8（管道与重定向场景）。

    为什么必须改**编码**而不只是 `errors="replace"`：父进程（测试、CI、release.py）
    统一按 UTF-8 解码子进程输出；子进程若按 Windows locale（cp1252/cp936）写管道，
    中文要么被就地替换成 `?`，要么被父进程解成乱码，断言里的中文直接对不上。
    console 分支保持本机编码（只防崩）：真控制台上 reconfigure 成 UTF-8 会让
    Windows 控制台按本地代码页误渲染，那里不需要为了管道语义牺牲显示。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if encoding == "utf8":
                continue
            if getattr(stream, "isatty", lambda: False)():
                stream.reconfigure(errors="replace")                       # type: ignore[union-attr]
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")     # type: ignore[union-attr]
        except Exception:                                                   # noqa: BLE001 - 输出层不许抛
            pass


_utf8_stdio()


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "AGENT_RULES.md"
CHANGELOG = ROOT / "CHANGELOG.md"
BOOT = ROOT / ".workspace" / "bootstrap.json"
SEMVER = re.compile(r"(\d+\.\d+\.\d+)")
BANNED = re.compile(r"模板政策版本：`([^`]+)`")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-changelog", action="store_true",
                    help="本仓库必须带 CHANGELOG.md（模板仓用；派生工作区通常没有）")
    a = ap.parse_args(argv)

    if not POLICY.is_file():
        print("缺少 AGENT_RULES.md", file=sys.stderr)
        return 2
    policy_text = POLICY.read_text(encoding="utf-8")
    m = BANNED.search(policy_text)
    policy = m.group(1) if m else None

    findings = []
    if not policy:
        findings.append({"code": "E1", "message": "AGENT_RULES.md 没有 `模板政策版本：` 行"})

    changelog = None
    if CHANGELOG.is_file():
        for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
            mm = re.match(r"^##\s+(\d+\.\d+\.\d+)", line)
            if mm:
                changelog = mm.group(1)
                break
        if changelog is None:
            findings.append({"code": "E2", "message": "CHANGELOG.md 没有 `## X.Y.Z` 版本条目"})
    elif a.require_changelog:
        findings.append({"code": "E2", "message": "缺少 CHANGELOG.md（--require-changelog）"})

    recorded = None
    if BOOT.is_file():
        try:
            boot = json.loads(BOOT.read_text(encoding="utf-8-sig"))
            recorded = ((boot.get("template") or {}).get("current_policy_version") or "").strip() or None
        except json.JSONDecodeError:
            findings.append({"code": "E3", "message": "bootstrap.json 不是合法 JSON"})

    if policy and changelog and policy != changelog:
        findings.append({"code": "E4", "message": f"政策版本 {policy} ≠ CHANGELOG 顶层 {changelog}"})
    if policy and recorded and policy != recorded:
        findings.append({"code": "E5", "message": f"政策版本 {policy} ≠ bootstrap 记录 {recorded}"})

    if a.json:
        print(json.dumps({"policy": policy, "changelog": changelog, "bootstrap": recorded,
                          "findings": findings}, ensure_ascii=False))
    else:
        for f in findings:
            print(f"{f['code']} {f['message']}")
        note = "" if CHANGELOG.is_file() else "（本工作区无 CHANGELOG，跳过该项）"
        print(f"policy={policy} changelog={changelog} bootstrap={recorded}{note} "
              f"→ {'OK' if not findings else 'MISMATCH'}")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
