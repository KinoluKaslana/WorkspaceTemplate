#!/usr/bin/env python3
"""WorkspaceTemplate release transaction (stdlib only; dry-run by default)."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from datetime import date
from pathlib import Path

# 文本 I/O、子进程解码与 stdio 契约的单一实现：osnorm。脚本直跑（sys.path[0]=scripts/）
# 与被 importlib 按文件加载两种入口都要能解析兄弟模块，所以先把脚本目录钉进 sys.path。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入

def _utf8_stdio() -> None:
    """stdio 契约的唯一实现在 osnorm（旧名保留：`tests/test_windows_compat` 直接调它）。"""
    osnorm.configure_stdio()


_utf8_stdio()


def _child_env() -> dict[str, str]:
    """子进程环境：强制 Python 子进程用 UTF-8 读写（否则 Windows 上按 locale 编码）。"""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env

ROOT = Path(__file__).resolve().parent.parent
LEDGER = Path(".workspace/releases.json")
# dev 账本与主账本**物理分离**：dev 版本是四段 `NN.X.Y.Z`（NN=迭代轮次，X.Y.Z=预计发布的
# 正式版本），不是政策版本。写 dev 绝不碰 releases.json / AGENT_RULES.md / bootstrap.json，
# 因此 version_check 的三方对账（政策头 / CHANGELOG / bootstrap）在 dev 发版后依旧只认三段。
DEV_LEDGER = Path(".workspace/release_dev.json")
DEV_CHANGELOG = Path("CHANGELOG_DEV.md")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# 四段版本：四段都禁前导零，NN 从 1 起（`0.x.y.z` 不是合法轮次）。
SEMVER_DEV = re.compile(r"^([1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
# 首个 dev 账本条目收录 R35–R44（用户裁决），故空账本时的基线轮次为 44：
# `--dev` 不带参数时派生 `44.<当前政策版本>`，即 44.3.8.0。
DEV_ROUND_BASE = 44
DEV_DEFAULT_SUMMARY = "（未提供 --summary：本轮 dev 条目正文待人工补充）"
POLICY_RE = re.compile(r"模板政策版本：`\d+\.\d+\.\d+`")
POLICY_VAL_RE = re.compile(r"模板政策版本：`(\d+\.\d+\.\d+)`")
MUTABLE = ("AGENT_RULES.md", "CHANGELOG.md", ".workspace/bootstrap.json",
           ".workspace/releases.json", ".workspace/rule-clauses.json", "README.md",
           "README_EN.md", "docs/architecture.md", "docs/skills.md", "docs/tools.md",
           "docs/.index.json")
# dev 事务的写盘面：**只有** dev 变更日志与 dev 账本属于本事务自己的事实；
# 其余条目是「必须跟着刷新」的派生/登记文件（生成块、docs 索引、注册表），
# 它们被写只是因为计数或哈希变了，不是 dev 版本的信息落点。
DEV_MUTABLE = ("CHANGELOG_DEV.md", ".workspace/release_dev.json",
               ".workspace/rule-clauses.json", "README.md", "README_EN.md",
               "docs/architecture.md", "docs/skills.md", "docs/tools.md",
               "docs/.index.json")

class ReleaseError(RuntimeError): pass

def run(cmd, root=ROOT):
    p = osnorm.run_text(cmd, cwd=root, errors="replace", env=_child_env())
    if p.returncode:
        raise ReleaseError(f"命令失败 ({p.returncode}): {' '.join(cmd)}\n{(p.stdout+p.stderr).strip()}")
    if p.stdout.strip(): print(p.stdout.rstrip())
    return p.stdout

def version_tuple(value):
    m = SEMVER.fullmatch(value or "")
    if not m: raise ReleaseError(f"非法 semver: {value!r}（须为 X.Y.Z）")
    return tuple(map(int, m.groups()))

def load_ledger(root=ROOT):
    try: data = json.loads(osnorm.read_text(root / LEDGER))
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
    p = osnorm.run_text(["git", "status", "--porcelain"], cwd=root, errors="replace",
                        env=_child_env())
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
    text = osnorm.read_text(path); m = re.search(r"(?m)^##\s+\d+\.\d+\.\d+", text)
    if not m: raise ReleaseError("CHANGELOG.md 没有可定位的版本条目")
    entry = f"## {version}（{date.today().isoformat()}）\n\n{summary}\n\n"
    osnorm.write_text(path, text[:m.start()] + entry + text[m.start():], newline="lf")

def backup(root, temp, files=MUTABLE):
    existed = {}
    for rel in files:
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
    osnorm.write_text(root/LEDGER, json.dumps(data, ensure_ascii=False, indent=2)+"\n", newline="lf")

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

# ── dev 四段版本（NN.X.Y.Z）：独立账本，绝不进入政策版本链 ─────────────────────

def dev_version_tuple(value):
    m = SEMVER_DEV.fullmatch(value or "")
    if not m:
        raise ReleaseError(f"非法 dev 版本: {value!r}（须为 NN.X.Y.Z：四段、无前导零、NN≥1）")
    return tuple(map(int, m.groups()))

def policy_version(root=ROOT):
    """`AGENT_RULES.md:4` 的政策版本：dev 版本只在它前面加轮次，绝不改写它。"""
    try:
        lines = osnorm.read_text(root / "AGENT_RULES.md").split("\n")
    except OSError as exc:
        raise ReleaseError(f"AGENT_RULES.md 不可读: {exc}") from exc
    m = POLICY_VAL_RE.search(lines[3]) if len(lines) >= 4 else None
    if not m:
        raise ReleaseError("AGENT_RULES.md:4 没有「模板政策版本：`x.y.z`」")
    return m.group(1)

def load_dev_ledger(root=ROOT):
    """dev 账本（append-only）。缺文件 = 空账本；条目非法一律 fail-closed。"""
    path = root / DEV_LEDGER
    if not path.exists():
        return []
    try:
        data = json.loads(osnorm.read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"发版账本不可读: {exc}") from exc
    if not isinstance(data, list):
        raise ReleaseError("release_dev.json 必须是 JSON 数组（append-only）")
    prev, prev_ver = None, None
    for item in data:
        if not isinstance(item, dict):
            raise ReleaseError("release_dev.json 含非对象条目")
        version = str(item.get("version", ""))
        tup = dev_version_tuple(version)
        if item.get("round") != tup[0]:
            raise ReleaseError(f"release_dev.json: {version} 的 round 字段"
                               f" {item.get('round')!r} ≠ 版本首段 {tup[0]}")
        if prev is not None and tup <= prev:
            raise ReleaseError(f"release_dev.json: 版本必须严格递增（{prev_ver} → {version}）")
        prev, prev_ver = tup, version
    return data

def resolve_dev_version(explicit=None, round_no=None, root=ROOT):
    """派生本轮 dev 版本：`<NN>.<政策版本>`。

    `--version NN.X.Y.Z` 必须与当前政策版本的后三段相等——dev 版本不是政策版本，
    它只表达「第 NN 轮迭代，目标是 X.Y.Z 这次正式发布」。
    """
    policy = policy_version(root)
    if explicit:
        dev_version_tuple(explicit)
        if explicit.split(".", 1)[1] != policy:
            raise ReleaseError(f"dev 版本 {explicit} 的 X.Y.Z 必须等于当前政策版本 {policy}"
                               "（dev 版本不改变政策版本）")
        return explicit
    ledger = load_dev_ledger(root)
    nn = round_no if round_no is not None else (int(ledger[-1]["round"]) if ledger else DEV_ROUND_BASE)
    if nn < 1:
        raise ReleaseError(f"dev 轮次 NN 必须 ≥ 1：{nn}")
    return f"{nn}.{policy}"

def require_dev_upgrade(version, ledger):
    if not ledger:
        return
    last = str(ledger[-1]["version"])
    if dev_version_tuple(version) <= dev_version_tuple(last):
        raise ReleaseError(f"dev 版本必须严格升级: 请求 {version}，账本末条 {last}"
                           "（同一版本不得二次入账；下一轮请显式给 --round NN 或 --version NN.X.Y.Z）")

DEV_ENTRY_RE = re.compile(r"(?m)^##[ \t]+(\d+\.\d+\.\d+\.\d+)(?![\dA-Za-z.\-])")

def dev_heading_versions(path):
    """`CHANGELOG_DEV.md` 里的四段条目标题（按文件顺序）。"""
    try:
        text = osnorm.read_text(path)
    except OSError:
        return []
    return [m.group(1) for m in DEV_ENTRY_RE.finditer(text)]

def dev_plan(version, ledger, summary):
    rows = [f"CHANGELOG_DEV.md: 首个 `## NN.X.Y.Z` 条目前插入 ## {version}（{date.today().isoformat()}）",
            f".workspace/release_dev.json: 追加 {version}（round {dev_version_tuple(version)[0]}）",
            "policy_facts.py --write / docs_index.py build: 只刷新计数类生成块与索引（通常无变化）",
            "mark_rules.py registry: .workspace/rule-clauses.json（CHANGELOG_DEV.md 与 dev 账本在 TEMPLATE_FILES 里）",
            "**不写** .workspace/releases.json / AGENT_RULES.md / .workspace/bootstrap.json",
            "**不** 打 tag、**不** push"]
    if ledger and dev_version_tuple(version) <= dev_version_tuple(str(ledger[-1]["version"])):
        rows.append(f"!! 账本末条已是 {ledger[-1]['version']}：--apply 会被拒（版本必须严格升级）")
    print(f"DRY-RUN（不写盘）：dev 版本 {version}\n" + "\n".join("- " + x for x in rows))
    if not summary:
        print("- 摘要：" + DEV_DEFAULT_SUMMARY)

def insert_dev_entry(path, version, summary):
    text = osnorm.read_text(path)
    if version in dev_heading_versions(path):
        raise ReleaseError(f"CHANGELOG_DEV.md 已有 {version} 条目（拒绝重复入账）")
    entry = f"## {version}（{date.today().isoformat()}）\n\n{summary}\n\n"
    m = DEV_ENTRY_RE.search(text)
    new = (text[:m.start()] + entry + text[m.start():]) if m else (text.rstrip("\n") + "\n\n" + entry)
    osnorm.write_text(path, new, newline="lf")

def append_dev_ledger(root, version, summary):
    data = load_dev_ledger(root)
    data.append({"version": version, "round": dev_version_tuple(version)[0],
                 "date": date.today().isoformat(), "summary": summary,
                 "commit": None, "tag": None,
                 "files_sha256": {"CHANGELOG_DEV.md": sha16(root / DEV_CHANGELOG),
                                  "AGENT_RULES.md": sha16(root / "AGENT_RULES.md")}})
    osnorm.write_text(root / DEV_LEDGER, json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      newline="lf")

def dev_checklist(version):
    rows = [("四段版本格式 + 严格升级", "PASS"), ("CHANGELOG_DEV.md 首条入账", "PASS"),
            (".workspace/release_dev.json 追加", "PASS"),
            ("生成块 / docs 索引 / 注册表刷新", "PASS"),
            ("政策版本未变（version_check 仍三段）", "PASS"),
            ("releases.json / AGENT_RULES.md 未写", "SKIP(设计如此)"),
            ("不打 tag、不 push", "SKIP(需人工)")]
    print("dev 发版清单")
    for i, (label, verdict) in enumerate(rows, 1):
        print(f"{i:02d}. {verdict} {label}")
    print(f"下一步: {sys.executable} -B scripts/release.py --dev --verify")
    print("提测/合流由人工提交（本脚本不 commit、不 push）")

def apply_release_dev(version, summary, root=ROOT):
    """只写 CHANGELOG_DEV.md + release_dev.json（外加随哈希变动的登记/生成物）。

    与主发版不同，dev 事务**不要求工作树干净**：dev 账本就是开发树里的中间产物；
    反过来它也不写政策版本链的任何一个事实位，所以 version_check 的三方对账不受影响。
    """
    ledger = load_dev_ledger(root)
    require_dev_upgrade(version, ledger)
    changelog = root / DEV_CHANGELOG
    if not changelog.is_file():
        raise ReleaseError("CHANGELOG_DEV.md 不存在（dev 变更日志由仓库维护，脚本只追加条目）")
    with tempfile.TemporaryDirectory(prefix="release-dev-backup-") as raw:
        temp = Path(raw); existed = backup(root, temp, DEV_MUTABLE)
        try:
            insert_dev_entry(changelog, version, summary)
            append_dev_ledger(root, version, summary)
            py = sys.executable
            # 两个 dev 文件都在 TEMPLATE_FILES 里：不重建注册表，verify_rules 会立刻
            # 报 HASH-DIFF。生成块/索引按权威源重渲染（计数没变时是零字节改动）。
            run([py, "-B", "scripts/policy_facts.py", "--write"], root)
            run([py, "-B", "scripts/docs_index.py", "build"], root)
            run([py, "-B", "scripts/mark_rules.py", "registry", "AGENT_RULES.md"], root)
            run([py, "-B", "scripts/version_check.py"], root)
            run([py, "-B", "scripts/policy_facts.py", "--check"], root)
            run([py, "-B", "scripts/verify_rules.py", "--workspace", "."], root)
        except Exception as exc:  # noqa: BLE001 — 与主发版同一套 best-effort 回滚
            failures = restore(root, temp, existed)
            rollback_report(root, temp, failures, exc)
            raise
    dev_checklist(version)

def verify_dev(root=ROOT):
    """dev 对账：账本末条 == CHANGELOG_DEV 首个标题；政策版本仍是三段原值。"""
    ledger = load_dev_ledger(root)
    if not ledger:
        raise ReleaseError("release_dev.json 为空或不存在：还没有 dev 发版可对账")
    heads = dev_heading_versions(root / DEV_CHANGELOG)
    if not heads:
        raise ReleaseError("CHANGELOG_DEV.md 没有 `## NN.X.Y.Z` 条目")
    problems = []
    if ledger[-1]["version"] != heads[0]:
        problems.append(f"账本末条 {ledger[-1]['version']} ≠ CHANGELOG_DEV 首个标题 {heads[0]}")
    dup = sorted({v for v in heads if heads.count(v) > 1})
    if dup:
        problems.append("CHANGELOG_DEV.md 重复标题：" + ", ".join(dup))
    if problems:
        raise ReleaseError("；".join(problems))
    print(f"dev 账本一致：末条 {ledger[-1]['version']}"
          f"（round {ledger[-1]['round']}，共 {len(ledger)} 条）= CHANGELOG_DEV 首条 {heads[0]}")
    run([sys.executable, "-B", "scripts/version_check.py"], root)
    run([sys.executable, "-B", "scripts/policy_facts.py", "--check"], root)

def apply_release(version, summary, root=ROOT):
    require_upgrade(version, load_ledger(root)); require_clean(root)
    with tempfile.TemporaryDirectory(prefix="release-backup-") as raw:
        temp = Path(raw); existed = backup(root, temp)
        try:
            p = root/"AGENT_RULES.md"; text, n = POLICY_RE.subn(f"模板政策版本：`{version}`", osnorm.read_text(p), count=1)
            if n != 1: raise ReleaseError("AGENT_RULES.md:4 政策版本定位失败")
            osnorm.write_text(p, text, newline="lf"); update_changelog(root/"CHANGELOG.md", version, summary)
            bp = root/".workspace/bootstrap.json"; b = json.loads(osnorm.read_text(bp)); b["template"]["current_policy_version"] = version
            osnorm.write_text(bp, json.dumps(b, ensure_ascii=False, indent=2)+"\n", newline="lf")
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
    p=argparse.ArgumentParser()
    # dev 通道：四段版本 NN.X.Y.Z 的独立账本；它的默认动作是 dry-run，所以
    # `--version/--verify/--list/--tag` 这一组在三段路径里仍是必选，在 --dev 下可省略。
    p.add_argument("--dev",action="store_true",
                   help="dev 四段版本：只写 CHANGELOG_DEV.md + .workspace/release_dev.json（不 push / 不打 tag）")
    p.add_argument("--round",type=int,help="dev 迭代轮次 NN（默认取账本末条轮次；空账本为 44）")
    a=p.add_mutually_exclusive_group()
    a.add_argument("--version"); a.add_argument("--verify",action="store_true"); a.add_argument("--list",action="store_true"); a.add_argument("--tag")
    m=p.add_mutually_exclusive_group(); m.add_argument("--dry-run",action="store_true"); m.add_argument("--apply",action="store_true"); p.add_argument("--summary")
    x=p.parse_args(argv)
    if not x.dev:
        if not (x.version or x.verify or x.list or x.tag):
            p.error("需要 --version / --verify / --list / --tag 之一（或加 --dev 走四段账本）")
        if x.round is not None: p.error("--round 只用于 --dev")
        if x.version and not x.summary: p.error("--version 需要 --summary")
        if x.tag and not x.apply: p.error("--tag 必须显式搭配 --apply")
        if (x.verify or x.list) and (x.apply or x.dry_run or x.summary): p.error("--verify/--list 不接受写入参数")
        return x
    if x.tag: p.error("--dev 不打 tag：dev 账本只追加版本与摘要（tag 属于正式发布）")
    if x.version is not None: dev_version_tuple(x.version)
    if x.list and (x.apply or x.dry_run or x.summary): p.error("--dev --list 不接受写入参数")
    if x.verify and (x.apply or x.dry_run or x.summary): p.error("--dev --verify 不接受写入参数")
    return x

def main(argv=None):
    try:
        a=args_for(sys.argv[1:] if argv is None else argv)
        if a.dev:
            if a.list:
                print(json.dumps(load_dev_ledger(),ensure_ascii=False,indent=2)); return 0
            if a.verify:
                verify_dev(); return 0
            version = resolve_dev_version(a.version,a.round)
            if a.apply: apply_release_dev(version,a.summary or DEV_DEFAULT_SUMMARY)
            else: dev_plan(version,load_dev_ledger(),a.summary)
            return 0
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
