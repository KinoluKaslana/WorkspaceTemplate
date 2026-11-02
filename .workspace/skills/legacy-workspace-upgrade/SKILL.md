---
name: legacy-workspace-upgrade
description: "Use when a workspace was created from an OLD template version and lacks the current skills (no template-update 3.x / workspace-repo-sync / docs-retrieval-maintenance). 老版本模板工作区缺少现行 skill 时用它自举升级（只依赖 Python，其余取自模板快照）。"
version: 1.0.0
---

# Legacy Workspace Upgrade — 旧版本工作区自举

工作区由**旧版本模板**创建时，`.workspace/skills/` 里的 `template-update` 是旧版（甚至不存在），**不能用工作区自身的 skill 完成升级**。本 skill 定义一条只依赖 Python 的自举路径：从模板快照取 `template_sync.py`，把当前模板层装进来，再补齐工作区自有登记。

**不负责**：新建工作区（→ README「快速开始」）、pre-v2.0 无血缘接管（→ `workspace-init`）、日常同步（→ `template-update`）、分支迁移（→ 用户自管）。

## 触发条件

- `AGENT_RULES.md` 版本低于模板当前版本，且 `.workspace/skills/` 缺当前 skill；
- `scripts/template_sync.py` 不存在，或 `skill_audit.py` 报 `REGISTRY 名称集合与目录不一致`；
- 用户说「把这个老工作区升到新模板 / 它没有你说的那些 skill」。

**误触发**：工作区已有 `template-update` 3.x 且 `verify_rules` clean → 用 `template-update` 正常同步。
**漏触发**：有 `template_sync.py` 但 `.workspace/skills/` 仍是旧版（半升级状态）——同样走本 skill，`--apply` 会补齐。

## 前置事实（先读，不可跳过）

1. `.workspace/bootstrap.json` 存在（无它则不是本模板工作区，停）；
2. 有 Python ≥3.10 与网络（或本地已有模板克隆）；
3. 用户已知悉会替换模板层文件（R59 高影响动作）并同意；
4. 任一条不成立 → 停下报告，不降级执行。

## 输入输出契约

- 读：`bootstrap.template.{source_repo,local_clone}`、`AGENT_RULES.md` 版本行、`.workspace/skills/` 目录；
- 写：模板层文件（经 `template_sync.py`）、`.workspace/backup-<ts>-sync/`、`bootstrap.template.*`、`skills/REGISTRY.md`（`--sync-registry`）、`TOOLS.md`（人工补登）；
- 报告：来源 ref/commit、版本前后、替换文件数、REGISTRY/TOOLS 补齐情况、四条验收输出。

## 标准流程

### 第 1 步：取快照并 dry-run

```bash
TPL=$(mktemp -d)/tpl
git clone --depth 1 --branch main https://github.com/KinoluKaslana/WorkspaceTemplate.git "$TPL"
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main
```

期望输出含 `<旧版本> → <新版本>` 与「将替换 N 个文件」。ref 可换分支/标签/提交。

### 第 2 步：写盘（自动备份）

```bash
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main --apply
```

### 第 3 步：补工作区自有登记

```bash
python3 notes/rebuild_index.py
python3 scripts/docs_index.py build
python3 scripts/skill_audit.py --sync-registry
python3 scripts/skill_audit.py --tools-snippet    # 按输出补 TOOLS.md
```

旧 `bootstrap.python` 缺 `runtime.identifier` 时：写**venv 目录绝对路径**（非 `bin/python`）+ `runtime.version`；记录指向的 venv 已不存在则按 §0.1 重建**同模式** venv；仅 `unconfigured` 才走 `pyenv_init.py`。

### 第 4 步：验收

```bash
python3 scripts/verify_rules.py --workspace . --vs-template "$TPL"
python3 scripts/mark_rules.py check AGENT_RULES.md
python3 scripts/skill_audit.py --strict
python3 scripts/workspace_preflight.py
```

## 失败与回滚

| 失败特征 | 处置 |
|---|---|
| dry-run 报 `ref-not-found` | `--list-refs` 选对 ref；或先 `git fetch --all --tags` |
| `--apply` 后 `verify_rules` 报 `HASH-DIFF` | 说明工作区里有人改过模板层：重跑 `--apply`；要保留的语义写进 `*.custom.md` |
| `skill_audit` 报名称集合/TOOLS 未登记 | 回到第 3 步（`--sync-registry` + `--tools-snippet`） |
| 任何验收红 | 用 `.workspace/backup-<ts>-sync/` 的 MANIFEST 回滚，不要带漂移继续 |

回滚后必须重跑第 4 步确认回到基线。

## 验收（可被他人复跑）

```bash
python3 scripts/verify_rules.py --workspace . --vs-template "$TPL"   # 期望 clean: true
python3 scripts/skill_audit.py --strict                              # 期望 exit 0，0 findings
python3 scripts/mark_rules.py check AGENT_RULES.md                   # 期望 all clauses marked
python3 scripts/workspace_preflight.py                               # 期望 mismatch=0（warn 逐条解释）
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main   # 幂等：应 0 个文件
```

## 维护

- 版本历史见仓库 `CHANGELOG.md`；改动后跑 `python3 scripts/skill_audit.py --strict`。
- 本 skill 的步骤与 `UPGRADE.md` 必须保持一致：改一处即改另一处（`tests/test_docs_consistency.py` 会检查链接，人工复核措辞）。
