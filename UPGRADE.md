# 从旧版本模板升级工作区（自举指南）

> 适用：工作区由**旧版本** WorkspaceTemplate 创建（如 2.x），`.workspace/skills/` 里没有当前的 `template-update` 3.x、`workspace-repo-sync`、`docs-retrieval-maintenance`，因此**无法用工作区内的 skill 完成升级**。
> 本文是**自举入口**：只需要 Python ≥3.10，其余全部从模板克隆取用。升级完成后，工作区即可用现行 skill 继续维护。
> 实现说明，不构成政策；义务与判定口径见 [`AGENT_RULES.md`](AGENT_RULES.md)。

## 0. 先判断是否真的需要

```bash
grep -m1 '模板政策版本' AGENT_RULES.md          # 当前工作区版本
ls .workspace/skills/                           # 是否已有 template-update / workspace-repo-sync
test -f scripts/template_sync.py && echo has-sync || echo no-sync
```

满足任一即按本文处理：

- `AGENT_RULES.md` 版本低于模板当前版本，且 `.workspace/skills/` 缺当前 skill；
- 没有 `scripts/template_sync.py`；
- `skill_audit.py` 不存在，或报 `REGISTRY 名称集合与目录不一致`。

## 1. 取模板快照（只读，不碰工作区）

```bash
TPL=$(mktemp -d)/tpl
git clone --depth 1 --branch main https://github.com/KinoluKaslana/WorkspaceTemplate.git "$TPL"
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main
```

第二条是 **dry-run**，应看到：

```
[dry-run] origin/main(commit)@<sha>  <旧版本> → <新版本>
将替换 N 个文件（共 M）
```

`--branch` 可换 `dev` / `alpha`；`--ref` 也接受标签与提交。工作区若已登记 `template.local_clone`，可省略 `--template`。

## 2. 备份并写盘

```bash
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main --apply
```

- 自动备份到 `.workspace/backup-<ts>-sync/`（含 `MANIFEST.txt`）；
- 回写 `bootstrap.template.*`；**不动** `bootstrap.python` 与 `.venv-*`（`python_mode_preserved=true`）。

回滚：

```bash
BK=$(ls -d .workspace/backup-*-sync | tail -1)
python3 - "$BK" <<'PY'
import shutil, sys
from pathlib import Path
bk = Path(sys.argv[1])
for src in sorted(bk.rglob("*")):
    if src.is_file() and src.name != "MANIFEST.txt":
        dst = Path(".") / src.relative_to(bk)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
print("restored from", bk)
PY
```

## 3. 补齐工作区自有登记（关键）

模板层可以整文件替换，但 **`skills/REGISTRY.md` 与 `TOOLS.md` 属工作区所有**，同步不会带过来；旧工作区通常缺这两处：

```bash
python3 notes/rebuild_index.py                    # 生成 notes/INDEX.md 与 _INVERTED.md
python3 scripts/skill_audit.py --sync-registry    # 新建/重写 REGISTRY 的入口表与计数
python3 scripts/skill_audit.py --tools-snippet    # 打印需补进 TOOLS.md 的行
```

把最后一条打印的行追加到 `TOOLS.md` 的 workspace-skills 小节（没有就新建），例如：

```markdown
### workspace-skills（工作区自维护技能）

| 项 | 内容 |
|---|---|
| **template-update** | 入口 `.workspace/skills/template-update/SKILL.md` |
| **workspace-repo-sync** | 入口 `.workspace/skills/workspace-repo-sync/SKILL.md` |
```

## 4. 验收（四条全绿）

```bash
python3 scripts/verify_rules.py --workspace . --vs-template "$TPL"   # clean: true
python3 scripts/mark_rules.py check AGENT_RULES.md                   # all clauses marked
python3 scripts/skill_audit.py --strict                              # 0 findings
python3 scripts/workspace_preflight.py                               # 无 mismatch
```

任一条红 → 用第 2 步备份回滚，不要在漂移状态下继续。

## 5. 升级之后

- 同步改用工作区内的 skill：`.workspace/skills/template-update/SKILL.md`（任意 ref、`--prune`、`--verify`）；
- 跨设备同步：`.workspace/skills/workspace-repo-sync/SKILL.md`；
- `python.mode` 仍为 `unconfigured` 时按 `AGENT_RULES.md` §0 三选一（uv 默认），用 `scripts/pyenv_init.py` 落地；
- `AGENT_RULES.custom.md` 里失效的 `overrides: R<id>` 按 `verify_rules` findings 逐条报告后改写。

## 常见失败

| 现象 | 原因 | 处置 |
|---|---|---|
| `not-a-workspace` | 没有 `.workspace/bootstrap.json` | 这是新工作区，按 README「快速开始」走 |
| `ref-not-found` | 分支/标签拼错或克隆过期 | `--list-refs` 或先 `git fetch --all --tags` |
| `skill_audit` 报名称集合不一致 | 没跑 `--sync-registry` | 重跑第 3 步 |
| `skill_audit` 报未登记 TOOLS | 没补 TOOLS 行 | 用 `--tools-snippet` 输出补 |
| `verify_rules` 报 `HASH-DIFF` | 有人改过模板层文件 | 重跑 `--apply`；要保留的语义写进 `*.custom.md` |
| `python` 报 mismatch | 旧 venv 路径失效 | 按 §0 重选运行时；本流程不改已配置模式 |
