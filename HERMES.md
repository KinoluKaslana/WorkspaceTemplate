# Agent Workspace — Hermes 入口

> 在本 Workspace 内工作的 Hermes 主 Agent 与经授权子 Agent 必须遵守本入口。

## 首先

在做任何操作前，必须**完整读取 [AGENT_RULES.md](AGENT_RULES.md)**，并检查 `.workspace/bootstrap.json` 是否要求执行首次 Python 选择协议。

## Hermes 专属补充

- 本文件只负责定位共同政策，不复制模式、Python、skills、工具、notes 或交付规则。
- 创建子 Agent（`delegate_task` 或 spawn 的 Hermes 进程）时，父 Agent 必须显式传递根 `AGENT_RULES.md` 路径、目标路径上的可信局部规则、范围和可写边界；不假设子 Agent 自动继承当前上下文。
- **全局记忆边界（落实 `AGENT_RULES.md` §9.1）**：Hermes 在本 Workspace 内形成的可复用经验只写入 `notes/`（按 §9 建 note），**不主动写入 Hermes 全局持久记忆**（`~/.hermes/memories/`，含 memory 工具写入）；全局记忆中关于本 Workspace 至多保留一条指向 `notes/INDEX.md` 的指针。环境事实归 `TOOLS.md`，同样不进全局记忆。
- Hermes 以 `--ignore-rules` 启动时会跳过项目上下文；这种会话不属于本入口与 `AGENT_RULES.md` 可保证覆盖的范围。
- 本入口与 `AGENT_RULES.md` 或更高层约束不一致时，以后两者为准。

## Workspace 级人格（可选，仅约束 Hermes）

本模板默认不定义人格，Hermes 使用其全局人格（`$HERMES_HOME/SOUL.md`）。若用户需要 Workspace 级人格/语气定义，**只写在下面的引用块中（或此节引用的单独文件）**——它经 Hermes 的项目上下文机制（`.hermes.md`/`HERMES.md` 向上遍历至 git 根）只对 Hermes 生效；**禁止写入 `AGENT_RULES.md`**：该文件约束所有客户端，人格定义会造成跨客户端越权。与全局人格冲突时，以用户当前明确指定为准。

<!-- 如需 Workspace 级 Hermes 人格，在此定义；不需要时保持本节为空。 -->
