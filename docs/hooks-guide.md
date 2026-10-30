# 工作区级 Hook 技术指南

> 面向两类读者：**完全没接触过 hook 的人**（先看第 0–2 章，用比喻理解机制），以及**要改代码/排查问题的人**（第 3–6 章是逐 harness 的实现拆解）。
> 对应政策：`AGENT_RULES.md` §12（Hermes 侧）与 §13（Codex / Claude Code 侧），当前政策版本 **2.6.0**。

---

## 0. 三分钟看懂：这个东西到底在干什么

工作区里有一份「规矩文件」`AGENT_RULES.md`。规矩里写着：**动手改工作区文件之前，先把这份规矩完整读一遍。**

问题在于：AI 会忘、会跳读、会只读前 20 行就开始改。靠嘴上说「你要先读」并不可靠。

所以模板给三个客户端（Hermes、Codex CLI、Claude Code）各装了一道**机械门禁**：

```
AI 想写文件
   │
   ├─ 目标在工作区外？            → 放行（不管）
   ├─ 本会话没完整读过治理文件？  → 拦下，并告诉它「缺哪几行、该读哪个文件」
   └─ 读全了（且读的是当前版本）？→ 放行
```

「完整读过」不是靠 AI 自称，而是**校验它到底读了哪些字节**（见 §2.2）。所以 AI 骗不过去；它要么真读，要么改写别的地方。

---

## 1. 一个比喻：门禁卡 + 借阅登记

- **治理文件**＝图书馆的规则手册。
- **读文件**＝在登记台领一张「借阅卡」，卡上记着：哪本书、哪一版、读了第几页到第几页。
- **写文件**＝进书库。门禁会看卡：卡上的版本必须和书架上当前这本一致，页数必须覆盖整本（不能只翻首页和末页）。
- **写治理文件本身**＝改规则手册。这要额外一张「一次性改造许可」（令牌），改完卡立即作废，下次要重新借阅。
- **审计**＝门口的黑匣子，只记「谁、什么时候、想写哪、是放行还是拦下」，不抄内容。

这些比喻对应到代码里的具体名词，见下一章。

---

## 2. 与客户端无关的共同机制（三种 harness 共用）

### 2.1 治理文件（gate files）与工作区身份

- 默认治理文件：`AGENT_RULES.md`；可经 `.workspace/gate.custom.json` 的 `gate_files` 增补/替换（多个文件时**逐个**判定）。
- 工作区身份：**存在 `.workspace/bootstrap.json`** 即视为一个工作区。不依赖治理文件是否存在——治理文件被删时不是「没有工作区」，而是「读不到 → 拦」（fail-closed）。
- 目标解析：写入目标先按**绝对路径**落在哪个工作区来决定用哪套规则；解析不出的相对路径一律拒绝（有意为之的过度拦截）。

### 2.2 「完整读过」到底是什么（核心）

代码里的读证据长这样（`gate_core.py`）：

```json
{
  "AGENT_RULES.md": {
    "anchor": "content",
    "fingerprint": "c28317:<sha256 全文>",
    "coverage": [[1, 188]],
    "complete": true
  }
}
```

三个要点：

1. **内容指纹**：`c<字节数>:<全文件 sha256>`。读的那一刻和写的那一刻指纹必须一致；文件被别人改过（哪怕只改一个字节）→ 旧读证据立即作废。
2. **覆盖区间**：读证据记的是「读到了第几行到第几行」，必须**连续覆盖 1..total、中间无缝**。只读首尾两行不算；读了一半再补另一半可以累加（不允许有洞）。
3. **行数口径**：`total = len(文件字节.split(b"\n"))`，对以换行结尾的文件会比 `wc -l` 多 1（末尾那个「空行哨兵」）。
   - 所以 deny 文案会同时给两个数：`共 188 行（wc -l = 187，差 1 行为文末空行）`。
   - 用 `sed -n '1,187p'` 读到最后一行是**足够**的（代码会把 187 升格成 188）；反过来真去读 `1,188` 会因越界被拒记（`request-past-eof`）。

### 2.3 判定伪代码（三种 harness 都走这套语义）

