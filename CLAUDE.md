# Agent Workspace — Claude Code 入口

> 在本 Workspace 内工作的 Claude Code 主 Agent 与经授权子 Agent 必须遵守本入口。

## 首先

在做任何操作前，必须**完整读取 [AGENT_RULES.md](AGENT_RULES.md)**，并检查 `.workspace/bootstrap.json` 是否要求执行首次 Python 选择协议。

## Claude Code 专属补充

- 本文件只负责定位共同政策，不复制模式、Python、skills、工具、notes 或交付规则。
- **自动记忆边界（落实 `AGENT_RULES.md` §9.1）**：本 Workspace 内形成的可复用经验只写入 `notes/`，不写入客户端自动记忆（如 Codex 内置记忆或 Claude Code Auto Memory）；该类记忆中关于本 Workspace 至多保留一条指向 `notes/INDEX.md` 的指针。
- 创建 Claude Code 子 Agent 时，父 Agent 必须显式传递根 `AGENT_RULES.md` 路径、目标路径上的可信局部规则、范围和可写边界；不假设子 Agent 自动继承当前上下文。
- 本入口与 `AGENT_RULES.md` 或更高层约束不一致时，以后两者为准。
