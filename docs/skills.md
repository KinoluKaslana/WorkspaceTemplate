# Workspace skills（8 个）

> 实现说明，不构成政策。义务与判定标准见 [`AGENT_RULES.md`](../AGENT_RULES.md)；本页只讲**每个 skill 干什么、什么时候进、失败怎么办**。

## 0. 一览

| skill | 版本 | 一句话 | 进入条件 |
|---|---|---|---|
| [`template-update`](../.workspace/skills/template-update/SKILL.md) | 3.2.1 | 从**任意 ref**（分支/标签/提交）同步模板层 | 用户要同步模板；bootstrap 版本落后；`verify_rules` 报漂移 |
| [`workspace-repo-sync`](../.workspace/skills/workspace-repo-sync/SKILL.md) | 1.1.2 | 跨设备同步**工作区自身**仓库 | 换设备；别端改了 notes/TOOLS；工具疑似失效 |
| [`workspace-init`](../.workspace/skills/workspace-init/SKILL.md) | 1.3.0 | 初始化/接管老版本或半初始化工作区 | 无 bootstrap 血缘；无条款标记；全新实例化 |
| [`git-init`](../.workspace/skills/git-init/SKILL.md) | 1.2.0 | 为工作区建 git 仓库并登记 | `git.status=declined` 后用户改主意；"建 git 仓库" |
| [`delivery-closure`](../.workspace/skills/delivery-closure/SKILL.md) | 1.1.0 | 声明完成前的交付闭环 | 任务模式产物落盘、准备说"完成" |
| [`docs-retrieval-maintenance`](../.workspace/skills/docs-retrieval-maintenance/SKILL.md) | 1.3.0 | 维护 docs 索引与别名表 | docs 改动、`check` 报 stale、命中率下降 |
| [`workspace-skill-authoring`](../.workspace/skills/workspace-skill-authoring/SKILL.md) | 1.0.0 | 把用户要的能力落成**工作区级** skill（含 kebab 命名、审批证据、三处登记） | 用户明确要求新建/修改本地 skill |
| [`legacy-workspace-upgrade`](../.workspace/skills/legacy-workspace-upgrade/SKILL.md) | 1.0.0 | 旧版本工作区**自举**升级（缺现行 skill 时） | 工作区模板版本落后且没有 `template-update` 3.x / `template_sync.py` |

**职责边界口诀**：`template-update` 管模板文件；`workspace-repo-sync` 管工作区仓库；`workspace-init`/`git-init` 管从零落地；`delivery-closure` 管收尾；`docs-retrieval-maintenance` 管检索。工作区自身仓库的**分支切换、迁移、历史改写**不在任何 skill 里——**用户自管**。

## 1. 8 维契约（所有 skill 必须齐）

新 skill 照 [`.workspace/skills/_TEMPLATE.md`](../.workspace/skills/_TEMPLATE.md) 写，`scripts/skill_audit.py` 会机械检查：

| 维度 | 要求 |
|---|---|
| 触发 | 进入条件 + 误触发例 + 漏触发例 |
| 前置 | 执行前必须成立的事实；不成立时 阻塞/降级/n.a. 三选一写明 |
| 契约 | 读什么、写什么（逐项路径）、报告哪些字段 |
| 边界 | 不做什么；转交给哪个 skill |
| 执行 | 命令可复制；参数来源明确 |
| 失败与回滚 | 失败特征（exit/输出）+ 回滚目标 + 回滚自验 |
| 验收 | 命令 + 期望输出，可被他人复跑 |
| 维护 | 版本语义、REGISTRY/TOOLS 双登记、`--sync-registry` |

`skill_audit.py` 缺任何一节 → `findings`；`--strict` 下 exit 1，可直接进验收链。

## 2. 七个 skill 的细节

### 2.1 `template-update`

```bash
python3 scripts/template_sync.py --workspace . --list-refs        # 看有哪些分支/标签
python3 scripts/template_sync.py --workspace . --ref alpha        # dry-run
python3 scripts/template_sync.py --workspace . --ref alpha --apply --verify --json
```

- `--ref` 接受分支 / 标签 / commit；缺省按 `bootstrap.template.last_synced_ref/branch` → `main`。
- `--prune` 删除"上游已移除"的模板层文件（先备份；默认只报告）。
- 备份含 `MANIFEST.txt`；**回滚 = 放回旧文件 + 删除本次新增**。
- 铁律：只改 `bootstrap.template.*`，**不动** `bootstrap.python`（`python_mode_preserved=true`）。

### 2.2 `workspace-repo-sync`

```bash
python3 scripts/workspace_preflight.py            # 11 项登记事实核验（先跑）
git fetch --all --prune && git status -sb         # 只读看清差异
# 由用户决定：方向 / ff|merge|rebase / 冲突处置
```

