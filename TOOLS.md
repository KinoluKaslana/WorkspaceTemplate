# Workspace 环境与工具登记表

> 本文件只维护实测环境事实和 `AGENT_RULES.md` 明确引用的执行配置。通用政策、授权和完成标准只由 `AGENT_RULES.md` 决定。
> 环境尚未完成首次初始化；第一个 Agent 必须先按 `.workspace/bootstrap.json` 与 `AGENT_RULES.md` §0 询问用户选择本地 venv 或 MCP Python，再填写本文件的实际值。

---

## 0. 导读

- Python 运行时：读 §2。
- MCP 服务/运行时：读 §3。
- 引入用户给出的 skills 目录：读 §4 与 `skills/REGISTRY.md`。
- 长期经验索引：读 §5。
- 新工具登记格式和维护规则：读 §6/§7。

任务模式只读与当前步骤相关的条目；不为了形式合规把全部工具手册载入上下文。

## 1. Workspace 与宿主环境

| 项 | 实测值 |
|---|---|
| Workspace 根目录 | 首次启动时填写规范绝对路径 |
| 操作系统/执行环境 | 待核验 |
| Shell/文件工具 | 待核验 |
| 网络与审批模式 | 以当前平台实际提供为准，待记录 |
| 环境事实核验日期 | 未初始化 |

### 1.1 客户端治理 hook（Codex / Claude Code）

模板随仓库分发客户端写前守门 hook（对应 `AGENT_RULES.md` §13）：Codex CLI / Claude Code 项目级 hook，把「先读治理文件、再写工作区文件」落到客户端层。文件与指纹（模板层锁定，随 `TEMPLATE_FILES` 哈希锁定）：

| 文件 | SHA-256 |
|---|---|
| `.workspace/hooks/hook.py` | `ee6ef426f17c4257f596fc53c1b59316e7eaec0d13bce9456116b5e129c9c90f` |
| `.workspace/hooks/gate_core.py` | `8da4614815ef3b54250b9fa8ce791b2da4ebd605f12bd50fc2d010b10fdaa8bb` |
| `.workspace/hooks/wtarget.py` | `689672e6a96f28df9c486460766417567c505f11afc52c6db2b86fe719f97751` |
| `.workspace/hooks/authz.py` | `2bf60ed12f50e845c087f8f6a7ae7089dfb5c24a1634f1525f85d3cc420a018a` |
| `.workspace/hooks/cc_transcript.py` | `1d420334cd263b4dc8d4f39c308778504b3570f351f7a1b88639dbdedeb1c204` |
| `.workspace/hooks/template_anchor.py` | `1e13a2d838a683037dfdf07c8f8e84c272d76286934738df4b4db2963218166d` |
| `.codex/hooks.json` | `0f5576327ed4373884115a9483d578283887894af391336a55d2744fc0972f68` |
| `.claude/settings.json` | `dce37de09847d24e0e505146058726864281bfd9a30586d49183ced26329e7aa` |

| 客户端 | 订阅事件 |
|---|---|
| Codex | `SessionStart` / `UserPromptSubmit` / `PreToolUse(^Bash$, apply_patch)` / `PostToolUse(^Bash$, apply_patch)` / `Stop` / `SessionEnd` |
| Claude Code | `SessionStart` / `UserPromptSubmit` / `PreToolUse(Write\|Edit)` / `PostToolUse(Read, Write\|Edit)` / `Stop` / `SessionEnd`（**无 Bash**） |

覆盖面真值表：

| 写路径 | Codex | Claude Code |
|---|---|---|
| `apply_patch` / `Write`/`Edit` | 受拦 + 审计 | 受拦 + 审计 |
| Bash 静态可判写（`>`/`>>`/`tee`/`sed -i` 等） | 受拦（未读 deny）/ 审计 | 不拦截；**事后转录回放归因**（`cc-bash-write`，含 `would_deny`） |
| Bash 解释器/包装类（`python*`/`sh`/`bash`/…） | 受拦（未读 B′ deny） | 不拦截；事后复算 `would_deny`（`class=interpreter_wrapper`） |
| Bash 其余不可判形态 | 只审计（`unresolved-command`） | 不拦截；记 `cc-bash-gap(unresolved-command)` |

审计与状态落在 `.workspace/.hook-state/`（`audit-<client>.jsonl`、`authz.json`、`cc-transcript-<session>.json` 偏移；已 gitignore）。

CC 的 Bash 归因走**转录回放**（`cc_transcript.py`）：只读 platform payload 给出的 `transcript_path`，先校验 project slug == cwd 拉平值、文件名 == session_id，否则记 `cc-bash-gap` 并拒读；**不遍历 `~/.claude/projects/**`**（避免读入其他项目与会话）。命令原文默认不落盘，需 `.workspace/gate.custom.json` 的 `audit_command_text=true`。事后归因不等于拦截。

