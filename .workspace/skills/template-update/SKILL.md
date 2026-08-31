---
name: template-update
description: "Use when updating a workspace instantiated from WorkspaceTemplate to the latest template version. 更新基于 WorkspaceTemplate 创建的工作区到最新模板版本时使用。"
version: 1.0.0
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
4. **版本号规则**：合并后 `AGENT_RULES.md` 版本行取**模板值**。工作区若有本地规范性扩展（如 ConchHelper 的 §1.1、§10），在版本头新增 `> YYYY-MM-DD [local-extension]：<描述>` 行注明，不占用模板版本号序列。
5. **并非所有工作区都由模板创建**：若 `bootstrap.json` 无 `template` 节，向用户确认来源后再继续；不往非模板工作区强行套用本流程。
6. **禁止事项**：不得删除本地任务目录、笔记、登记行；不得跳过版本头更新；未跑 check 脚本不得报告；备份不完整不得开始合并。

## 标准流程（六步，顺序固定）

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
    --template "$TEMPLATE_DIR" --workspace <本工作区绝对路径>
```

脚本输出 JSON：`template_version`、`workspace_version`、`base_version`（bootstrap 记录的基准）、逐文件 `verdict`（`in_sync` / `template_new` / `local_drift` / `drift` / `workspace_only`）与 `template_new_lines`（模板版本头新增行，即变更清单）。

### 第 3 步：备份

```bash
mkdir -p .workspace/backup-<yy-mm-dd> && \
cp AGENT_RULES.md AGENTS.md CLAUDE.md HERMES.md TOOLS.md .workspace/bootstrap.json .workspace/backup-<yy-mm-dd>/
```

备份不完整不得进入第 4 步。

### 第 4 步：按判定表逐文件处理

| 脚本 verdict | 处理 | 写盘后验证 |
|---|---|---|
| `in_sync` | 不动 | — |
| `template_new`（模板所有类） | 整文件 `cp` 替换 | `diff` 为空 |
| `template_new`（混合类） | 三方合并：模板新增行并入，本地专属行保留 | 重读确认本地行仍在、无重复版本行 |
| `local_drift` | 报告差异，按第 2 步的版本语义决定；默认模板行为基线、本地行为增量 | 同上 |
| `drift`（两边都改） | 逐 hunk 列给用户；PATCH/MINOR 报告后合并，MAJOR 逐项确认 | 同上 |
| `workspace_only` | 保留不动 | — |

合并 `AGENT_RULES.md` 的机械规则：版本头区（`>` 引用行）以模板为准逐行替换；本地扩展行以 `[local-extension]` 标记追加其后；正文章节按模板结构重排，模板没有而本地有的章节（如 §1.1、§10）原样保留并列入报告。

### 第 5 步：自举 + 状态回写

```bash
cp -r "$TEMPLATE_DIR/.workspace/skills/template-update" .workspace/skills/   # 刷新本 skill
```

更新 `.workspace/bootstrap.json`：`template.source_repo`、`template.current_policy_version` = 模板最新版本、`template.last_synced_at` = 今天。同时检查 `skills/REGISTRY.md` 是否已有 `workspace-skills` 命名空间下的 `template-update` 条目，没有则补登记。

### 第 6 步：验证与报告

```bash
<configured-python> notes/rebuild_index.py        # 索引脚本如被替换，跑通即验证
<configured-python> "$TEMPLATE_DIR/.workspace/skills/template-update/check_template.py" \
    --template "$TEMPLATE_DIR" --workspace .      # 重跑核验
```

重跑核验标准：**无 `template_new`**（模板增量已全部吸收）；剩余 `drift` 只允许已在报告中记录的本地适配（如本地人格节、更具体的记忆条款）；`template_new_lines` 为空。混合文件合并后哈希必然不同于模板，`drift` 本身不是失败，未记录的漂移才是。

报告格式：`结果 → 判定表 → 版本决策（PATCH/MINOR/MAJOR）→ 备份位置 → 前后版本号`。

## 陷阱

- 版本头是最容易合坏的地方：模板替换版本行后必须检查没有残留旧版本行、没有重复行。
- `HERMES.md` 在旧实例里可能不存在（模板 1.1.0 才加入）——`template_new` 时直接创建，不要当漂移处理。
- 工作区可能删过 README 换成自己的首页——`README*` 判定为 `drift` 时默认保留本地版本，仅在用户明确要求时才同步模板 README。
- 实例无 git（如 ConchHelper）时备份是唯一恢复点，第 3 步不可跳过。
