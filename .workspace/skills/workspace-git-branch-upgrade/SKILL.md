---
name: workspace-git-branch-upgrade
description: "Use when an existing workspace git repo must move from main to dev (or any target branch) without losing commits or breaking template sync. 把已有工作区仓库迁到 dev/目标分支时使用（含脏树、落后/分叉、回滚、模板校验）。"
version: 1.0.0
---

# Workspace Git Branch Upgrade — 工作区分支迁移 skill

把**已有** git 仓库从 `main` 迁到 `dev`（或任意目标分支），并保证模板同步、守门与记录三者一致。
**不负责**：建仓（用 `git-init`）、模板同步（用 `template-update`）、冲突仲裁（停下问用户）。

## 非目标（硬边界）

- 不自动 `push`、不自动建/删**远端**分支（`AGENT_RULES.md` §7 R63：版本控制动作仅凭用户当次明确要求）。
- 不自动 `rebase`/`merge` 冲突解决；分叉即有本地提交时**停下**，把两边的提交列给用户。
- 不改模板层锁定文件的判定语义。

## 第 0 步：记录基线（失败即停）

```bash
git rev-parse --abbrev-ref HEAD        # 原分支
git rev-parse HEAD                     # 原 HEAD
git status --porcelain                 # 脏树
git stash list                         # 已有 stash（回滚要用）
```

## 第 1 步：前置检查

| 检查 | 命令 | 判定与动作 |
|---|---|---|
| remote 指向 | `git remote -v` | 指向**模板库**（`WorkspaceTemplate`）→ 按 `AGENT_RULES.md` §0.3 R15 **暂停请用户裁决**，不自动迁 |
| 状态文件已忽略 | `git check-ignore -v .workspace/.hook-state/` | 未忽略 → 先把它加进 `.gitignore`（工作区自有文件） |
| 子模块 | `git submodule status` | 有输出 → 报告，不自动 `--recurse-submodules` |
| 仓库类型 | `python3 -c "import json;print(json.load(open('.workspace/bootstrap.json'))['git'])"` | `remote` 是模板库还是产物库；产物库默认建议留 `main`，但**用户明确要求迁移时按用户要求执行** |

## 第 2 步：迁移（三态，别用 `switch -C`）

**a) 远端没有 dev、本地也没有**

```bash
git switch -c dev          # HEAD 不变，未提交改动随工作树带过去（无需 stash）
```

**b) 远端有 dev、本地没有**

```bash
git fetch origin
git switch --track origin/dev     # 显式 track，不依赖自动推断
git branch -vv                    # 核对 upstream = origin/dev
```

**c) 本地已有 dev**

```bash
git switch dev
git merge-base --is-ancestor dev origin/dev && echo ancestor || echo diverged
# 仅落后（ancestor）：
git merge --ff-only origin/dev
# 分叉 / 有本地提交：停。给用户看两边：
git log origin/dev..dev --oneline      # 会被保留的本地提交
git log dev..origin/dev --oneline      # 将获得的远端提交
```

**禁止** `git switch -C dev origin/dev`：它会重置分支引用、静默丢弃本地提交，只在用户明确要求「丢弃本地 dev」时才用。

## 第 3 步：默认分支指针（可选，需用户同意）

```bash
git remote set-head origin -a     # 只改本地 origin/HEAD
```

它**不修改 GitHub 服务端默认分支**；要改服务端默认分支必须去仓库设置里做，本 skill 不自动执行。

## 第 4 步：模板同步校验

```bash
python3 scripts/verify_rules.py --workspace . --vs-template "<干净模板克隆>" --json /tmp/post.json
```

判据（三条同时满足，缺一不算通过）：

1. 退出码 `0`（`1` = 有 findings，按 `template-update` 从模板恢复；`2` = 参数/registry/IO 错）；
2. JSON `clean: true`；
3. JSON `template_clean: true` —— 脏文件不在 `TEMPLATE_FILES` 内时第 2 条仍可能为真，必须单独核对。

注意：**换分支可能检出不同树**（不是「字节永不变」）；模板层文件若因此变化，按 `template-update` 恢复，不仲裁。

## 第 5 步：记录回写（写文件 → 守门会要求先读 `AGENT_RULES.md`）

- `bootstrap.json` 的 `git.default_branch` 是「默认分支记录」，**不是当前检出分支**；只有用户要求改默认分支时才更新，并同步 `note` 里硬编码的 `branch=main` 文案。
- 不改 `initialized_at`；不把 `status` 从 `initialized` 改回 `unconfigured`。
- `TOOLS.md` 记一行：`分支迁移 main → dev（日期）`。

## 第 6 步：验证清单

```bash
git rev-parse --abbrev-ref HEAD     # = 目标分支
git status --porcelain              # 空（或只剩用户确认保留的改动）
git branch -vv                      # upstream 正确；没有 upstream 就如实写「未设」
git log --oneline -1                # HEAD 符合预期
python3 scripts/verify_rules.py --workspace . --vs-template "<干净模板克隆>"   # 见第 4 步三条判据
```

多克隆场景（模板开发克隆 + 产物克隆）必须核对 GitHub Desktop 打开的是**哪一个**、显示的分支是否为目标分支。

## 回滚

**未推送**

```bash
git switch <原分支>
git restore .workspace/bootstrap.json TOOLS.md   # 若第 5 步已写，先还原，否则会带到原分支
git branch -d dev                                # 用 -d，不用 -D
git stash list                                   # 若有 stash：确认它是在哪个分支打的
git stash apply stash@{n}                        # 用 apply 验证后再 drop
```

**已推送**：本地同上；删除远端 dev 必须用户当次明确要求，删前记录 `git log origin/dev -1` 的哈希。

## 与其他 skill 的关系

| 场景 | 用哪个 |
|---|---|
| 还没有 git 仓库 | `git-init` |
| 模板版本落后、需要同步文件 | `template-update` |
| 已有仓库要换分支（本 skill） | `workspace-git-branch-upgrade` |
| 首次初始化整仓（无血缘/老版本） | `workspace-init` |

## 已知坑

1. `git log a..b` 显示的是 **b 独有**的提交；要「将被丢弃的本地提交」应写 `git log origin/dev..dev`。
2. `switch -c` 不设 upstream；判据里若要求 upstream 正确，必须额外 `git branch --set-upstream-to=origin/dev dev`（`push -u` 属 R63，需用户授权）。
3. `git stash pop` 不分分支：stash 在 dev 上打、在 main 上 pop 会错位。
4. `origin/HEAD` 与 GitHub 服务端默认分支是两回事，改错方向会让 GitHub Desktop 显示旧分支。
