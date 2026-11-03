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

| dsh 工具 | hook 事件 | 实测行为（真实回合：0.2.1-rc.4；0.2.2-alpha.2 已复验插件加载与 8 条合成用例） |
|---|---|---|
| `read` | `post_read` | 记读证据；正文解析要求「行号连续 + 标准尾注」 |
| `write` / `edit` / `str_replace_editor` | `pre_write` / `post_write` | 目标取 `file_path` 或 `path`；两者都缺 → 拒绝（不猜） |
| `bash` / `pwsh` → hook 侧 `Bash` | `pre_bash` | 未读时：静态重定向 **拒**、解释器写 **拒**；变量间接 / `awk` 重定向 **放行（盲区）** |
| 其他 | — | 不拦 |

**pwsh 实测矩阵**（Windows PowerShell 5.1，经 win-host 在真实 Windows 上执行；下表为**真实创建文件**通过的部分）：

第一步证明「这些写法真的会写文件」——`f1..f9` + `src.txt` 全部落地（`cmd /c` 未实测成功，见下）：

```
Set-Content / Out-File / Add-Content / New-Item -Value / '>' / '>>' /
Tee-Object / [IO.File]::WriteAllText / Copy-Item
```

（`cmd /c "echo > f"` 在同一批取证里**没有**落地成文件——见下条反例，不要把它算进「已确认会写」。）

第二步证明「守门认得它们」——同一命令串喂给 `hook.py pre_bash`（client=dsh, dsh_tool=pwsh）：

```
10 种写形态 → 全部 deny（理由是「未读治理文件」）
Get-Content / ls / Get-ChildItem / Measure-Object → allow
iex (Get-Content x.ps1) → deny（写目标不可静态确定，fail-closed）
```

另有 9 种**等价但更隐蔽**的写法也一律 deny（别名与 .NET 写对象，不识别就是绕过）：

```
sc / ac / cp / mv / & 'Set-Content' / % { … } / foreach { … } /
New-Object System.IO.StreamWriter / [IO.File]::OpenWrite
```

**两个实测反例（不能想当然）**：

- `Set-Item -Path f.txt -Value x` 在 FileSystem provider 上直接报 `NotSupported`，**不会写文件** →
  不能算写形态，已从写表移除（否则是误报）。
- `cmd /c …` 在本机 PATH 命中的是 `D:\msys64\usr\bin\cmd`，PowerShell 报
  「无法在管道中间运行文档」→ **cmd 内层写没能实测**。分类器仍按写意图拦（fail-safe 方向），
  但文档不得声称「已验证会写」。

`2>&1 | Out-Null` 这类流重定向不算写文件，allow。

**误报已被压掉**（复核席提出、逐个构造验证）：引号内或 `#` 注释里的 `>` 不再当成重定向
（`Write-Output 'x > y'`、`Get-Content f # > f` 都 allow），`Select-String` 的参数里出现
`Set-Content` 也不会误判。

> **为什么以前是洞**：`pwsh` 的写形态与 POSIX 完全不同（`Set-Content`/`Out-File` 根本没有 `>`），
> 旧分类器把它们全判成「无写目标」→ 放行。现在 `wtarget.classify_pwsh()` 覆盖上述 10 类，
> 并用 `tests/test_pwsh_writes.py` 锁住。
>
> **未做**：dsh 本体没有在 Windows 上跑过（Windows 侧没装 dsh），所以「dsh 的 pwsh 工具 → 本 hook」
> 这一跳是**按契约接线 + 两侧各自实测**，不是端到端。

**bash 实测矩阵**（未读状态，同一 hook 直接驱动）：

```
echo hi > zz.txt                  → deny（静态可判写）
python3 -c "open('a.txt','w')..." → deny（解释器 B′）
f=b.txt; echo hi > $f             → ALLOW  ← 盲区
awk 'BEGIN{print "x" > "c.txt"}'  → ALLOW  ← 盲区（真实回合已确认文件被创建）
ls -la                            → ALLOW
```

拒绝理由带 `[workspace-gate:no-read]` 前缀（R98 码表；`pre_bash` 与 `pre_write` 一致，dsh 客户端才加）。

## 4.0 最后一次超严格测试（2026-11-03，预注册判据）

