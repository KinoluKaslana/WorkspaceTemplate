#!/usr/bin/env python3
"""notes 索引一致性：INDEX.md / _INVERTED.md 是否与「重新生成」的结果一致。

对应 R80（两个索引文件由 rebuild_index.py 生成，禁止手改）。
做法：把 notes/ 复制到临时目录（去掉两个生成物），在那里跑同一份 rebuild_index.py，
再逐字节比对生成结果与仓库里的文件。不修改仓库。

**C2（索引自老化）的修法**：索引正文里不再出现「距今 N 天 / N 天后到期」这类**派生自今天**的
值，只写绝对量（上次策展日、间隔、到期日）。所以比对侧**不需要**任何「剔除相对天数行」的豁免
——只归一生成时刻（每次生成都不同，且不承载语义）。反过来把派生值从比对里剔掉是错的：索引每
天仍会写出不同的字节，跨设备/跨时间对账时两份索引对不上，而检查却报绿。

用法：python3 scripts/notes_index_check.py [--json]
退出码：0 一致；1 有漂移（手改或忘记重跑）；2 用法/IO。
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


# 文本 I/O 单一实现：osnorm。脚本直跑（sys.path[0]=scripts/）与被 importlib
# 按文件加载两种入口都要能解析兄弟模块，所以先把脚本目录钉进 sys.path。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import osnorm  # noqa: E402 - 依赖上面的 sys.path 注入

# stdio 编码契约的单一实现（TTY / 非 TTY 两个分支在 osnorm 内）
osnorm.configure_stdio()


ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "notes"
BUILDER = NOTES / "rebuild_index.py"
GENERATED = ("INDEX.md", "_INVERTED.md")
TS = re.compile(r"（\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{4}）")


def _norm(text: str) -> str:
    """只归一生成时刻（每次生成都不同，且不承载语义）；其余必须逐字节一致。

    这里**故意不**再加「剔除相对天数」之类的日期豁免：C2 的措施是让 `rebuild_index.py`
    不往索引里写派生自今天的值（见 load_curation），而不是放宽比对。
    """
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
        p = osnorm.run_text([sys.executable, "-B", str(dst / "rebuild_index.py")], cwd=dst)
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
            if _norm(osnorm.read_text(fresh)) != _norm(osnorm.read_text(cur)):
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
