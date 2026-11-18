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
        boot = ROOT / ".workspace" / "bootstrap.json"      # 策展状态会影响索引头部文案
        if boot.is_file():
            (Path(tmp) / ".workspace").mkdir(exist_ok=True)
            shutil.copy2(boot, Path(tmp) / ".workspace" / "bootstrap.json")
        p = subprocess.run([sys.executable, "-B", str(dst / "rebuild_index.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=dst)
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
