#!/usr/bin/env python3
"""Rebuild notes/INDEX.md (compact routing) and notes/_INVERTED.md (on-demand detail).

The parser intentionally accepts only the small YAML-like frontmatter subset
documented in notes/_TEMPLATE.md: one-line scalar fields and indented dash
lists. This keeps the fresh workspace independent of third-party packages.

Layered output (v2):
- INDEX.md  — compact routing table only, kept small for startup loading.
- _INVERTED.md — full technique inverted index and related graph, loaded on demand.
- Notes with a non-empty `superseded_by` frontmatter field are archived:
  they leave the active routing table and are listed in a compact archive
  section instead (never silently deleted).
- The script also reports curation status from .workspace/bootstrap.json
  (count threshold / days since last curation) so agents can see whether
  AGENT_RULES.md §9.2 curation is due.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


NOTES_DIR = Path(__file__).resolve().parent
BOOTSTRAP = NOTES_DIR.parent / ".workspace" / "bootstrap.json"
SKIP = {"INDEX.md", "_TEMPLATE.md", "_INVERTED.md"}
REQUIRED = ("name", "description", "category", "techniques")
LIST_FIELDS = {"techniques", "related"}
DEFAULT_THRESHOLD = 25
DEFAULT_INTERVAL_DAYS = 90


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
    """Extract a sortable YY-MM-DD string from a note filename.

    Accepts both hyphenated (``*-26-08-17.md``) and compact (``*-260817.md``)
    suffixes; compact 6-digit dates are treated as yyMMdd.
    """
    match = re.search(r"(\d{2}-\d{2}-\d{2})\.md$", filename)
    if match:
        return match.group(1)
    match = re.search(r"(\d{2})(\d{2})(\d{2})\.md$", filename)
    if match:
        return "-".join(match.groups())
    return "00-00-00"


def display_date(filename: str) -> str:
    date = note_date(filename)
    return "20" + date if date != "00-00-00" else "—"


def escape_table(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def load_curation(active_count: int) -> tuple[str, bool]:
    """Read curation config from bootstrap.json; return (status_line, due)."""
    threshold = DEFAULT_THRESHOLD
    interval = DEFAULT_INTERVAL_DAYS
    last: str | None = None
    if BOOTSTRAP.is_file():
        try:
            cfg = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
            cur = (cfg.get("notes") or {}).get("curation") or {}
            threshold = int(cur.get("count_threshold") or DEFAULT_THRESHOLD)
            interval = int(cur.get("interval_days") or DEFAULT_INTERVAL_DAYS)
            last = cur.get("last_curated_at") or None
        except (ValueError, OSError, TypeError):
            return "策展状态：bootstrap.json 解析失败，按默认阈值判断", active_count >= DEFAULT_THRESHOLD
    due = active_count >= threshold
    if last is None:
        if active_count > 0:
            due = True
            return f"策展状态：{active_count} 篇活跃 / 阈值 {threshold}；从未策展 → **已到期**", True
        return f"策展状态：{active_count} 篇活跃 / 阈值 {threshold}；尚未策展（无 note，不触发）", False
    try:
        last_day = dt.date.fromisoformat(str(last)[:10])
    except ValueError:
        return f"策展状态：last_curated_at 无法解析（{last}），请人工核对", True
    days = (dt.date.today() - last_day).days
    if days >= interval:
        due = True
    state = "**已到期**" if due else f"{interval - days} 天后到期"
    return f"策展状态：{active_count} 篇活跃 / 阈值 {threshold}；上次策展 {last}（距今 {days} 天，{state}）", due


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
                "superseded_by": str(data.get("superseded_by") or "").strip(),
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
        if note["superseded_by"] and note["superseded_by"] not in by_name:
            warnings.append(
                f"{note['file']}: superseded_by 指向不存在的 note '{note['superseded_by']}'"
            )

    active = [note for note in notes if not note["superseded_by"]]
    archived = [note for note in notes if note["superseded_by"]]

    tech_map: dict[str, list[dict[str, Any]]] = {}
    for note in active:
        for technique in note["techniques"]:
            tech_map.setdefault(technique, []).append(note)

    curation_line, curation_due = load_curation(len(active))
    timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")

    # ---- INDEX.md: compact routing table -------------------------------------
    lines = [
        "# Notes Index — Workspace 经验索引（紧凑路由）",
        "",
        f"> **本文件由 `notes/rebuild_index.py` 自动生成**（{timestamp}），禁止手改。",
        "> 只做路由：按 name+摘要定位 note；关键词倒排索引与关联图在 `_INVERTED.md`（按需加载）。",
        "",
        f"共 **{len(active)}** 篇活跃 note，**{len(archived)}** 篇已归档，**{len(tech_map)}** 个关键词（详见 `_INVERTED.md`）。",
        f"> {curation_line}",
        "",
        "## 活跃 notes（按日期倒序）",
        "",
        "| 日期 | 类别 | Note | 摘要 |",
        "|---|---|---|---|",
    ]
    if not active:
        lines.append("| — | — | 尚无 note | — |")
    for note in active:
        lines.append(
            f"| {display_date(note['file'])} | {escape_table(note['category'])} | "
            f"[{escape_table(note['name'])}]({note['file']}) | "
            f"{escape_table(note['description']) or '—'} |"
        )

    if archived:
        lines.extend(
            [
                "",
                "## 已归档（superseded，正文仍可读）",
                "",
                "| Note | 被取代于 |",
                "|---|---|",
            ]
        )
        for note in archived:
            target = by_name.get(note["superseded_by"])
            target_link = (
                f"[{escape_table(target['name'])}]({target['file']})" if target else f"`{note['superseded_by']}`（缺失）"
            )
            lines.append(f"| [{escape_table(note['name'])}]({note['file']}) | {target_link} |")
    lines.append("")
    (NOTES_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")

    # ---- _INVERTED.md: on-demand detail --------------------------------------
    detail = [
        "# Notes Detail — 倒排索引与关联图（按需加载）",
        "",
        f"> **本文件由 `notes/rebuild_index.py` 自动生成**（{timestamp}），禁止手改。",
        "> 任务命中关键词或需要关联扩展时才读取本文件；启动检索走 `INDEX.md`。",
        "",
        "## 全量清单（含技术/方法，† = 已归档）",
        "",
        "| 日期 | 类别 | Note | 技术/方法 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    if not notes:
        detail.append("| — | — | 尚无 note | — | — |")
    for note in notes:
        mark = "†" if note["superseded_by"] else ""
        techniques = "、".join(note["techniques"]) or "—"
        detail.append(
            f"| {display_date(note['file'])} | {escape_table(note['category'])} | "
            f"[{escape_table(note['name'])}]({note['file']}){mark} | "
            f"{escape_table(techniques)} | {escape_table(note['description']) or '—'} |"
        )

    detail.extend(["", "## 技术/方法倒排索引（活跃 note）", "", "| 关键词 | 相关 notes |", "|---|---|"])
    if not tech_map:
        detail.append("| — | 尚无 |")
    for technique in sorted(tech_map, key=lambda value: (-len(tech_map[value]), value)):
        hits = sorted(
            tech_map[technique],
            key=lambda note: (technique in note["description"], note_date(note["file"]), note["name"]),
            reverse=True,
        )
        refs = "、".join(f"[{item['name']}]({item['file']})" for item in hits)
        detail.append(f"| {escape_table(technique)} | {refs} |")

    detail.extend(["", "## Note 关联图（related 双向链接，含归档）", ""])
    edges = False
    for note in notes:
        if not note["related"]:
            continue
        edges = True
        refs = "、".join(
            f"[{by_name[name]['name']}]({by_name[name]['file']})" if name in by_name else f"`{name}`（缺失）"
            for name in note["related"]
        )
        detail.append(f"- **{note['name']}** ↔ {refs}")
    if not edges:
        detail.append("- 暂无关联")
    detail.append("")
    (NOTES_DIR / "_INVERTED.md").write_text("\n".join(detail), encoding="utf-8")

    print(
        f"OK: INDEX.md（路由）+ _INVERTED.md（详情）已生成 — "
        f"{len(active)} 活跃 / {len(archived)} 归档 / {len(tech_map)} 关键词"
    )
    print(curation_line + (" → 按 AGENT_RULES §9.2 执行策展" if curation_due else ""))
    if warnings:
        print(f"共 {len(warnings)} 条警告：", file=sys.stderr)
        for warning in warnings:
            print(f"  [WARN] {warning}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
