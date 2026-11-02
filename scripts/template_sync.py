#!/usr/bin/env python3
"""模板分支同步：把工作区的模板层文件更新到模板仓库**指定分支**的最新提交。

与 `template-update` skill 的分工：
- 本脚本负责**机械部分**（取分支 → 比对 → 备份 → 替换 → 回写 bootstrap → 报告）；
- 语义部分（custom 引用迁移、MAJOR 逐项确认、README 处置）仍按 skill 正文执行。

默认 **dry-run**，只报告将要发生什么；`--apply` 才写盘。

用法：
  python3 scripts/template_sync.py --workspace ~/Workspaces/TemplateDev --branch alpha
  python3 scripts/template_sync.py --workspace ~/Workspaces/TemplateDev --branch alpha --apply
  python3 scripts/template_sync.py --workspace . --branch dev --apply --verify --json

退出码：0 成功 / 1 前置检查失败 / 2 写盘后校验失败
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def template_files(worktree: Path) -> list[str]:
    """从模板分支的 verify_rules.py 读取 TEMPLATE_FILES（唯一事实源）。"""
    sys.path.insert(0, str(worktree / "scripts"))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("tpl_verify_rules", worktree / "scripts" / "verify_rules.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # type: ignore[union-attr]
        return list(mod.TEMPLATE_FILES)
    finally:
        sys.path.pop(0)


def policy_version(path: Path) -> str:
    try:
        m = re.search(r"模板政策版本：`([0-9]+\.[0-9]+\.[0-9]+)`", path.read_text(encoding="utf-8"))
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def clause_ids(registry: Path) -> set[str]:
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, dict):
        for key in ("clauses", "ids", "clause_ids"):
            if isinstance(data.get(key), list):
                return {c if isinstance(c, str) else c.get("id") for c in data[key] if c}
    return set()


def dangling_custom(ws: Path, new_ids: set[str]) -> list[dict]:
    out = []
    for p in sorted(ws.glob("*.custom.md")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"(overrides|extends)\s*:\s*(R\d+)", line):
                if m.group(2) not in new_ids:
                    out.append({"file": p.name, "line": i, "ref": m.group(2), "kind": m.group(1)})
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--template", default=os.environ.get("TEMPLATE_DIR", "~/Github/WorkspaceTemplate"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--keep-readme", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    tpl = Path(a.template).expanduser().resolve()
    if not (ws / ".workspace" / "bootstrap.json").is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    if not (tpl / ".git").exists():
        print(json.dumps({"error": "template-clone-missing", "template": str(tpl)}, ensure_ascii=False))
        return 1

    if a.fetch:
        git(tpl, "fetch", "--all", "--prune")

    ref = f"origin/{a.branch}"
    if subprocess.run(["git", "-C", str(tpl), "rev-parse", "--verify", ref],
                      capture_output=True).returncode != 0:
        ref = a.branch
        if subprocess.run(["git", "-C", str(tpl), "rev-parse", "--verify", ref],
                          capture_output=True).returncode != 0:
            print(json.dumps({"error": "branch-not-found", "branch": a.branch}, ensure_ascii=False))
            return 1
    commit = git(tpl, "rev-parse", ref)

    tmp = Path(tempfile.mkdtemp(prefix="tplsync-"))
    wt = tmp / "wt"
    git(tpl, "worktree", "add", "--detach", str(wt), commit)
    try:
        files = template_files(wt)
        before = policy_version(ws / "AGENT_RULES.md")
        after = policy_version(wt / "AGENT_RULES.md")
        old_ids = clause_ids(ws / ".workspace" / "rule-clauses.json")
        new_ids = clause_ids(wt / ".workspace" / "rule-clauses.json")

        changed, missing = [], []
        for rel in files:
            src, dst = wt / rel, ws / rel
            if not src.is_file():
                missing.append(rel)
                continue
            if a.keep_readme and rel.startswith("README"):
                continue
            if not dst.is_file() or src.read_bytes() != dst.read_bytes():
                changed.append(rel)

        report = {"branch": a.branch, "ref": ref, "commit": commit,
                  "version_before": before, "version_after": after,
                  "template_files": len(files),
                  "changed": changed, "identical": len(files) - len(changed) - len(missing),
                  "missing_in_template": missing,
                  "clauses_added": sorted(new_ids - old_ids),
                  "clauses_removed": sorted(old_ids - new_ids),
                  "custom_dangling": dangling_custom(ws, new_ids) if new_ids else [],
                  "apply": bool(a.apply)}

        if not a.apply:
            if a.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                print(f"[dry-run] {a.branch}@{commit[:8]}  {before} → {after}")
                print(f"将替换 {len(changed)} 个文件（共 {len(files)}）")
                for c in changed:
                    print("  ~", c)
                if report["clauses_removed"]:
                    print("条款删除:", ", ".join(report["clauses_removed"]))
                if report["custom_dangling"]:
                    print("custom 悬空引用:", json.dumps(report["custom_dangling"], ensure_ascii=False))
            return 0

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ws / ".workspace" / f"backup-{stamp}-sync"
        backup.mkdir(parents=True, exist_ok=True)
        for rel in files:
            src_dst = ws / rel
            if src_dst.is_file():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_dst, backup / rel)
        for extra in ("AGENT_RULES.custom.md", "HERMES.custom.md", ".workspace/bootstrap.json"):
            p = ws / extra
            if p.is_file():
                (backup / extra).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, backup / extra)

        copied = []
        for rel in files:
            src = wt / rel
            if not src.is_file():
                continue
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)

        boot_path = ws / ".workspace" / "bootstrap.json"
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        tpl_sec = boot.setdefault("template", {})
        tpl_sec["current_policy_version"] = after
        tpl_sec["last_synced_at"] = dt.date.today().isoformat()
        tpl_sec["last_synced_branch"] = a.branch
        tpl_sec["last_synced_commit"] = commit
        boot_path.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report.update({"backup": str(backup), "copied": copied,
                       "bootstrap": {"current_policy_version": after,
                                     "last_synced_branch": a.branch,
                                     "last_synced_commit": commit}})

        verify = None
        if a.verify:
            p = subprocess.run([sys.executable, str(ws / "scripts" / "verify_rules.py"),
                                "--workspace", str(ws), "--vs-template", str(wt), "--json"],
                               cwd=ws, capture_output=True, text=True)
            try:
                verify = json.loads(p.stdout or "{}")
            except Exception:
                verify = {"raw": (p.stdout or p.stderr)[:400]}
            report["verify"] = {"clean": verify.get("clean"), "findings": verify.get("findings", [])[:10]}

        if a.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"{a.branch}@{commit[:8]}  {before} → {after}；替换 {len(copied)} 个文件")
            print("备份:", backup)
            if a.verify:
                print("verify clean:", (verify or {}).get("clean"))
        return 0 if (not a.verify or (verify or {}).get("clean")) else 2
    finally:
        subprocess.run(["git", "-C", str(tpl), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
