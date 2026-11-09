---
name: workspace-skill-authoring
description: "Use when the user asks to create or modify a workspace-level (local) skill in THIS workspace — 「给我建一个 skill」「把这个流程固化成技能」「改一下本地 skill」。不用于模板层 skill 的修改（那要回模板仓）。当用户明确要求新增/修改工作区级 skill 时使用。"
version: 1.0.0
---

# workspace-skill-authoring — 把用户要的能力落成工作区级 skill

只在**用户明确要求**时执行。产出一个 `.workspace/skills-local/<name>/SKILL.md`，并让它在三处登记、可被审计、可被回滚。

## 职责边界

- **归本 skill**：判定缺口 → 查重叠 → 命名与落盘 → 生成 8 维契约 → 三处登记 → 审批与状态 → 退役与墓碑。
- **不归**：修改模板层 skill（`.workspace/skills/*` 属模板层，实例里改了会被同步覆盖，见 R90）；修改政策条款本身（回模板仓）；替用户决定「要不要这个 skill」。
- **不动**：`AGENT_RULES.md`、`scripts/*`、`docs/*`、`.workspace/skills/*` 等模板层文件；`notes/` 与既有任务目录。

## 触发条件

- 进入：用户说「建一个 skill / 固化成技能 / 改这个本地 skill」，或他明确同意你提出的固化建议；
- **误触发例**：用户只是问「能不能做成 skill」但没让你建（先答，不动手）；要改的是模板层 skill（回模板仓）；要写的是 note（走 §9）。
- **漏触发例**：用户说「以后都按这个流程来」——隐含 skill 需求，先确认再动手。

## 前置事实（先读，不可跳过）

1. 本工作区必须是**模板派生工作区**：同时存在 `AGENT_RULES.md`、`.workspace/bootstrap.json`、`skills/REGISTRY.md`、`scripts/skill_audit.py`。
   缺任一项 → **停止**并说明：这不是 WorkspaceTemplate 派生工作区，本 skill 不适用（改用该仓库自己的约定，或先走 `workspace-init` 接管）。
2. 读 `.workspace/bootstrap.json` 的 `python.mode`；**动态取** `.workspace/skills/*/SKILL.md`（模板层）与 `.workspace/skills-local/*/SKILL.md`（本地层）全集，不写死数量。
3. 用户是否**明确要求**？没有明确要求 → 只给建议，不落盘（R129）。

## 输入输出契约

- 读：`AGENT_RULES.md`、`.workspace/bootstrap.json`、`skills/REGISTRY.md`、`TOOLS.md`、`.workspace/skills/*/SKILL.md` 的「职责边界」节、`.workspace/skills-local/**`。
- 写（逐项；别的一律不写）：
  - `.workspace/skills-local/_drafts/<name>/SKILL.md`（草稿）
  - `.workspace/skills-local/<name>/SKILL.md`（批准后）
  - `.workspace/skills-local/_retired/<name>/SKILL.md`（退役）
  - `skills/REGISTRY.md` 的「## 本地层入口」节（由 `skill_audit --sync-registry` 生成，不手写）
  - `TOOLS.md` 的本地 skill 生成区（`--tools-snippet` 给行，按标记块写入）
  - `.workspace/bootstrap.json` 的 `skills.registered_roots`（整根只加一次 `workspace-skills-local`）
- 报告：name、路径、状态、sha256、比对过的既有 skill、登记结果、`skill_audit --strict` 的 exit code。

## 标准流程（7 步，顺序固定）

### 第 1 步：缺口证据

记录：哪个真实任务缺这个能力、现有 skill（模板层 N + 本地层 M，动态取）为什么都不覆盖。
**没有真实缺口 → 停**，不要为「以后可能用」造 skill。

### 第 2 步：重叠检查（只读）

```bash
grep -n "^## 职责边界" -A 6 .workspace/skills/*/SKILL.md .workspace/skills-local/*/SKILL.md 2>/dev/null
```

- 与**本地层**某项重叠 → 改/合并那项，不新增。
- 与**模板层**某项重叠 → 停：在实例里改模板层会被同步覆盖（R90）。产出「模板变更提案」交回模板仓，不在此动手。

### 第 3 步：命名（强制 lowercase-kebab）

```bash
echo "<name>" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' || echo "非法：必须 lowercase-kebab"
```

目录名 = frontmatter `name`，两者必须一致；不允许大写、下划线、空格、点号。