两位主理（gpt-6-sol / deepseek-v4-pro）在跑前审过用例表；判据先写死，跑完只逐条核对。

**Linux（dsh 0.2.2-alpha.2，headless，deepseek-flash）**

| 用例 | 结果 | 证据 |
|---|---|---|
| 未读 `write` / `bash` / `edit` | 拒（3/3） | `decision=deny`，目标文件/内容未变 |
| 整读后 `write` / `edit` / `bash` | 放（3/3） | 文件落地、内容正确 |
| 只读 1..50 后写 | 拒 | deny（缺失 51..204） |
| 读本身 | 放 | `post_read allow` |
| 子 agent 未读 | 拒 | 子会话 deny，文件未创建 |
| hook 缺失 / 超时 30s | 放 + 日志 | `hook-missing` / `hook-timeout` |
| hook 缺失 + `WORKSPACE_GATE_DSH_FAIL_CLOSED=1` | 拒 | `deny`，文件未创建 |

**Windows（同一版本，Node 24.22.2，headless）**

| 用例 | 结果 | 证据 |
|---|---|---|
| 未读 `write` / `edit` / `pwsh` | 拒（3/3） | deny，文件不存在 / 内容未变 |
| 整读后 `write` | 放 | 内容 `hi` 落地 |
| 整读后 `pwsh` | 放 | 文件落地 |
| 只读 1..50 后 `pwsh` | 拒 | deny |
| 子 agent 未读 | 拒 | 文件未创建 |
| hook 缺失 / 超时 30s | 放 + 日志 | `hook-missing` / `hook-timeout` |
| hook 缺失 + `FAIL_CLOSED=1` | 拒 | deny，文件未创建 |
| 非 UTF-8 输入（hook 级） | 拒 | `deny` + `UnicodeDecodeError` |
| deny 文案（正常输入） | 可读 | 含 `AGENT_RULES.md` 与缺失区间，无 U+FFFD |

**未测（如实）：** ① 会话隔离单独一例——每回合本就是新会话（未读即拒已由「未读 write」覆盖），但
「模型自行先读」会让该例退化成非未读场景，故不单列；② Windows 运行时工具枚举——`tools.definitions()`
在该平台返回空数组（Linux 可枚举 25 个），改用「Linux 枚举 + 全仓 grep 只命中 Anthropic SDK」作为
`str_replace_editor` 不存在的证据。

## 4.1 失败姿态开关

默认 **advisory**（放行 + 审计）；设 `WORKSPACE_GATE_DSH_FAIL_CLOSED=1` 则 hook 缺失 / 解释器缺失 /
超时一律 **deny**（`[workspace-gate:internal-error]`），把「故障时也拦」变成显式配置而不是默认假设。

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
- `str_replace_editor`：当前构建运行时枚举 25 个工具，未见该名；合成用例已验证其 `path` 映射会触发写前拦截。此项**仅映射已验，非真实工具端到端验证**。
- `pwsh`：**已在真实 Windows 上验证命令语义与分类结果**（见上方矩阵）；但 dsh 本体未在 Windows 运行，端到端未验。

### Windows 其余面（2026-11-03 真机）

| 面 | 期望 | 实测 |
|---|---|---|
| `edit` 未读 | 拒 | deny，`e1.txt` 仍为 `hello` ✅ |
| `edit` 读后 | 放 | allow，`e1.txt` 变 `world` ✅ |
| 只读前 50 行 → 写 | 拒 | deny，理由精确到「已覆盖 1..50，缺失 51..204」✅ |
| 子 agent 写（父读、子未读） | 拒 | 子会话两次写均 deny，`sub.txt` 未创建 ✅ |
| 读后治理文件被外部修订 → 再写 | 拒 | hook 级实测：deny（覆盖清空，缺失 1..206）✅ |
| hook 缺失 | 放行（advisory） | `C.txt` 被创建 ✅ |
| hook 超时 30s > 20s | 放行（advisory） | `D.txt` 被创建，日志 `hook-timeout` ✅ |

**模型层的一次真实拒绝**：让模型「读 → 用 `Add-Content` 改 `AGENT_RULES.md` → 再写」，模型自己按 R90（模板治理文件不得直接修改）**拒绝执行第 2 步**，因此撤销只能改用 hook 级构造验证。

