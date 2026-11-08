---
name: template-update
description: "Use when syncing a workspace's template layer from any template source revision (branch, tag, or commit) — not tied to one branch. 从任意模板来源（分支/标签/提交）更新工作区模板层时使用。"
version: 3.2.1
---

# Template Update — 工作区模板同步 skill（任意来源 ref）

把一个由模板实例化的工作区，更新到模板仓库**任意一个来源修订**：分支、标签或提交都行。
本 skill 定义**无歧义的机械流程**，Agent 只按流程执行，不做自由发挥。

**职责边界（先说清）**：本 skill 只处理**模板仓库**的来源与 ref——同步模板层文件、并在该模板源上选分支/标签/提交。
**工作区自身 git 仓库**的分支（创建后切到哪条、要不要迁移）由**用户自行维护**：本 skill 不切换、不迁移、不改写工作区仓库的分支或远程。

**Python 运行时不动**：同步只改 `bootstrap.template.*`，**不得**改写 `bootstrap.python`（`mode` / `runtime` / `version`）与 `.venv-*` 目录。`template_sync.py` 会回报 `python_mode_preserved`，必须为 `true`。

**v3.0 泛用化**：不再假定"追最新 main"，也不再枚举 `main/dev/alpha`。
`ref` 是一个参数，可以是 `main`、`release/2026`、`v3.1.0` 标签、或某个 commit sha；
**选哪个由用户决定**，未指定时沿用本工作区上次同步的 ref，再缺省 `main` 并明确写进报告。

**v2.0 架构不变（锁定 + custom 覆写）**：模板层文件**整文件替换**，哈希即完整性；本地规则一律外置到 `<file>.custom.md`（custom 优先，见 `AGENT_RULES.md` §10）。同步 = **替换 + 迁移 custom 引用**，没有行级三方合并。

## 旧工作区没有本 skill 的新版时（自举）

若工作区来自旧版本模板、`.workspace/skills/` 里没有本 skill 的 3.x 版本（或根本没有 `scripts/template_sync.py`），
**不要**按本文硬套：先按仓库根目录 [`UPGRADE.md`](../../../UPGRADE.md)（或 `.workspace/skills/legacy-workspace-upgrade/SKILL.md`）自举——
从模板快照取 `template_sync.py` 完成一次安装，再回到本 skill 做日常同步。

## 触发条件

- 用户要求"同步模板 / 更新工作区到某个版本 / 切到某个分支的模板 / update workspace template"；
- `bootstrap.template.current_policy_version` 与目标 ref 的版本不同，且用户同意同步；
- `verify_rules.py` 报告模板层漂移（HASH-DIFF / clause findings），需要从模板恢复。

## 前置事实（先读，不可跳过）

1. **来源是参数**：模板仓库地址取 `bootstrap.template.source_repo`，本地克隆取 `--template` → `TEMPLATE_DIR` → `bootstrap.template.local_clone` → `~/Github/WorkspaceTemplate`；都没有时按 `--clone-url`（或 `source_repo`）自动克隆。
2. **ref 是参数**：`--ref` 可给分支名、标签名或 commit sha；解析顺序 `<remote>/<ref>` → `<ref>`；不存在就停下并用 `--list-refs` 让用户选，**不猜**。
3. **文件清单唯一事实源**：`scripts/verify_rules.py` 的 `TEMPLATE_FILES`（数量随模板演进，**不要在任何文档里写死数字**）。第 4 步由它驱动，禁止另维护手工清单。
4. **分层**：
   - 模板层·锁定替换 = `TEMPLATE_FILES` 全集；
   - custom 层·永不触碰 = `*.custom.md`；
   - 工作区所有·永不触碰 = `WORKSPACE.md`、`TOOLS.md`、`notes/*.md` 正文、`skills/REGISTRY.md`、`bootstrap.json` 的本地值、`<task-slug>-*/`、一切用户文件。