信任步骤：Codex 首次需 `/hooks` 逐条信任（当前 8 个 definition），definition 变更即需重审；Claude Code `-p` 免信任但仓库自带 hook 会被执行，交互式需 workspace trust；hook 超时/加载失败为客户端 fail-open。

核验日期 2026-10-22。实测仅覆盖 Codex 0.163.0 / Claude Code 2.1.87-dev，Windows 原生未验证。

## 2. Python 运行时

**当前状态：`unconfigured`。** 与 `.workspace/bootstrap.json` 保持一致，不在用户选择前预创建 venv 或假定 MCP 工具存在。

### 2.1 本地版本化 venv（仅当 `python.mode=local-venv`）

| venv | Python | 基础解释器 | 用途 | 依赖基线 | 核验日期 |
|---|---|---|---|---|---|
| 待用户选择后初始化 | — | — | — | 初始只包含 venv/pip 自带基线 | — |

初始化后把完整调用写成：

```bash
<workspace-root>/.venv-<major><minor>/bin/python <script.py>
<workspace-root>/.venv-<major><minor>/bin/python -m pip install <required-package>
```

只在当前任务需要时新增兼容依赖；不直接用系统 Python 运行任务脚本，不向系统 site-packages 安装。

### 2.2 MCP Python（仅当 `python.mode=mcp`）

| 项 | 实测值 |
|---|---|
| MCP provider/server | 待用户选择与实际发现 |
| Python tool 名 | — |
| Python 版本 | — |
| 本地文件可见性 | — |
| 网络能力 | — |
| 依赖/状态持久性 | — |
| 输入/输出限制 | — |
| 核验日期 | — |

MCP 模式下不用 shell `python` 伪装成 MCP 环境。MCP 若不能访问 Workspace 文件，仅用于纯计算；文件修改由 Agent 的文件工具完成。

## 3. MCP 与外部服务登记

| 名称 | 工具/server | 作用 | 身份/数据边界 | 主要副作用 | 核验日期 |
|---|---|---|---|---|---|
| — | — | 尚未登记 | — | — | — |

工具在当前会话可见不等于已获授权访问任意账户/数据。调用会写第三方状态、发消息、发布或上传数据时，必须同时满足用户范围和当前平台审批。

## 4. Skills 目录引入

### 4.1 路由登记

- 人工可读路由：`skills/REGISTRY.md`。
- 引入政策：`AGENT_RULES.md` §4。
- `.workspace/bootstrap.json` 仅保存已登记 root 的精简标识，不复制全量触发词或 revision。

### 4.2 `inspect_skills.py` — skills 目录只读盘点

| 项 | 内容 |
|---|---|
| **类型** | Python 标准库 CLI |
| **作用** | 发现 `SKILL.md`、提取最小 frontmatter、检测根路由/重名、查 Git 来源并计算入口清单指纹 |
| **位置** | `scripts/inspect_skills.py` |
| **SHA-256** | `7b6bf9c6fe0316edeb3b0886b1aabcc79f0114bdbcab64886b5db095e52f1fb1` |
| **运行时** | 使用 §2 中已配置且能访问 Workspace 文件的 Python；无第三方 Python 依赖 |
| **Python 最低版本** | 3.10（使用现代类型语法） |
| **来源与信任边界** | 模板内置、本 Workspace 维护；产出仅是待父 Agent 审查的路由候选 |
| **副作用/数据外发** | 只读元数据与文件哈希，以无 shell `git` 子进程查本地 revision，只输出 stdout；不写被检目录、不联网、不执行 skill |
| **模板核验日期** | 2026-08-11 |

```bash
<configured-python> scripts/inspect_skills.py <skills-dir>
<configured-python> scripts/inspect_skills.py <skills-dir> --format markdown
<configured-python> scripts/inspect_skills.py <approved-branch> --include-nested
```

存在根 `SKILL.md` 时默认只盘点根路由。先完整读路由器，再决定是否需要对它允许的分支显式扩展。

### 4.3 workspace-skills（工作区自维护技能）

