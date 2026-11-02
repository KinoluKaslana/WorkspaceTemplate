#!/usr/bin/env python3
"""模板分支同步：把工作区的模板层文件更新到模板仓库**指定分支**的最新提交。

与 `template-update` skill 的分工：
- 本脚本负责**机械部分**（取分支 → 比对 → 备份 → 替换 → 回写 bootstrap → 报告）；
- 语义部分（custom 引用迁移、MAJOR 逐项确认、README 处置）仍按 skill 正文执行。

默认 **dry-run**，只报告将要发生什么；`--apply` 才写盘。

用法：
  python3 scripts/template_sync.py --workspace . --ref alpha            # 分支
  python3 scripts/template_sync.py --workspace . --ref v3.1.0           # 标签
  python3 scripts/template_sync.py --workspace . --ref 13eeb83          # 提交
  python3 scripts/template_sync.py --workspace . --ref dev --apply --verify --json
  python3 scripts/template_sync.py --workspace . --list-refs            # 可选来源一览

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
    ap.add_argument("--ref", "--branch", dest="ref", default=None,
                    help="分支 / 标签 / 提交；缺省用工作区 bootstrap 记录的分支，再缺省 main")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--template", default=os.environ.get("TEMPLATE_DIR"))
    ap.add_argument("--clone-url", default=None, help="模板克隆不存在时按此 URL 浅克隆")
    ap.add_argument("--list-refs", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--keep-readme", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_path = ws / ".workspace" / "bootstrap.json"
    if not boot_path.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    boot = json.loads(boot_path.read_text(encoding="utf-8"))
    tpl_sec = boot.get("template") or {}

    # 模板克隆：显式参数 > TEMPLATE_DIR > bootstrap 记录的本地克隆 > 默认路径
    cand = a.template or tpl_sec.get("local_clone") or "~/Github/WorkspaceTemplate"
    tpl = Path(cand).expanduser().resolve()
    url = a.clone_url or tpl_sec.get("source_repo")
    if not (tpl / ".git").exists():
        if not url:
            print(json.dumps({"error": "template-clone-missing", "template": str(tpl),
                              "hint": "给 --template 或 --clone-url"}, ensure_ascii=False))
            return 1
        tpl.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["git", "clone", "--quiet", url, str(tpl)], capture_output=True, text=True)
        if p.returncode != 0:
            print(json.dumps({"error": "clone-failed", "url": url, "stderr": p.stderr.strip()[:300]},
                             ensure_ascii=False))
            return 1

    if a.fetch:
        git(tpl, "fetch", "--all", "--prune", "--tags")

    if a.list_refs:
        branches = git(tpl, "for-each-ref", "--format=%(refname:short)", "refs/remotes", "refs/heads", "refs/tags")
        refs = set()
        for b in branches.splitlines():
            b = b.strip()
            if not b or b.endswith("/HEAD") or b == a.remote:
                continue
            refs.add(b.split("/", 1)[1] if b.startswith(f"{a.remote}/") else b)
        refs = sorted(refs)
        print(json.dumps({"refs": refs}, ensure_ascii=False))
        return 0

    wanted = a.ref or tpl_sec.get("last_synced_branch") or "main"
    candidates = [f"{a.remote}/{wanted}", wanted]
    resolved = None
    for c in candidates:
        if subprocess.run(["git", "-C", str(tpl), "rev-parse", "--verify", "{}^0".format(c)],
                          capture_output=True).returncode == 0:
            resolved = c
            break
    if resolved is None:
        print(json.dumps({"error": "ref-not-found", "ref": wanted,
                          "hint": "用 --list-refs 看可选来源"}, ensure_ascii=False))
        return 1
    kind = git(tpl, "cat-file", "-t", resolved)
    commit = git(tpl, "rev-parse", f"{resolved}^0")
    ref = resolved

    tmp = Path(tempfile.mkdtemp(prefix="tplsync-"))
    wt = tmp / "wt"
    git(tpl, "worktree", "add", "--detach", str(wt), commit)
    try:
        files = template_files(wt)
        before = policy_version(ws / "AGENT_RULES.md")
        after = policy_version(wt / "AGENT_RULES.md")
        old_ids = clause_ids(ws / ".workspace" / "rule-clauses.json")
        new_ids = clause_ids(wt / ".workspace" / "rule-clauses.json")

        # README 处置：显式 --keep-readme 恒保留；否则「工作区 README 与上次同步点不同」视为用户首页，保留
        prev_commit = (boot.get("template") or {}).get("last_synced_commit")
        readme_is_local = False
        if prev_commit and subprocess.run(["git", "-C", str(tpl), "cat-file", "-e", f"{prev_commit}^{{commit}}"],
                                          capture_output=True).returncode == 0:
            for name in ("README.md", "README_EN.md"):
                cur, old = ws / name, git(tpl, "show", f"{prev_commit}:{name}", check=False)
                if cur.is_file() and old and cur.read_text(encoding="utf-8", errors="replace") != old:
                    readme_is_local = True

        changed, missing, skipped = [], [], []
        for rel in files:
            src, dst = wt / rel, ws / rel
            if not src.is_file():
                missing.append(rel)
                continue
            if rel.startswith("README") and (a.keep_readme or readme_is_local):
                skipped.append(rel)
                continue
            if not dst.is_file() or src.read_bytes() != dst.read_bytes():
                changed.append(rel)

        report = {"requested_ref": wanted, "resolved_ref": ref, "ref_kind": kind, "commit": commit,
                  "readme_kept": readme_is_local or a.keep_readme,
                  "version_before": before, "version_after": after,
                  "template_files": len(files),
                  "changed": changed, "skipped": skipped,
                  "identical": len(files) - len(changed) - len(missing) - len(skipped),
                  "missing_in_template": missing,
                  "clauses_added": sorted(new_ids - old_ids),
                  "clauses_removed": sorted(old_ids - new_ids),
                  "custom_dangling": dangling_custom(ws, new_ids) if new_ids else [],
                  "apply": bool(a.apply)}

        if not a.apply:
            if a.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                print(f"[dry-run] {ref}({kind})@{commit[:8]}  {before} → {after}")
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
            if not src.is_file() or rel in skipped:
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
        tpl_sec["last_synced_ref"] = wanted
        if kind == "commit":                      # 分支或标签解析出来的提交
            tpl_sec["last_synced_branch"] = wanted
        tpl_sec["last_synced_commit"] = commit
        boot_path.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report.update({"backup": str(backup), "copied": copied,
                       "bootstrap": {"current_policy_version": after,
                                     "last_synced_ref": wanted,
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
            print(f"{ref}({kind})@{commit[:8]}  {before} → {after}；替换 {len(copied)} 个文件")
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
