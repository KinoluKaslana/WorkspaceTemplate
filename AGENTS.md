# Agent Workspace — Codex 入口

> 在本 Workspace 内工作的 Codex 主 Agent 与经授权子 Agent 必须遵守本入口。

## 首先

在做任何操作前，必须**完整读取 [AGENT_RULES.md](AGENT_RULES.md)**，并检查 `.workspace/bootstrap.json` 是否要求执行首次 Python 选择协议。

## Codex 专属补充

- 本文件只负责定位共同政策，不复制模式、Python、skills、工具、notes 或交付规则。
- 创建 Codex 子 Agent 时，父 Agent 必须显式传递根 `AGENT_RULES.md` 路径、目标路径上的可信局部规则、范围和可写边界；不假设子 Agent 自动继承当前上下文。
- 本入口与 `AGENT_RULES.md` 或更高层约束不一致时，以后两者为准。
