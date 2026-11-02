# DeepSeek Harness（dsh）接入

> 实现说明，不构成政策。义务与判定口径见 [`AGENT_RULES.md`](../AGENT_RULES.md) §12/§13。

## 1. dsh 是什么形态

`dsh` = DeepSeek Harness（`@deepseek-ai/dsh`），Cordis 插件内核。启动一个 profile：

```bash
dsh --profile web                 # 等价于 dsh web
dsh --profile web --patch extra.yml --port 3099 --no-open
dsh --profile web --dump-config   # 只打印合成后的插件树，不启动
```

拦截点是 Cordis 事件，不是 JSON 配置文件：

```ts
'tools/pre-execute'(exec: ToolExecution, next) => PreToolDecision
PreToolDecision = { kind: 'allow' } | { kind: 'deny', reason } | { kind: 'ask' } | { kind: 'cancel' }
ToolExecution   = { callId, rootCallId, name, arguments, agent?, signal, token }
'tools/post-execute'(exec, result, next) => PostToolDecision
```

内置工具名：`read` / `write` / `edit` / `str_replace_editor` / `bash` / `pwsh` / `glob` / `grep` / `web_fetch` …

## 2. 本模板提供的插件

```
.dsh/workspace-gate/
├── plugin.js      # 载荷翻译 + 决策映射（不含规则）
├── cordis.yml     # insert 补丁：把插件挂进 profile
└── package.json   # type: module
```

**判定不在这里**：插件把 dsh 的 `ToolExecution` 翻译成工作区 hook 的载荷，调用
`.workspace/hooks/hook.py`（同 CC / Codex 一套 `gate_core`），再把结果映射成 dsh 决策。
这样三个 harness 共用同一份读证据与拒绝理由。

启用：

```bash
dsh --profile web --patch .dsh/workspace-gate/cordis.yml
```

也可以把这段 `insert` 合并进你自己的 profile patch 层。

## 3. 映射表

| dsh 工具 | hook 事件 | 说明 |
|---|---|---|
| `read` | `post_read` | 记录读证据（正文经启发式提取；取不到就不记账） |
| `write` / `edit` / `str_replace_editor` | `pre_write` / `post_write` | 目标取 `file_path` 或 `path`，两者都缺 → 拒绝（不猜） |
| `bash` / `pwsh` | `pre_bash` | 走既有的 shell 静态可判写分析；不可判形态只审计 |
| 其他 | — | 不拦 |

拒绝理由带 `[workspace-gate:no-read]` 前缀（R98 码表）。

## 4. 失败姿态

| 情形 | 行为 | 原因 |
|---|---|---|
| 插件自身异常 / hook 超时（20s）/ spawn 失败 | **放行** + 写 `.workspace/.dsh-gate.log` | advisory，与 R99 一致 |
| 拿不到会话身份 | **拒绝** | 没有身份就没有读证据可核对（fail-closed） |
| hook 返回非 JSON | 放行 + 记日志 | 不把解析失败当拒绝 |

## 5. 验证到什么程度（分清证据等级）

| 级别 | 证据 | 命令 |
|---|---|---|
| 配置层 | `--dump-config` 里出现 `workspace-gate-dsh` 且解析为 `file://…/plugin.js` | `dsh --profile web --patch .dsh/workspace-gate/cordis.yml --dump-config` |
| 加载层 | 真实 `dsh web` 启动后 `.workspace/.dsh-gate.log` 出现 `{"event":"apply"}` | 启动一次 web profile |
| 逻辑层 | 真实 `plugin.js` + 真实 `hook.py` 的 7 条合成用例 | `python3 tests/test_dsh_wiring.py` |
| **未做** | **真实模型回合里的拦截** | 需要 DeepSeek API key；本机未跑 |

**已知未验证项**（不要当成已验证）：

1. dsh `read` 的 `tool_response` 形状 —— 插件用启发式提取（字符串 / `{content|text|output|stdout}` / `[{type:'text'}]`），
   取不到正文就不记账；首次真实会话请开 `WORKSPACE_GATE_DSH_PROBE=1`，原始载荷会写进
   `.workspace/.dsh-probe.jsonl`，据此校正 `_dsh_text()`。
2. dsh `write` / `edit` 的参数名（`path` 还是 `file_path`）—— 两者都接受，仍缺则拒绝。
3. 子 agent 的会话身份 —— 插件依次尝试 `exec.agent.session.id`、`exec.agent.id`、`ctx.session.id`、`DSH_SESSION_ID`；
   全拿不到即 fail-closed（子 agent 若因此被拦，是**已知限制**，不是 bug）。

## 6. 与其他 harness 的关系

| harness | 接线 | Bash 写 |
|---|---|---|
| Claude Code | `.claude/settings.json` → `.workspace/hooks/hook.py` | 不订阅，不拦（已声明盲区） |
| Codex CLI | `.codex/hooks.json` → 同一 hook | 静态可判写会拦 |
| Hermes | `.hermes/plugins/workspace-gate/`（Python plugin） | 由 plugin 判定 |
| **dsh** | `.dsh/workspace-gate/`（Cordis plugin） | 静态可判写会拦 |
