---
name: git-init
description: "Use when the user asks to create/initialize a git repository for a workspace that previously declined one (bootstrap git.status=declined), or to re-record git info in bootstrap. 为此前未建仓的工作区创建 git 仓库并登记 bootstrap 时使用。"
version: 1.0.0
---

# Git Init — 工作区 git 仓库创建 skill

为工作区创建独立 git 仓库（多设备同步用），并把仓库信息记入 `bootstrap.json`。适用于：初始化时选择"暂不建仓"（`git.status: declined`）后用户改变主意，或任何"给这个工作区建 git 仓库"的请求。

## 前置检查

1. `bootstrap.json` 的 `git.status`：
   - `initialized` → 已有仓库：核验 `.git` 存在与 remote 一致即可，不重复建；用户想**重建**时须先确认（旧仓库有未推送提交时先导出 patch 备份）。
   - `declined` / 无 `git` 节 → 走本流程。
2. `.git` 已存在但 bootstrap 无记录 → 按接管处理：确认 remote 非模板库后直接登记（第 4 步）。
3. `.git` remote 仍指向模板库 → 先按 workspace-init skill 第 6a 步抹除（含独有 commit patch 备份），再走本流程。

## 流程（五步）

### 第 1 步：决策确认

向用户确认三件事（已有明确指示的跳过）：

- 是否现在建仓（本 skill 的触发本身就代表"是"）；
- remote：用户自己的仓库地址（多设备同步），或纯本地仓（暂不配 remote）；
- 首次提交范围：默认全部治理文件 + 工作区文件（`.gitignore` 已忽略 `.venv-*` 等）。

### 第 2 步：创建仓库

```bash
cd <工作区根>
git init -b main
git add -A
git commit -m "chore: initialize workspace repository (from WorkspaceTemplate instance)"
```

用户提供了 remote 时：

```bash
git remote add origin <用户提供的地址>
git push -u origin main    # 需用户凭据/授权；失败不回滚本地仓，报告阻塞即可
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

### 第 5 步：验证与报告

```bash
git log --oneline -1 && git status --short | head -3 && git remote -v
python3 -c "import json; print(json.load(open('.workspace/bootstrap.json'))['git'])"
```

报告：`仓库状态 → remote 与分支 → bootstrap git 节内容 → 首次提交哈希`。

## 陷阱

- **凭据安全**：推送用用户的凭据（credential helper/SSH），Agent 不读取、不打印任何凭据内容；push 失败只报告错误，不代输密码。
- remote 是用户资产：地址由用户提供，Agent 不猜测、不默认填模板库地址。
- `git.add -A` 前扫一眼 `git status`：任务目录里的临时导出、大二进制文件应先入 `.gitignore` 或征求用户意见，避免首次提交塞满仓库。
- bootstrap `git` 节与真实仓库状态可能漂移（用户手动改 remote）；本 skill 每次执行都以 `git remote -v` 实测为准，发现不一致先校正 bootstrap 再继续。
- 多设备同步冲突（push 被拒）不属本 skill 范围：报告冲突，让用户决定 rebase/merge 策略。