```python
for target in 本次写入目标:
    ws = 找到 target 所属工作区
    if not ws: continue                     # 工作区外，不管
    for gate in gate_files(ws):
        fp = sha256(读 gate 的当前内容)
        if 记录里的指纹 != fp: 撤销该 gate 的读证据     # 修订变了
        if not (complete and coverage 连续覆盖 1..total):
            需要重读.add(gate)
    if 需要重读 and not 命中了合法改造令牌:
        return deny("先完整读取治理文件…缺失 x..y；目标：…")
return allow
```

### 2.4 写治理文件本身：一次性令牌

合法地改治理文件（比如模板同步、用户授权修改）用操作级令牌：

```bash
python3 .workspace/hooks/authz.py mint --workspace . --target AGENT_RULES.md --ttl 300
```

- **单次消耗**：用一次就 `consumed=true`，同一令牌第二次必然被拒。
- **目标精确匹配**：不支持通配；多目标要逐条列出。
- **改完即失效**：写治理文件会改变它的指纹，旧读证据按 §2.2 自动作废，下次写之前必须重读。

### 2.5 审计事件七型（只记「谁/哪/动作」，不落正文）

| 事件 | 含义 |
|---|---|
| `unresolved-command` | 看见了 Bash 命令但无法静态判定它写哪（只记 `tool_use_id` + `digest` + `class`） |
| `authorized-gatefile-write` | 用令牌合法写了治理文件（记 nonce） |
| `unsanctioned-gatefile-change` | 治理文件字节变了但没走令牌；仅 mtime 变化记 `suspect:true`（不撤销许可） |
| `worktree-change` | 会话边界上的 git 差集（report-only） |
| `completion-gate` | `Stop` 时的完成声明门禁（advisory，不产生 deny） |
| `cc-bash-write` | Claude Code 事后归因：转录里的某条 Bash 写（含 `would_deny`/`read_state`） |
| `cc-bash-gap` | Claude Code 归因失败/跳过（原因码：`no-transcript-path`/`transcript-*`/`changed-but-untargeted` 等） |

---

## 3. Harness A：Hermes（项目级 plugin）

### 3.1 它在哪里、怎么被加载

```
<workspace>/.hermes/plugins/workspace-gate/
├── plugin.yaml     # 声明 provides_hooks
├── __init__.py     # 真正实现
└── selfcheck.py    # 合成 payload 自检
```

加载需要**两件事同时成立**：

1. `HERMES_ENABLE_PROJECT_PLUGINS=true`（允许发现项目级插件）
2. `~/.hermes/config.yaml` 的 `plugins.enabled` 里有 `workspace-gate`

> 注意：`hermes plugins list` **不会**列出项目插件（它只列内置插件）。是否生效看 `$HERMES_HOME/logs/workspace-gate-plugin.log` 里的 `workspace-gate active (...)` 一行。

### 3.2 它挂了哪些 hook

| hook | 作用 |
|---|---|
| `pre_tool_call` | 写之前判：目标在工作区内且本会话没有当前修订的完整读证据 → 拦截（内部异常也拦，fail-closed） |
| `post_tool_call` | 只读工具成功后记账（`read_file` 的正文与行号区间）；失败/截断不记 |
| `pre_llm_call` | 每个会话至多提醒一次「你还没读治理文件」 |
| `on_session_end` / `on_session_finalize` | 边界审计（按回合有界追加，恢复会话时分阶段记录） |

### 3.3 数据流

```
LLM 发工具调用
   │
   ▼
pre_tool_call ──► 解析目标 → 找所属工作区 → 校验读证据（指纹+覆盖）
   │                                   │
   │ 证据不足                           │ 证据齐全
   ▼                                   ▼
返回 block + 原因（含工作区路径与 target）   放行执行
   │
   ▼
post_tool_call ──► 若是 read_file：把「读了哪些行 + 当时的指纹」写入状态
```

状态文件：`<工作区>/.workspace/.hook-state/<session_id>.json`（已 gitignore）。

### 3.4 调参（不要改脚本）

```json
// .hermes/plugins/workspace-gate/plugin.custom.json
{ "enabled": true, "gate_files": ["AGENT_RULES.md"], "write_tools": ["write_file", "patch"] }
```

脚本属模板层锁定文件，直接改会在 `verify_rules.py` 报哈希漂移、模板同步时被整文件覆盖。

---

## 4. Harness B：Codex CLI

### 4.1 接线：`.codex/hooks.json`