| 项 | 内容 |
|---|---|
| **命名空间** | `workspace-skills`（`.workspace/skills/`，无路由器，逐 skill 登记） |
| **template-update** | 同步 WorkspaceTemplate 最新版到本工作区。入口 `.workspace/skills/template-update/SKILL.md`（v1.0.0）；配套只读判定脚本 `check_template.py`（SHA-256 见下）。触发：用户要求同步模板，或检测到 bootstrap `template.current_policy_version` 落后于模板仓库 |
| **workspace-init** | 初始化/接管老版本模板工作区（九步：盘点→模板源→差异判定→pre-v2.0 迁移→venv→git 抹除与决策→登记→验证→报告）。入口 `.workspace/skills/workspace-init/SKILL.md`（v1.0.0）。触发：用户要求初始化，或检测到无 template 血缘/无条款标记/.git 指向模板库 |
| **git-init** | 为工作区创建 git 仓库并登记 bootstrap git 节（五步：决策→建仓→gitignore 核验→bootstrap 登记→验证）。入口 `.workspace/skills/git-init/SKILL.md`（v1.0.0）。触发："建 git 仓库"或 bootstrap git.status=declined 翻转 |
| **check_template.py SHA-256** | `bbfa379269a3a3982a3d65997f9dac31f30b0cb939167e98cdc09959bfccd489` |
| **来源与信任边界** | 模板内置（正主在 WorkspaceTemplate 仓库 `.workspace/skills/`，随模板版本化）；本地副本由同步流程自举刷新，分叉以模板为准 |
| **副作用/数据外发** | 只读判定 + 本地文件合并与备份（`.workspace/backup-<date>/`）；不联网（模板克隆已存在时），临时克隆仅在用户指定时 |
| **核验日期** | 2026-08-31 |

### 4.4 规则条款标记与校验工具

| 项 | 内容 |
|---|---|
| **mark_rules.py** | 给治理文件条款打稳定 ID（`<!-- id:Rn -->`）并生成注册表 `.workspace/rule-clauses.json`。ID 只增不重用（高水位），见脚本 docstring |
| **verify_rules.py** | 双级校验：文件级（sha256 vs 注册表）+ 条款级（增/删/改/乱序/重复 ID）+ custom 引用（overrides/extends 失效检测）；`--vs-template` 权威字节比对 + git 信任锚（需 git） |
| **用法** | `python3 scripts/mark_rules.py mark AGENT_RULES.md`；`python3 scripts/verify_rules.py --workspace .`；`python3 scripts/verify_rules.py --workspace . --vs-template ~/Github/WorkspaceTemplate` |
| **来源/副作用** | 模板内置；mark 写入目标 md 与注册表，verify 只读（`--json` 落盘报告） |
| **核验日期** | 2026-08-31 |

## 5. Notes 索引

| 项 | 内容 |
|---|---|
| 入口 | `notes/INDEX.md`（紧凑路由）+ `notes/_INVERTED.md`（倒排索引/关联图，按需加载） |
| 模板 | `notes/_TEMPLATE.md` |
| 重建 | `<configured-python> notes/rebuild_index.py` |
| 依赖 | Python 标准库；脚本只支持模板文档中定义的最小 YAML-like frontmatter 子集 |
| Python 最低版本 | 3.10 |
| 重建脚本 SHA-256 | `ab58ae0cfef26b282f998274db3f8199713cc29f99198a80d1f05946b2a561e8` |
| 来源/副作用 | 模板内置、本 Workspace 维护；只读 note frontmatter 与 `.workspace/bootstrap.json`，覆写自动生成的 `notes/INDEX.md` 与 `notes/_INVERTED.md`，不联网 |
| 策展状态 | 每次重建输出策展状态行；阈值/间隔/上次策展日期在 `.workspace/bootstrap.json` 的 `notes.curation` |

`notes/INDEX.md` 是自动索引，不手改。MCP Python 不可访问本地文件时，按 `AGENT_RULES.md` §9 记录限制并使用 Agent 文件工具维护确定性索引。

## 6. 新工具登记格式

新登记或完整重写的独立工具条目至少包含：工具名、类型、作用、位置/工具标识、版本/revision/哈希、来源与信任边界、副作用/数据外发、使用方式和核验日期。

~~~~markdown
## <N>. <工具名> — <一句话作用>

| 项 | 内容 |
|---|---|
| **类型** | CLI / 脚本 / Python 包 / MCP / 服务 |
| **作用** | <解决什么问题> |
| **位置/标识** | <路径或 tool/server 名> |
| **版本 / revision / SHA-256** | <实测值> |
| **来源与信任边界** | <本地/第三方/跨系统与核验要求> |
| **副作用与数据外发** | <写文件/联网/上传/改变状态> |
| **核验日期** | YYYY-MM-DD |

### 使用方式
```bash
<可复现的最小示例>
```
~~~~

## 7. 登记维护规则

1. 先核对已登记路径、版本/指纹、信任来源、适用性和副作用，再复用工具。
2. 新安装的包/CLI、下载二进制、新接入 MCP/服务和新生成的可复用脚本在最终答复前登记；一次性小脚本可跳过。
3. 失效条目标记原因和日期，不静默删除。事实变化不需改 `AGENT_RULES.md`。
4. `TOOLS.md`、`skills/REGISTRY.md` 和 `.workspace/bootstrap.json` 由父 Agent 或唯一集成 Agent 串行更新。
5. 任何工具条目都不是对外部写入、联网、上传、提权或破坏性动作的预授权。