5. **PATCH 的"静默"**指**不逐项确认**，不是"不写报告"：报告仍要列出改动文件与前后版本，只是不需要用户逐条批准。
5. **版本语义**：PATCH = 修复（静默合并）；MINOR = 新增（报告后合并）；MAJOR = 破坏性变更（逐项确认）。版本语义只影响**报告详略与确认粒度**，机械流程相同。
6. **条款 ID**：模板层条款带 `<!-- id:Rn -->`；`.workspace/rule-clauses.json` 是注册表（随模板分发）；custom 用 `overrides: R<id>` / `extends:` 引用。
7. **不是所有工作区都由模板创建**：bootstrap 无 `template` 节时先向用户确认来源，不强行套用。
8. **禁止**：改模板层文件内容；删 custom 或用户文件；未跑校验就报告；备份不完整就开始替换；把 `skills/REGISTRY.md` 整文件覆盖。

## 标准流程（七步，顺序固定）

### 第 1 步：确定来源与 ref

```bash
python3 "$TEMPLATE_DIR/scripts/template_sync.py" --workspace . --list-refs   # 看有哪些分支/标签
```

- `--ref` 缺省时，脚本按 `bootstrap.template.last_synced_ref` → `last_synced_branch` → `main` 解析，并在报告里写明"未指定 ref，沿用/回退到 X"。
- 跨来源（换 remote、换仓库）必须显式给 `--template` 或 `--clone-url`，不要悄悄用旧克隆。
- 本地克隆脏没问题：脚本用 `git worktree add --detach` 取目标提交快照，**不切分支、不动工作树**。

### 第 2 步：同步前校验（必跑，禁止凭印象）

```bash
<py> scripts/verify_rules.py --workspace .            # 工作区自身
<py> "$TEMPLATE_DIR/scripts/verify_rules.py" --workspace "$TEMPLATE_DIR" \
     --registry "$TEMPLATE_DIR/.workspace/rule-clauses.json"
```

- 工作区侧 `[file]/[clause]` 漂移 = 有人直接改了模板层文件 → 默认从模板恢复（v2.0 核心保证）。
- custom 层 findings（`overrides/extends` 指向不存在条款）→ 留到第 4.5 步与新版本一起裁决。

### 第 3 步：备份

```bash
BK=".workspace/backup-$(date +%Y%m%d-%H%M%S)-sync"; mkdir -p "$BK"
python3 - "$BK" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from verify_rules import TEMPLATE_FILES
bk = Path(sys.argv[1]); ws = Path(".")
manifest = []
for rel in TEMPLATE_FILES:
    src = ws / rel
    if src.is_file():
        (bk / rel).parent.mkdir(parents=True, exist_ok=True)
        (bk / rel).write_bytes(src.read_bytes())
        manifest.append(rel)
(bk / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
print(f"backed up {len(manifest)} files -> {bk}")
PY
```

脚本 `--apply` 会自动做同样的事，并写 `MANIFEST.txt`（`[replaced]` / `[added]` / `[preserved]` 三段，另备份 `*.custom.md` 与 `bootstrap.json`）。
**回滚用脚本，不手抄反向清单**：

```bash
python3 "$TEMPLATE_DIR/scripts/template_sync.py" --workspace . --rollback <备份目录>   # 手动回滚（失败时已自动执行）
```

它放回 `[replaced]`、删除 `[added]`、复原 `[preserved]`；只放回不删除会让新版多出来的文件留在工作区
（`verify_rules` 报 EXTRA）。

### 第 4 步：锁定替换（机械，无合并）

```bash
python3 "$TEMPLATE_DIR/scripts/template_sync.py" --workspace . --ref "$REF"            # dry-run
python3 "$TEMPLATE_DIR/scripts/template_sync.py" --workspace . --ref "$REF" \
        --apply --verify --json                                                         # 写盘；verify 不过会自动回滚
```

