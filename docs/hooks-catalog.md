# 工作区守门需要哪些 hook（三 harness 对照 + 新 harness 照搬清单）

> 适用：政策 **3.1.0**，插件 `workspace-gate` **v1.9.0**，客户端 hooks `.workspace/hooks/hook.py`。
> **本文是实现说明，不构成政策**：授权、禁止、例外与判定标准只写在 `AGENT_RULES.md`；本文与政策冲突时以政策为准，且不得以读本文替代必读规则。
> 目的：让「先读治理文件、再写工作区文件」在任何 harness 上都能被**机械判定**，而不是靠模型自觉。

## 0. 一句话模型

守门只回答三个问题：**这是谁**（会话 / 子 agent 身份）、**读过没有**（当前修订的完整读证据）、**真的写了吗**（写前意图 → 写后提交的对账）。对应三类 hook：

```
创建期  SessionStart → 建账 + 取 gate 指纹基线；SubagentStart → 登记身份、建子账本
每次工具 PreToolUse(写/命令) / PostToolUse(读/写) → 判许可、记证据、对账 commit
收尾    Stop / SessionEnd / SubagentStop   → 补漏（转录回放）、冲刷账本、边界审计
```

## 1. 必需事件（8 类 + 1 可选）

| # | 事件 | 何时触发 | 为什么必须有 | 我们的用法 | 缺了会怎样 |
|---|---|---|---|---|---|
| 1 | **SessionStart** | 会话创建、首条消息前 | 没有它就没有「会话 → 工作区」绑定，也拿不到基线 | 记工作区基线、绑定 `session_id` | 分不清「本来就在」和「会话中途被写」，`unobserved` 会误报 |
| 2 | **SubagentStart** | 主 agent 派生子 agent | 子 agent 是独立写入方，必须独立建账 | 存 `parent_session_id`/`agent_id`，子账本独立 | 子 agent 冒用父的读证据 → 未读也能写 |
| 3 | **PreToolUse（写工具）** | `Write`/`Edit`/`apply_patch`/`write_file`/`patch` 前 | **唯一能真正拦住**的时刻 | 判目标归属 + 当前修订的完整读证据 | 只剩事后审计，没有阻断 |
| 4 | **PreToolUse（命令）** | 每条 shell 命令前（**仅订阅 Bash 的 harness**：本仓只有 Codex） | 读常走 `cat`/`sed`；写也可能藏在 shell 里 | 剥壳后识别读/写意图 | 读证据拿不到；shell 写失控 |
| 5 | **PostToolUse（读）** | 读工具成功后 | 证据必须绑**当时磁盘内容**，否则「读旧的写新的」会被误放行 | 行区间 + 记账时磁盘内容指纹（正文不落盘）。客户端 hooks 另做内容锚定比对（返回正文须等于同一快照）；**插件不做该比对** | 只按「调用过读」放行，可被截断读绕过 |
| 6 | **PostToolUse（写）** | 写工具成功后 | 把写前意图与写后字节对账 | 落 `commit`；无 intent 的写落 `unobserved` | 只能证「字节过了 gate」，不能证「哪次写被授权」 |
| 7 | **Stop** | 一轮回答结束前 | 本仓不订阅 CC 的 `Bash` 写，只能在回合末回放转录事后归因 | 回放转录补账、算 `would_deny` | shell 写永久失踪（只有事后归因） |
| 8 | **SessionEnd / SubagentStop** | 会话 / 子 agent 结束 | 冲刷账本、写边界审计 | 落 `agent-stop`、边界记录 | 账本半截，跨会话对账断裂 |
| 9 | *（可选）* **UserPromptSubmit** | 用户每次提交提示 | 第一次工具调用前就能提醒读规则 | 客户端 hooks：未读期间每次提交都提醒（只更新活动计数，不留证据档）；Hermes 插件改用 `pre_llm_call`，**每会话至多一次** | 不影响正确性，只影响体验 |

## 2. 三个 harness 的实际接线

| 概念 | Claude Code | Codex CLI | Hermes（插件） |
|---|---|---|---|
| 配置文件 | `.claude/settings.json` | `.codex/hooks.json` | `.hermes/plugins/workspace-gate/plugin.yaml` |
| 写前拦截 | `PreToolUse` matcher `Write\|Edit` | `PreToolUse` matcher `apply_patch` | `pre_tool_call`（`WRITE_TOOLS={write_file,patch}`） |
| 命令类 | 不订阅 `Bash` → 只能 `Stop`/`SessionEnd` 回放 | `PreToolUse`/`PostToolUse` matcher `^Bash$` | 无独立命令事件（工具调用统一走 `pre_tool_call`） |
| 读证据 | `PostToolUse` matcher `Read`（**只认原生 Read**） | `PostToolUse` matcher `^Bash$`（剥壳后认 `cat` / `sed -n a,bp`） | `post_tool_call`（`read_file` 正文 + 行号区间） |
| 身份 | `session_id` + 可选 `agent_id`（子 agent 与父共用 `session_id`，实测） | `session_id`；子 agent 实测 payload 带 `agent_id`，作用域键拼 `session_id.agent_id` | `session_id`（子 agent 有自己的 session） |
| 生命周期 | `SessionStart`/`SubagentStart`/`SubagentStop`/`SessionEnd` | 同左 | `on_session_start`/`subagent_start`/`subagent_stop`/`on_session_end` |
| 传参 | JSON stdin：`cwd`、`transcript_path`、`tool_input`、`tool_response` 等 | 同左（实测：`cwd, hook_event_name, model, permission_mode, session_id, transcript_path, tool_input, tool_name, tool_use_id, tool_response, turn_id`） | Python kwargs：`tool_name, args, session_id, task_id, tool_call_id, turn_id, api_request_id, status, result`，**无 `cwd`** |

