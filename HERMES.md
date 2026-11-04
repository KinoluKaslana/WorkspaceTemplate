# Agent Workspace — Hermes 入口

> 本入口只做定位，不复制政策。在本 Workspace 内工作的 Hermes 主 Agent 与经授权子 Agent 必须遵守本入口。

## 首先

任何操作前：**完整读取 [AGENT_RULES.md](AGENT_RULES.md)**，并检查 `.workspace/bootstrap.json` 是否要求首次 Python 选择协议；**开工前还须按 §15 核验 gate 接线**（`scripts/gate_status.py --require <harness> --ttl-minutes 30`，无证据先报告不可写入）。之后按序遵守 ① [`AGENT_RULES.custom.md`](AGENT_RULES.custom.md)（若存在）→ ② [`HERMES.custom.md`](HERMES.custom.md)（若存在）。**冲突时 `AGENT_RULES.md` 及其 custom 优先级最高。**

## Hermes 专属补充

- 创建子 Agent（`delegate_task` 或 spawn 的 Hermes 进程）时，父 Agent 必须显式传递根 `AGENT_RULES.md` 路径、目标路径上的可信局部规则、范围与可写边界；不假设子 Agent 继承当前上下文。
- **全局记忆边界**（落实 §9.1）：可复用经验只写 `notes/`，**不写 Hermes 全局记忆**（`~/.hermes/memories/`）；全局记忆对本 Workspace 至多留一条指向 `notes/INDEX.md` 的指针。环境事实归 `TOOLS.md`。
- `--ignore-rules` 启动会跳过项目上下文，该会话不在本入口与 `AGENT_RULES.md` 的覆盖范围内。
- 本入口与 `AGENT_RULES.md` 或更高层约束不一致时，以后两者为准。

## Workspace 级人格（可选，仅约束 Hermes）

默认不定义人格，使用全局 `$HERMES_HOME/SOUL.md`。需要 Workspace 级人格时**只写 [`HERMES.custom.md`](HERMES.custom.md)**：禁止写入 `AGENT_RULES.md`（跨客户端越权），也禁止写入本文件正文（模板层锁定文件会被哈希校验判为 TAMPERED 并在同步时覆盖）。与全局人格冲突时以用户当次明确指定为准。