脚本输出（`--json` 可机器读）：`requested_ref / resolved_ref / ref_kind / commit`、版本前后、
`changed / skipped / identical` 文件、条款增删、`custom_dangling`、备份路径、`verify.clean`。

**上游删除的文件**：脚本会对比"上次同步点的 `TEMPLATE_FILES`"与本次清单，列出 `removed_upstream`。
默认**只报告不删除**；确认这些文件确属模板层后，加 `--prune` 才会删除本地副本（先备份到同一备份目录）。

- **README 处置**：默认"工作区 README 与上次同步点不同"即视为用户首页，自动保留并记入 `skipped`；`--keep-readme` 强制保留。
- **dry-run 与 apply 之间不得改动工作区**。
- 替换后如手工执行：逐文件 `diff` 模板原件，必须全空，否则回滚。

### 第 4.5 步：custom 引用迁移（唯一的"智能"环节）

对比新旧注册表条款 ID：

- 引用仍存在 → 不动；
- 引用失效 → **逐条报告用户**，给建议（改指新 ID / 该覆写已无必要 / 需人工重写），用户确认后才改 custom 文件；**永不静默丢弃 custom 语义**；
- 模板新增条款 → 列出，确认无 custom 冲突即可。

### 第 5 步：状态回写

脚本自动写 `bootstrap.template`：`current_policy_version`、`last_synced_at`、`last_synced_ref`、
`last_synced_branch`（仅当 ref 解析为分支/标签）、`last_synced_commit`。手工流程必须等价写回。
`template.source_repo` 只在首次接入时写。

### 第 6 步：同步后验证（三项全过）

```bash
<py> scripts/verify_rules.py --workspace . --vs-template "$TEMPLATE_DIR" --json /tmp/post.json
<py> notes/rebuild_index.py
```

- `clean: true` 必须来自 `--vs-template`（字节级比对 + 克隆 git 干净状态为信任锚）。
  克隆脏时 `[trust]` 发现即视为失败——此时改用 `git worktree` 快照目录做 `--vs-template`。
- README 被保留时报 `[vs-template] REPLACED`，进 `info` 不计 `clean`。
- 任一不过 → 用第 3 步备份回滚，回到第 2 步。

### 第 7 步：报告

`结果 → 来源（repo + ref + kind + commit）→ 漂移判定 → 替换/跳过清单 → custom 引用裁决 →
版本决策（PATCH/MINOR/MAJOR）→ 校验结果 → 备份位置 → 前后版本号`。

## 陷阱

- **别把 ref 写死成某个分支名**：模板可能同时有发布线、集成分支、实验线，选哪个是用户决策；skill 只保证"给什么 ref 就同步什么"。
- **别在文档里写死 `TEMPLATE_FILES` 条数**：数量跟着模板走，唯一事实源是脚本。
- **改了 `TEMPLATE_FILES` 内的文件后必须重建注册表**：`python3 scripts/mark_rules.py registry AGENT_RULES.md`，否则实例同步后 `verify_rules` 会报 `HASH-DIFF`（注册表里的哈希还是旧的）。
- **条款 ID 标记不得手改**：模板侧重排由 `mark_rules.py` 在模板仓库完成，实例只消费。
- **`skills/REGISTRY.md` 属工作区所有**：同步时只允许增补/更新 `workspace-skills` 命名空间条目，绝不整文件覆盖。
- **README 已替换为首页的工作区**：跳过 README 并在报告注明，否则会覆盖用户首页。
- **custom 文件的 `overrides:` 必须指向当前注册表的 ID**：写之前先查 `rule-clauses.json`。
- 实例无 git 时备份是唯一恢复点；`.custom.md` 必须包含在备份里。

## 维护

- 版本历史见仓库 `CHANGELOG.md`；本 skill 的版本按语义化规则递增（PATCH 措辞 / MINOR 能力 / MAJOR 边界）。
- 改动本 skill 后运行 `python3 scripts/skill_audit.py --strict`（契约节 + REGISTRY/TOOLS 双登记）。
