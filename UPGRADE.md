# 从旧版本模板升级工作区（自举指南）

> 适用：工作区由**旧版本** WorkspaceTemplate 创建（如 2.x），`.workspace/skills/` 里没有当前的 `template-update` 3.x、`workspace-repo-sync`、`docs-retrieval-maintenance`，因此**无法用工作区内的 skill 完成升级**。
> 本文是**自举入口**：只需要 Python ≥3.10，其余全部从模板克隆取用。升级完成后，工作区即可用现行 skill 继续维护。
> 实现说明，不构成政策；义务与判定口径见 [`AGENT_RULES.md`](AGENT_RULES.md)。
> **这是高影响动作（`AGENT_RULES.md` §7 R59）**：会整文件替换模板层、写入 `bootstrap.json` 并新建文件。执行前须取得工作区负责人同意；不同意就停在 dry-run。

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
# $TPL 只在当前 shell 有效：下面每段都在同一 shell 里执行，或把它写进文件后 source
TPL=$(mktemp -d)/tpl
echo "$TPL" > /tmp/tpl.path          # 换 shell 时用 TPL=$(cat /tmp/tpl.path)
git clone --depth 1 --branch main https://github.com/KinoluKaslana/WorkspaceTemplate.git "$TPL"
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main
```

第二条是 **dry-run**，应看到：

```
[dry-run] origin/main(commit)@<sha>  <旧版本> → <新版本>
将替换 N 个文件（共 M）
```

`--branch` 可换 `dev` / `alpha`。**`--depth 1` 的浅克隆只有一条分支**：要取标签或历史提交，改用完整克隆（去掉 `--depth 1`，或 `git fetch --tags` 后再 `--ref <tag>`），否则会报 `ref-not-found`。工作区若已登记 `template.local_clone`，可省略 `--template`。

## 2. 备份并写盘

```bash
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main --apply --keep-readme
```

`--keep-readme`：旧工作区多半没有 `last_synced_commit` 记录，脚本会因此把 `README.md` 当成模板页替换掉。若这个 README 是你自己的首页，**必须**带该参数；想让模板 README 覆盖时才省略。

- 自动备份到 `.workspace/backup-<ts>-sync/`（含 `MANIFEST.txt`）；
- 回写 `bootstrap.template.*`；**不动** `bootstrap.python` 与 `.venv-*`（`python_mode_preserved=true`）。

回滚（任一步校验不过时）：

```bash
ls -d .workspace/backup-*-sync        # 先看有几个备份，确认要回滚到哪一个
BK=.workspace/backup-<选中的时间戳>-sync
python3 "$TPL/scripts/template_sync.py" --workspace . --rollback "$BK" --purge-generated
# 期望：放回 N、删除本次新增 M、清除引导期生成文件；工作区回到升级前的文件集合
```

`MANIFEST.txt` 分 `[replaced]` / `[added]` / `[preserved]` 三段；`--rollback` 会放回旧文件并**删除本次新增**（`docs/`、`UPGRADE.md`、`scripts/`、`.workspace/skills/*` 等）。只放回不删除会留下残留，不要手抄 `cp` 脚本。
`--purge-generated` 会一并删除升级前不存在的生成文件（`skills/REGISTRY.md`、`notes/INDEX.md`、`notes/_INVERTED.md`、`docs/.index.json`、`docs/.aliases.json`）；不带它时只放回 replaced + 删除 added，生成文件会残留。
`TOOLS.md` 的补登行不在这套机制里：回滚前先记下加了几行，回滚后手工删掉。

## 3. 补齐工作区自有登记（关键）

模板层可以整文件替换，但 **`skills/REGISTRY.md` 与 `TOOLS.md` 属工作区所有**，同步不会带过来；旧工作区通常缺这两处：

```bash
python3 notes/rebuild_index.py                    # 生成 notes/INDEX.md 与 _INVERTED.md
python3 scripts/docs_index.py build               # 生成 docs/.index.json
python3 scripts/skill_audit.py --sync-registry    # 新建/重写 REGISTRY 的入口表与计数
python3 scripts/skill_audit.py --tools-snippet    # 打印需补进 TOOLS.md 的行
```

**旧 `bootstrap.python` 记录不全时**（`preflight` 报 `python` mismatch）：
- `python.runtime.identifier` 写**venv 目录的绝对路径**（如 `/path/.venv-314` 或 `D:\ws\.venv-314`，**不是** `.../bin/python` / `...\Scripts\python.exe`）；`python.version` 写该 venv 的 `X.Y.Z`（`preflight` 读 `python.version`，缺失时回退 `python.runtime.version`）；
- 记录指向的 venv 已不存在（换机/搬目录）→ 按 §0.1 **重建同模式 venv**，`python.mode` 不变；
- 模式本身为 `unconfigured` 才走 `scripts/pyenv_init.py`（uv 默认）。

把最后一条打印的行追加到 `TOOLS.md` 的 workspace-skills 小节（没有就新建）；**先 `grep -c '<skill 名>' TOOLS.md` 再写**，重复执行不要产生重复行。例如：

```markdown
### workspace-skills（工作区自维护技能）

| 项 | 内容 |
|---|---|
| **template-update** | 入口 `.workspace/skills/template-update/SKILL.md` |
| **workspace-repo-sync** | 入口 `.workspace/skills/workspace-repo-sync/SKILL.md` |
```

**照 `--tools-snippet` 的完整输出抄，不要只抄上面两行**：模板当前带 8 个 skill，少登记一个就会在 `skill_audit --strict` 里报「未登记于 TOOLS.md」。

## 4. 验收（四条全绿）

```bash
python3 scripts/verify_rules.py --workspace . --vs-template "$TPL"   # clean: true
python3 scripts/mark_rules.py check AGENT_RULES.md                   # all clauses marked
python3 scripts/skill_audit.py --strict                              # 0 findings
python3 scripts/workspace_preflight.py                               # 无 mismatch
```

任一条红 → 用第 2 步备份回滚，不要在漂移状态下继续。
`preflight` 的 `mismatch` 必须为 0；`warn`（如本机无 `.git`、`TOOLS §1` 缺本机行）需在报告里逐条解释。
幂等复核：再跑一次 `template_sync` 的 dry-run，应显示 `→ 将替换 0 个文件`。

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
