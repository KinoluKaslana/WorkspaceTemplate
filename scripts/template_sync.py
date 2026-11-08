#!/usr/bin/env python3
"""模板分支同步：把工作区的模板层文件更新到模板仓库**指定分支**的最新提交。

与 `template-update` skill 的分工：
- 本脚本负责**机械部分**（取分支 → 比对 → 备份 → 替换 → 回写 bootstrap → 报告）；
- 语义部分（custom 引用迁移、MAJOR 逐项确认、README 处置）仍按 skill 正文执行。

默认 **dry-run**，只报告将要发生什么；`--apply` 才写盘。

用法：
  python3 scripts/template_sync.py --workspace . --ref alpha            # 分支
  python3 scripts/template_sync.py --workspace . --ref <tag>            # 标签
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

# Windows 的控制台/CI 管道常见 cp1252：中文 print 会抛 UnicodeEncodeError，
# 把真正的失败原因盖掉（Windows CI 上就是这么红的）。改成不可编码字符用替代符，
# 而不是让脚本在报错路径上再抛一次。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")      # type: ignore[union-attr]
    except Exception:
        pass
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


def previous_template_files(tpl: Path, prev_commit: str | None) -> list[str]:
    """读取上次同步点记录的 TEMPLATE_FILES，用于识别上游已删除的文件。"""
    if not prev_commit:
        return []
    blob = subprocess.run(["git", "-C", str(tpl), "show", f"{prev_commit}:scripts/verify_rules.py"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        return []
    m = re.search(r"TEMPLATE_FILES\s*=\s*\[(.*?)\n\]", blob.stdout, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


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


GENERATED = ("skills/REGISTRY.md", "notes/INDEX.md", "notes/_INVERTED.md",
             "docs/.index.json", "docs/.aliases.json")


def _rollback(ws: Path, backup: Path, purge_generated: bool = False) -> int:
    """按 MANIFEST 回滚：先放回 replaced，再删除 added，最后恢复 preserved。"""
    man = backup / "MANIFEST.txt"
    if not man.is_file():
        print(f"缺少 MANIFEST.txt：{man}", file=sys.stderr)
        return 2
    sec, buf = {}, []
    for line in man.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and line.endswith("]"):
            if sec:
                pass
            cur = line[1:-1]
            sec.setdefault(cur, [])
            buf = sec[cur]
        elif line.strip() and not line.startswith("#") and buf is not None:
            buf.append(line.strip())
    restored = 0
    for rel in sec.get("replaced", []):
        src = backup / rel
        if src.is_file():
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
    removed = 0
    for rel in sec.get("added", []):
        tgt = ws / rel
        if tgt.is_file():
            tgt.unlink()
            removed += 1
            parent = tgt.parent
            while parent != ws and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    for rel in sec.get("pruned", []):          # --prune 删掉的上游旧文件：放回
        src = backup / rel
        if src.is_file():
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
    for rel in sec.get("preserved", []):
        src = backup / rel
        if src.is_file():
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    purged = []
    if purge_generated:
        for rel in GENERATED:
            tgt = ws / rel
            if tgt.is_file() and not (backup / rel).is_file():
                tgt.unlink()
                purged.append(rel)
                parent = tgt.parent
                while parent != ws and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
    print(f"回滚完成：放回 {restored}，删除本次新增 {removed}，源 {backup}")
    if purged:
        print("已清除引导期生成（升级前不存在）:", ", ".join(purged))
    elif not purge_generated:
        print("提示：第 3 步生成的工作区自有文件不在 MANIFEST 内；需要一并清除时加 --purge-generated")
    return 0


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
    ap.add_argument("--prune", action="store_true",
                    help="删除「上次同步有、本次模板已移除」的模板层文件（先备份）")
    ap.add_argument("--purge-generated", action="store_true",
                    help="回滚时一并删除升级前不存在的生成文件（REGISTRY/notes 索引/docs 索引）")
    ap.add_argument("--rollback", default=None, metavar="BACKUP_DIR",
                    help="按备份目录的 MANIFEST.txt 回滚（放回 replaced、删除 added）")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--keep-on-fail", action="store_true",
                    help="verify 未通过时保留已写入的结果（默认自动回滚到同步前）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ws = Path(a.workspace).expanduser().resolve()
    boot_path = ws / ".workspace" / "bootstrap.json"
    if not boot_path.is_file():
        print(json.dumps({"error": "not-a-workspace", "workspace": str(ws)}, ensure_ascii=False))
        return 1
    if a.rollback:
        return _rollback(ws, Path(a.rollback).expanduser().resolve(), a.purge_generated)

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
        # 本地目录当 URL 时统一转 file:// URL：Git for Windows 会把 `D:\wt\ws` 这类裸盘符路径
        # 交给 MSYS 路径转换，结果 dest 变成 `/d/wt/ws` 而失败（CI Windows 上就是这么红的）。
        if os.path.isdir(os.path.expanduser(url)):
            url = Path(url).expanduser().resolve().as_uri()
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
                cur = ws / name
                if not cur.is_file():
                    continue
                blob = subprocess.run(["git", "-C", str(tpl), "show", f"{prev_commit}:{name}"],
                                      capture_output=True)
                if blob.returncode == 0 and blob.stdout and blob.stdout != cur.read_bytes():
                    readme_is_local = True

        prev_files = previous_template_files(tpl, (boot.get("template") or {}).get("last_synced_commit"))
        removed_upstream = sorted(set(prev_files) - set(files))
        changed, missing, skipped = [], [], []
        for rel in files:
            src, dst = wt / rel, ws / rel
            if not src.is_file():
                missing.append(rel)
                continue
            if rel in ("README.md", "README_EN.md") and (a.keep_readme or readme_is_local):
                # 只保用户已有的首页；README_EN 缺失时仍安装（否则 verify 报 MISSING）
                if rel == "README.md" or (ws / rel).is_file():
                    skipped.append(rel)
                    continue
            if not dst.is_file() or src.read_bytes() != dst.read_bytes():
                changed.append(rel)

        report = {"requested_ref": wanted, "resolved_ref": ref, "ref_kind": kind, "commit": commit,
                  "removed_upstream": removed_upstream, "pruned": [],
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
                if removed_upstream:
                    print("上游已移除（加 --prune 才会删除本地副本）:", ", ".join(removed_upstream))
                if report["clauses_removed"]:
                    print("条款删除:", ", ".join(report["clauses_removed"]))
                if report["custom_dangling"]:
                    print("custom 悬空引用:", json.dumps(report["custom_dangling"], ensure_ascii=False))
            return 0

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ws / ".workspace" / f"backup-{stamp}-sync"
        backup.mkdir(parents=True, exist_ok=True)
        replaced, added = [], []
        for rel in files:
            src_dst = ws / rel
            if src_dst.is_file():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_dst, backup / rel)
                replaced.append(rel)
            else:
                added.append(rel)
        for extra in ("AGENT_RULES.custom.md", "HERMES.custom.md", ".workspace/bootstrap.json"):
            p = ws / extra
            if p.is_file():
                (backup / extra).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, backup / extra)

        preserved = [x for x in ("AGENT_RULES.custom.md", "HERMES.custom.md",
                                 ".workspace/bootstrap.json") if (backup / x).is_file()]
        (backup / "MANIFEST.txt").write_text(
            "# template_sync backup manifest\n"
            f"# ref={wanted} commit={commit} {before}->{after}\n"
            "[replaced]\n" + "\n".join(replaced) + "\n"
            "[added]\n" + "\n".join(added) + "\n"
            "[preserved]\n" + "\n".join(preserved) + "\n", encoding="utf-8")

        boot_path = ws / ".workspace" / "bootstrap.json"
        boot_before = json.loads(boot_path.read_text(encoding="utf-8"))
        python_before = json.dumps(boot_before.get("python"), sort_keys=True)
        gate_before = json.dumps(boot_before.get("gate"), sort_keys=True)

        def _apply_changes() -> list[str]:
            """真正落盘的部分：任一异常都交给外层回滚，不留半套状态。"""
            done: list[str] = []
            for rel in files:
                src = wt / rel
                if not src.is_file() or rel in skipped:
                    continue
                dst = ws / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                done.append(rel)

            dropped: list[str] = []
            if a.prune and removed_upstream:
                for rel in removed_upstream:
                    tgt = ws / rel
                    if not tgt.exists():
                        continue
                    bak = backup / rel
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    if tgt.is_dir():
                        shutil.copytree(tgt, bak, dirs_exist_ok=True)
                        shutil.rmtree(tgt)
                    else:
                        shutil.copy2(tgt, bak)
                        tgt.unlink()
                        parent = tgt.parent
                        while parent != ws and parent.is_dir() and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                    dropped.append(rel)
                report["pruned"] = dropped
                if dropped:
                    with (backup / "MANIFEST.txt").open("a", encoding="utf-8") as fh:
                        fh.write("[pruned]\n" + "\n".join(dropped) + "\n")

            boot = json.loads(boot_path.read_text(encoding="utf-8"))
            tpl_sec = boot.setdefault("template", {})
            tpl_sec["current_policy_version"] = after
            tpl_sec["last_synced_at"] = dt.date.today().isoformat()
            tpl_sec["last_synced_ref"] = wanted
            if kind in ("commit", "tag"):             # 分支、标签或裸提交：记 requested ref
                tpl_sec["last_synced_branch"] = wanted
            tpl_sec["last_synced_commit"] = commit
            boot_path.write_text(json.dumps(boot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return done

        try:
            copied = _apply_changes()
        except Exception as exc:  # noqa: BLE001 —— 写盘中途失败必须回到同步前
            print(f"同步失败：{exc}", file=sys.stderr)
            rb = _rollback(ws, backup)
            report["auto_rollback"] = {"reason": f"exception: {exc}", "rollback_rc": rb}
            report["copied"] = []
            if a.json:
                print(json.dumps(report, ensure_ascii=False))
            if rb != 0:
                print(f"回滚也失败（rc={rb}）：按 {backup}/MANIFEST.txt 手工恢复", file=sys.stderr)
                return 4
            print(f"已自动回滚（backup={backup}）", file=sys.stderr)
            return 3

        python_after = json.dumps(json.loads(boot_path.read_text(encoding="utf-8")).get("python"),
                                  sort_keys=True)
        report["python_mode_preserved"] = (python_before == python_after)
        # §15：同步不得改写本机的 gate 接线结论（运行期事实不是模板事实）
        gate_after = json.dumps(json.loads(boot_path.read_text(encoding="utf-8")).get("gate"),
                                sort_keys=True)
        report["gate_preserved"] = (gate_before == gate_after)
        report.update({"backup": str(backup), "copied": copied,
                       "bootstrap": {"current_policy_version": after,
                                     "last_synced_ref": wanted,
                                     "last_synced_commit": commit}})

        verify = None
        if a.verify:
            out_json = ws / ".workspace" / ".verify-tmp.json"
            try:
                p = subprocess.run([sys.executable, str(ws / "scripts" / "verify_rules.py"),
                                    "--workspace", str(ws), "--vs-template", str(wt),
                                    "--json", str(out_json)],
                                   cwd=ws, capture_output=True, text=True)
            except Exception as exc:  # noqa: BLE001 —— 校验器本身起不来也算同步失败
                rb = _rollback(ws, backup)
                report["auto_rollback"] = {"reason": f"verify-exception: {exc}", "rollback_rc": rb}
                print(f"verify 无法运行：{exc}", file=sys.stderr)
                if a.json:
                    print(json.dumps(report, ensure_ascii=False))
                if rb != 0:
                    print(f"回滚也失败（rc={rb}）：按 {backup}/MANIFEST.txt 手工恢复", file=sys.stderr)
                    return 4
                print(f"已自动回滚（backup={backup}）", file=sys.stderr)
                return 3
            try:
                verify = json.loads(out_json.read_text(encoding="utf-8"))
            except Exception:
                verify = {"raw": (p.stdout or p.stderr)[:400]}
            finally:
                out_json.unlink(missing_ok=True)
            report["verify"] = {"clean": verify.get("clean"), "findings": verify.get("findings", [])[:10]}
            if not verify.get("clean") and not a.keep_on_fail:
                # 校验不通过 = 这次同步没有产出可用的工作区：回到同步前，而不是留一套半对的状态
                rb = _rollback(ws, backup)
                report["auto_rollback"] = {"reason": "verify-not-clean", "rollback_rc": rb}
                if rb != 0:
                    print(f"verify 未通过且回滚失败（rc={rb}）：按 {backup}/MANIFEST.txt 手工恢复",
                          file=sys.stderr)
                    if a.json:
                        print(json.dumps(report, ensure_ascii=False))
                    return 4
                print(f"verify 未通过，已自动回滚（backup={backup}）；"
                      f"要用当前结果请加 --keep-on-fail", file=sys.stderr)
                if a.json:
                    print(json.dumps(report, ensure_ascii=False))
                return 2

        if a.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"{ref}({kind})@{commit[:8]}  {before} → {after}；替换 {len(copied)} 个文件")
            print("备份:", backup)
            if report["pruned"]:
                print("已删除上游移除的文件:", ", ".join(report["pruned"]))
            if a.verify:
                print("verify clean:", (verify or {}).get("clean"))
        return 0 if (not a.verify or (verify or {}).get("clean")) else 2
    finally:
        subprocess.run(["git", "-C", str(tpl), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
