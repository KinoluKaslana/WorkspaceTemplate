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

## 5. 验证到什么程度（实测记录）

| 级别 | 证据 | 结果 |
|---|---|---|
| 配置层 | `--dump-config` 出现 `workspace-gate-dsh` → `file://…/plugin.js` | ✓ |
| 加载层 | 真实启动后 `.workspace/.dsh-gate.log` 记 `{"event":"apply"}` | ✓ |
| 逻辑层 | `tests/test_dsh_wiring.py`（真实 plugin + hook，8 条用例） | ✓ |
| **真实回合** | dsh headless + DeepSeek 账号登录，真实模型调用 | ✓ |

真实回合的实测结论（2026-11-02，`@deepseek-ai/dsh` 0.2.1-rc.4，`deepseek-flash`）：

1. 未读先写 → **被拒**，文件未创建，模型收到拒绝文案并要求用户授权；
2. 先 `read` 完整政策再写 → **放行**，文件创建，账本 `complete: true`；
3. `bash` 重定向写（`echo hello > f`）→ **被拒**（走 `pre_bash` 静态可判写）；
4. 非写工具（`glob` / `read`）→ 不拦。

真实 payload 形状（据此实现，不再是猜测）：

```jsonc
// pre-execute: exec.name / exec.arguments / exec.agent.id
{"tool":"write","args":{"file_path":"/abs/AGENT_RULES.md","content":"x"},
 "agentId":"session-<uuid>","callId":"call_00_..."}
{"tool":"bash","args":{"command":"echo hello > f","description":"..."}}
// post-execute (read): result = {isError:false, content:[{type:'text', text:
//   "<path>…</path>\n<type>file</type>\n<content>\n1: …\n\n(End of file - total 203 lines)\n</content>"}]}
```

**已知限制**：

- 子 agent 的会话身份：插件用 `exec.agent.id`；若 dsh 给子 agent 复用了父 id，子会话的账本会与父合并（未实测子 agent）。
- windowed read（`offset/limit`）逐段累计；只要求覆盖 `1..total`，不要求一次读完。
- `read` 正文解析只接受「行号连续 + 标准尾注」；形态变化即不记账（安全方向：拒绝写入）。

## 5.1 在 headless 里跑（账号登录，无 API key）

`headless` 默认只用 `DEEPSEEK_API_KEY` 路线；账号登录（Web 端登录写入 `~/.dsh/.credentials.yaml`）需要把账号 provider 挂进 profile：

```yaml
# /tmp/dsh-headless-account.yml
- id: agent-default-model
  config:
    provider: deepseek-account
    model: deepseek-flash
- insert:
    - id: credentials
      name: '@deepseek-ai/dsh-credentials-local'
    - id: authorization
      name: '@deepseek-ai/dsh-authorization'
    - id: deepseek-account
      name: '@deepseek-ai/dsh-deepseek-account-platform'
    - id: llm-deepseek-account
      name: '@deepseek-ai/dsh-llm-deepseek-account'
```

```bash
dsh --profile headless \
    --patch /tmp/dsh-headless-account.yml \
    --patch .dsh/workspace-gate/cordis.yml \
    --permission-mode workspace-write --approval-policy never \
    "先 read AGENT_RULES.md（offset=1, limit=203），再创建 e2e.txt"
```

## 6. 与其他 harness 的关系

| harness | 接线 | Bash 写 |
|---|---|---|
| Claude Code | `.claude/settings.json` → `.workspace/hooks/hook.py` | 不订阅，不拦（已声明盲区） |
| Codex CLI | `.codex/hooks.json` → 同一 hook | 静态可判写会拦 |
| Hermes | `.hermes/plugins/workspace-gate/`（Python plugin） | 由 plugin 判定 |
| **dsh** | `.dsh/workspace-gate/`（Cordis plugin） | 静态可判写会拦 |