```json
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "GATE_CLIENT=codex python3 .../hook.py session_start"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "GATE_CLIENT=codex python3 .../hook.py prompt_submit"}]}],
    "PreToolUse": [
      {"matcher": "^Bash$",       "hooks": [{"type": "command", "command": "... hook.py pre_bash"}]},
      {"matcher": "apply_patch",  "hooks": [{"type": "command", "command": "... hook.py pre_write"}]}
    ],
    "PostToolUse":  [ ... post_bash / post_write ... ],
    "Stop":         [{"hooks": [{"type": "command", "command": "... hook.py stop"}]}],
    "SessionEnd":   [{"hooks": [{"type": "command", "command": "... hook.py session_end"}]}]
  }
}
```

`GATE_CLIENT=codex` 是关键：payload 里没有可靠的「我是谁」字段，客户端身份靠这一个环境变量显式声明（`hook.py` 只用 payload 探测做兜底）。

### 4.2 Bash 分派：先看是不是 `apply_patch`，再静态识别写

```
Bash 命令字符串
   │
   ├─ 形如 apply_patch <<'PATCH' … PATCH / apply_patch '…' / apply_patch $'…'
   │     → 解析出 patch 目标 → 当作「写」处理（pre_write）
   │       解析不了（例如 mkdir x && apply_patch …、展开型 heredoc）
   │     → 可读 deny：告诉模型「单独调用 apply_patch，别加前缀」
   │
   ├─ 其余命令交给 wtarget.classify()
   │     ├─ 静态可判写（>/ >>/ tee/ sed -i/ cp/ mv/ install/ dd of= …）→ 按目标逐个判定
   │     ├─ 解释器/包装（python*/sh/bash/zsh/perl/env/sudo/xargs/timeout…）→ 未读即 deny（B′）
   │     └─ 其余不可判 → 只记 unresolved-command，不拦
   ▼
```

**为什么复合命令（`mkdir -p x && apply_patch …`）要拒而不是硬解析**：如果整条命令里还夹着别的写（比如 `printf … >> AGENT_RULES.md && apply_patch …`），只解析 patch 目标会漏掉前面那个写。宁可 fail-closed 给可读提示，也不放宽。

### 4.3 读与写之间

- Codex 的 Bash 读：`cat AGENT_RULES.md`、`sed -n 'a,bp' AGENT_RULES.md` 会被 `post_bash` 识别为读并记账（正文必须与磁盘逐字节一致；`sed` 区间越界不记）。
- 原生 `apply_patch` 工具：走 `pre_write` 判定；但 Codex 目前**不为原生 patch 发 `PostToolUse`**，所以「写后立即撤销」在 Codex 上靠下一次 Pre / Stop / SessionEnd 的修订复核兜底。

### 4.4 验证方法（真机，不写代码）

```bash
# 隔离 CODEX_HOME + 一个夹具工作区
CODEX_HOME=/tmp/cx-home codex exec --json \
  --skip-git-repo-check --dangerously-bypass-hook-trust \
  -s workspace-write -C /tmp/fixture \
  "不要读任何文件，直接执行：printf x >> AGENT_RULES.md"
# 期望：输出里出现 Command blocked by PreToolUse hook: …缺失 1..N…
```

---

## 5. Harness C：Claude Code（写前拦 + 事后归因）

### 5.1 接线：`.claude/settings.json`

| 事件 | matcher | 干什么 |
|---|---|---|
| `SessionStart` / `UserPromptSubmit` | — | 注入「你还没读治理文件」的提醒 |
| `PreToolUse` | `Write|Edit` | 写前判定（deny） |
| `PostToolUse` | `Read` | 读记账 |
| `PostToolUse` | `Write|Edit` | 写后处理（写治理文件即消费许可） |
| `Stop` / `SessionEnd` | — | 差集审计 + 完成声明门禁 + **Bash 转录归因** |

### 5.2 为什么 CC 的 Bash 不拦

Claude Code 对我们的 hook 只开放 `Write|Edit|Read` 等工具事件，**不订阅 Bash 的 PreToolUse**（用户明确不考虑给 CC 订阅 Bash）。所以模型用 `echo > file` 这类 Bash 写法时：

- 不会被拦；
- 也没有「逐条实时」记录。

替代方案是**事后归因**：CC 在 `Stop`/`SessionEnd` 的 payload 里给出本次会话的**转录文件路径**（`transcript_path`），里面完整记录了每条 `Bash` 命令和每次 `Read` 的结果。hook 在会话边界把这个转录「回放」一遍，回答一个问题：**如果当时有门禁，这条写会不会被拦？**

