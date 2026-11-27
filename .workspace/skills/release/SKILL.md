---
name: release
description: "Use when any request asks to raise the policy version, create a release tag, publish WorkspaceTemplate, or record a dev iteration version (NN.X.Y.Z). 任何升政策版本、打 tag、发版、记 dev 四段版本的请求都必须使用。"
version: 1.1.0
---

# Release — 模板发版机械闭环

## 职责边界

- **归本 skill**：政策版本、CHANGELOG、bootstrap、生成物、账本和 tag 的顺序化发布。
- **不归**：push 与实例同步；二者由人工在本地事务全绿后执行。
- **不动**：remote、分支和提交历史；脚本绝不 push。

## 触发条件

- 进入：任何「升政策版本 / 打 tag / 发版」请求。
- **误触发例**：只改业务文档但不发版，应走对应开发流程。
- **漏触发例**：只说「把政策号改成 X.Y.Z」也属于发版，不得直接编辑。

## 前置事实（先读，不可跳过）

1. 工作树必须干净，`.workspace/releases.json` 末条必须是当前政策版本；否则阻塞。
2. 新版本必须是严格递增 semver；不成立时阻塞，不降级、不跳过。

## 输入输出契约

- 读：用户给出的版本与摘要、git 状态、政策头、bootstrap、发版账本。
- 写：仅由 `scripts/release.py` 写政策头、CHANGELOG、bootstrap、生成块、注册表、docs 索引和账本。
- 报告：dry-run 计划、11 项清单、人工 tag/push/实例同步命令。

## 标准流程（7 步，顺序固定）

```bash
.venv/bin/python -B scripts/release.py --version X.Y.Z --summary "…" --dry-run
# 人工确认后：
.venv/bin/python -B scripts/release.py --version X.Y.Z --summary "…" --apply
.venv/bin/python -B scripts/release.py --verify
.venv/bin/python -B scripts/release.py --tag X.Y.Z --apply
git push origin HEAD && git push origin vX.Y.Z
# 再按 template-update skill 从 vX.Y.Z 做实例同步
```

强制顺序：`--dry-run` → 人工确认 → `--apply` → `--verify` → `--tag --apply` → 人工 push → 实例同步。禁止手改版本号、CHANGELOG 条目或 `releases.json`；任一步失败立即停工，不许跳过继续。

## dev 发版（四段版本 `NN.X.Y.Z`）

dev 迭代有**自己的版本链**，与政策版本链物理分离：

| 链 | 版本形态 | 事实位 | 对账命令 |
|---|---|---|---|
| 政策（main） | 三段 `X.Y.Z` | `AGENT_RULES.md:4` / `CHANGELOG.md` 顶层 / `bootstrap.current_policy_version` / `.workspace/releases.json` 末条 | `scripts/version_check.py`、`scripts/policy_facts.py --check` |
| dev | 四段 `NN.X.Y.Z` | `CHANGELOG_DEV.md` 首个标题 / `.workspace/release_dev.json` 末条 | `scripts/release.py --dev --verify`、`scripts/policy_facts.py --check` |

- `NN` = 迭代轮次（从 1 起），`X.Y.Z` = 本轮**预计发布**的正式版本，必须等于当前政策版本的后三段。
  **dev 版本不是政策版本**：`AGENT_RULES.md` 头部、README 徽章、`version_check` 永远只认三段号。
- 空账本时的基线轮次是 44（首个 dev 版本 `44.3.8.0` 收录 R35–R44）。

```bash
.venv/bin/python -B scripts/release.py --dev --dry-run               # 派生本轮版本，不写盘
.venv/bin/python -B scripts/release.py --dev --apply --summary "…"   # 只写 CHANGELOG_DEV.md + release_dev.json
.venv/bin/python -B scripts/release.py --dev --verify                # 账本末条 == CHANGELOG_DEV 首条
.venv/bin/python -B scripts/release.py --dev --list                  # 打印 dev 账本
```

- 显式推进轮次：`--dev --round 45`（等价 `--dev --version 45.X.Y.Z`）；不给参数时派生
  `<账本末条轮次>.<政策版本>`，所以同一轮次二次 `--apply` 必被拒（版本只升不降）。
- `--dev --apply` **不要求工作树干净**（dev 账本是开发树里的中间产物），但仍会刷新
  生成块 / docs 索引 / 条款注册表（两个 dev 文件都在 `TEMPLATE_FILES` 里，不重建注册表会立刻 `HASH-DIFF`）。
- **绝不**写 `releases.json`、不改 `AGENT_RULES.md` / `bootstrap.json`、不打 tag、不 push。

## 发版前 dev/main 对账（5 项，逐条跑，任一不成立就不发）

1. `scripts/release.py --dev --verify` rc=0：dev 账本末条 == `CHANGELOG_DEV.md` 首个标题。
2. `scripts/policy_facts.py --check` ok：政策版本字面量只出现在生成块内；dev 双账本成对且递增。
3. `scripts/version_check.py` ok：政策头 / `CHANGELOG.md` 顶层 / bootstrap 仍是**同一个三段版本**（dev 没串味）。
4. `git log --oneline <dev 基线>..HEAD` 的每个提交都能在 `CHANGELOG_DEV.md` 对应轮次里找到落点
   （找不到 = 这轮改动没被记录，补条目而不是补提交信息）。
5. `scripts/check_all.py --check` 10/10：登记、生成物、索引、条款、版本五项全绿后再 `--full`。

## 失败与回滚

| 失败特征（exit code / 输出特征） | 处置（回滚到哪个状态 + 自验命令） |
|---|---|
| `--apply` 非零 | 脚本从临时备份恢复全部事务文件；跑 `git status --short`，必须回到执行前基线 |
| 回滚后仍有差异 | 停工；用 `git diff -- <文件>` 定位，仅恢复本次差异，再重跑 dry-run；不得手改三处发版事实 |
| tag 身份校验失败 | 脚本删除刚建的本地 tag；修复后从 `--verify` 重来，不得 push |

## 验收（命令 + 期望输出，可被他人复跑）

```bash
.venv/bin/python -B scripts/release.py --verify  # 期望 exit 0
.venv/bin/python -B scripts/release.py --list    # 期望末条版本等于政策头
.venv/bin/python -B scripts/release.py --dev --verify  # 期望 exit 0：dev 账本末条 == CHANGELOG_DEV 首条
```

## 维护

- 改本 skill 后：`python3 scripts/skill_audit.py --strict`；登记重排用 `--sync-registry`。
- 同步 `skills/REGISTRY.md` 与 `TOOLS.md`；版本历史见 `CHANGELOG.md`。
- 版本语义：PATCH=措辞；MINOR=新增步骤；MAJOR=改变发布边界。
