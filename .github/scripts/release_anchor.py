#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布锚：模板层文件变了，CHANGELOG 必须同时变。

为什么需要：`verify_rules` 的信任源是仓库内的 `.workspace/rule-clauses.json`，而
`mark_rules.py registry` 一条命令就能把它重建成"和当前文件一致"——所以「改模板文件 +
重建注册表」在机械门禁里是**不可见**的（审计 261104 的 R1，两位席位一致认定）。
本脚本把「不可见」变成至少「必须留痕」：这次提交里只要动了 `TEMPLATE_FILES` 里的文件，
`CHANGELOG.md` 就必须一起变；否则失败。

挡不住什么（必须写清）：
- 挡不住「同一次提交里既改文件又改 CHANGELOG」——那仍然要靠人评审。
- 挡不住有人直接改 `.github/workflows/ci.yml` 把这个步骤拿掉。
- 它校验的是"有没有留痕"，不是"留痕是否真实/恰当"。

用法：
  python3 .github/scripts/release_anchor.py --base <sha> --head <sha>
  python3 .github/scripts/release_anchor.py --self-test     # 负向自检（真 git 仓库 + 真失败）
退出码：0 通过；1 有模板层变更但 CHANGELOG 未变；2 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
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


ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = "CHANGELOG.md"


def load_template_files(root: Path) -> list[str]:
    """从 scripts/verify_rules.py 取 TEMPLATE_FILES（唯一真源，不复制清单）。"""
    src = (root / "scripts" / "verify_rules.py").read_text(encoding="utf-8")
    ns: dict = {}
    # 只执行到 TEMPLATE_FILES 赋值处，避免整文件副作用
    start = src.index("TEMPLATE_FILES")
    end = src.index("]", src.index("[", start)) + 1
    exec(compile("TEMPLATE_FILES" + src[start + len("TEMPLATE_FILES"):end], "<TEMPLATE_FILES>", "exec"), ns)
    return list(ns["TEMPLATE_FILES"])


def changed_files(root: Path, base: str, head: str) -> list[str]:
    r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", base, head],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"git diff failed: {r.stderr.strip()[:300]}")
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def check(root: Path, base: str, head: str) -> tuple[bool, dict]:
    tpl = set(load_template_files(root))
    changed = changed_files(root, base, head)
    tpl_changed = sorted(f for f in changed if f in tpl)
    ok = not tpl_changed or CHANGELOG in changed
    return ok, {
        "base": base, "head": head,
        "template_files_total": len(tpl),
        "changed_total": len(changed),
        "template_files_changed": tpl_changed,
        "changelog_changed": CHANGELOG in changed,
        "verdict": "pass" if ok else "fail",
        "hint": "" if ok else (
            "本次提交动了模板层文件但没动 CHANGELOG.md。"
            "要么补一条 CHANGELOG（说明改了什么、为什么），要么把改动移出模板层。"
            "这条闸挡不住「两处一起改」——那是评审责任。"),
    }