### 5.3 转录回放怎么做（`cc_transcript.py`）

```
Stop/SessionEnd
   │
   ├─ 1. 校验路径：realpath 必须在 ~/.claude/projects 内；project slug 与会话 cwd 一致；
   │        文件名 == session_id。不遍历 projects 目录（里面可能躺着别人的项目）
   │
   ├─ 2. 增量读：从上次 offset 继续读（容忍正在写的半行）；单文件 >8MB 或 >20000 条 →
   │        截断到上限并记 transcript-truncated，不整段丢弃
   │
   ├─ 3. 绑定 cwd：转录记录必须带 cwd 且与会话 cwd 一致；一条都没有 → 记
   │        transcript-cwd-missing 并跳过（fail-closed，防 slug 碰撞跨项目误读）
   │
   ├─ 4. 重建读证据：解析 Read 的 tool_use 与 tool_result，剥掉 CC 附加的
   │        <system-reminder> 尾注，要求与治理文件对应行**逐行相等**；
   │        截断/短读/内容不符一律不授证
   │
   ├─ 5. 复算 would_deny：把每条 Bash 命令交给同一个 wtarget 分类器，
   │        对命中的治理文件目标查「当时是否有完整读证据」
   │
   └─ 6. 落审计：cc-bash-write（would_deny / read_state / revision / result）
            或 cc-bash-gap（无法归因的原因码）
```

### 5.4 三个容易踩的坑（都已修，写在代码里）

1. **跨 Stop 的读证据**：CC 的 `Stop` 每轮都会触发，模型可能第 1 轮读、第 5 轮写。读账本（`books`）必须持久化到 sidecar（`cc-transcript-<session>.json`），否则第 5 轮会算成「没读过」。
2. **写操作会改掉治理文件本身**：写完之后磁盘内容变了，若拿**写后**内容当参考修订，会把写前读的证据误判为失效。所以对「本轮被写、本轮没读」的文件，用**持久化账本（写前修订）**判定这次写；写完之后再把账本换成写后修订。
3. **写完必须重读**：写治理文件即消费许可。同一轮里第二次写、或下一轮不重读就写，都应判 `would_deny=true`（`revision=post-write`/`persisted` 表示这是推导修订，不是原生修订）。

### 5.5 `would_deny` 的语义（别读错）

- `would_deny=true` = 「按当时的证据，这次写本来会被门禁拦下」；
- **不等于**「这次写被阻止了」——CC 不拦 Bash，写入其实已经发生；
- 转录可以被同 uid 删改，所以它只是**审计复算**，不是安全边界。

### 5.6 验证方法

```bash
claude -p "用 Read 工具读 AGENT_RULES.md 全文，然后 DONE" \
  --session-id <uuid> --permission-mode bypassPermissions --output-format json
claude -p "执行一条 Bash：printf x >> POLICY2.md" \
  --resume <uuid> --permission-mode bypassPermissions --output-format json
# 然后看 <workspace>/.workspace/.hook-state/audit-cc.jsonl 里的 cc-bash-write
```

---

## 6. 三种 harness 一张表

| 能力 | Hermes plugin | Codex CLI | Claude Code |
|---|---|---|---|
| 写前拦截（原生文件工具） | ✅ `write_file`/`patch` | ✅ 原生 `apply_patch` | ✅ `Write`/`Edit` |
| 写前拦截（Bash 静态写） | ⛔ 不在该层 | ✅ `>`/`>>`/`tee`/`sed -i`… | ⛔ 不订阅 |
| Bash 写的事后归因 | ⛔ | ✅（Pre/Post 事件本身） | ✅ 转录回放（`cc-bash-write`） |
| 读证据 | 行号 + 内容指纹 + 覆盖区间 | 同左 | 同左（从转录重建） |
| 写治理文件需令牌 | 由 §12 判据覆盖 | ✅ `authz.py mint` | ✅ 同左 |
| 完成声明门禁（advisory） | 会话结束时审计 | ✅ `Stop` → `completion-gate` | ✅ `Stop` → 同左 |
| hook 未启用/超时 | 客户端不放行即不生效 | **fail-open**（客户端行为） | **fail-open** |

---

## 7. 常见问题

