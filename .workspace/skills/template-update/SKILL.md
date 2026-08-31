---
name: template-update
description: "Use when updating a workspace instantiated from WorkspaceTemplate to the latest template version. 更新基于 WorkspaceTemplate 创建的工作区到最新模板版本时使用。"
version: 2.0.0
---

# Template Update — 工作区模板同步 skill

把一个由 WorkspaceTemplate 实例化的工作区更新到模板最新版本。本 skill 定义**无歧义的机械流程**，Agent 只按流程执行，不做自由发挥。

**v2.0 架构（锁定 + custom 覆写）**：模板规则文件是**锁定只读**的（整文件替换，哈希即完整性），本地规则一律外置到 `<file>.custom.md`（custom 优先，见 AGENT_RULES §10）。同步因此不再做行级三方合并——**替换 + 迁移 custom 引用**就是全部。

## 触发条件

- 用户要求"同步模板 / 更新工作区到最新模板 / update workspace template"，**或**
- `bootstrap.json` 的 `template.current_policy_version` 低于模板仓库最新 `AGENT_RULES.md` 版本，且用户同意同步，**或**
- `verify_rules.py` 报告模板层文件漂移（HASH-DIFF / clause findings），需要从模板恢复。

## 前置事实（先读，不可跳过）

1. 模板正主在 GitHub 仓库 `KinoluKaslana/WorkspaceTemplate`；实例的模板层文件 = 模板原件（逐字节一致），custom 层文件（`*.custom.md`）属工作区所有、同步永不触碰。
2. 模板升级按语义化版本处理：PATCH = 修复（静默合并）；MINOR = 新增（报告后合并）；MAJOR = 破坏性变更（逐项确认）。版本语义只影响**报告详略与确认粒度**，同步动作本身是同一个机械流程。
3. **文件分类（v2.0）**：
   - **模板层·锁定替换**：`AGENT_RULES.md`（含条款 ID 标记）、`AGENTS.md`、`CLAUDE.md`、`HERMES.md`、`README.md`、`README_EN.md`、`notes/rebuild_index.py`、`notes/_TEMPLATE.md`、`scripts/inspect_skills.py`、`scripts/mark_rules.py`、`scripts/verify_rules.py`、`.workspace/skills/template-update/`、`.workspace/skills/workspace-init/`、`.workspace/skills/git-init/`、`.workspace/rule-clauses.json`。**该清单的唯一事实源是 `scripts/verify_rules.py` 的 `TEMPLATE_FILES`（当前 16 项）；第 4 步替换命令直接由它驱动，禁止另维护一份手工清单（手工清单漂移是「校验失败 → 回滚」死循环的根因，见「陷阱」）。**
   - **custom 层·永不触碰**：`*.custom.md`（AGENT_RULES.custom.md、HERMES.custom.md 等）。
   - **工作区所有·永不触碰**：`WORKSPACE.md`、`TOOLS.md`（本地登记为主；模板仅提供格式节）、`notes/*.md` 笔记正文、`skills/REGISTRY.md`、`.workspace/bootstrap.json` 的本地值、`<task-slug>-*/`、一切用户文件。
4. **条款 ID**：模板层条款带 `<!-- id:Rn -->` 标记；`.workspace/rule-clauses.json` 是条款注册表（随模板分发）。custom 条款用 `overrides: R<id>` 覆写或 `extends: R<id>` 扩展模板条款。
5. **并非所有工作区都由模板创建**：bootstrap 无 `template` 节时先向用户确认来源，不强行套用。
6. **禁止事项**：不得修改模板层文件内容（同步=整文件替换）；不得删除 custom/工作区文件；未跑校验不得报告；`.workspace/backup-<date>/` 不完整不得开始替换。

## 标准流程（七步，顺序固定）

### 第 1 步：定位模板源

```bash
TEMPLATE_DIR=~/Github/WorkspaceTemplate   # 本地克隆；否则临时浅克隆
```

### 第 2 步：同步前校验（必跑，禁止凭印象）

```bash
<py> scripts/verify_rules.py --workspace .    # 在工作区跑（用模板最新 scripts）
<py> "$TEMPLATE_DIR/scripts/verify_rules.py" --workspace "$TEMPLATE_DIR" --registry "$TEMPLATE_DIR/.workspace/rule-clauses.json"
```

- 工作区侧发现 [file]/[clause] 漂移 → 属"用户直接改了模板层文件"：按 findings 逐条报告用户，**默认从模板恢复该文件**（这正是 v2.0 的核心保证——不会误判为同步冲突，因为冲突只可能存在于 custom 层引用）。
- custom 层 findings（overrides/extends 指向不存在条款）→ 留到第 5 步随模板版本变化一起处理。

### 第 3 步：备份

```bash
mkdir -p .workspace/backup-<yy-mm-dd> && cp AGENT_RULES.md *.custom.md AGENTS.md CLAUDE.md HERMES.md TOOLS.md .workspace/bootstrap.json .workspace/backup-<yy-mm-dd>/ 2>/dev/null; true
```

### 第 4 步：锁定替换（机械，无合并）

