---
name: git-init
description: "Use when the user asks to create/initialize a git repository for a workspace that previously declined one (bootstrap git.status=declined), or to re-record git info in bootstrap. 为此前未建仓的工作区创建 git 仓库并登记 bootstrap 时使用。"
version: 1.2.0
---

# Git Init — 工作区 git 仓库创建 skill

为工作区创建独立 git 仓库（多设备同步用），并把仓库信息记入 `bootstrap.json`。适用于：初始化时选择"暂不建仓"（`git.status: declined`）后用户改变主意，或任何"给这个工作区建 git 仓库"的请求。

**职责边界**：只管**创建**仓库与**首次记录**分支/remote。建仓之后的**分支切换、迁移、历史改写**属用户自管，本 skill 不执行也不给建议流程；分支名只作为当时的事实写入 `bootstrap.git` 与 `TOOLS.md`。

## 前置检查

1. `bootstrap.json` 的 `git.status`：
   - `initialized` → 已有仓库：核验 `.git` 存在与 remote 一致即可，不重复建；用户想**重建**时须先确认（旧仓库有未推送提交时先导出 patch 备份）。
   - `declined` / 无 `git` 节 → 走本流程。
2. `.git` 已存在但 bootstrap 无记录 → 按接管处理：确认 remote 非模板库后直接登记（第 4 步）。
3. `.git` remote 仍指向模板库 → 先按 workspace-init skill 第 6a 步抹除（含独有 commit patch 备份），再走本流程。

## 流程（六步）

### 第 1 步：决策确认

向用户确认三件事（已有明确指示的跳过）：

- 是否现在建仓（本 skill 的触发本身就代表"是"）；
- remote：用户自己的仓库地址（多设备同步），或纯本地仓（暂不配 remote）；
- 首次提交范围：默认全部治理文件 + 工作区文件（`.gitignore` 已忽略 `.venv-*` 等）。

### 第 2 步：创建仓库（先登记、后提交、再核提交树）

顺序固定：**init → .gitignore 核验 → bootstrap/TOOLS 登记 → 审查暂存清单 → 提交**。
先提交后登记会让首次提交缺 `bootstrap.json`/`TOOLS.md` 的登记内容，且 `git add -A` 会把无关文件一起纳入。

```bash
cd <工作区根>
git init -b main
# 第 3 步的 .gitignore 核验在此完成
# 第 4 步的 bootstrap/TOOLS 登记在此完成
git add -A
git status --short          # 必读：逐条确认将被提交的文件都在预期范围内
git diff --cached --stat    # 期望：只有治理文件 + 工作区业务文件，无 .venv-*、无备份目录、无 .hook-state
git commit -m "chore: initialize workspace repository (from WorkspaceTemplate instance)"
git show --stat --oneline HEAD   # 期望：提交树 == 上一步审查过的暂存清单
```

**提交范围纪律**：`git add -A` 之前必须看过 `git status --short`；发现 `.venv-*`、`backup-*`、`.hook-state`、临时任务目录 → 先补 `.gitignore` 再 add，不要用 `git add -A` 硬吃。

用户提供了 remote 时（**仅在用户明确要求推送时执行**，R63）：

```bash
git remote add origin <用户提供的地址>
git push -u origin <branch>    # 需用户凭据/授权；失败不回滚本地仓，报告阻塞即可
```

### 第 3 步：.gitignore 核验

确认 `.gitignore` 至少含：`.venv-*/`、`__pycache__/`、`*.py[cod]`、`.env`、`.env.*`（模板自带；用户可追加）。`git status` 应显示干净（或只有用户故意未跟踪文件）。

### 第 4 步：bootstrap 登记（核心动作）

更新 `.workspace/bootstrap.json`，新增/翻转 `git` 节：