- 无 `.git` 或 `git.status=declined` → **不做任何 git 步骤**，只做预检与记录。
- 冲突回滚：`merge --abort` / `rebase --abort`；`reset --hard` 仅在用户明确同意、工作树干净时。
- 拉后复验：`notes/rebuild_index.py` → `docs_index build`（有 docs 时）→ `verify_rules` → `skill_audit` → `selfcheck` → `preflight`。

### 2.3 `workspace-init`

- 检测版本：`grep -c 'id:R[0-9]' AGENT_RULES.md`（**全文件计数**；早期版本用 `head -20`，对现行模板必然误判）。
- 备份集必须 ⊇ 替换集（读 `TEMPLATE_FILES`），空备份即停。
- Python：**已配置就保持**（不提问、不重建、不切 uv）；`unconfigured` 才三选一（默认 uv）。
- 登记核验：入口集合 = `.workspace/skills/*/SKILL.md` 目录集合，用 `skill_audit --sync-registry`。

### 2.4 `git-init`（1.2.0)

顺序固定：`git init` → `.gitignore` 核验 → bootstrap/TOOLS 登记 → **审查暂存清单** → 提交 → 核提交树。
撤销建仓：先导出 `git format-patch`，再 `rm -rf .git`，bootstrap 回写 `declined`。

### 2.5 `delivery-closure`

- 产物清点：有仓用 `git status --short`；**无仓必须改用文件时间戳列举**（`git status` 空 ≠ 无改动）。
- 第 5 步外部动作逐项机械验证；`docs/*.md` 改动走 `docs-retrieval-maintenance` 的五条验收。
- 三档口径：**完成**（全绿）/ **已交付带限制**（披露且用户接受）/ **阻塞**（P0 失败）。

### 2.6 `docs-retrieval-maintenance`

```bash
python3 scripts/docs_index.py build && python3 scripts/docs_index.py check
python3 scripts/docs_hit_report.py --min 9        # 判据集 tests/docs_probe.json
python3 scripts/docs_alias_audit.py --strict      # 死指针/指纹/来源/留一验证
```

- 自主分级：索引全自动；**别名新增只认 `docs/.miss-log.jsonl` 的真实查询缺口**；重指/删除/改阈值需人工。
- 「停用 / 删除 / 重指」三档分开，不得混用。
- 实测结论：省**账单**不省 **token**（见 [`docs-retrieval.md`](docs-retrieval.md)）。

### 2.7 `legacy-workspace-upgrade`（1.0.0）

工作区来自旧模板、`.workspace/skills/` 里没有现行 skill 时的**唯一入口**（没有它就只能靠人读文档）：

```bash
TPL=$(mktemp -d)/tpl
git clone --depth 1 --branch main https://github.com/KinoluKaslana/WorkspaceTemplate.git "$TPL"
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main          # dry-run
python3 "$TPL/scripts/template_sync.py" --workspace . --template "$TPL" --ref main --apply  # 写盘 + 备份
python3 notes/rebuild_index.py
python3 scripts/skill_audit.py --sync-registry
python3 scripts/skill_audit.py --tools-snippet      # 按输出补 TOOLS.md
```

随后四条验收（`verify_rules --vs-template` / `mark_rules check` / `skill_audit --strict` / `workspace_preflight`）。
与 `workspace-init` 的分界：**有模板血缘但版本旧** → 本 skill；**无血缘/pre-v2.0 接管** → `workspace-init`。
完整说明见仓库根 [`UPGRADE.md`](../UPGRADE.md)。

## 3. 组合流程（常见路径）

| 用户目标 | 顺序 |
|---|---|
| 新工作区落地 | §0 Python 三选一 → `git-init`（可选）→ `delivery-closure` 收尾 |
| 老工作区升级 | `workspace-init`（迁移）→ `template-update --apply` → `verify_rules` |
| 模板换分支 | `template-update --ref <ref>` → `verify_rules --vs-template` |
| 换台机器继续 | `workspace-repo-sync`（预检 → 拉 → 重建索引 → 复验） |
| 改完 docs | `docs-retrieval-maintenance`（build → hit → audit）→ `delivery-closure` |
| 老工作区升上来 | `legacy-workspace-upgrade`（自举）→ 之后改用 `template-update` |

## 4. 维护

```bash
python3 scripts/skill_audit.py                    # 契约/双登记/计数
python3 scripts/skill_audit.py --sync-registry     # 按目录重写 REGISTRY 入口表
```

触发时机：政策 MAJOR/MINOR 落地后、模板同步后、新增/改名 skill 后、每季度或发布前。审计只判机械项，**触发语是否真的在对的时机命中**仍需人工复核。

