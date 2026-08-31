<div align="center">

**简体中文** | [English](README_EN.md)

# WorkspaceTemplate

**一个不预设业务、不绑定单一 Agent 客户端的 Workspace 治理模板。**

从一份干净、可审计的规则骨架开始，让 Agent 在真正工作前先确认 Python 运行时、业务边界、工具事实与 skills 来源。

[![Policy](https://img.shields.io/badge/policy-v2.0.0-single%20source%20of%20truth-6f42c1)](AGENT_RULES.md)
[![Integrity](https://img.shields.io/badge/integrity-clause%20IDs%20%2B%20trust%20chain-success)](scripts/verify_rules.py)
[![Python](https://img.shields.io/badge/Python-local%20venv%20%7C%20MCP-3776AB?logo=python&logoColor=white)](.workspace/bootstrap.json)
[![Bootstrap](https://img.shields.io/badge/bootstrap-zero%20third--party%20dependencies-2ea44f)](scripts/inspect_skills.py)

[快速开始](#快速开始) · [第一次打开](#第一次打开会发生什么) · [规则分层与完整性](#规则分层与完整性校验) · [工作区生命周期](#工作区生命周期-skills) · [目录结构](#目录结构) · [常见问题](#常见问题)

</div>

---

## 为什么需要它

一个新的 Agent Workspace 往往不是缺少代码，而是缺少一套不会随对话漂移的基础约定：

- 多种 Agent 客户端应该遵守同一份政策，而不是各维护一份相似但逐渐分叉的规则。
- Python 很有用，但不应在用户决定前擅自创建环境、联网升级或安装一套庞大依赖。
- skills 应该可以按目录接入，同时保留来源、版本、命名冲突与副作用边界。
- 业务目标、运行时事实、长期经验和 Agent 政策需要分层维护。
- 用户对本工作区的自定义规则，不应与模板基线搅在一起制造同步冲突。
- 外部文档与工具可以提供能力，但不能自行扩大授权。

WorkspaceTemplate 把这些约定固化为一组小而明确的文件。它可以继续长成软件项目、数据分析工作区、研究仓库、内容生产流程、运维项目或其他获授权业务，而不要求先接受某个固定技术栈。

> [!IMPORTANT]
> 本仓库根目录就是可使用的 Workspace 模板，不需要再进入第二层模板目录。README 是 GitHub 展示与上手说明，不是 Agent 政策源；真正的权威规则只有 [`AGENT_RULES.md`](AGENT_RULES.md)。

## 核心能力

| 能力 | 模板如何实现 |
|---|---|
| 单一政策源 | [`AGENT_RULES.md`](AGENT_RULES.md) 统一约束主 Agent 与子 Agent；客户端入口只负责定位它 |
| 规则分层 | 模板层文件**锁定只读**（同步=整文件替换）；本地规则全部外置到 `<file>.custom.md`，**custom 优先**（§10） |
| 完整性校验 | 条款带稳定 ID（`<!-- id:Rn -->`）+ 注册表双级校验；`--vs-template` 对模板克隆做**字节级比对 + git 信任锚**，堵死"改文件再重生成注册表"的自我作证漏洞 |
| 生命周期 skills | `template-update`（同步新模板）、`workspace-init`（接管老版本工作区）、`git-init`（后补建仓）随模板分发到 `.workspace/skills/` |
| 首次初始化协议 | [`.workspace/bootstrap.json`](.workspace/bootstrap.json) 持久化 `python.mode`（**必须**：local-venv / mcp）与 `git.status`（**可选**：initialized / declined） |
| 业务无关 | [`WORKSPACE.md`](WORKSPACE.md) 初始为空，由用户的稳定需求定义目标、范围与验收 |
| Skills 单路径接入 | 用户只提供根目录，Agent 自动发现入口、记录指纹并处理同名冲突 |
| 环境事实可追溯 | [`TOOLS.md`](TOOLS.md) 记录实测运行时、MCP、工具、来源和副作用，不承担授权职责 |
| 渐进式知识复用 | [`notes/`](notes/) 保存有长期价值的经验，双层索引（紧凑路由 + 按需倒排）加策展节奏防碎片化 |
| 客户端记忆边界 | 工作区经验只写 `notes/`；各客户端全局记忆（Hermes memory / Claude Auto Memory / Codex 内置记忆）至多保留一条指向 `notes/INDEX.md` 的指针（§9.1） |
| 安全默认值 | 外部 skills 默认只读；不自动执行 Hook、安装依赖、联网或发送用户数据 |

## 快速开始

### 方式一：从 GitHub 模板创建新仓库（推荐）

1. 在仓库页面点击 **Use this template**，选择 **Create a new repository**。
2. 克隆新仓库，让支持 Workspace 规则的 Agent 打开仓库根目录。
3. Agent 会先问 Python 运行时（必须项），再问 git 建仓决策（可选项）。
4. 描述你的业务目标，开始工作。

### 方式二：直接克隆

```bash
git clone https://github.com/KinoluKaslana/WorkspaceTemplate.git my-workspace
cd my-workspace
```

> [!NOTE]
> 克隆自带的 `.git` 指向模板仓库。首次初始化时 Agent 会按 §0.3 **抹除模板 git 标记**（先确认 remote、导出本地独有 commit 为 patch 备份），然后询问你是否把工作区建成独立 git 仓库。你不需要手动处理 remote；若跳过初始化协议直接开发，请自行 `rm -rf .git` 或改指自己的 remote，避免误推送回模板库。

然后用 Agent 打开 `my-workspace`。Codex 读取 [`AGENTS.md`](AGENTS.md)，Claude Code 读取 [`CLAUDE.md`](CLAUDE.md)，Hermes 读取 [`HERMES.md`](HERMES.md)；其他客户端应先被明确要求完整读取 [`AGENT_RULES.md`](AGENT_RULES.md) 与 [`.workspace/bootstrap.json`](.workspace/bootstrap.json)。

**已有老版本模板工作区？** 直接对它说"初始化/接管这个工作区"，或让 Agent 执行 [workspace-init skill](#工作区生命周期-skills)——它会完成盘点、把本地改动迁入 custom 层、同步最新模板，并处理 venv 与 git 决策。

## 第一次打开会发生什么

模板不会替用户预选环境。当 `python.mode` 仍为 `unconfigured` 时，第一个 Agent 必须先询问：

> 是否由我自动初始化本 Workspace 的本地版本化 venv（推荐），还是使用当前会话中 MCP 提供的 Python 环境？

```mermaid
flowchart LR
    A[Agent 打开 Workspace] --> B{python.mode}
    B -->|unconfigured| C[询问用户：venv 或 MCP]
    C -->|本地 Python| D[创建 .venv-major-minor]
    C -->|MCP Python| E[核验 MCP 能力边界]
    D --> F[登记 bootstrap 与 TOOLS]
    E --> F
    F --> G{git 决策 §0.3}
    G -->|先抹除模板 .git| H[询问用户是否建仓]
    H -->|现在建| I[git init + remote + bootstrap 登记]
    H -->|暂不建| J[git.status = declined<br>后续可经 git-init skill 翻转]
    I --> K[workspace_status = active]
    J --> K
    B -->|已配置| L[只核验现有选择]
```

两种 Python 模式互斥，后续可由用户明确要求切换。**venv 是必须项，git 是可选项**：暂不建仓的工作区保持纯文件状态，随时可以说"建 git 仓库"触发 git-init skill。

### 两种 Python 模式

| | 本地版本化 venv | MCP Python |
|---|---|---|
| 适合 | 需要稳定文件访问、可复现依赖与本地脚本的项目 | 不希望初始化本地 Python，或当前客户端已有受管计算环境 |
| 初始化 | 创建 `.venv-<major><minor>` | 不创建本地 venv |
| 默认联网 | 否；不自动升级 pip 或安装依赖 | 取决于 MCP 实测能力，必须登记 |
| 文件访问 | 使用 Workspace 内本地路径 | 必须核验；不可见时 MCP 只做纯计算 |
| 依赖持久性 | venv 内持久保存 | 取决于 MCP provider，不能假定 |
| 不可用时 | 报告解释器或 `venv` 模块的精确阻塞 | 不伪装成功，请用户接入 MCP 或回退到本地 venv |

模板自带的 Python 脚本都只依赖标准库，并要求 Python 3.10 或更高版本。选择运行时之前，不会执行它们。

## 规则分层与完整性校验

治理文件分两层，这是模板同步不产生合并冲突的根本原因：

| 层 | 文件 | 所有权 | 同步行为 |
|---|---|---|---|
| **模板层（锁定）** | `AGENT_RULES.md`、`AGENTS/CLAUDE/HERMES.md`、`README*`、`notes/rebuild_index.py`、`notes/_TEMPLATE.md`、`scripts/*.py`、`.workspace/skills/`、`.workspace/rule-clauses.json` | 模板 | **整文件替换**，逐字节与模板一致 |
| **custom 层** | `*.custom.md`（如 `AGENT_RULES.custom.md`） | 工作区 | 永不触碰；**与模板层冲突时以 custom 为准** |
| **工作区层** | `WORKSPACE.md`、`TOOLS.md`、`notes/*.md`、`skills/REGISTRY.md`、bootstrap 本地值、任务目录 | 工作区 | 永不触碰 |

custom 条款用 `overrides: R<id>` / `extends: R<id>` 声明与模板条款的关系；模板条款被删改导致引用失效时，校验脚本会点名，同步 skill 会逐条请你裁决——**custom 语义永不静默丢弃**。

**怎么确认规则文件确实没被改过？** 两条路径：

```bash
# 快速自查：文件哈希 + 条款级比对（增/删/改/乱序）
python3 scripts/verify_rules.py --workspace .
# 权威校验：对模板克隆逐字节比对 + git 信任锚（GitHub 对象哈希 → 克隆 HEAD → 工作区字节相等）
python3 scripts/verify_rules.py --workspace . --vs-template ~/Github/WorkspaceTemplate
```

条款 ID（`<!-- id:Rn -->`，由 [`scripts/mark_rules.py`](scripts/mark_rules.py) 维护，注册表在 `.workspace/rule-clauses.json`；ID 跨文件**全局唯一、永不复用**）让"用户在模板文件里加了条款/调了顺序"被精确识别为漂移，而不是等到同步时变成一锅冲突。本地注册表可被重生成，所以**权威判定只认 `--vs-template`**。`--vs-template` 依赖本机 `git`（对模板克隆做 `git status` / `rev-parse` 信任锚定）；没有 git 时用快速自查路径即可。

## 工作区生命周期 skills

三个 skill 随模板分发在 `.workspace/skills/`（workspace-skills 命名空间），登记于 `skills/REGISTRY.md` 与 `TOOLS.md`：

| Skill | 版本 | 触发 | 做什么 |
|---|---|---|---|
| [template-update](.workspace/skills/template-update/SKILL.md) | v2.0 | "同步模板/更新到最新模板"，或检测到版本落后 | 七步锁定替换式同步：模板源 → 校验 → 备份 → 整文件替换 → custom 引用迁移 → bootstrap 回写 → `--vs-template` 权威验证 |
| [workspace-init](.workspace/skills/workspace-init/SKILL.md) | v1.0 | "初始化/接管这个工作区"，或检测到无 template 血缘/无条款标记/.git 指向模板库 | 九步接管老工作区：盘点 → 差异判定 → pre-v2.0 迁移（本地语义外置进 custom）→ venv → git 抹除与决策 → 登记 → 验证 → 报告 |
| [git-init](.workspace/skills/git-init/SKILL.md) | v1.0 | "建 git 仓库"，或 bootstrap `git.status=declined` 后用户改主意 | 五步建仓：决策 → `git init` + 首提交 → .gitignore 核验 → bootstrap git 节登记 → 验证 |

## 工作原理

模板采用"入口、政策、custom、业务、状态、事实、路由、经验"分层：

| 层 | 文件 | 职责 |
|---|---|---|
| 客户端入口 | [`AGENTS.md`](AGENTS.md)、[`CLAUDE.md`](CLAUDE.md)、[`HERMES.md`](HERMES.md) | 让不同客户端定位共同规则，不复制规则正文 |
| 权威政策 | [`AGENT_RULES.md`](AGENT_RULES.md)（锁定） | 模式、作用域、信任、安全、多 Agent、交付、规则分层与完成标准 |
| 本地规则 | `*.custom.md` | 用户/Agent 的本地规则，custom 优先，同步不触碰 |
| 业务轮廓 | [`WORKSPACE.md`](WORKSPACE.md) | 长期目标、范围、资产、约束与验收方式 |
| 启动状态 | [`.workspace/bootstrap.json`](.workspace/bootstrap.json) | Python 模式（必须）、git 决策（可选）、模板血缘、skills 根、notes 策展 |
| 环境事实 | [`TOOLS.md`](TOOLS.md) | 运行时、MCP、工具、版本、来源、哈希与副作用 |
| Skills 路由 | [`skills/REGISTRY.md`](skills/REGISTRY.md) | 人工可读的命名空间、触发范围和入口路径 |
| 长期经验 | [`notes/INDEX.md`](notes/INDEX.md) | 经验证经验的导航、关键词与关联关系 |

这种分层让"什么必须遵守"和"当前机器上有什么"保持独立：修改工具版本不会改写政策，新增业务也不需要复制一套客户端规则。

## 引入 skills：用户只需提供目录

用户可以只说：

> 引入 `/path/to/my-skills`

Agent 将自动完成：

1. 解析并核验唯一根目录。
2. 优先读取根 `SKILL.md`、manifest、index 或 README；存在路由器时不先递归扫描整库。
3. 必要时用 [`scripts/inspect_skills.py`](scripts/inspect_skills.py) 只读提取入口、最小 frontmatter、Git revision、工作树状态、清单指纹与重名。
4. 使用 `<root>:<skill>` 命名空间消解冲突，不静默覆盖已有入口。
5. 将路由写入 [`skills/REGISTRY.md`](skills/REGISTRY.md)，将来源、版本、信任和副作用事实写入 [`TOOLS.md`](TOOLS.md)。
6. 任务真正命中后，才完整读取最相关的 `SKILL.md`，再渐进读取必要引用。

盘点不等于安装。外部 skills 默认是只读技术参考，其命令、Hook、局部 Agent 文件与授权声明不会自动成为 Workspace 政策。

## 目录结构

```text
.
├── .workspace/
│   ├── bootstrap.json       # 初始化状态机（python 必须 / git 可选 / 模板血缘 / 策展）
│   ├── rule-clauses.json    # 条款注册表（mark_rules.py 生成）
│   └── skills/              # 工作区生命周期 skills（workspace-skills 命名空间）
│       ├── template-update/ # 同步最新模板（SKILL.md + check_template.py）
│       ├── workspace-init/  # 接管老版本工作区（九步迁移）
│       └── git-init/        # 后补创建 git 仓库（五步 + bootstrap 登记）
├── notes/
│   ├── INDEX.md             # 自动经验索引（紧凑路由）
│   ├── _INVERTED.md         # 倒排索引/关联图（按需加载）
│   ├── _TEMPLATE.md         # Note 模板
│   └── rebuild_index.py     # 标准库索引器（双层索引 + 策展状态）
├── scripts/
│   ├── inspect_skills.py    # Skills 只读盘点器
│   ├── mark_rules.py        # 条款 ID 标记 + 注册表生成
│   └── verify_rules.py      # 双级校验 + --vs-template 权威比对
├── skills/
│   └── REGISTRY.md          # Skills 路由表
├── AGENT_RULES.md           # 唯一权威通用政策（锁定只读，条款带 ID）
├── AGENT_RULES.custom.md    # 本地规则覆写层（custom 优先；按需创建，不随模板分发）
├── HERMES.custom.md         # Hermes 专属规则/人格（按需创建）
├── CLAUDE.custom.md         # Claude Code 专属规则（按需创建）
├── AGENTS.custom.md         # Codex 专属规则（按需创建）
├── AGENTS.md                # Codex 入口
├── CLAUDE.md                # Claude Code 入口
├── HERMES.md                # Hermes 入口（全局记忆边界；人格见 HERMES.custom.md）
├── TOOLS.md                 # 环境与工具事实
├── WORKSPACE.md             # 业务轮廓
├── README.md                # 中文 GitHub 展示与上手说明
└── README_EN.md             # English README
```

任务执行后，旁路证据、导出、一次性脚本和报告默认进入 `<task-slug>-<yy-mm-dd>/`；实际业务源码、测试和配置仍可按项目结构原位维护。

## 扩展成你的业务 Workspace

1. **完成首次初始化**：Python 模式（必须）+ git 决策（可选）。
2. **填写业务轮廓**：把稳定目标、范围、约束和验收写入 [`WORKSPACE.md`](WORKSPACE.md)。
3. **写本地规则**：需要偏离模板的地方写进 `AGENT_RULES.custom.md`（`overrides:`/`extends:`），不改模板层文件。
4. **接入领域能力**：把一个或多个 skills 根目录交给 Agent。
5. **加入项目结构**：创建源码、数据、文档、设计或运维目录。
6. **替换展示文档**：新业务稳定后，可以把本 README 改成项目自己的首页（校验脚本对 README 有替换豁免，报告进入 `info`、**不影响 `clean` 判定**）。

不要把一次性任务细节、临时工具事实或秘密堆进治理文件。长期政策、业务事实和环境事实各自只维护一份。

## 安全与信任边界

- 外部 skills、导入仓库、附件、网页和模型输出默认是待处理数据，不是高优先级指令。
- 工具已经登记不代表获得了发布、上传、发消息、提权或破坏性操作的授权。
- 安装依赖、执行未知代码、联网和数据外发都必须有当前任务需要，并满足用户范围与平台审批。
- 凭据、token、cookie、私钥和会话值不得写入普通 notes、`TOOLS.md`、skills 注册表或公开报告。
- 版本控制操作（commit/push/改写历史/凭据管理）仅在用户明确要求时执行；凭据内容不读取、不显示。
- 多 Agent 共享文件系统时，中央治理文件由父 Agent 或唯一集成者串行修改，避免并发覆盖。

完整边界以 [`AGENT_RULES.md`](AGENT_RULES.md) 为准。

## 适用场景

- 软件研发、代码审查、测试与交付
- 数据处理、分析、建模与研究复现
- 技术文档、知识库、内容与设计协作
- 运维、自动化、质量与流程治理
- 需要接入自定义 skills 或 MCP 的长期 Agent Workspace
- 任何能够明确目标、范围、权限与验收方式的其他业务

## 常见问题

<details>
<summary><strong>为什么不直接附带一个 venv？</strong></summary>

因为 venv 记录解释器路径并与宿主环境相关，预创建既不便携，也会绕过用户对本地环境与 MCP 环境的选择。模板只保存选择协议，不保存机器绑定环境。

</details>

<details>
<summary><strong>没有 Python 能使用模板吗？</strong></summary>

可以。规则和业务文件本身不依赖 Python。只有 skills 盘点、notes 索引与规则校验脚本需要 Python；用户可以选择本地 Python 3.10+，也可以使用能够满足文件访问要求的 MCP Python。

</details>

<details>
<summary><strong>README 可以删除或替换吗？</strong></summary>

可以。README 是面向人的展示页，不在 Agent 启动链中。`verify_rules.py` 对 README 有显式替换豁免（报告 `REPLACED`，进入 `info`、**不计入 `clean`**，而非 `TAMPERED`）。保留 `AGENT_RULES.md`、各客户端入口与 `.workspace/bootstrap.json` 即可维持规则加载和首次状态判断。

</details>

<details>
<summary><strong>引入 skills 会自动执行里面的代码吗？</strong></summary>

不会。默认流程只读取入口与元数据、计算指纹并建立路由。安装、Hook、联网或执行代码必须在真实任务中另行满足必要性、授权和审批。

</details>

<details>
<summary><strong>它只能用于 Codex、Claude Code 或 Hermes 吗？</strong></summary>

不是。仓库为这三类客户端提供了薄入口；其他 Agent 只要能够读取 Workspace 文件，并被要求遵守 `AGENT_RULES.md`，也可以使用同一治理结构。具体自动加载行为仍取决于客户端本身。注意：Workspace 级人格定义只放在 `HERMES.custom.md`（仅约束 Hermes），不进入约束所有客户端的 `AGENT_RULES.md`。

</details>

<details>
<summary><strong>我能直接修改 AGENT_RULES.md 吗？</strong></summary>

不能也不需要。模板层文件是锁定只读的——直接修改会被 `verify_rules.py` 报为漂移，同步时被模板原件覆盖。你的自定义规则写进 `AGENT_RULES.custom.md`：用 `overrides: R<id>` 覆写某条模板条款，或 `extends:` 纯新增；与模板层冲突时以 custom 为准。

</details>

<details>
<summary><strong>怎么确认规则文件没被改过？</strong></summary>

两档校验：`verify_rules.py --workspace .` 做文件哈希 + 条款级比对（秒级自查）；`--vs-template <模板克隆>` 做权威校验——先锚定克隆的 git 状态（status 干净 + HEAD），再逐字节比对工作区文件。后者堵死了"改文件后重跑 mark_rules.py 伪造注册表"的自我作证漏洞：本地状态不能做自己的审判官，信任锚只能是模板正主的 git 历史。

</details>

<details>
<summary><strong>老版本模板建的工作区怎么升级？</strong></summary>

对工作区说"初始化/接管这个工作区"或直接执行 workspace-init skill。九步流程会盘点现状、把你在治理文件里的本地改动外置进 `*.custom.md`（语义零丢失）、把模板层还原为最新原件、完成 venv/git 决策并全量验证。若工作区 `.git` 仍指向模板库，它会先导出本地独有 commit 为 patch 再抹除，不会丢提交。

</details>

<details>
<summary><strong>工作区需要自己的 git 仓库吗？</strong></summary>

由你决定（§0.3）：venv 是必须项，git 是可选项。初始化时选"暂不建"会记录为 `git.status: declined`——这不是终态，之后说一声"建 git 仓库"，git-init skill 会完成建仓、remote 配置与 bootstrap 登记，方便多设备同步。

</details>

<details>
<summary><strong>Hermes 的全局记忆和 Workspace notes 是什么关系？</strong></summary>

按 `AGENT_RULES.md` §9.1 的记忆边界：Workspace 内产生的可复用经验唯一归宿是 `notes/`，Hermes 不主动把这类经验写入自己的全局持久记忆（`~/.hermes/memories/`）；全局记忆中关于本 Workspace 至多保留一条指向 `notes/INDEX.md` 的指针。这样每个 Workspace 的知识留在本仓库内，可版本化、可审计，也不会在多个 Workspace 之间漂移。其他客户端的全局记忆/偏好机制同理适用该边界。

</details>

## 维护与贡献

- 政策变更只修改 [`AGENT_RULES.md`](AGENT_RULES.md)，更新模板政策版本，**并重跑 `scripts/mark_rules.py mark` 刷新条款 ID 与注册表**（条款 ID 是校验锚点，不得手改）。
- 运行时、工具或 MCP 事实只修改 [`TOOLS.md`](TOOLS.md) 与启动状态。
- 新增 skills 路由时，同时维护事实登记和 [`skills/REGISTRY.md`](skills/REGISTRY.md)。
- 生命周期 skills（template-update / workspace-init / git-init）的正主在本仓库 `.workspace/skills/`；实例工作区靠同步自举刷新，不各自维护。
- README 的改版不会改变 Agent 权限或完成标准。

欢迎通过 [Issues](https://github.com/KinoluKaslana/WorkspaceTemplate/issues) 提交问题或建议，也可以通过 [Pull Requests](https://github.com/KinoluKaslana/WorkspaceTemplate/pulls) 提交改进。

---

<div align="center">

从一个清晰的 Workspace 开始，让业务决定能力，让规则约束能力。

</div>
