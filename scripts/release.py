#!/usr/bin/env python3
"""WorkspaceTemplate release transaction (stdlib only; dry-run by default)."""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, subprocess, sys, tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = Path(".workspace/releases.json")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
POLICY_RE = re.compile(r"模板政策版本：`\d+\.\d+\.\d+`")
MUTABLE = ("AGENT_RULES.md", "CHANGELOG.md", ".workspace/bootstrap.json",
           ".workspace/releases.json", ".workspace/rule-clauses.json", "README.md",
           "README_EN.md", "docs/architecture.md", "docs/skills.md", "docs/tools.md",
           "docs/.index.json")

class ReleaseError(RuntimeError): pass

def run(cmd, root=ROOT):
    p = subprocess.run(cmd, cwd=root, text=True, capture_output=True)
    if p.returncode:
        raise ReleaseError(f"命令失败 ({p.returncode}): {' '.join(cmd)}\n{(p.stdout+p.stderr).strip()}")
    if p.stdout.strip(): print(p.stdout.rstrip())
    return p.stdout

def version_tuple(value):
    m = SEMVER.fullmatch(value or "")
    if not m: raise ReleaseError(f"非法 semver: {value!r}（须为 X.Y.Z）")
    return tuple(map(int, m.groups()))

def load_ledger(root=ROOT):
    try: data = json.loads((root / LEDGER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ReleaseError(f"发版账本不可读: {exc}") from exc
    if not isinstance(data, list) or not data: raise ReleaseError("releases.json 必须是非空 JSON 数组")
    if any(not isinstance(x, dict) or not SEMVER.fullmatch(str(x.get("version", ""))) for x in data):
        raise ReleaseError("releases.json 含非法条目")
    return data

def require_upgrade(version, ledger):
    current = str(ledger[-1]["version"])
    if version_tuple(version) <= version_tuple(current):
        raise ReleaseError(f"版本必须严格升级: 请求 {version}，账本末条 {current}")

def require_clean(root=ROOT):
    p = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True)
    if p.returncode: raise ReleaseError("工作目录不是可用的 git 仓库")
    if p.stdout.strip(): raise ReleaseError("工作树不干净，拒绝发版:\n" + p.stdout.rstrip())

def sha16(path): return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

def plan(version, summary):
    return [f"AGENT_RULES.md:4 -> 模板政策版本：`{version}`",
            f"CHANGELOG.md: 首个版本条目前插入 ## {version}（{date.today().isoformat()}） + {summary}",
            f".workspace/bootstrap.json: template.current_policy_version -> {version}",
            "policy_facts.py --write: README.md / README_EN.md / docs/{architecture,skills,tools}.md",
            "mark_rules.py registry: .workspace/rule-clauses.json",
            "docs_index.py build: docs/.index.json", f".workspace/releases.json: 追加 {version}"]

def update_changelog(path, version, summary):
    text = path.read_text(encoding="utf-8"); m = re.search(r"(?m)^##\s+\d+\.\d+\.\d+", text)
    if not m: raise ReleaseError("CHANGELOG.md 没有可定位的版本条目")
    entry = f"## {version}（{date.today().isoformat()}）\n\n{summary}\n\n"
    path.write_text(text[:m.start()] + entry + text[m.start():], encoding="utf-8")

def backup(root, temp):
    existed = {}
    for rel in MUTABLE:
        src = root / rel; existed[rel] = src.exists()
        if src.is_file():
            dst = temp / rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    return existed

def restore(root, temp, existed):
    """Best-effort rollback: one failing file must never abort the others.

    Returns a list of (rel, existed, exc) for the restores that failed, so
    the caller can report a complete "what is still not restored" list and
    still exit non-zero. A formerly-absent file is removed (never created);
    an unlink failure is recorded exactly like a copy failure.
    """
    failures = []
    for rel, old in existed.items():
        dst, src = root / rel, temp / rel
        try:
            if old:
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
            elif dst.exists(): dst.unlink()
        except Exception as exc:  # noqa: BLE001 — rollback must keep going
            failures.append((rel, old, exc))
    return failures

def rollback_report(root, temp, failures, original):
    """Print the unrestored list (per-file reason) plus the original exception."""
    if not failures: return
    print(f"回滚未恢复清单：{len(failures)} 项 restore 失败（其余文件已按备份恢复）", file=sys.stderr)
    for rel, old, exc in failures:
        try:
            same = old and (root/rel).is_file() and (root/rel).read_bytes() == (temp/rel).read_bytes()
        except OSError:
            same = False
        verdict = "磁盘内容与备份一致，无需人工处理" if same else "磁盘内容与备份不一致，需人工恢复"
        print(f"- {rel}: {type(exc).__name__}: {exc} → {verdict}", file=sys.stderr)
    print(f"原始异常: {type(original).__name__}: {original}", file=sys.stderr)

def append_ledger(root, version, summary):
    data = load_ledger(root)
    data.append({"version": version, "date": date.today().isoformat(), "summary": summary,
                 "commit": None, "tag": None, "files_sha256": {
                     "AGENT_RULES.md": sha16(root/"AGENT_RULES.md"),
                     "CHANGELOG.md": sha16(root/"CHANGELOG.md")}})
    (root/LEDGER).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

def checklist(version):
    rows = ["参数与版本升级校验", "工作树干净", "政策/CHANGELOG/bootstrap 同步", "policy facts 生成",
            "条款注册表重建", "docs 索引重建", "发版账本追加", "version_check + policy_facts --check",
            "tag", "push", "实例同步"]
    print("发版清单")
    for i, label in enumerate(rows, 1):
        print(f"{i:02d}. {'PASS' if i <= 8 else 'SKIP(需人工执行：release.py --tag)'} {label}")
    print(f"下一步: {sys.executable} -B scripts/release.py --tag {version} --apply")
    print(f"人工 push: git push origin HEAD && git push origin v{version}")
    print(f"实例同步: 按 .workspace/skills/template-update/SKILL.md 从 v{version} 同步")

def apply_release(version, summary, root=ROOT):
    require_upgrade(version, load_ledger(root)); require_clean(root)
    with tempfile.TemporaryDirectory(prefix="release-backup-") as raw:
        temp = Path(raw); existed = backup(root, temp)
        try:
            p = root/"AGENT_RULES.md"; text, n = POLICY_RE.subn(f"模板政策版本：`{version}`", p.read_text(encoding="utf-8"), count=1)
            if n != 1: raise ReleaseError("AGENT_RULES.md:4 政策版本定位失败")
            p.write_text(text, encoding="utf-8"); update_changelog(root/"CHANGELOG.md", version, summary)
            bp = root/".workspace/bootstrap.json"; b = json.loads(bp.read_text(encoding="utf-8")); b["template"]["current_policy_version"] = version
            bp.write_text(json.dumps(b, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            py = sys.executable
            run([py,"-B","scripts/policy_facts.py","--write"],root); run([py,"-B","scripts/mark_rules.py","registry","AGENT_RULES.md"],root)
            run([py,"-B","scripts/docs_index.py","build"],root); append_ledger(root, version, summary)
            # 账本追加也是一次写盘，注册表必须排在它之后重建：否则 registry 里
            # .workspace/releases.json 的 file_sha256 是追加前的旧值，一次成功的
            # 发版会立刻被 verify_rules 判成 HASH-DIFF（仓库留在脏状态）。
            run([py,"-B","scripts/mark_rules.py","registry","AGENT_RULES.md"],root)
            run([py,"-B","scripts/version_check.py"],root); run([py,"-B","scripts/policy_facts.py","--check"],root)
            # 事务最末自检：发版成功必须等于 verify_rules clean，否则整体回滚。
            run([py,"-B","scripts/verify_rules.py","--workspace","."],root)
        except Exception as exc:  # noqa: BLE001 — 统一走 best-effort 回滚
            failures = restore(root, temp, existed)
            rollback_report(root, temp, failures, exc)
            raise
    checklist(version)

def verify_commands(py=None):
    """Commands covered by --verify (data, so coverage is testable without running)."""
    py = py or sys.executable
    return [[py,"-B","scripts/version_check.py"],[py,"-B","scripts/policy_facts.py","--check"],
            [py,"-B","scripts/verify_rules.py","--workspace","."],[py,"-B","scripts/skill_audit.py","--strict"],
            [py,"-B","-m","unittest","discover","-s","tests","-p","test_*.py"]]

def verify(root=ROOT):
    for cmd in verify_commands(): run(cmd,root)

def tag_release(version, root=ROOT):
    version_tuple(version); ledger=load_ledger(root)
    if ledger[-1]["version"] != version: raise ReleaseError(f"只能给账本末条打 tag: {ledger[-1]['version']}")
    require_clean(root); run(["git","tag",f"v{version}"],root)
    try: run([sys.executable,"-B",".github/scripts/release_anchor.py","--assert-tag-identity"],root)
    except Exception: subprocess.run(["git","tag","-d",f"v{version}"],cwd=root,capture_output=True); raise
    print(f"PASS tag v{version}；未 push\n人工执行: git push origin v{version}")

def args_for(argv):
    p=argparse.ArgumentParser(); a=p.add_mutually_exclusive_group(required=True)
    a.add_argument("--version"); a.add_argument("--verify",action="store_true"); a.add_argument("--list",action="store_true"); a.add_argument("--tag")
    m=p.add_mutually_exclusive_group(); m.add_argument("--dry-run",action="store_true"); m.add_argument("--apply",action="store_true"); p.add_argument("--summary")
    x=p.parse_args(argv)
    if x.version and not x.summary: p.error("--version 需要 --summary")
    if x.tag and not x.apply: p.error("--tag 必须显式搭配 --apply")
    if (x.verify or x.list) and (x.apply or x.dry_run or x.summary): p.error("--verify/--list 不接受写入参数")
    return x

def main(argv=None):
    try:
        a=args_for(sys.argv[1:] if argv is None else argv)
        if a.list: print(json.dumps(load_ledger(),ensure_ascii=False,indent=2))
        elif a.verify: verify()
        elif a.tag: tag_release(a.tag)
        else:
            version_tuple(a.version); require_upgrade(a.version,load_ledger())
            if a.apply: apply_release(a.version,a.summary)
            else: print("DRY-RUN（不写盘）\n"+"\n".join("- "+x for x in plan(a.version,a.summary)))
        return 0
    except (ReleaseError,OSError,KeyError,json.JSONDecodeError) as exc: print(f"FAIL {exc}",file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main())