**新 harness 至少要给**：创建期就有且稳定的 `session_id`、工具名、工具入参、工具返回、**当前工作目录或可解析的绝对路径**，以及「写前 / 写后 / 读后」三个时刻的回调。缺 `cwd` 时必须能给出可解析的相对目标锚点（Hermes 用 `task_id` 的 task cwd）；两者都没有就只能保守拒绝（§4.3）。

## 2.6 审计事件与拒绝码（实现清单）

**审计事件（13 型，落 `audit-<client>.jsonl`）**：`unresolved-command` / `authorized-gatefile-write` / `unsanctioned-gatefile-change` / `worktree-change` / `completion-gate` / `cc-bash-write` / `cc-bash-gap` / `gate-intent` / `gate-commit` / `unobserved-write` / `agent-start` / `agent-stop` / `gate-deny`。

**插件拒绝码（`[workspace-gate:<code>]`）**：`no-session`（无会话身份）/ `container-unmapped`（容器路径映射不回）/ `no-read`（缺当前修订的完整读证据）/ `unresolved-target`（相对路径无锚点）/ `internal-error`（插件异常，fail-closed）。

**Codex 静态可判写清单**：`>` / `>>` / `tee` / `sed -i` / `perl -i` / `truncate` / `cp` / `mv` / `install` / `dd of=`；解释器/包装类（`python*` / `sh` / `bash` / `zsh` / `dash` / `perl` / `make` / `env` / `sudo` / `nice` / `nohup` / `timeout` / `xargs` / `command` / `exec`）未读即 deny；其余形态只审计。

**hook 脚本**：`.workspace/hooks/` 七个（`hook` / `gate_core` / `wtarget` / `authz` / `template_anchor` / `cc_transcript` / `delegations`）+ `.codex/hooks.json` + `.claude/settings.json`，均随 `TEMPLATE_FILES` 哈希锁定。

## 2.7 实现细则（自政策 R98/R100/R104/R105 迁出的机械事实）

> 本节只陈述「机器怎么记账」，不构成义务；义务与判定标准仍在 `AGENT_RULES.md`。

**账本与审计落盘**：状态在 `.workspace/.hook-state/`（已 gitignore）。Hermes 插件订阅 `on_session_start`/`subagent_start`/`subagent_stop`；放行落 `intents{target,base_sha}`，写后落 `commits` 并追加工作区级 `commits.jsonl`（`client="hermes"`），无 intent 的写落 `unobserved`；`on_session_finalize` 落边界审计。客户端 hooks 把 `gate-intent`/`gate-commit`/`unobserved-write`/`gate-deny`/`agent-start`/`agent-stop` 写进 `audit-<client>.jsonl`，`commits.jsonl` 按 sha 精确匹配、轮转时按 target 压缩。

**CC 转录回放**：只读 payload 给的 `transcript_path`（校验 slug 与 session，不遍历 `projects/`）；按静态可判目标分类转录里的 `Bash`，用 `Read` 记录重建读证据并复算 `would_deny`（记 `cc-bash-write`），归因不上的记 `cc-bash-gap`。命令原文默认只记 digest，`audit_command_text=true` 才落原文。

**委托记录（delegations）**：`.workspace/.hook-state/delegations.jsonl` 存 `{id,parent_scope,child_scope,targets[],client,gate_fingerprint,issued_at,expires_at,status}`。签发命令：
```
python3 .workspace/hooks/delegations.py --workspace . mint \
  --parent <父会话> --child <子会话> --client hermes --ttl 1800 \
  --targets /abs/a.txt,/abs/b.txt
```
签发前校验父会话对 gate 当前修订的完整读证据；目标必须是绝对路径且不含通配；`check` 只读校验，`revoke` 撤销。插件与 `gate_core` 在 `no-read` 前先查有效委托，命中记 `delegation-allow`。**注意**：同 uid 可改委托文件，与其余账本同一信任边界（R107）。

## 3. 接入新 harness 的 7 步