def self_test() -> int:
    """负向自检：真 git 仓库，先造一次违规提交（必须失败），再造一次带 CHANGELOG 的（必须通过）。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "verify_rules.py").write_text(
            'TEMPLATE_FILES = [\n    "AGENT_RULES.md",\n    "docs/a.md",\n]\n', encoding="utf-8", newline="\n")
        (repo / "AGENT_RULES.md").write_text("# v1\n", encoding="utf-8", newline="\n")
        (repo / "CHANGELOG.md").write_text("# changelog\n", encoding="utf-8", newline="\n")
        g = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]

        def run(*a):
            r = subprocess.run(list(a), capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise SystemExit(f"{' '.join(a)} -> {r.stderr[:300]}")
            return r

        run(*g, "init", "-q", "-b", "main")
        run(*g, "add", "-A")
        run(*g, "commit", "-qm", "c1")
        base = run(*g, "rev-parse", "HEAD").stdout.strip()

        # 违规：只改模板层文件
        (repo / "AGENT_RULES.md").write_text("# v2\n", encoding="utf-8", newline="\n")
        run(*g, "add", "-A")
        run(*g, "commit", "-qm", "c2-template-only")
        head_bad = run(*g, "rev-parse", "HEAD").stdout.strip()
        # 用子进程跑真实 CLI，确保测的是入口而不是函数
        rb = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--base", base,
                             "--head", head_bad, "--root", str(repo)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        # 合规：同时改 CHANGELOG
        (repo / "CHANGELOG.md").write_text("# changelog\n- entry\n", encoding="utf-8", newline="\n")
        run(*g, "add", "-A")
        run(*g, "commit", "-qm", "c3-with-changelog")
        head_ok = run(*g, "rev-parse", "HEAD").stdout.strip()
        rg = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--base", base,
                             "--head", head_ok, "--root", str(repo)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")

        good = rb.returncode == 1 and rg.returncode == 0
        print(json.dumps({
            "self_test": "pass" if good else "fail",
            "template_only_commit_rc": rb.returncode,     # 期望 1
            "with_changelog_commit_rc": rg.returncode,     # 期望 0
            "detail": json.loads(rb.stdout or "{}"),
        }, ensure_ascii=False, indent=2))
        return 0 if good else 1


def tag_identity(root: Path) -> tuple[bool, dict]:
    """同一版本号不得对应两套**分发内容**：tag `v<policy>` 存在时，其 TEMPLATE_FILES
    的 blob 必须与 HEAD 的完全相同。

    为什么要这条：`version_check` 只比「政策 / CHANGELOG / bootstrap」三个字符串；
    脚本内容改了但版本号没升，它照样绿，而工作区以 tag 为同步锚 → 已同步的工作区对 dev
    永久 TAMPERED（3.5.10/3.5.11 的真实事故）。

    只比 TEMPLATE_FILES，不比整棵树：`.github/`、CI 配置这类不下发的东西改了不需要升版本。
    tag 还不存在时给 warning（发布流程里 main 先推、tag 后打，属正常顺序）。
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        # 显式 UTF-8：Windows 默认按 locale（cp1252/cp936）解码 git 输出，
        # 非 ASCII 路径/分支名会抛 UnicodeDecodeError 或变成乱码。
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    if git("rev-parse", "HEAD").returncode != 0:
        return False, {"error": "not a git repo"}
    ver = None
    for line in (root / "AGENT_RULES.md").read_text(encoding="utf-8").splitlines()[:6]:
        m = re.search(r"模板政策版本：`([0-9]+\.[0-9]+\.[0-9]+)`", line)
        if m:
            ver = m.group(1)
            break
    if not ver:
        return False, {"error": "policy version not found"}
    tag = f"v{ver}"
    ref = f"refs/tags/{tag}"
    # tag 存在性用 show-ref --verify（`0 存在 / 1 不存在`），不用
    # `rev-parse <ref>^{commit}`：后者要求 git 做一次 peel（`^{...}` 语法），
    # Windows 上这一步失败时与「ref 不存在」完全同形（同样非零、无 stdout），
    # 于是「tag 明明在」被误报成 `tag-missing`——那是两个不同的判决。
    probe = git("show-ref", "--verify", "--quiet", ref)
    if probe.returncode == 1:
        return True, {"version": ver, "tag": tag, "verdict": "tag-missing",
                      "hint": f"{tag} 尚未创建（main 先推、tag 后打属正常）；发布完成前不要同步实例"}
    if probe.returncode != 0:
        # git 自身出错（128/129…）≠ tag 不存在：必须报出来，不能降级成 tag-missing
        return False, {"version": ver, "tag": tag, "verdict": "git-failed",
                       "error": (probe.stderr or "").strip()[:300]
                       or f"git show-ref --verify {ref} -> rc={probe.returncode}",
                       "hint": "tag 存在性探测本身失败（不是「tag 不存在」）；先看 error 里的 git stderr"}

    verify_src = (root / "scripts" / "verify_rules.py").read_text(encoding="utf-8")
    st = verify_src.index("TEMPLATE_FILES")
    en = verify_src.index("]", verify_src.index("[", st)) + 1
    ns: dict = {}
    exec(compile(verify_src[st:en], "<tf>", "exec"), ns)          # noqa: S102 - 只取常量清单
    files = ns["TEMPLATE_FILES"]

    def blobs(rev: str) -> tuple[dict[str, str], str]:
        """`ls-tree -r` 的 path→blob 映射；失败时第二项是非空错误串。"""
        proc = git("ls-tree", "-r", rev)
        if proc.returncode != 0:
            return {}, (proc.stderr or "").strip()[:300] or f"git ls-tree -r {rev} -> rc={proc.returncode}"
        m = {}
        for line in proc.stdout.splitlines():
            meta, _, path = line.partition("\t")
            fields = meta.split()
            if len(fields) >= 3:
                m[path] = fields[2]
        return m, ""

    head_b, head_err = blobs("HEAD")
    tag_b, tag_err = blobs(ref)
    if head_err or tag_err:
        return False, {"version": ver, "tag": tag, "verdict": "git-failed",
                       "error": head_err or tag_err,
                       "hint": "取 tree 失败（不是内容不一致）；先看 error 里的 git stderr"}
    diffs = [f for f in files if head_b.get(f) != tag_b.get(f)]
    ok = not diffs
    return ok, {"version": ver, "tag": tag, "checked_files": len(files),
                "differing": diffs[:8], "verdict": "pass" if ok else "content-mismatch",
                "hint": "" if ok else "分发内容与同名 tag 不同：改了模板层文件就必须升版本，不要复用已发布版本号"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--assert-tag-identity", action="store_true",
                    help="发布分支用：tag v<policy> 必须存在且 tree == HEAD tree")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.assert_tag_identity:
        ok, info = tag_identity(Path(a.root))
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if not a.base or not a.head:
        ap.error("--base and --head are required (or use --self-test)")
    ok, info = check(Path(a.root), a.base, a.head)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
