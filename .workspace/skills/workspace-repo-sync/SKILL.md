---
name: workspace-repo-sync
description: "Use when syncing the workspace's own git repo across devices (notes/TOOLS/artifacts changed elsewhere), or when recorded tool paths may belong to another machine. 跨设备同步工作区自身仓库时使用：先核验登记事实与工具真实性，再让用户裁决差异。"
version: 1.0.0
---

# Workspace Repo Sync — 工作区自身仓库的跨设备同步

在多台设备（不同主机名、不同根路径、不同 venv）之间同步**工作区自己的 git 仓库**——
`notes/`、`TOOLS.md`、`skills/REGISTRY.md`、业务产物都会在另一端变化。

**这类同步必须先核验、后动手**：记录里的路径与工具可能来自**另一台设备**，直接拿来用会让
`verify_rules`、`docs_index`、hooks、skills 全部静默失效，而报错看起来像"仓库坏了"。

## 职责边界

| 归本 skill | 不归本 skill |
|---|---|
| 工作区自身仓库的 fetch/pull/push（**用户明确要求时**） | 模板层文件同步 → `template-update` |
| 拉取后重建索引、复验工具与登记 | 工作区仓库的分支切换/迁移/历史改写 → **用户自管** |
| 核验「登记事实 ↔ 本机实际」并在用户裁决后回写 | 建仓 → `git-init`；初始化 → `workspace-init` |

**硬规则**：不 `push --force`；不自动解决 `TOOLS.md`/`notes/`/`WORKSPACE.md` 的语义冲突；
不改写另一台设备的登记行。

## 第 0 步：预检（必跑，先看再动）

```bash
python3 scripts/workspace_preflight.py            # 人读
python3 scripts/workspace_preflight.py --json     # 机器读
```

输出 9 项：`host / workspace_root / python / template_clone / skills / template_files / notes_index / docs_index / git`，
每项带 `recorded / actual / status / action`。判定：

| status | 含义 | 动作 |
|---|---|---|
| `mismatch` | 登记与实机冲突（路径、venv、remote、skill 缺失） | **阻塞**：先与用户裁决，不进入同步 |
| `warn` | 可继续，但需记录（本机未登记、notes 索引缺失、无 .git） | 记录并继续，同步后补 |
| `n/a` | 不适用（MCP 模式、无 docs/） | 跳过 |

### 设备本地事实 vs 共享事实（裁决依据）

| 类别 | 例子 | 处理 |
|---|---|---|
| **设备本地** | 工作区根路径、venv 目录、模板克隆路径、HERMES_HOME、插件是否启用 | **按主机分列登记**（`TOOLS.md` §1 每机一行）；同一文件里不同主机行互不改写 |
| **共享** | `bootstrap.git.remote`、`source_repo`、policy 版本、skill 版本、`notes/` 内容 | 必须一致；不一致即 mismatch，用户裁决后回写 |

> 已知坑：把设备本地值写成**单值**，会被两台设备来回"校正"，每次同步互相覆盖（本仓 TOOLS §1 有过实例）。
> 正确做法是按主机名分列，或在 note 里记录"本机覆盖值"。

## 第 1 步：看清差异（只读）

```bash
git fetch --all --prune
git status -sb                 # 当前分支 / ahead-behind / 脏文件
git log --oneline HEAD..@{u}    # 远端新提交
git diff --stat @{u}...HEAD     # 本地待推
```

把结果分三类摆给用户：

1. **只读产物**（notes、报告、任务目录）——可直接拉；
2. **共享登记**（`TOOLS.md`、`skills/REGISTRY.md`、`bootstrap.json`、`WORKSPACE.md`）——**逐项问**，因为这些必冲突；
3. **本机专属**（venv、备份目录、`.hook-state`）——不应进仓库（若进了，先确认 `.gitignore`）。

## 第 2 步：让用户决定（不替用户做主）

必须明确问到的三件事：

1. **同步方向**：只拉、只推、还是双向？
2. **分叉策略**：能 `--ff-only` 就 ff；否则 merge（保留历史）还是 rebase（线性）——由用户选，**不默认**；
3. **冲突处置**：登记类文件逐段确认；内容文件（notes）由用户决定保留哪版或合并。

