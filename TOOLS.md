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

## 5. Notes 索引

| 项 | 内容 |
|---|---|
| 入口 | `notes/INDEX.md` |
| 模板 | `notes/_TEMPLATE.md` |
| 重建 | `<configured-python> notes/rebuild_index.py` |
| 依赖 | Python 标准库；脚本只支持模板文档中定义的最小 YAML-like frontmatter 子集 |
| Python 最低版本 | 3.10 |
| 重建脚本 SHA-256 | `878cd4926911c61ac0982cacbb258b5a74591961899b51330546d4cdb6f4e57e` |
| 来源/副作用 | 模板内置、本 Workspace 维护；只读 note frontmatter 并覆写自动生成的 `notes/INDEX.md`，不联网 |

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