```json
"git": {
  "status": "initialized",
  "remote": "<地址或 null（纯本地仓）>",
  "default_branch": "main",
  "initialized_at": "<今天>",
  "note": "declined → initialized 由 git-init skill 翻转；remote 变更须同步本节"
}
```

同时 TOOLS.md 记一行环境事实（仓库地址、分支、初始化日期）。

### 第 5 步：README 决策（AGENT_RULES §0.4，建仓后必问）

建仓完成后立刻问用户：**是否需要重构 README？**

- 用户说「不需要」→ `python3 scripts/workspace_preflight.py --record-readme declined`
- 用户说「需要」→ 让用户在两种方式中**明确选一个**，不许代选、不许混合：
  - `auto`：仅依据工作区现有内容（文件、skills、脚本、notes）生成；
  - `provided`：用户先给说明/要点，再结合工作区现有内容生成。
  选定后 `--record-readme auto|provided`，再按所选方式产出，**经用户确认后才写 `README.md`**。
- 两种方式都不得编造工作区里不存在的功能/命令/目录；`provided` 方式下用户说明与事实冲突时按用户说明写，
  并在交付时点名冲突。

### 第 6 步：验证与报告

```bash
git log --oneline -1 && git status --short | head -3 && git remote -v
python3 -c "import json; print(json.load(open('.workspace/bootstrap.json'))['git'])"
```

报告：`仓库状态 → remote 与分支 → bootstrap git 节内容 → 首次提交哈希`。

## 失败与回滚

| 失败特征 | 处置 |
|---|---|
| `git commit` 前发现暂存清单含不该提交的文件 | `git reset` 清暂存 → 补 `.gitignore` → 重来（不提交半成品） |
| `git commit` 之后发现提交范围错误（含大文件/秘密） | **不要** `--amend` 静默掩盖：报告用户，按 R59 决定是否 `reset --soft` 重做；若已推送，改写历史需用户明确授权 |
| `git remote add` 失败/remote 写错 | `git remote remove origin` 后重加；bootstrap 的 remote 字段同步更正 |
| 用户反悔、要撤销建仓 | `rm -rf .git` + 把 `bootstrap.git.status` 回写 `declined`（先确认无未推送提交；有则先导出 `git format-patch` 备份） |
| 提交成功但登记没写 | 立即补 `bootstrap.json` + `TOOLS.md` 并**重新提交**（不得只在工作区留改动） |

## 验收（命令 + 期望输出）

```bash
git rev-parse --is-inside-work-tree     # 期望：true
git log --oneline -1                    # 期望：一条初始化提交
git status --short                      # 期望：空（或仅用户声明的未跟踪文件）
python3 -c "import json;d=json.load(open('.workspace/bootstrap.json'));print(d['git']['status'], d['git']['remote'])"   # 期望：initialized <remote 或 None>
python3 scripts/skill_audit.py --strict  # 期望：exit 0
```

## 陷阱

- **凭据安全**：推送用用户的凭据（credential helper/SSH），Agent 不读取、不打印任何凭据内容；push 失败只报告错误，不代输密码。
- remote 是用户资产：地址由用户提供，Agent 不猜测、不默认填模板库地址。
- `git.add -A` 前扫一眼 `git status`：任务目录里的临时导出、大二进制文件应先入 `.gitignore` 或征求用户意见，避免首次提交塞满仓库。
- bootstrap `git` 节与真实仓库状态可能漂移（用户手动改 remote）；本 skill 每次执行都以 `git remote -v` 实测为准，发现不一致先校正 bootstrap 再继续。
- 多设备同步冲突（push 被拒）不属本 skill 范围：报告冲突，让用户决定 rebase/merge 策略。

## 维护

- 版本历史见仓库 `CHANGELOG.md`；本 skill 的版本按语义化规则递增（PATCH 措辞 / MINOR 能力 / MAJOR 边界）。
- 改动本 skill 后运行 `python3 scripts/skill_audit.py --strict`（契约节 + REGISTRY/TOOLS 双登记）。