**中文 deny 文案（已复验）**：Windows PowerShell 默认按本地码页（GBK）解码 node 的输出，重定向时中文会变乱码。
在脚本/会话里先执行

```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
chcp 65001 > $null
```

复跑结果（`X4` 回合）：`decision=deny` 且文案完整可读——「文件 AGENT_RULES.md：已覆盖 无；缺失 1..204；共 204 行（wc -l = 203，差 1 行为文末空行）。下一步：read_file 补齐缺失区间后重试。」判定不受影响，只是显示编码。

### Windows 侧（真机，2026-11-03 实测）

真实模型回合（`deepseek-flash`，`dsh --profile headless`，工作区 `D:\wt\ws`）：

| 回合 | 期望 | 实测 |
|---|---|---|
| 未读 → `pwsh Set-Content -Path winA.txt` | 拒 | `{"event":"pre_bash","decision":"deny"}`，`winA.txt` 不存在 ✅ |
| 未读 → `write` 工具 | 拒 | `pre_write deny`，文件不存在 ✅ |
| 先整读 `AGENT_RULES.md`（limit 400）→ 再 `pwsh Set-Content -Path winC.txt` | 放 | `post_read allow`（coverage 1..204 complete）→ `pre_bash` 未拒 → `winC.txt` 存在（4 字节）✅ |

**Windows 上踩到并修掉的四个坑（Linux 全都不复现）**：

1. `gate_core` 顶部 `import fcntl`：Windows 没有 → hook 一启动就崩 → 插件按 advisory 放行（**实测首跑真的把文件写出来了**）。改为 `fcntl`/`msvcrt.locking` 双实现。
2. 插件按 `python3` 找解释器：Windows 只有 `python`，spawn 失败 → 静默放行。改为按平台探测 + 环境变量覆盖。
3. 探测到 `python` 也可能是 **WindowsApps 的 Store 存根**（退出码 0、零输出、什么都不做）→ 现在用 `-c "import sys;print(sys.version_info[0])"` 实测，必须打印 `3` 才算可用。
4. **stdin/stdout 编码**：Windows 默认 GBK，而客户端传 UTF-8 → `json.load(sys.stdin)` 直接 `JSONDecodeError`（表现为读永远记不上、deny 文案乱码）。现在 `sys.stdin/stdout/stderr.reconfigure(encoding="utf-8")`。

> 第 4 条是最隐蔽的一条：hook 退出码 0、stderr 空，看起来「跑过了」，实际什么都没做。

### Windows 侧（真机）

- 装了 `@deepseek-ai/dsh` 0.2.2-alpha.2（npm，615 包）到 `C:\wt-dsh`；Node **必须 ≥ 24.9**（`engines`），
  Node 20 上 `dsh` **静默退出、rc=0、零输出**——这个坑记在这里，别再用旧 Node 判断"装坏了"。
- `--dump-config` 已确认插件挂上：`- id: workspace-gate-dsh` / `name: file:///D:/wt/ws/.dsh/workspace-gate/plugin.js`。
- **修掉一个跨平台真 bug**：插件原先硬编码 `python3`，Windows 只有 `python` → spawn 失败 → **静默 fail-open**。
  现在按平台探测（win32: `python`,`py`,`python3`；其他: `python3`,`python`），仍可用
  `WORKSPACE_GATE_DSH_PYTHON` 覆盖；都找不到 → 记 `python-missing` 并放行（advisory，不假装拦）。

> 本页只记 dsh 的接线与实测结论；**接入新 harness 的通用测试清单与平台坑**见 [`harness-onboarding.md`](harness-onboarding.md)。

### 版本漂移（必须知道）

- 上表与 V1–V11 的**真实回合**证据跑在 `0.2.1-rc.4` 上；本机现为 **`0.2.2-alpha.2`**（`dsh --version`）。
- 在新版本上已复验：`--dump-config` 仍出现 `workspace-gate-dsh` → `file://…/plugin.js`；
  `tests/dsh_harness.mjs` **8/8 PASS**（未读拒 / 读后放 / 改版再拒 / 非写放 / 工作区外放 / edit / 缺身份 fail-closed）。
- **未做**：0.2.2-alpha.2 上的真实模型回合。插件契约（`tools/pre-execute`）未变，但这条不能拿旧证据顶替。

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
