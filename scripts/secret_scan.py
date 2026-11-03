#!/usr/bin/env python3
"""凭据扫描：仓库里出现高置信度明文凭据即失败（R61）。

只匹配高置信度模式，避免把文档里的「token」「key」等词误判：
私钥块、AWS Access Key、GitHub token、Slack token、通用 `password = "..."` 赋值、
以及 `Bearer <长串>`。命中只打印文件:行号与脱敏片段，不打印原文。

用法：python3 scripts/secret_scan.py [--json] [--root PATH]
退出码：0 干净；1 命中；2 用法/IO。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", "node_modules", "venv", "backups"}
SKIP_PREFIX = (".workspace/backup-",)      # 同步备份是模板层副本，不是工作区内容
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz", ".pyc", ".woff", ".woff2"}

PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws-akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{24,}")),
    ("password-assign", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
]
ALLOW = {
    # 文档/测试里的占位符：明确不是真凭据
    "REPLACE_ME", "CHANGEME", "<token>", "xxx", "***",
}


def masked(s: str) -> str:
    return s[:4] + "…" + s[-2:] if len(s) > 8 else "…"


def _ignores(root: Path) -> list[str]:
    """`.secretscanignore`：每行一个路径前缀/目录名，`#` 开头忽略。"""
    f = root / ".secretscanignore"
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.rstrip("/"))
    return out


def _skipped(rel: str, extra: list[str]) -> bool:
    if any(part in SKIP_DIRS or part.startswith(".venv") for part in rel.split("/")):
        return True
    if any(rel.startswith(pre) for pre in SKIP_PREFIX):
        return True
    return any(rel == e or rel.startswith(e + "/") for e in extra)


def scan(root: Path):
    hits = []
    extra = _ignores(root)
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIX:
            continue
        rel = p.relative_to(root).as_posix()
        if _skipped(rel, extra):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text or len(text) > 2_000_000:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(a in line for a in ALLOW):
                continue
            for name, pat in PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append({"file": p.relative_to(root).as_posix(), "line": i,
                                 "pattern": name, "sample": masked(m.group(0))})
                    break
    return hits


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if not root.is_dir():
        print(f"不是目录：{root}", file=sys.stderr)
        return 2
    hits = scan(root)
    if a.json:
        print(json.dumps({"hits": hits, "scanned_root": str(root)}, ensure_ascii=False))
    else:
        for h in hits:
            print(f"{h['file']}:{h['line']}  {h['pattern']}  {h['sample']}")
        print(f"{len(hits)} 处高置信度凭据模式" if hits else "未发现明文凭据")
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
