#!/usr/bin/env python3
"""notes 索引一致性：INDEX.md / _INVERTED.md 是否与「重新生成」的结果一致。

对应 R80（两个索引文件由 rebuild_index.py 生成，禁止手改）。
做法：把 notes/ 复制到临时目录（去掉两个生成物），在那里跑同一份 rebuild_index.py，
再逐字节比对生成结果与仓库里的文件。不修改仓库。
用法：python3 scripts/notes_index_check.py [--json]
退出码：0 一致；1 有漂移（手改或忘记重跑）；2 用法/IO。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "notes"
BUILDER = NOTES / "rebuild_index.py"
GENERATED = ("INDEX.md", "_INVERTED.md")
TS = re.compile(r"（\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{4}）")


def _norm(text: str) -> str:
    """生成时间戳每次不同，比对前抹掉；其余必须逐字节一致。"""
    return TS.sub("（<ts>）", text)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if not BUILDER.is_file():
        print("缺少 notes/rebuild_index.py", file=sys.stderr)
        return 2

    findings = []
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "notes"
        shutil.copytree(NOTES, dst,
                        ignore=shutil.ignore_patterns(*GENERATED, "__pycache__"))
        p = subprocess.run([sys.executable, "-B", str(dst / "rebuild_index.py")],
                           capture_output=True, text=True, cwd=dst)
        # rebuild_index 在「有警告 / 策展到期」时返回 1，但仍会产出文件；只有产出缺失才算失败
        if p.returncode not in (0, 1):
            print(json.dumps({"error": "rebuild-failed", "stderr": p.stderr[-400:]},
                             ensure_ascii=False) if a.json else f"重建失败：{p.stderr[-400:]}",
                  file=sys.stderr)
            return 2
        for name in GENERATED:
            fresh = dst / name
            cur = NOTES / name
            if not fresh.is_file():
                findings.append({"code": "E1", "message": f"重建未产出 {name}"})
                continue
            if not cur.is_file():
                findings.append({"code": "E2", "message": f"仓库缺少 {name}（未运行 rebuild_index.py？）"})
                continue
            if _norm(fresh.read_text(encoding="utf-8")) != _norm(cur.read_text(encoding="utf-8")):
                findings.append({"code": "E3", "message": f"{name} 与重建结果不一致（被手改或索引过期）"})

    if a.json:
        print(json.dumps({"findings": findings}, ensure_ascii=False))
    else:
        for f in findings:
            print(f"{f['code']} {f['message']}")
        print("notes 索引一致" if not findings else f"{len(findings)} 处漂移")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