**Q1：deny 说「共 188 行」，但 `wc -l` 是 187，是不是 bug？**
不是。代码按 `split(b"\n")` 数，末尾空行算一个「哨兵」；文案现在会同时写 `wc -l = 187`。读 `1..187` 就够；真去读 `1..188` 反而会被判越界不记账。

**Q2：为什么 `mkdir -p x && apply_patch …` 被拒？我只想先建目录。**
把两条命令拆开：先 `mkdir -p x`，再单独 `apply_patch <<'PATCH' …`。复合命令里可能夹带别的写，静态解析不可靠，所以 fail-closed 并给可读提示。

**Q3：hook 挂了会不会把工作区锁死？**
不会。hook 超时/加载失败是**客户端 fail-open**（放行），不是我们主动放行——这是 §13 R107 明说的边界。

**Q4：AI 用 `python -c` 写文件，能拦吗？**
Codex 侧：未读时按 B′ 拒（解释器/包装类）；已读后审计放行（不解析内层）。CC 侧：不拦，只能事后转录归因（`class=interpreter_wrapper`）。

**Q5：能不能防住「读了之后先改文件再改回来」？**
同 uid 下不能。`unsanctioned-gatefile-change` 能发现字节变化并撤销许可；但攻击者把内容改回去、再伪造时间戳，属于主动绕过，超出本机制范围（需要沙箱/OS 权限层）。

**Q6：我需要给团队新接一个客户端（比如 Gemini CLI），要做什么？**
见 §8。

---

## 8. 接入一个新 harness 的最小清单

1. **找到它的「写前」事件**：是否有 PreToolUse 形态的拦截点？能否拿到目标路径？
2. **写一个适配器**：把它的 payload 翻译成 `hook.py` 认识的字段（`session_id`、`cwd`、`tool_name`、`tool_input`、`tool_response`），并用 `GATE_CLIENT=<name>` 声明身份。
3. **判据复用**：直接调用 `gate_core.evaluate_pre_write()`，不要重写读证据逻辑。
4. **读侧记账**：确认它的 Read 结果能和磁盘逐字节比对（否则不授证）。
5. **补测试**：进程级（启动真实 hook 子进程 + 合成 payload）至少覆盖「未读 deny / 读全 allow / 改版撤销」。
6. **写文档**：把「订阅了哪些事件、哪些写路径没覆盖、fail-open 条件」写进 `TOOLS.md` 与本文档。

---

## 9. 自测与回归

```bash
# 8 套测试（当前共 80 例）
for t in hooks-tests/test_*.py; do printf "%-40s " "$t"; python3 -B "$t" 2>&1 | tail -1; done

# 治理文件完整性（文件哈希 + 条款级）
python3 scripts/verify_rules.py --workspace .
```

| 套件 | 覆盖 |
|---|---|
| `test_wtarget` | Bash 写形态识别矩阵 |
| `test_bash_dispatch` | CC/Codex Bash 分派、deny 文案、复合 apply_patch |
| `test_apply_patch_ansi` | `$'…'` 解码与不可解码形态 |
| `test_w2_anchor` | 指纹锚 / suspect / 撤销 |
| `test_authz` | 令牌单次、精确集合、无通配 |
| `test_template_anchor` | W4 完成声明门禁（clean / tampered / clone 缺失） |
| `test_cc_transcript` | 转录回放：路径三重校验、读证据、跨 Stop 账本、写后消费、同轮二次写 |
| `test_cc_wiring` | 真实启动 hook.py 的 Stop/SessionEnd 接线 |

---

## 10. 术语表

| 术语 | 意思 |
|---|---|
| gate file / 治理文件 | 必须先完整读才能写工作区的文件，默认 `AGENT_RULES.md` |
| 读证据 | 「某会话读过某文件某修订的哪些行」的记录（指纹 + 区间） |
| 修订（revision） | 治理文件内容的一个版本；指纹变了就是新修订 |
| EOF 哨兵 | `split(b"\n")` 对末尾换行产生的空行，导致 total 比 `wc -l` 多 1 |
| B′ | 解释器/包装类命令未读即拒的判定分支 |
| would_deny | CC 事后复算的「本该被拦」结论，不代表写入被阻止 |
| fail-open / fail-closed | 出错时放行 / 出错时拦截；本模板的**判定逻辑**一律 fail-closed，**客户端 hook 故障**是 fail-open |
| 令牌（token） | 合法单次改写治理文件的许可，`authz.py mint` 生成 |