## 第 3 步：执行同步

```bash
git pull --ff-only            # 首选
# 若被拒且用户选 merge：git pull --no-rebase
# 若用户选 rebase：git pull --rebase
```

- 冲突时**停下**，逐文件列出冲突块，按第 2 步裁决人工合并；不 `--ours/--theirs` 一把梭。
- 任何情况下不 `push --force`；确实需要改写历史时，明确告诉用户后果并让用户执行。

## 第 4 步：拉后重建与复验（缺一不可）

```bash
python3 notes/rebuild_index.py                 # notes 索引（R72）
python3 scripts/docs_index.py build            # 仅当 docs/ 存在
python3 scripts/verify_rules.py --workspace .  # 模板层完整性
python3 scripts/skill_audit.py --strict        # skills 登记与引用
python3 .hermes/plugins/workspace-gate/selfcheck.py   # 守门插件
python3 scripts/workspace_preflight.py         # 再跑一次预检
```

## 第 5 步：推送（仅用户要求）

```bash
git fetch --all --prune        # 推前再确认没有新提交
git status -sb
git push origin <branch>       # 不用 --force
```

## 第 6 步：记录回写（只写本机相关）

- `TOOLS.md` §1：本机行改成实测值，核验日期更新；**其他主机行原样保留**。
- `bootstrap.json`：只改设备无关的共享事实（remote、policy 版本）；`git.default_branch` 是记录，不是当前分支。
- 发现登记本身有误（如 remote 写错）→ 报告用户并给出改法，不擅自改写。

## 跨设备工具真实性核验清单

同步前逐项确认"这台机器上这些工具真的能用"：

- [ ] `bootstrap.python.runtime.identifier` 指向的 venv 存在，且 `python -V` 与 `bootstrap.python.version` 一致；
- [ ] `template.local_clone` 存在、`git remote` 与 `source_repo` 一致（不同设备可能克隆在不同路径）；
- [ ] `TEMPLATE_FILES` 全部存在（缺文件时 `verify_rules` 与 hooks 都会失真）；
- [ ] `.workspace/skills/` 目录与 `skills/REGISTRY.md`、`TOOLS.md` 三方一致；
- [ ] hook 插件在本机是否真的启用（`HERMES_HOME/.env`、`hermes config get plugins.enabled`），未启用时守门不生效；
- [ ] `.workspace/.hook-state/` 被 git 忽略（设备间不共享会话账本）；
- [ ] `docs/.index.json` 若存在则 `check` 通过（索引按内容指纹，跨设备会 stale）。

## 失败模式与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `git pull` 报 divergent | 两端都提交 | 让用户选 merge/rebase；先看 `git log --oneline HEAD..@{u}` |
| 拉完 `verify_rules` 报 HASH-DIFF | 另端改过模板层或在模板更新中途 | 跑 `template_sync.py --apply`，不要手改模板层 |
| `verify_rules` 报 `[trust] clone dirty` | 模板克隆有未提交改动 | 用 `git worktree` 快照比对，或先提交/暂存克隆改动 |
| preflight 报 `workspace_root` mismatch | TOOLS 记录来自另一台设备 | 改本机行，保留他机行；不要写单值 |
| preflight 报 `python` missing | 本机未建 venv | 按 §0.1 重建，并更新 bootstrap/TOOLS |
| 拉完 notes 路由缺失 | 未跑索引 | `notes/rebuild_index.py` |
| push 被拒 | 远端有新提交 | fetch → 让用户决定合并方式，不 force |

## 交付记录

预检结果（mismatch/warn 明细）→ 用户裁决（方向/策略/冲突处置）→ 实际执行的 git 命令 →
四项复验输出 → 回写的登记项 → 未决项。

## 变更记录

- 1.0.0（2026-11-02）：首版。跨设备场景来自实际记录冲突（TOOLS §1 单值被两端互相改写）；
  固定"先预检、按主机分列、用户裁决分叉、拉后四验"的流程。