### 第 4 步：写草稿

```bash
mkdir -p .workspace/skills-local/_drafts/<name>
# 正文按 .workspace/skills/_TEMPLATE.md 生成，8 节齐全
```

草稿进 `_drafts/`，**不进**可路由区：`status: draft`，并写 `proposed_by` / `created_at`。
当前任务可以**按显式路径**试跑草稿，但不得让其他任务按裸名触达。

### 第 5 步：生成 8 维契约

正文必须可被审计逐个定位：职责边界 / 触发条件 / 前置事实 / 输入输出契约 / 标准流程 / 失败与回滚 / 验收 / 维护。
命令要可直接复制；写清「事实不成立时：阻塞 / 降级 / n/a」。

### 第 6 步：转正 + 一处生成、三处登记

```bash
mv .workspace/skills-local/_drafts/<name> .workspace/skills-local/<name>   # 批准后
python3 scripts/skill_audit.py --sync-registry   # REGISTRY 本地节 + 计数（幂等，写后回读校验）
python3 scripts/skill_audit.py --tools-snippet    # TOOLS 缺行，按标记块写入
python3 scripts/skill_audit.py --strict           # 期望 exit 0
```

- 审批证据写在**同目录的 `APPROVAL.json`**（不要写进 SKILL.md frontmatter：把 sha256 写进被哈希的文件是自引用，永远对不上）：
  `{"approved_by": "<角色>", "approved_at": "<带时区的 ISO 时间>", "approved_skill_sha256": "<当前 SKILL.md 的 sha256>",
    "approved_directive": "<用户原话>"}`。
- `approved_skill_sha256` 必须等于**当前** SKILL.md 字节的 sha256；改一个字即失效，需重新批准。
- **主路径是用户直令**：`approved_directive` 逐字记用户那句要求，不要逼用户走二次审批；
  只有审批来自二席评审时，才用 `review_artifact`（工作区相对路径）替代 `approved_directive`。
- 撤销批准 = 删除 `APPROVAL.json`（skill 立即变为不可路由）。
- 目录移动、REGISTRY、TOOLS、bootstrap 根登记必须在**同一次提交**完成；半套状态会被 `--strict` 判红。

### 第 7 步：试用证据（诚实优先）

第一个本地 skill 的试用证据只能是「自举」——如实写 `trial_evidence: self-bootstrap`，**不算真实试用**，
把首次真实试用留给下一个任务。后续 skill 的试用证据必须来自真实任务（任务目录里有可复查输出）。
**不得**用「审计 exit 0」冒充试用。

## 失败与回滚

| 失败特征 | 处置 |
|---|---|
| `skill_audit --strict` 报缺节 / 未登记 | 补节或跑 `--sync-registry`；不要手改 REGISTRY |
| 名称非 kebab | 改名并同步 `name`；既有条目按退役处理 |
| `approved_skill_sha256` 与当前字节不符 | 视为未批准：退回草稿并重审 |
| 登记写了一半 | `git checkout -- skills/REGISTRY.md TOOLS.md .workspace/bootstrap.json` 后重跑第 6 步 |
| 要撤销某个本地 skill | 草稿直接删；已批准的移到 `.workspace/skills-local/_retired/<name>/` 并在 `_retired/TOMBSTONES.md` 记日期与取代者，再跑 `--sync-registry` |

回滚自验：回到基线后再跑 `python3 scripts/skill_audit.py --strict`，必须 exit 0。

## 验收（可被他人复跑）

```bash
python3 scripts/skill_audit.py --strict          # 期望 exit 0（模板层与本地层都 OK）
python3 scripts/skill_audit.py --sync-registry   # 期望 {"sync_registry":"ok","registry_problems":[]}
grep -n "skills-local/<name>/SKILL.md" skills/REGISTRY.md   # 期望命中本地层入口行
grep -n "<name>" TOOLS.md                                   # 期望一行登记（含 sha256 与日期）
```

## 维护

- 本 skill 属**模板层**：要改回模板仓（`~/Github/WorkspaceTemplate`），发版后再同步；实例里直改会被覆盖。
- 版本语义：PATCH=措辞/示例；MINOR=新增步骤或能力；MAJOR=改变职责边界或验收口径。
- 改动后跑 `python3 scripts/skill_audit.py --strict` 与 `python3 scripts/verify_rules.py --workspace . --vs-template <模板克隆>`。
