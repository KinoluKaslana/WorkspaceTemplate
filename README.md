<div align="center">

**简体中文** | [English](README_EN.md)

# WorkspaceTemplate

**一个不预设业务、不绑定单一 Agent 客户端的 Workspace 治理模板。**

从一份干净、可审计的规则骨架开始，让 Agent 在真正工作前先确认 Python 运行时、业务边界、工具事实与 skills 来源。

[![Policy](https://img.shields.io/badge/policy-v3.6.1%20single%20source%20of%20truth-6f42c1)](AGENT_RULES.md)
[![Integrity](https://img.shields.io/badge/integrity-clause%20IDs%20%2B%20trust%20chain-success)](scripts/verify_rules.py)
[![Python](https://img.shields.io/badge/Python-local%20venv%20%7C%20MCP-3776AB?logo=python&logoColor=white)](.workspace/bootstrap.json)
[![Bootstrap](https://img.shields.io/badge/bootstrap-zero%20third--party%20dependencies-2ea44f)](scripts/inspect_skills.py)
[![CI](https://img.shields.io/github/actions/workflow/status/KinoluKaslana/WorkspaceTemplate/ci.yml?branch=main&label=CI)](https://github.com/KinoluKaslana/WorkspaceTemplate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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

## 什么时候**不该**用它

这套东西的成本在「机械一致性」：127 条政策、11 项预检、三层门禁（plugin / 客户端 hook / 差集审计）。以下场景它不划算：

- **一次性任务或单文件改动**：读完政策、跑预检、写 note 的时间比改代码还长，直接干活更快。
- **只是偶尔问答**：`hook` 与 `preflight` 的收益来自「长期、反复、多会话」；一次性咨询用不上。
- **需要强隔离或多租户**：守门防的是「无意识跳过」，**不防主动绕过**——同 uid 没有安全边界，恶意绕过要靠沙箱/OS 权限。
- **不能接受客户端执行仓内 hook 代码**：Codex/Claude Code 的 hook 是随仓分发的可执行脚本，合规上不接受就别装。
- **不想跟版本**：政策条款集（R1..R136）与模板同步链是配套的；长期停在旧版本会碰到条款演进与 `verify_rules` 漂移。

正向的适用范围见上面的「为什么需要它」与「核心能力」。

## 核心能力

| 能力 | 模板如何实现 |
|---|---|
| 单一政策源 | [`AGENT_RULES.md`](AGENT_RULES.md) 统一约束主 Agent 与子 Agent；客户端入口只负责定位它 |
| 规则分层 | 模板层文件**锁定只读**（同步=整文件替换）；本地规则全部外置到 `<file>.custom.md`，**custom 优先**（§10） |
| 完整性校验 | 条款带稳定 ID（`<!-- id:Rn -->`）+ 注册表双级校验；`--vs-template` 对模板克隆做**字节级比对 + git 信任锚**，堵死"改文件再重生成注册表"的自我作证漏洞 |
| 生命周期 skills | 8 个随模板分发到 `.workspace/skills/`：`template-update`（任意 ref 同步）、`workspace-repo-sync`（跨设备同步工作区仓库）、`workspace-init`（接管/首次落地）、`git-init`（建仓）、`delivery-closure`（交付闭环）、`docs-retrieval-maintenance`（文档检索维护）、`legacy-workspace-upgrade`（旧版本工作区自举）、`workspace-skill-authoring`（把用户要的能力落成**工作区级** skill）。每个 skill 必须满足 8 维契约，`skill_audit.py` 机械检查 |
| 首次初始化协议 | [`.workspace/bootstrap.json`](.workspace/bootstrap.json) 持久化 `python.mode`（**必须**：`uv` / `local-venv` / `mcp`，默认 uv）与 `git.status`（**可选**：initialized / declined）；**既有工作区一旦配置就不被模板演进改写** |
| 业务无关 | [`WORKSPACE.md`](WORKSPACE.md) 初始为空，由用户的稳定需求定义目标、范围与验收 |
| Skills 单路径接入 | 用户只提供根目录，Agent 自动发现入口、记录指纹并处理同名冲突 |
| 环境事实可追溯 | [`TOOLS.md`](TOOLS.md) 记录实测运行时、MCP、工具、来源和副作用，不承担授权职责 |
| 渐进式知识复用 | [`notes/`](notes/) 保存有长期价值的经验，双层索引（紧凑路由 + 按需倒排）加策展节奏防碎片化 |
| 客户端记忆边界 | 工作区经验只写 `notes/`；各客户端全局记忆（Hermes memory / Claude Auto Memory / Codex 内置记忆）至多保留一条指向 `notes/INDEX.md` 的指针（§9.1） |
| 安全默认值 | 外部 skills 默认只读；不自动执行 Hook、安装依赖、联网或发送用户数据 |

## 设计要点

模板把「规则常驻、机械可验」落到子 Agent 与工具层，关键设计如下：

| 变化 | 一句话 | 证据 |
|---|---|---|
| 子 Agent 授权模型 | 主 Agent 读完政策并常驻；**子 Agent 不重读全量政策**，写工作区须「目标精确命中委托 + 父证据仍有效」，自读不解锁 | R24 / R98 / R108 + `delegations.py` |
| 机械守门补强 | 拒绝码前缀、委托审计、`gate-deny` 独立核验、审计十三型 | 插件 1.9.0 + `docs/hooks-catalog.md` §2.6 |
| skills 契约化 | 8 个 skill 必须齐 8 维；`skill_audit.py` 缺节即失败；REGISTRY 由目录派生 | `docs/skills.md` §1 |
| Python 三选一 | **uv 默认** / 手动 venv / MCP；已配置不被改写 | R2–R8 + `pyenv_init.py` |
| 跨设备可核验 | 11 项预检把"登记 vs 本机实际"摊开；模板按任意 ref 同步且带 `--prune` | `workspace_preflight.py` / `template_sync.py` |
| docs 检索（可选） | 确定性索引，**省账单不省 token**，命中 10/10 | `docs/docs-retrieval.md` |

> 完整文档入口：[`docs/README.md`](docs/README.md)（按读者选路径）。

## 快速开始

### 方式一：从 GitHub 模板创建新仓库（推荐）

1. 在仓库页面点击 **Use this template**，选择 **Create a new repository**。
2. 克隆新仓库，让支持 Workspace 规则的 Agent 打开仓库根目录。
3. Agent 会先问 Python 运行时（三选一，默认 **uv**），再问 git 建仓决策（可选项）。
4. 描述你的业务目标，开始工作。

### 方式二：直接克隆

```bash
git clone https://github.com/KinoluKaslana/WorkspaceTemplate.git my-workspace
cd my-workspace
```

> [!NOTE]
> 克隆自带的 `.git` 指向模板仓库。首次初始化时 Agent 会按 §0.3 **抹除模板 git 标记**（先确认 remote、导出本地独有 commit 为 patch 备份），然后询问你是否把工作区建成独立 git 仓库。你不需要手动处理 remote；若跳过初始化协议直接开发，请自行 `rm -rf .git` 或改指自己的 remote，避免误推送回模板库。

然后用 Agent 打开 `my-workspace`。Codex 读取 [`AGENTS.md`](AGENTS.md)，Claude Code 读取 [`CLAUDE.md`](CLAUDE.md)，Hermes 读取 [`HERMES.md`](HERMES.md)；其他客户端应先被明确要求完整读取 [`AGENT_RULES.md`](AGENT_RULES.md) 与 [`.workspace/bootstrap.json`](.workspace/bootstrap.json)。

**工作区来自旧版本模板、连当前 skill 都没有？** 见 [`UPGRADE.md`](UPGRADE.md)：只需要 Python，从模板快照自举安装，再补 `REGISTRY.md`/`TOOLS.md` 登记。

**已有老版本模板工作区？** 直接对它说"初始化/接管这个工作区"，或让 Agent 执行 [workspace-init skill](#工作区生命周期-skills)——它会完成盘点、把本地改动迁入 custom 层、同步最新模板，并处理 venv 与 git 决策。

### 旧版本工作区怎么升级（自举）

工作区由 2.x 模板创建时，`.workspace/skills/` 里没有现行 skill，也没有 `scripts/template_sync.py`——**它没法用自己的能力升级自己**。这时把下面整段发给那个工作区里的 Agent 即可（完整步骤与回滚见 [`UPGRADE.md`](UPGRADE.md)，skill 见 [legacy-workspace-upgrade](.workspace/skills/legacy-workspace-upgrade/SKILL.md)）：

```text
本工作区是用旧版本模板建的，现在要把模板层升到当前版本（含 skill 与脚本）。我授权你修改本工作区的模板层文件，但必须按下面的约束做。

第一步：先完整读这份指南，读完再动手：
https://raw.githubusercontent.com/KinoluKaslana/WorkspaceTemplate/main/UPGRADE.md
（取不到就告诉我，不要凭记忆猜步骤。）

第二步：严格按它执行，不许跳步：
1) 克隆模板快照到临时目录（只读，不许克隆进工作区）；
2) 先 dry-run，贴出「旧版本 → 新版本」与「将替换 N 个文件」；
3) 再 --apply，贴出备份目录；
4) 跑 notes/rebuild_index.py、docs_index.py build、skill_audit.py --sync-registry、
   skill_audit.py --tools-snippet，并按输出把行补进 TOOLS.md（先 grep 再写）；
5) 四条验收原样贴输出：verify_rules --vs-template、mark_rules check、
   skill_audit --strict、workspace_preflight；
6) 再跑一次 dry-run 证明幂等（应为「将替换 0 个文件」）。

硬约束：不许改 python.mode；不许动业务文件/notes 正文/WORKSPACE.md 业务内容；
不许 commit/push；任何验收红就用 --rollback 回滚并如实说明。

报告：版本前后 → 文件数 → 备份目录 → 四条验收真实输出 → warn 逐条解释 → 未做的事。
```

验收通过后，该工作区应能自己复述：`AGENT_RULES.md` 版本行等于模板当前版本、`skill_audit --strict` 为 0 findings、`workspace_preflight` 的 `mismatch` 为 0。此后日常同步改用 `template-update`，跨设备同步用 `workspace-repo-sync`。

## 第一次打开会发生什么

模板不会替用户预选环境。当 `python.mode` 仍为 `unconfigured` 时，第一个 Agent 必须先询问：

> 本工作区的 Python 运行时用哪种？（**推荐默认：uv**）
> 1. **uv 管理**（`uv venv`，默认）——机器上没有 uv 时我先提示安装方式，由你执行安装；
> 2. **手动 venv**（`python -m venv`，用你指定的解释器）；
> 3. **MCP Python**（用当前会话提供的 Python 工具，不在本地建 venv）。

```mermaid
flowchart LR
    A[Agent 打开 Workspace] --> B{python.mode}
    B -->|unconfigured| C[询问用户：uv / 手动 venv / MCP]
    C -->|uv| D1[uv venv .venv-major-minor]
    C -->|手动 venv| D2[python -m venv .venv-major-minor]
    C -->|MCP Python| E[核验 MCP 能力边界]
    D1 --> F[登记 bootstrap 与 TOOLS]
    D2 --> F
    E --> F
    F --> G{git 决策 §0.3}
    G -->|先抹除模板 .git| H[询问用户是否建仓]
    H -->|现在建| I[git init + remote + bootstrap 登记]
    H -->|暂不建| J[git.status = declined<br>后续可经 git-init skill 翻转]
    I --> K[workspace_status = active]
    J --> K
    B -->|已配置| L[只核验现有选择]
```

三种 Python 模式互斥，后续可由用户明确要求切换；**已配置的工作区不因模板升级而改变模式**。**选定 Python 运行时是必须项；本地 venv 只在选 uv 或手动 venv 时存在；git 是可选项**：暂不建仓的工作区保持纯文件状态，随时可以说"建 git 仓库"触发 git-init skill。

### 三种 Python 模式

| | uv（默认） | 手动 venv | MCP Python |
|---|---|---|---|
| 适合 | 想要快、可复现、依赖解析稳定的项目 | 机器已有合意解释器、不想引入 uv | 不希望初始化本地 Python，或客户端已有受管计算环境 |
| 初始化 | `uv venv .venv-<mm> --python <ver>` | `<python> -m venv .venv-<mm>` | 不创建本地 venv |
| 装包 | `uv pip install --python .venv-<mm>/bin/python <pkg>`（Windows：`.venv-<mm>\Scripts\python.exe`） | `.venv-<mm>/bin/python -m pip install <pkg>`（Windows 同上） | 取决于 MCP 能力，必须登记 |
| 默认联网 | 否；**uv 缺失只提示安装方式，不自动装** | 否 | 取决于 MCP 实测能力 |
| 依赖持久性 | venv 内持久保存 | venv 内持久保存 | 取决于 provider，不能假定 |
| 不可用时 | exit 3 + 安装方式；用户可拒绝 | 报告解释器或 `venv` 模块的精确阻塞 | 不伪装成功，请用户接入 MCP 或回退 |

机械入口：`python3 scripts/pyenv_init.py --mode uv|venv|mcp [--apply]`（默认 dry-run；**已配置的工作区会 exit 5 拒绝改写**，需 `--force`）。

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

### 条款属性：政策为什么是「中文正文 + 机器属性」

政策正文保持中文——它要由你裁决；机器层不去猜语义，只校验**可判定事实**。每条 R 号在 `.workspace/clauses.meta.json` 里有四个属性：`severity`（MUST / MUST_NOT / SHOULD / MAY / INFO）、`trigger`（受控条件码）、`check`（可执行命令或 `manual:<理由>`）、`reviewed`（是否经人工定级）。

```bash
python3 scripts/clause_lint.py            # 用词与声明一致 → 0 errors
python3 scripts/clause_lint.py --report   # 覆盖率：多少条有命令可查、多少条只能人判
```

它**防的是自欺**：凡是机器能查的条款（hook、模板同步、skills 登记、docs 索引、条款本身）写 `manual:` 直接判错；强条款未复核、正文没有对应情态词也判错。正文只用四个情态词，禁用「尽量/必要时/建议/通常」。原理与边界见 [docs/policy-language.md](docs/policy-language.md)。

## Hook 治理（工作区级机械守门）

模板随仓库分发一个 **Hermes 项目级 plugin**（`.hermes/plugins/workspace-gate/`），把「先读治理文件、再写工作区文件」从软提醒变成**机械拦截**——对应 `AGENT_RULES.md` §12：

> **想先弄懂原理？** 完整技术实现（小白版比喻 → 读证据/指纹/覆盖区间的语义 → 三种 harness 逐项拆解 → 排查 FAQ）见 **[docs/hooks-guide.md](docs/hooks-guide.md)**。
> **想接新 harness？** 事件清单（何时触发 / 为何必须 / 缺了会怎样）+ 接入 7 步 + 验收断言见 **[docs/hooks-catalog.md](docs/hooks-catalog.md)**。

| 项 | 事实 |
|---|---|
| 位置 | `.hermes/plugins/workspace-gate/`（`plugin.yaml` + `__init__.py` + `selfcheck.py`，属模板层锁定文件，参与哈希校验） |
| 守门判据 | 目标路径落在工作区内，且**本会话**缺少「当前修订的完整连续行覆盖（`1..total`，无洞）」→ 拦截并说明原因；子 Agent 另须精确命中有效委托且父证据仍有效；工作区外写入不受影响 |
| 事件 | `pre_tool_call` 守门（内部异常也 block，fail-closed）、`post_tool_call` 记账（仅 `status=ok`）、`pre_llm_call` 提醒（每个会话至多一次）、`on_session_end` 审计（按回合有界追加） |
| 启用 | `HERMES_ENABLE_PROJECT_PLUGINS=true`（允许发现项目插件）**且** `~/.hermes/config.yaml` 有 `plugins.enabled: [workspace-gate]`（加载）——缺一不生效 |
| 调参 | 写工作区所有的工作区级覆写 `.hermes/plugins/workspace-gate/plugin.custom.json`（`enabled` / `gate_files` / `write_tools`）；**不要直接改脚本**（锁定层，同步时会被整文件覆盖） |
| 自检 | `<configured-python> .hermes/plugins/workspace-gate/selfcheck.py`（合成 payload 断言，全绿即守门逻辑未回归） |
| 信任边界 | 防「无意识跳过」，不防「主动绕过」：守门状态文件在 `.workspace/.hook-state/`（已 gitignore），拥有 `terminal`/`execute_code` 的 Agent 可绕到工具层之外——要防恶意绕过需沙箱/OS 权限层 |

拦截是**兜底**而不是替代：规则内容仍由 `AGENT_RULES.md` §2/§9/§11 与 `delivery-closure` skill 界定（**skill 治心、hook 治手**）。hook 未启用时义务不变。

**怎么确认守门真的在场**（不要用 `hermes plugins list`——实测它只列 bundled 插件，项目插件即使已生效也不出现）：

```bash
# ① 在场信号：plugin 每次加载都会写一行
tail -3 "$HERMES_HOME/logs/workspace-gate-plugin.log"   # 期望：workspace-gate active (v1.4.3, hermes_home=…)  ← 版本号随插件升级变化
# ② 行为验证：在工作区内、未读规则时尝试写文件，应被拦截并报出工作区路径与 target
# ③ 状态文件：<工作区根>/.workspace/.hook-state/<session_id>.json 记录该会话已读文件
```

## 客户端治理 hook（Codex / Claude Code / DeepSeek Harness）

模板另随仓库分发一套**客户端写前守门 hook**（对应 `AGENT_RULES.md` §13）：`.workspace/hooks/{hook,gate_core,wtarget,authz,template_anchor,cc_transcript}.py` + `.codex/hooks.json` + `.claude/settings.json`，把「先读治理文件、再写工作区文件」落到 Codex CLI / Claude Code 的项目级 hook 层。覆盖面真值表：

| 写路径 | Codex | Claude Code |
|---|---|---|
| `apply_patch` / `Write`/`Edit` | 受拦 + 审计 | 受拦 + 审计 |
| Bash 静态可判写（`>`/`>>`/`tee`/`sed -i` 等） | 受拦（未读 deny）/ 审计 | **不拦**；`Stop`/`SessionEnd` 转录回放**事后归因**（`cc-bash-write`） |
| Bash 解释器/包装类（`python*`/`sh`/`bash`/…） | 受拦（未读 B′ deny） | **不拦**；事后归因（`class=interpreter_wrapper`） |
| Bash 其余不可判形态 | 只审计（`unresolved-command`） | **不拦**；归因不出则记 `cc-bash-gap`（原因码） |

边界必须一并声明：**Claude Code 经 Bash 的一切写入不订阅、不拦截**；能拿到的只是**会话边界上的事后归因**——`Stop`/`SessionEnd` 依平台给的 `transcript_path` 回放转录、复算 `would_deny`。三点限制：`would_deny=true` **不表示写入被阻止**；转录可被同 uid 删改，不构成安全边界；读证据依赖转录内容与磁盘逐字节一致（截断/短读不授证）。也不得据此推断整仓写路径受管；hook 超时/加载失败/未信任为客户端 fail-open。逐 harness 实现细节见 **[docs/hooks-guide.md](docs/hooks-guide.md)**，事件映射、文件指纹与信任步骤见 `TOOLS.md` §1.1。

**DeepSeek Harness（dsh）** 走 Cordis 插件而不是 JSON hook 配置：`.dsh/workspace-gate/` 里是一个 `insert` 补丁 + 一个把 `ToolExecution` 翻译成工作区 hook 载荷的 `plugin.js`（判定仍由 `.workspace/hooks/hook.py` 做，三端共用一套读证据）。启用：

```bash
dsh --profile web --patch .dsh/workspace-gate/cordis.yml
```

**政策 §15（与 §0 的 Python 选择同性质）要求开工前核验接线**。dsh 没有项目级自动发现：不带 `--patch` 启动，就等于完全没有 gate——读证据、写前拦截、事后审计都不会发生，但工作区里的文件一个不少。所以「克隆下来长这样」不等于「已经受管」，先查接线再干活：

```bash
python3 scripts/gate_status.py --ttl-minutes 30                 # 四种 harness 各自有没有运行期证据
python3 scripts/gate_status.py --require dsh,cc --ttl-minutes 30 # 要求项缺证据 → 退出码 1
```

判定看的是**运行期证据**而不是文件在不在：`cc`/`codex` 看 `.workspace/.hook-state/gate.log` 里的 `client=`；`hermes` 看 `$HERMES_HOME/logs/workspace-gate-plugin.log` 的 `workspace-gate active`（机器级，不绑工作区）；`dsh` **必须同时**有 `.workspace/.dsh-gate.log` 的 `apply` 与 `.hook-state/dsh-session-*.json` 账本。
dsh 侧覆盖：`write`/`edit`/`str_replace_editor` 受拦，`bash`/`pwsh` 走静态可判写，`read` 记读证据；插件异常/hook 超时放行并记 `.workspace/.dsh-gate.log`，拿不到会话身份则 fail-closed。已在**真实模型回合**里验证（dsh headless + DeepSeek 账号）：未读先写被拒且文件未创建、先读后写放行、`bash` 重定向写被拒；另有 8 条走真实 `plugin.js`+`hook.py` 的合成用例。细节与已知限制见 **[docs/dsh.md](docs/dsh.md)**。

## 工作区生命周期 skills

8 个 skill 随模板分发在 `.workspace/skills/`（workspace-skills 命名空间），登记于 `skills/REGISTRY.md` 与 `TOOLS.md`，全部满足 8 维契约（触发/前置/契约/边界/执行/失败回滚/验收/维护）：

| Skill | 版本 | 触发 | 做什么 |
|---|---|---|---|
| [template-update](.workspace/skills/template-update/SKILL.md) | 3.2.1 | "同步模板/换成某个分支或标签"，或版本落后、`verify_rules` 报漂移 | 按 **任意 ref**（分支/标签/提交）同步模板层：dry-run → 备份（MANIFEST）→ 整文件替换 → custom 引用迁移 → `--vs-template` 验证；`--prune` 清理上游已删文件 |
| [workspace-repo-sync](.workspace/skills/workspace-repo-sync/SKILL.md) | 1.1.2 | 换设备、别端改了 notes/TOOLS、怀疑工具失效 | 先 `workspace_preflight.py` 核验登记与实机 → 用户裁决方向/分叉/冲突 → 拉后重建索引与四验 |
| [workspace-init](.workspace/skills/workspace-init/SKILL.md) | 1.3.0 | "初始化/接管这个工作区"，或无可信血缘、无条款标记 | 盘点 → 差异判定 → pre-v2.0 迁移（本地语义进 custom）→ Python（已配置则不动）→ git 决策 → 登记 → 验证 |
| [git-init](.workspace/skills/git-init/SKILL.md) | 1.2.0 | "建 git 仓库"，或 `git.status=declined` 后改主意 | init → .gitignore → 登记 → 审查暂存清单 → 提交 → 核提交树；含撤销建仓 |
| [delivery-closure](.workspace/skills/delivery-closure/SKILL.md) | 1.1.0 | 任务模式产物落盘、准备声明完成 | 产物清点（无仓用时间戳列举）→ 查 INDEX → note 三选一 → 索引回写 → 外部动作验证 → 三档口径声明 |
| [legacy-workspace-upgrade](.workspace/skills/legacy-workspace-upgrade/SKILL.md) | 1.0.0 | 工作区模板版本旧、缺现行 skill / 无 `template_sync.py` | 从模板快照自举 → `--apply` → `--sync-registry` + TOOLS 补登 → 四条验收 |
| [workspace-skill-authoring](.workspace/skills/workspace-skill-authoring/SKILL.md) | 1.0.0 | 用户明确要求新建/修改**工作区级** skill | 缺口证据 → 重叠检查 → kebab 命名 → 草稿 → 8 维契约 → 转正与三处登记 → 审批/试用 |
| [docs-retrieval-maintenance](.workspace/skills/docs-retrieval-maintenance/SKILL.md) | 1.3.0 | docs 改动、索引 stale、命中率下降 | build/check → 命中报告 → 只在真实缺口下加别名（上限 8、留一验证）→ 五条验收 |

**边界**：工作区**自身** git 仓库的分支切换、迁移、历史改写不在任何 skill 内——由用户自行维护。

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
│       ├── _TEMPLATE.md     # skill 契约模板（8 维骨架）
│       ├── template-update/ # 任意 ref 同步模板层
│       ├── workspace-repo-sync/ # 跨设备同步工作区仓库
│       ├── workspace-init/  # 接管/首次落地
│       ├── git-init/        # 建仓与登记
│       ├── delivery-closure/ # 交付闭环
│       ├── docs-retrieval-maintenance/ # docs 索引与别名维护
│       └── legacy-workspace-upgrade/   # 旧版本工作区自举
├── docs/
│   ├── README.md           # 文档索引（按读者选入口 + 必跑命令）
│   ├── architecture.md     # 四层模型 / 写路径 / 读证据 / 失败姿态 / 跨设备
│   ├── policy-language.md  # 政策双轨：受控词表 + 条款属性 + clause_lint
│   ├── skills.md           # 8 个 skill 的触发、边界、组合流程、8 维契约
│   ├── tools.md            # 15 个脚本的用法与退出码
│   ├── hooks-guide.md      # 三 harness hook 技术详解（原理 + 作用 + 排查）
│   ├── hooks-catalog.md    # 事件清单 + 新 harness 接入 7 步 + 验收断言
│   └── docs-retrieval.md   # docs 检索工具与实测结论（省账单不省 token）
├── notes/
│   ├── INDEX.md             # 自动经验索引（紧凑路由）
│   ├── _INVERTED.md         # 倒排索引/关联图（按需加载）
│   ├── _TEMPLATE.md         # Note 模板
│   └── rebuild_index.py     # 标准库索引器（双层索引 + 策展状态）
├── .hermes/
│   └── plugins/
│       └── workspace-gate/  # 工作区级治理 hook（先读规则再写文件；§12）
│           ├── plugin.yaml  # 插件清单（provides_hooks）
│           ├── __init__.py  # 守门实现（模板层锁定）
│           └── selfcheck.py # 合成 payload 自检（不依赖 Hermes 运行时）
├── scripts/
│   ├── inspect_skills.py    # Skills 只读盘点器
│   ├── mark_rules.py        # 条款 ID 标记 + 注册表生成
│   ├── verify_rules.py      # 双级校验 + --vs-template 权威比对
│   ├── skill_audit.py       # skills 契约 / 双登记 / --sync-registry
│   ├── workspace_preflight.py # 跨设备 11 项预检
│   ├── template_sync.py     # 任意 ref 同步 + --prune
│   ├── pyenv_init.py        # uv / venv / mcp 三模式初始化
│   ├── docs_index.py        # docs 确定性索引
│   ├── docs_hit_report.py   # 判据集命中报告
│   └── docs_alias_audit.py  # 别名审计与回滚
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

由你决定（§0.3）：Python 运行时选择是必须项（本地 venv 只在选 uv/手动 venv 时创建），git 是可选项。初始化时选"暂不建"会记录为 `git.status: declined`——这不是终态，之后说一声"建 git 仓库"，git-init skill 会完成建仓、remote 配置与 bootstrap 登记，方便多设备同步。

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

---

## 许可

本项目以 [MIT](LICENSE) 发布。你若把它改造成自己的 Workspace 模板，保留许可证声明即可，无需公开你的修改。

发现安全问题请走[私密报告](SECURITY.md)，不要开公开 issue。