1. **接创建期**：拿 `session_id`；有子 agent 的一并接 `SubagentStart`（`parent_session_id` + 子 id）。
2. **接写前**：解析目标路径；解析不出所属工作区就**保守拒绝**，不放行。
3. **接读后**：记「读了哪些行 + 当次磁盘指纹」；正文对不上就不记账。
4. **接写后**：`intent → commit` 对账；无 intent 的写落 `unobserved`。
5. **接收尾**：把不经过钩子的写（shell）在回合末回放补账。
6. **定义失败姿态**：分两层说清——① **钩子内部异常**：策略/写前钩子（`PreToolUse` / `pre_tool_call`）一律 deny，观察型钩子（读后、收尾）按 fail-open 处理；② **客户端层面的超时/加载失败**默认 fail-open（R107），**Hermes 例外**：其 `pre_tool_call` 超时也 fail-closed（`hermes_cli/plugins_dispatch.py:48`）。新 harness 必须写明自己属于哪一种。
7. **跑三条验收并留证据**（缺一不可；本仓已在 MUX 压测跑过，新接入须自行重跑并附日志）：
   - A 读全 → 写放行，账本有 `commit`；
   - B 未读 → 写被拦，且客户端把 block 原文回传模型（不能静默失败）；
   - C 子 agent 未读 → 被拦，**父的读证据不能让子通过**。

## 3.1 活体拒绝腿怎么测（以 CC 为例，21 次实测）

1. 给子 agent 一份**结构性无读**的定义：`.claude/agents/deny-live.md` 的 frontmatter 写 `tools: Write`（单值；多值用 YAML 列表）。
2. 让父会话用 `Task(subagent_type=deny-live)` 派它，任务只说"创建 X，内容 Y"，**不要**写"不要读"之类的禁令（模型会把禁令当成错误指令而不做事）。
3. 断言四件事：① 子转录（`~/.claude/projects/<slug>/<sid>/subagents/*.jsonl`）工具集只有 `Write`；② `tool_result` 里出现守门原文；③ 目标文件不存在；④ `audit-<client>.jsonl` 里有该子作用域（`session.agent`）的 `gate-deny`。
4. 必须同时跑**允许对照**（`tools: [Write, Read]`，期望 5/5 通过）与**账户隔离对照**（父读、子冷写，期望仍拒），否则无法把拒绝归因于"缺读"。
5. 已声明的盲区要单列：`tools: Bash` 的 `echo >> gate` 实测 3/3 **不拦截**，这是覆盖盲区，不能算 PASS。

实测数据与原始日志见 `mc-261030/LIVE-DENY-REPORT.md`；批量复跑脚本 `mc-261030/run_live_matrix.py`。

## 4. 已知差异与坑（都踩过）

1. **CC 只认原生 `Read`**：让子 agent 用 `cat` 读，读不记账 → 写被拒（MUX 压测实测）。Codex 相反：没有原生读工具，只能靠 `Bash` + 剥壳识别。
2. **Codex 同轮并发读+写会被保守拒绝**：`cat` 与 `apply_patch` 同一轮并行时，`post_bash` 记账晚于 `pre_write` 判定 → `已覆盖 无` 被拒（MUX 压测实测）；顺序执行即通过。这是 fail-closed 的代价。
3. **Hermes 的 `pre_tool_call` 没有 `cwd`**：相对路径的主锚点是 `task_id` 的核心同源 task cwd（`session_id` 只用于提醒与归属面，不发许可）；两者都拿不到 → 无法归属 → 拦。
4. **空 `session_id` 时工作区内的写一律拦**（政策 2.7.0 / R98）：包括无法解析的相对目标；**非写工具与工作区外绝对路径写入不受影响**。
5. **hook 未送达 = 客户端 fail-open**：客户端不调钩子时，守门只能事后用 `unobserved` 暴露，不能阻止。所以口径是 **advisory**（写前拦截 + 事后可判定），不是安全边界。
6. **子 agent 的 shell 写**（CC `Bash`）不经过 `PreToolUse`：主会话在 `Stop`/`SessionEnd` 回放归因，子 agent 的 `agent_transcript_path` 在 `SubagentStop` 单独回放（作用域含 `.agent_id`，v2.8.4 起）。

## 5. 术语

- **gate 文件**：必须先读的治理文件（默认 `AGENT_RULES.md`）。覆写文件按客户端分：Hermes 用 `.hermes/plugins/workspace-gate/plugin.custom.json`，Codex/Claude 的 hooks 用 `.workspace/gate.custom.json`。
- **读证据**：行区间 `coverage[]` + 修订指纹 `c<len>:<sha256>`；覆盖完整且指纹等于判定时刻磁盘内容才算 `read=True`。**客户端 hooks 额外做内容锚定**（返回正文须等于同一快照的期望字节）；**Hermes 插件不做这层比对**，只按行区间 + 磁盘指纹记账。正文本身不落盘。
- **intent / commit / unobserved**：写路径上的三个记账点——`gate-intent`（**写前**的授权意图，带 `base_sha`）→ `gate-commit`（**写后**对账，带 `new_sha`）；没有对应 intent 的写落 `unobserved-write`。
- **fail-closed / fail-open**：出错时拦 / 放。守门只在写前判定上 fail-closed。
- **advisory**：能拦能查，但不构成安全边界（客户端可能不送达钩子）。
