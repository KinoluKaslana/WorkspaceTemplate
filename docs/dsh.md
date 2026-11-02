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

| dsh 工具 | hook 事件 | 实测行为（dsh 0.2.1-rc.4） |
|---|---|---|
| `read` | `post_read` | 记读证据；正文解析要求「行号连续 + 标准尾注」 |
| `write` / `edit` / `str_replace_editor` | `pre_write` / `post_write` | 目标取 `file_path` 或 `path`；两者都缺 → 拒绝（不猜） |
| `bash` / `pwsh` → hook 侧 `Bash` | `pre_bash` | 未读时：静态重定向 **拒**、解释器写 **拒**；变量间接 / `awk` 重定向 **放行（盲区）** |
| 其他 | — | 不拦 |

**bash 实测矩阵**（未读状态，同一 hook 直接驱动）：

```
echo hi > zz.txt                  → deny（静态可判写）
python3 -c "open('a.txt','w')..." → deny（解释器 B′）
f=b.txt; echo hi > $f             → ALLOW  ← 盲区
awk 'BEGIN{print "x" > "c.txt"}'  → ALLOW  ← 盲区（真实回合已确认文件被创建）
ls -la                            → ALLOW
```

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

**V1–V7 实跑结论**（2026-11-02，headless + 账号，真实模型回合）：

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| V1 | 子 agent 身份 | 子 `agent.id` 独立（`0c498da2-…`，父是 `session-…`），账本独立，父读证据**不**继承，子写被拒 | `.hook-state/dsh-<child>.json` reads 空；sub1.txt 未创建 |
| V2 | 分段读 | 只读 1..50/204 → 写入**拒**；补齐 1..204 → 放行 | 账本 coverage `[[1,50]]` vs `[[1,204]]` |
| V3 | hook 缺失 | **放行** + 日志 `hook-missing` | `v3.txt` 创建；`.dsh-gate.log` |
| V4 | 不可判 bash | 解释器 **拒**；变量间接 / `awk` **放行**（盲区） | 见上方矩阵；`v4e.txt` 被创建 |
| V5 | hook 超时 | 20s 超时 → **放行** + `hook-timeout` | `v5.txt` 创建 |
| V6 | 失败 read | 读不存在文件不入账；随后写入仍拒 | deny 在 read 失败后仍触发 |
| V7 | 修订变化 | 写后账本记 `post-write-invalidation`，第二次写入**拒** | revocations 记录；第二轮追加未发生 |
| V8 | `edit` 工具 | 未读时 `edit` 被拒（日志 `tool:edit`），文件未变 | e2e-edit.txt 仍为 `hello` |
| V9 | 会话隔离 | 前序会话已有完整读证据，新会话写仍被拒 | 多次 fresh session → deny |

**已知限制（如实）**：

- **bash 盲区**：变量间接与 `awk`/重定向类外部程序写不被识别——这是已声明的盲区，不是待修 bug（要修需要 shell 解析器）。
- 子 agent **没有委托机制**：dsh 子会话必须自己读政策才能写；父不能代授（与 CC/Codex 的 `delegations.py` 不对齐，属已知差异）。
- `pwsh` 只在 Linux 上得到 shell 解析路径，未在 Windows 实测。
- `str_replace_editor` 未在真实回合出现（模型优先用 `edit`）；`pwsh` 无法在 Linux 验证——标为**无法验证**，不算通过。

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
