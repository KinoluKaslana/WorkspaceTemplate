#!/usr/bin/env python3
"""Read-only inventory for a filesystem-backed skills directory.

The script discovers SKILL.md entries, extracts minimal frontmatter without
executing skill content, records Git provenance when available, and computes a
deterministic manifest fingerprint. It never writes to the inspected directory
or to the workspace; callers decide how to update skills/REGISTRY.md and
TOOLS.md after reviewing the output.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
}
FRONTMATTER_LIMIT = 256 * 1024


def _scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[:1] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
            return str(parsed)
        except (ValueError, SyntaxError):
            pass
    return value


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(FRONTMATTER_LIMIT)
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    lines = text[4:end].splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", lines[index])
        if not match:
            index += 1
            continue
        key = match.group(1)
        raw = (match.group(2) or "").strip()
        if raw in {"|", ">"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                line = lines[index]
                if line and not line[0].isspace():
                    break
                block.append(line.strip())
                index += 1
            separator = "\n" if raw == "|" else " "
            result[key] = separator.join(part for part in block if part).strip()
            continue
        result[key] = _scalar(raw)
        index += 1
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path) -> dict[str, Any] | None:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )

    try:
        top = run("rev-parse", "--show-toplevel")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if top.returncode != 0:
        return None
    revision = run("rev-parse", "HEAD")
    status = run("status", "--short", "--untracked-files=no")
    if revision.returncode != 0 or status.returncode != 0:
        return None
    return {
        "root": str(Path(top.stdout.strip()).resolve()),
        "revision": revision.stdout.strip(),
        "tracked_worktree": "dirty" if status.stdout.strip() else "clean",
    }


def _namespace(name: str) -> str:
    normalized = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-_.")
    return normalized.lower() or "skills"


def inspect(root_arg: str, max_depth: int, include_nested: bool) -> dict[str, Any]:
    requested = Path(root_arg).expanduser()
    if not requested.exists():
        raise ValueError(f"目录不存在: {requested}")
    if not requested.is_dir():
        raise ValueError(f"不是目录: {requested}")
    root = requested.resolve()
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    root_router_path = root / "SKILL.md"
    root_router_exists = root_router_path.is_file() and not root_router_path.is_symlink()

    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in EXCLUDED_DIRS
            and not (current_path / directory).is_symlink()
            and depth < max_depth
        )
        if root_router_exists and not include_nested and current_path == root:
            dirs[:] = []
        if "SKILL.md" not in files:
            continue
        path = current_path / "SKILL.md"
        if path.is_symlink():
            warnings.append(f"跳过符号链接入口: {path.relative_to(root)}")
            continue
        try:
            metadata = _frontmatter(path)
            file_hash = _sha256(path)
        except OSError as exc:
            warnings.append(f"无法读取 {path.relative_to(root)}: {exc}")
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "name": metadata.get("name") or path.parent.name or root.name,
                "description": metadata.get("description", ""),
                "entry": relative,
                "sha256": file_hash,
                "root_router": relative == "SKILL.md",
            }
        )

    entries.sort(key=lambda item: (not item["root_router"], item["entry"]))
    manifest = hashlib.sha256()
    for item in entries:
        manifest.update(item["entry"].encode("utf-8"))
        manifest.update(b"\0")
        manifest.update(item["sha256"].encode("ascii"))
        manifest.update(b"\n")

    names: dict[str, list[str]] = {}
    for item in entries:
        names.setdefault(item["name"], []).append(item["entry"])
    duplicates = {name: paths for name, paths in names.items() if len(paths) > 1}
    if duplicates:
        warnings.append("发现重复 skill name；登记时必须使用命名空间或显式消解")

    return {
        "schema_version": 1,
        "requested_path": str(requested),
        "canonical_root": str(root),
        "suggested_namespace": _namespace(root.name),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git": _git(root),
        "root_router": "SKILL.md" if root_router_exists else None,
        "nested_scan": include_nested or not root_router_exists,
        "skill_count": len(entries),
        "manifest_sha256": manifest.hexdigest(),
        "duplicate_names": duplicates,
        "warnings": warnings,
        "skills": entries,
    }


def _escape(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def to_markdown(data: dict[str, Any]) -> str:
    git = data["git"]
    lines = [
        "# Skills 目录只读盘点",
        "",
        f"- 规范路径：`{data['canonical_root']}`",
        f"- 建议命名空间：`{data['suggested_namespace']}`",
        f"- 根路由入口：`{data['root_router'] or '无'}`",
        f"- 已扫描嵌套入口：`{'yes' if data['nested_scan'] else 'no'}`",
        f"- Skill 数：`{data['skill_count']}`",
        f"- 入口清单 SHA-256：`{data['manifest_sha256']}`",
    ]
    if git:
        lines.extend(
            [
                f"- Git 根：`{git['root']}`",
                f"- Git revision：`{git['revision']}`",
                f"- 已跟踪工作树：`{git['tracked_worktree']}`",
            ]
        )
    else:
        lines.append("- Git：`不适用`")
    if data["warnings"]:
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {_escape(item)}" for item in data["warnings"])
    lines.extend(
        [
            "",
            "## Skill 入口",
            "",
            "| 名称 | 入口 | 根路由 | SHA-256 | 触发摘要 |",
            "|---|---|---|---|---|",
        ]
    )
    for item in data["skills"]:
        description = _escape(item["description"])
        if len(description) > 240:
            description = description[:237] + "..."
        lines.append(
            f"| {_escape(item['name'])} | `{item['entry']}` | "
            f"{'yes' if item['root_router'] else 'no'} | `{item['sha256']}` | "
            f"{description or '—'} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读盘点 skills 目录中的 SKILL.md 入口、来源和指纹"
    )
    parser.add_argument("skills_dir", help="用户提供的 skills 根目录")
    parser.add_argument(
        "--format", choices=("json", "markdown"), default="json", help="输出格式"
    )
    parser.add_argument(
        "--max-depth", type=int, default=8, help="递归搜索最大目录深度（默认 8）"
    )
    parser.add_argument(
        "--include-nested",
        action="store_true",
        help="即使根目录已有路由型 SKILL.md，仍显式扫描嵌套入口",
    )
    args = parser.parse_args()
    if not 0 <= args.max_depth <= 64:
        parser.error("--max-depth 必须介于 0 和 64 之间")
    return args


def main() -> int:
    args = parse_args()
    try:
        data = inspect(args.skills_dir, args.max_depth, args.include_nested)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        sys.stdout.write(to_markdown(data))
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0 if data["skill_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
