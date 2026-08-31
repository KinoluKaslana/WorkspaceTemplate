---
name: template-update
description: "Use when updating a workspace instantiated from WorkspaceTemplate to the latest template version. 更新基于 WorkspaceTemplate 创建的工作区到最新模板版本时使用。"
version: 1.1.0
---

# Template Update — 工作区模板同步 skill

把一个由 WorkspaceTemplate 实例化的工作区更新到模板最新版本。本 skill 定义**无歧义的机械流程**，Agent 只按流程执行，不做自由发挥。

## 触发条件

- 用户要求"同步模板 / 更新工作区到最新模板 / update workspace template"，**或**
- Agent 检查发现 `bootstrap.json` 的 `template.current_policy_version` 低于模板仓库最新 `AGENT_RULES.md` 版本，且用户同意同步。

## 前置事实（先读，不可跳过）

1. 本工作区由 WorkspaceTemplate 实例化，模板正主在 GitHub 仓库 `KinoluKaslana/WorkspaceTemplate`。
2. 模板升级按语义化版本处理：PATCH（1.2.0→1.2.1）= 错误修复，静默合并；MINOR（1.2.0→1.3.0）= 新增条款/文件，向用户报告新增内容后合并；MAJOR（1.x→2.0.0）= 破坏性政策变更，必须用户逐项确认后才合并。
3. **文件三分法**：
   - **模板所有**（整文件替换）：`notes/rebuild_index.py`、`notes/_TEMPLATE.md`、`scripts/inspect_skills.py`、`.workspace/skills/template-update/`（本 skill 自身）。
   - **混合**（三方合并）：`AGENT_RULES.md`（模板行进、本地行留）、`AGENTS.md`/`CLAUDE.md`/`HERMES.md`（模板行进、本地专属行留）、`TOOLS.md`（模板行进、本地登记行留）、`.workspace/bootstrap.json`（结构键更新、本地值保留）、`README.md`/`README_EN.md`（仅当工作区尚未替换为自己的 README）。
   - **工作区所有**（永不触碰）：`WORKSPACE.md`、`notes/*.md`（笔记正文）、`skills/REGISTRY.md`、`<task-slug>-*/` 任务目录、一切用户文件。
4. **基线快照（BASE）**：`.workspace/template-base/` 保存**上次同步时模板正主文件**的逐字快照（注意：是模板原件，不是合并结果）。它是三方合并的锚点——`本地改动 = diff(BASE, 工作区)`，`模板更新 = diff(BASE, 新模板)`。每次同步成功后必须刷新。禁止手改；快照与 `template.current_policy_version` 必须同版本。
5. **版本号规则**：合并后 `AGENT_RULES.md` 版本行取**模板值**（旧版本行被替换是预期行为，不算丢失本地内容）。工作区本地规范性扩展在版本头用 `> YYYY-MM-DD [local-extension]：<描述>` 行标记，不占用模板版本号序列。
6. **并非所有工作区都由模板创建**：若 `bootstrap.json` 无 `template` 节，向用户确认来源后再继续；不往非模板工作区强行套用本流程。
7. **禁止事项**：不得删除本地任务目录、笔记、登记行；不得跳过版本头更新；未跑 check 脚本不得报告；备份不完整不得开始合并；`dropped_local_lines` 未清零（或未逐条说明）不得宣布完成。

## 标准流程（七步，顺序固定）

### 第 1 步：定位模板源

```bash
# 本地已有模板克隆（推荐，避免联网）：
TEMPLATE_DIR=~/Github/WorkspaceTemplate   # 或用户指定的克隆位置
# 否则临时浅克隆：
git clone --depth 1 https://github.com/KinoluKaslana/WorkspaceTemplate.git /tmp/wt-update
```

### 第 2 步：只读检查（必跑脚本，禁止凭印象）

```bash
<configured-python> "$TEMPLATE_DIR/.workspace/skills/template-update/check_template.py" \
    --template "$TEMPLATE_DIR" --workspace <本工作区绝对路径> \
    [--base .workspace/template-base] --json /tmp/check-report.json
```

无 `--base` 时输出哈希判定与版本头差异（两方模式）。带 `--base` 时额外输出每个混合文件的**机械三方分析**：`local_only_lines`（本地新增行 = 必须存活的保护清单）、`conflict_regions`（本地与模板改了同一基线区域 = 真冲突，附基线行号与内容摘要）。

### 第 3 步：备份

```bash
mkdir -p .workspace/backup-<yy-mm-dd> && \
cp AGENT_RULES.md AGENTS.md CLAUDE.md HERMES.md TOOLS.md .workspace/bootstrap.json .workspace/backup-<yy-mm-dd>/
```

备份不完整不得进入第 4 步。备份目录是第 6 步不变量校验的"合并前现场"。

### 第 4 步：按判定表逐文件处理

