#!/usr/bin/env python3
"""Rebuild notes/INDEX.md using only the Python standard library.

The parser intentionally accepts only the small YAML-like frontmatter subset
documented in notes/_TEMPLATE.md: one-line scalar fields and indented dash
lists. This keeps the fresh workspace independent of third-party packages.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any


NOTES_DIR = Path(__file__).resolve().parent
SKIP = {"INDEX.md", "_TEMPLATE.md"}
REQUIRED = ("name", "description", "category", "techniques")
LIST_FIELDS = {"techniques", "related"}


def scalar(value: str) -> str:
    value = value.strip()
    if value[:1] in {"'", '"'}:
        try:
            return str(ast.literal_eval(value))
        except (ValueError, SyntaxError):
            pass
    return value


def parse_frontmatter(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None, [f"{path.name}: 缺少 frontmatter"]
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, [f"{path.name}: frontmatter 未闭合"]

    data: dict[str, Any] = {"techniques": [], "related": []}
    active_list: str | None = None
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  - "):
            if active_list not in LIST_FIELDS:
                warnings.append(f"{path.name}:{number}: 列表项没有对应列表字段")
                continue
            item = scalar(line[4:])
            if item:
                data[active_list].append(item)
            continue
        if line[:1].isspace() or ":" not in line:
            warnings.append(f"{path.name}:{number}: 不支持的 frontmatter 语法")
            active_list = None
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if key in LIST_FIELDS:
            if raw and raw not in {"[]"}:
                warnings.append(f"{path.name}:{number}: {key} 必须使用缩进列表")
            data[key] = []
            active_list = key
        else:
            data[key] = scalar(raw)
            active_list = None
    return data, warnings


def note_date(filename: str) -> str:
    match = re.search(r"(\d{2}-\d{2}-\d{2})\.md$", filename)
    return match.group(1) if match else "00-00-00"


def escape_table(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def main() -> int:
    notes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(NOTES_DIR.glob("*.md")):
        if path.name in SKIP:
            continue
        data, local_warnings = parse_frontmatter(path)
        warnings.extend(local_warnings)
        if data is None:
            continue
        for field in REQUIRED:
            if not data.get(field):
                warnings.append(f"{path.name}: 缺少必填字段 '{field}'")
        notes.append(
            {
                "file": path.name,
                "name": str(data.get("name") or path.stem),
                "description": str(data.get("description") or "").strip(),
                "category": str(data.get("category") or "未分类").strip(),
                "techniques": [str(item).strip() for item in data.get("techniques", []) if str(item).strip()],
                "related": [str(item).strip() for item in data.get("related", []) if str(item).strip()],
            }
        )

    notes.sort(key=lambda note: (note_date(note["file"]), note["name"]), reverse=True)
    by_name: dict[str, dict[str, Any]] = {}
    for note in notes:
        if note["name"] in by_name:
            warnings.append(f"note name 重复: {note['name']}")
        by_name[note["name"]] = note
    for note in notes:
        for related in note["related"]:
            if related not in by_name:
                warnings.append(f"{note['file']}: related 指向不存在的 note '{related}'")
            elif note["name"] not in by_name[related]["related"]:
                warnings.append(
                    f"related 不是双向: {note['name']} 列了 {related}，但对方未回链"
                )

    tech_map: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        for technique in note["techniques"]:
            tech_map.setdefault(technique, []).append(note)

    timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    lines = [
        "# Notes Index — Workspace 经验索引",
        "",
        f"> **本文件由 `notes/rebuild_index.py` 自动生成**（{timestamp}），禁止手改。",
        "> 修改索引 = 修改 note 的最小 frontmatter 后重跑脚本。",
        "",
        f"共 **{len(notes)}** 篇 note，**{len(tech_map)}** 个技术/方法关键词。",
        "",
        "## 笔记清单（按日期倒序）",
        "",
        "| 日期 | 类别 | Note | 技术/方法 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    if not notes:
        lines.append("| — | — | 尚无 note | — | — |")
    for note in notes:
        date = note_date(note["file"])
        display_date = "20" + date if date != "00-00-00" else "—"
        techniques = "、".join(note["techniques"]) or "—"
        lines.append(
            f"| {display_date} | {escape_table(note['category'])} | "
            f"[{escape_table(note['name'])}]({note['file']}) | "
            f"{escape_table(techniques)} | {escape_table(note['description']) or '—'} |"
        )

    lines.extend(
        [
            "",
            "## 技术/方法倒排索引",
            "",
            "| 关键词 | 相关 notes |",
            "|---|---|",
        ]
    )
    if not tech_map:
        lines.append("| — | 尚无 |")
    for technique in sorted(tech_map, key=lambda value: (-len(tech_map[value]), value)):
        hits = sorted(
            tech_map[technique],
            key=lambda note: (technique in note["description"], note_date(note["file"]), note["name"]),
            reverse=True,
        )
        refs = "、".join(f"[{item['name']}]({item['file']})" for item in hits)
        lines.append(f"| {escape_table(technique)} | {refs} |")

    lines.extend(["", "## Note 关联图（related 双向链接）", ""])
    edges = False
    for note in notes:
        if not note["related"]:
            continue
        edges = True
        refs = "、".join(
            f"[{by_name[name]['name']}]({by_name[name]['file']})" if name in by_name else f"`{name}`（缺失）"
            for name in note["related"]
        )
        lines.append(f"- **{note['name']}** ↔ {refs}")
    if not edges:
        lines.append("- 暂无关联")
    lines.append("")

    (NOTES_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: INDEX.md 已生成 — {len(notes)} 篇 note, {len(tech_map)} 个关键词")
    if warnings:
        print(f"共 {len(warnings)} 条警告：", file=sys.stderr)
        for warning in warnings:
            print(f"  [WARN] {warning}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