替换清单的唯一事实源 = `scripts/verify_rules.py` 的 `TEMPLATE_FILES`（16 项）。**由它直接驱动拷贝，不要手写文件清单**：

```bash
cd "$TEMPLATE_DIR" && <py> - "$OLDPWD" <<'PY'
import shutil, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from verify_rules import TEMPLATE_FILES
dst = Path(sys.argv[1])
copied = []
for rel in TEMPLATE_FILES:
    src = Path(rel)
    if src.is_file():
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst / rel)
        copied.append(rel)
print(f"replaced {len(copied)}/{len(TEMPLATE_FILES)} files per TEMPLATE_FILES")
PY
```

写盘后立即 `diff` 每个文件与模板原件——必须全空，否则回滚重做。（README 仅当工作区未替换为自己的首页；已替换则跳过并在报告注明。）

### 第 4.5 步：custom 引用迁移（唯一的"智能"环节）

对比新旧注册表的条款 ID：模板若**删除/改写**了某条款（新注册表缺该 ID 或文本变化），检查所有 `*.custom.md` 中指向它的 `overrides:`/`extends:`：

- 引用仍存在 → 不动；
- 引用失效 → **逐条报告用户**：模板条款没了/变了，custom 覆写的目标需要迁移。给出建议（改指向新条款 ID，或该覆写已无必要可删除），用户确认后修改 custom 文件。custom 语义**永不静默丢弃**。
- 模板新增条款 → 列出，确认无 custom 冲突即可（custom 优先原则天然处理）。

### 第 5 步：状态回写

更新 `.workspace/bootstrap.json`：`template.current_policy_version` = 模板版本、`last_synced_at` = 今天；首次接入的写 `template.source_repo`。

### 第 6 步：同步后验证（三项全过）

```bash
<py> scripts/verify_rules.py --workspace . --vs-template "$TEMPLATE_DIR" --json /tmp/post.json   # 权威校验：字节级比对+git 信任锚
<py> notes/rebuild_index.py                                                                      # 索引脚本跑通
```

- `verify_rules.py` 必须 `clean: true`——**必须带 `--vs-template`**：本地注册表可被重生成（自我作证漏洞），只有对模板克隆的字节级比对 + 克隆自身的 git 干净状态（信任锚：GitHub 对象哈希 → 克隆 HEAD → 工作区逐字节相等）才是权威判定。克隆脏（含未提交改动）时 `[trust]` 发现即视为校验失败。README 豁免：工作区替换为自己的首页时报 `[vs-template] REPLACED`（进入 `info`，**不计入 `clean`**）；`clean` 只看 `findings`。
- 任一不过 → 用备份回滚全部替换文件，回到第 2 步。

### 第 7 步：报告

`结果 → 漂移判定 → 替换清单 → custom 引用迁移裁决 → 版本决策（PATCH/MINOR/MAJOR 报告详略）→ 校验结果 → 备份位置 → 前后版本号`。

## 冲突哲学（v2.0）

冲突只在两处出现，且都有确定答案：

1. **模板层被直接改过**（verify 报 drift）→ 不仲裁，从模板恢复；用户真想保留的语义 → 写进 custom。
2. **custom 引用失效**（模板条款删除/改写）→ 报告 + 用户裁决迁移，不自动改写。

行级三方合并已随 v1.x BASE 机制退役；如遇到 v1.x 时代工作区（存在 `.workspace/template-base/`），先按本 skill 重建（把当时本地扩展迁入 custom 文件，模板层还原为模板原件），再执行同步。

## 陷阱

- 条款 ID 标记是注册表的锚：**任何人不得手改 `<!-- id:Rn -->`**；模板侧 ID 重排由 mark_rules.py 在模板仓库内完成，实例侧只消费。
- **模板层文件清单唯一事实源 = `TEMPLATE_FILES`**：第 4 步拷贝、§3 分类、`verify_rules.py` 三者共用这一份清单。给模板新增/删除模板层文件时，只改 `scripts/verify_rules.py` 的 `TEMPLATE_FILES`，然后重跑 `mark_rules.py mark AGENT_RULES.md` 刷新注册表——第 4 步会自动跟着变，无需（也不允许）再改 skill 正文里的清单。
- **`skills/REGISTRY.md` 属工作区所有**：同步时只允许**增补/更新 workspace-skills 命名空间条目**，绝不整文件覆盖——本地 skills/localskills 登记行是用户资产，覆盖即数据丢失（已发生过一次事故）。
- `check_template.py`（v1 时代的判定脚本）仍用于文件级对比，但混合文件不再做行级合并；判定为 `drift` 的模板层文件在 v2.0 语义下就是"待恢复"，不是"待合并"。
- custom 文件里的 `overrides:` 必须指向当前注册表存在的 ID；写 custom 时先查 `rule-clauses.json`。
- README 已替换为本地首页的工作区，同步时跳过 README（报告注明），否则会覆盖用户首页。
- 实例无 git 时备份是唯一恢复点；`.custom.md` 必须包含在备份里（它们是用户语义所在））。