| 脚本 verdict | 处理 | 写盘后验证 |
|---|---|---|
| `in_sync` | 不动 | — |
| `template_new`（模板所有类） | 整文件 `cp` 替换 | `diff` 为空 |
| `template_new`（混合类，如新建 HERMES.md） | 以模板为底创建 | 重读确认 |
| `drift` 且 `conflict_regions = 0` | **机械合并**：`git merge-file -p <工作区文件> <BASE对应文件> <新模板文件> > merged && mv merged <工作区文件>`；退出码 0 = 无冲突干净合并 | 重读确认本地行仍在、无重复版本行 |
| `drift` 且 `conflict_regions > 0` | **冲突处理**（见下方冲突决策表） | 同上 + 冲突记录 |
| `workspace_only` | 保留不动 | — |

`git merge-file` 不可用时回退：按第 2 步报告的 `local_only_lines` / 模板新增行做手工行级合并——本地新增行一条不落地保留，模板新增行逐条并入，两份清单都来自脚本而非印象。

**冲突决策表**（`conflict_regions > 0` 时逐区域套用）：

| 冲突类型 | 判定特征 | 处理 |
|---|---|---|
| A. 同处修改、语义相容 | 本地与模板改了同一节但表达同一约束（如 §9.1 两种措辞） | 向用户并列展示两版，推荐"模板结构 + 本地更具体措辞"的融合稿；用户说"你定"时按此融合并在报告记录 |
| B. 编号/结构碰撞 | 模板新增节号与本地扩展节号撞车（如都有 §10） | 本地节顺延重编号，版本头 `[local-extension]` 行更新节号引用；语义不动 |
| C. 语义冲突 | 本地与模板对同一行为规定相反（如授权范围互斥） | **不自动合并**；保留本地版本，把模板条款写进 `template.pending_conflicts`（bootstrap），请用户裁决；模板 MAJOR 版本时全部冲突升级为逐项确认 |

任何冲突类型下，用户不在场或未裁决时：该文件**不合并**，保持本地现状，记入 `pending_conflicts`，其余无冲突文件照常同步。

### 第 5 步：自举 + 基线刷新 + 状态回写

```bash
cp -r "$TEMPLATE_DIR/.workspace/skills/template-update" .workspace/skills/                 # 刷新本 skill
mkdir -p .workspace/template-base && cd "$TEMPLATE_DIR" && \
cp --parents AGENT_RULES.md AGENTS.md CLAUDE.md HERMES.md TOOLS.md README.md README_EN.md \
   notes/rebuild_index.py notes/_TEMPLATE.md scripts/inspect_skills.py \
   "$OLDPWD/.workspace/template-base/"                                                    # 刷新 BASE（模板原件）
```

更新 `.workspace/bootstrap.json`：`template.current_policy_version` = 模板最新版本、`last_synced_at` = 今天；有未决冲突时写 `template.pending_conflicts`（文件、区域、摘要、日期）。检查 `skills/REGISTRY.md` 是否已有 `workspace-skills` 命名空间下的 `template-update` 条目，没有则补登记。

### 第 6 步：验证（三项全过才算完）

```bash
# 6a. 索引脚本如被替换，跑通即验证
<configured-python> notes/rebuild_index.py
# 6b. 重跑 check（带 BASE 与备份现场）：模板增量必须吸干，本地不变量必须存活
<configured-python> .workspace/skills/template-update/check_template.py \
    --template "$TEMPLATE_DIR" --workspace . \
    --base .workspace/template-base --verify-backup .workspace/backup-<yy-mm-dd> --json /tmp/verify.json
```

- 6b 判定：`template_new` = 无；`template_new_lines` = 0；`dropped_local_lines` = **空**（版本头旧行按规则被替换会单独列在 `expected_drops`，属预期，不计失败）；`conflict_regions` 只允许已在第 4 步处理并记录的区域。
- 任一不满足 → 用备份回滚该文件，回到第 4 步重做。

### 第 7 步：报告

格式：`结果 → 判定表 → 冲突清单与裁决 → 版本决策（PATCH/MINOR/MAJOR）→ 不变量校验结果 → 备份位置 → 前后版本号`。

## 陷阱

- **BASE 必须存模板原件**：存成合并结果会让下次同步把本地改动当成"基线"，本地扩展会被静默洗掉。
- 版本头是最容易合坏的地方：模板替换版本行后必须检查没有残留旧版本行、没有重复行。
- `dropped_local_lines` 里的版本头旧行（匹配"模板政策版本"的行）属预期替换；除此之外的任何丢失都要恢复。
- `HERMES.md` 在旧实例里可能不存在（模板 1.1.0 才加入）——`template_new` 时直接创建，不要当漂移处理。
- 工作区可能删过 README 换成自己的首页——`README*` 判定为 `drift` 时默认保留本地版本，仅在用户明确要求时才同步模板 README。
- 实例无 git（如 ConchHelper）时，备份目录是唯一恢复点，第 3 步不可跳过。
