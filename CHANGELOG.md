# CHANGELOG — WorkspaceTemplate 变更记录

> 本文件只记录模板的政策/功能变更历史，供模板开发者与维护者查阅。工作区 Agent 无需读取本文件——实际生效规则以 `AGENT_RULES.md` 及其 custom 文件（`AGENT_RULES.custom.md`）为准。

## 2.9.1（2026-11-02）

- **PATCH 段：删历史旁注 + 同义压缩**（无判定语义变化、R 号零变动、custom 引用不受影响）。
  - 20 条改写：R18/R39/R41/R42/R61/R63/R67/R72/R73/R81/R90/R93/R96/R98/R100/R102/R103/R104/R105/R107。
  - `AGENT_RULES.md` 16,545 → **15,013 字符（−9.3%，≈−0.9k tokens/会话）**。
  - 保留的全部是义务句；删掉的是版本旁注、重复限定语与举例性解释（细节见 `mc-261030/apply_patch_compress.py` 的逐条映射）。
- 验证：107 个条款标记完好；`mark_rules check` 通过；registry 重建后 `verify_rules` clean；94 例测试与 selfcheck 全绿。

## 2.9.0（2026-11-02）

- **新增 skill `workspace-git-branch-upgrade`**（`.workspace/skills/workspace-git-branch-upgrade/SKILL.md`）：把已有仓库从 `main` 迁到 `dev`/目标分支，覆盖前置检查（remote/忽略/子模块/仓库类型）、三态迁移（新建 `switch -c`、远端跟踪 `--track`、仅落后 `merge --ff-only`；**禁止 `switch -C` 重置**）、默认分支指针（只改本地 `origin/HEAD`，不改服务端）、模板校验三条判据（exit 0 + `clean` + `template_clean`）、记录回写（`default_branch` 是记录不是当前分支）、验证清单与双态回滚。
- **方案来源**：GLM-5.3 主笔 → deepseek-v4-pro 与 gpt-6-sol 交叉评审 → 主理人裁决；两席共提 13 处修正（其中 DS 的「TEMPLATE_FILES=20 项」被判错，实测 28 项）。
- **登记**：`TEMPLATE_FILES` 28 → 29（`verify_rules.py`）；`skills/REGISTRY.md` 与 `TOOLS.md` §4.3 各补一条；R18 指向新 skill。
- 政策 2.8.5 → **2.9.0**（MINOR：新增能力，无判定语义变化）。

## 2.8.5（2026-11-02）

- **`gate-deny` 审计事件**：拒绝本身进审计（`tool` / `tool_use_id` / `targets` / 原因摘要），作用域键含 `.agent_id`，`Stop`/`SubagentStop`/`SessionEnd` flush 到 `audit-<client>.jsonl`。此前拒绝只存在于客户端转录，无法独立核验。`test_cc_wiring` 增 1 例（6 例）。
- **`scoped_id` 幂等**：客户端把 agent 折进 `session_id` 时不再二次拼接（实测出现过 `cc-<sid>.<agent>.<agent>.json`）。
- **活体拒绝腿压测（CC 2.1.87-dev，21 次）**：子 agent `tools: Write`（结构上无读工具）→ **10/10** 被拒且文件未落盘（子转录工具集仅 `Write`，审计有 `gate-deny`）；`tools: [Write, Read]` 对照 **5/5** 放行；父读后子冷写 **3/3** 仍拒（账户隔离）；`Bash` 写治理文件 **3/3 未拦截**（已声明的盲区，记 gap 不算 PASS）。带 `Bash` 的 `[Write, Bash]` 白名单未被客户端应用，该变体作废。
- **口径**：这是「结构性剥读下的 fail-closed 边界测试」，不等于正常配置能兜住对抗性写入。Codex 子 agent 活体拒绝腿仍 NOT-OBSERVED（子 agent 两次都回「没有具体任务」）；Hermes 的拒绝腿属提示词服从型，不得与 CC 等价。
- 政策 2.8.4 → 2.8.5。

## 2.8.4（2026-11-01）

- **CC 子 agent 转录归因接通**：`SubagentStop` 现在按 payload 的 `agent_transcript_path` 单独回放子转录（作用域键含 `.agent_id`，与主会话账本互不覆盖）。`cc_transcript.validate_path` 接受子 agent 形状 `<slug>/<session_id>/subagents/agent-<agent_id>.jsonl`（父会话不匹配仍拒）。新增 `test_cc_transcript` 用例 1 例（33）。
- **确定性验证（不依赖模型是否听话）**：
  - `verify_subagent_attribution.py`：构造「子 agent 转录里有 Bash 写 gate 文件」的样本 → 真实 `hook.py subagent_stop` → 审计产出 `cc-bash-write{would_deny=true}`，子作用域账本成立 → **PASS**。
  - `verify_codex_subagent_scope.py`：父会话读 gate 后，子 agent（`agent_id` 不同）写 → **deny（父读不传子）**；子自己读后写 → 放行 → **PASS**。
- **如实披露**：两次**活体**尝试（CC 子 agent 执行 Bash 写、Codex 子 agent 未读直接写）模型都没有照指令做（CC 子 agent 只读文件、Codex 子 agent 回「没有任务」），因此「真模型回合里的子 agent 拒绝腿」仍记为 **NOT-OBSERVED**，上述结论只覆盖确定性路径。
- 政策 2.8.3 → 2.8.4（R104 补子 agent 转录回放口径）。

## 2.8.3（2026-11-01）

- **插件 v1.8.0：与客户端 hooks 记账对齐**。放行写时落 `intents{target, base_sha}`；`post_tool_call` 处理写工具后落 `commits` 并追加工作区级 `commits.jsonl`（`client="hermes"`，字段与 hooks 同构）；无 intent 的写落 `unobserved{reason="no-intent"}`。`selfcheck` 增 P13-1..5。此前 Hermes 侧只记「读过没有」，无法与 CC/Codex 的 intent/commit 对账。
- **新增 `docs/hooks-catalog.md`**：三 harness 事件矩阵（何时触发 / 为何必须 / 缺了会怎样）、新 harness 接入 7 步与三条验收断言、6 条已知坑。经 LUNA + GLM 逐条对照 `file:line` 核验。
- **Codex 子 agent 排查结论**：「workspace Python mode is unconfigured」**不是 Codex 运行时错误**，而是子 agent 读到 §0 首次启动协议后按规则要求用户选择 Python 模式；子 agent 没有用户通道 → 反复空转、零工具调用。修复：① 子 agent 沿用父会话已记录模式，父也未配置则报告阻塞并停止（R2 补充）；② 压测夹具先把 `python.mode` 配置好再测子 agent。
- **政策 2.8.2 → 2.8.3**：R2 补「子 Agent 无用户通道时不得反复追问」；R98 补插件三值记账口径。

## 2.8.2（2026-10-31）

- **拒绝可观测（插件 v1.7.0）**：`_block()` 统一加 `[workspace-gate:<code>]` 机器可读前缀（`no-session` / `container-unmapped` / `no-read` / `unresolved-target` / `internal-error`），并落一条 best-effort 审计（`BLOCK code=… session=… tool=… target=…`）。**只加可观测性，不动判定**：`action` 仍为 `block`，消息仍指示先读哪份文件；审计写失败不影响 block。`selfcheck` 增 P12-1..3。
- **拒绝腿（客户端派发）实测**：新增 `deny_leg_probe.py` 走真实分派链 `model_tools._pre_dispatch_guards(skip=False)` → `_dispatch_pre_tool_call_hooks` → 插件 block：未读写 → `error_type=plugin_block`、目标文件未创建；真实读一次后同调用放行 → **PASS**。这是「客户端尊重 block 且不执行工具」的首个确定性证据（不含 LLM）。
- **真模型回合的自然拒绝：未观测到（如实记录）**。预登记探针（`DENY-PRE-OVERSIZE.md`，N=5，>100KB gate 文件迫使分页）**5/5 次模型仍完整读**后合规放行 → K=0，不追加轮次、不换提示词，也不把它写成命中率。
- **同轮验证「不影响其他 harness」**：CC/Codex 不加载本插件；其既有控制运行复跑（`test_cc_wiring` 5、`test_cc_transcript` 32 等九套 92 例全绿）、Codex 真机 `apply_patch` 写 smoke 通过、`verify_rules` clean。
- **四席研判收敛**（GLM / LUNA / deepseek-v4-pro / 主理人）：O2（无模型真实分派）为主证 + O5（机器码与审计）为辅证；O1（toolset 去读工具）经实测不可行（`-t write_file` → `Unknown toolsets`，`file` 组打包读写）；超长文件夹具降级为「预登记、全量披露的存在性探针」。三方一致要求：不得把插件层结论写成客户端层。

## 2.8.1（2026-10-30）

- **Codex 读证据被 shell 包装击穿（D3）修复**：Codex 每条 Bash 都是 `/usr/bin/zsh -lc "…"`，读识别只认裸 `cat`/`sed -n a,bp` → 读证据永远是 0，模型只能自铸令牌或停摆（真机压测复现：读后写仍 deny）。新增 `hook._strip_shell_wrap()`：整串字面解析**恰 3 词**、`exe ∈ {bash, sh, zsh}`、`flag ∈ {-c, -lc}`、递归 ≤3；读识别与 `_bash_patch` 共用。剥壳失败（含 `$`/反引号/反斜杠/引号不闭合）一律原样返回 → 不授证。新增 `test_bash_dispatch` W11/W12/W13（wrapped cat 授证、wrapped 越界不授证、含展开不剥）。
- **Hermes 插件生命周期（v1.5.0 → v1.6.0）**：注册 `on_session_start` / `subagent_start` / `subagent_stop`；主会话与子 agent 各按自身 `session_id` 建账（`agents` 映射 + `agent_events` 有界缓冲），子 agent **不继承**父会话读证据。`selfcheck` 新增 P11（父子登记、agent-start/stop、父读后子写仍 BLOCK）。
- **压测（同工作区并发）**：
  - Hermes 插件层 2 主 × 2 子：reader 子允许、未读子 BLOCK；父账本登记 2 个子身份，`agent-start/stop` 成对。
  - Codex 2 会话并发（修复后）：读后写 allow + `gate-intent`/`gate-commit`，未读写 deny；两者互不影响。
  - **Hermes 客户端层（重启后）部分成立**：根因是陈旧的 `.hermes/plugins/workspace-gate/__pycache__/__init__.cpython-311.pyc`（10-22 生成）被加载，日志一直打 `v1.4.3`；清掉 `__pycache__` 并重启 gateway 后加载 **v1.6.0**。2 主 × 各 2 子并发：父账本登记子身份、`agent-start/stop` 成对、子读证据互不继承。**客户端级 deny 腿仍未验证**（未读子由模型自行拒绝写入，不是 hook 拦截）。
- **仍未闭合**：CC 子 agent 的 Bash 写只有会话级转录归因；Hermes 插件不记录 worktree 基线/未观测写（与客户端 hooks 的 `unobserved` 不对等）；hook 未送达仍为客户端 fail-open。政策 2.8.0 → 2.8.1。

## 2.8.0（2026-10-30）

- **agent 作用域账本（P1）**：状态键 = `session_id`，子 agent 再拼 `.agent_id`（`gate_core.scoped_id/split_scoped_id`，`hook.py::_session` 从 payload 取 `agent_id`）。CC 主线程与子 agent 共用同一 `session_id`（实测 dump：`SessionStart` 无 `agent_id`、`SubagentStart`/子调用带 `agent_id`），因此**父会话的读证据不再授予子 agent**。
- **子 agent 生命周期**：`.claude/settings.json` 与 `.codex/hooks.json` 增订 `SubagentStart` / `SubagentStop` → `hook.py subagent_start/subagent_stop` 落 `agent-start` / `agent-stop`，子 agent 状态在 SubagentStop 时冲刷进审计。
- **intent / commit 两阶段**：allow 时落 `gate-intent{target, base_sha}`，写后 `evaluate_post_write` 落 `gate-commit{target, new_sha}`（sha 与磁盘一致才算）；无对应 intent 的写落 `unobserved-write{reason, detected_by}`，**不表示通过**。工作区级 `commits.jsonl` 记录 commit（按 sha 精确匹配；轮转时按 target 压缩保留最新，不丢历史判定所需的最新记录）。
- **worktree 差集按会话基线**：`SessionStart` 记 `worktree_baseline`，`Stop/SessionEnd` 只报相对基线变化；跨会话/跨 agent 的合法写通过全局 commit 账本排除，避免把别人的写误报成自己的 `unobserved`。
- **审计事件 7 → 12 型**：新增 `gate-intent` / `gate-commit` / `unobserved-write` / `agent-start` / `agent-stop`。政策 2.7.0 → 2.8.0（§13 R102 身份粒度、R105 事件与对账口径）。
- **回归**：新增 `hooks-tests/test_agent_scope.py`（8 例：作用域键、父子隔离双向、intent 的 base_sha、commit 的 new_sha、无意图写落 unobserved、worktree 差集、hook 子进程按 agent 落状态）；九套合计 **89 例** 全绿，`verify_rules` clean，`selfcheck` 全绿。
- **压测（CC 侧）**：同工作区 2 主会话 × 2 子 agent 并发——子 A（读后写）allow + intent/commit；子 B（未读）**deny**；`agent-start/stop` 各 4 条；主会话 worktree 审计不再把子写误报为 `unobserved`；另以 Bash 旁路写 `rogue.txt` 产出真实 `unobserved-write`。
- **未完成/边界**：Codex 子 agent payload 与压测受本地代理网络故障阻塞（仅确认二进制含 `SubagentStart`）；CC 子 agent 的 **Bash 写**仍只有会话级转录归因（`agent_transcript_path` 未回放）；Hermes 插件未接 `on_session_start`/`subagent_start`；hook 未送达仍为客户端 fail-open。


## 2.7.0（2026-10-30）

- **空 `session_id` 改为 fail-closed（行为变更）**：Hermes 项目插件原先在 `session_id` 为空时**跳过守门**（日志 `trust boundary`）。多 Agent/多 harness 并发测试证实这条通道会被用作无身份写入的口子（CC 主线程与子 agent 共用同一 `session_id`、Hermes persistence-disabled fork 与父共享 id、身份本身是客户端自报），因此改为：**写工具 + 目标落在工作区内（或相对目标无法解析 / 目标缺失）→ 一律 deny**；非写工具与工作区外绝对路径写入不受影响。插件 `1.4.3 → 1.5.0`（`plugin.yaml` 与激活日志同步）。
- **政策 §12 R98 补边界句**：会话身份缺失时不放行，写入工作区文件一律拦截（fail-closed），非写工具与工作区外写入不受影响；R99 措辞同步为「被拦截/放行时义务不变」。政策 `2.6.1 → 2.7.0`。
- **回归**：`selfcheck.py` 旧用例 `A3 empty session_id -> no gate` 改判为 **BLOCK**，新增 `P10-1a..1e`（空 id + 工作区写 / patch / 未知相对路径 → BLOCK；空 id + `read_file` / 工作区外绝对路径 → 放行）。全部通过。
- **文档**：`docs/hooks-guide.md` §3.2 与 §6 真值表、`hooks-tests/README.md`、两份 README 徽章同步到 2.7.0。
- **未包含（仍为提案）**：agent 粒度账本（`(harness, session_id, agent_id?)`）、`intent/commit` 两阶段与 `unobserved` 三值记账——见工作区 `mc-261030/SESSION-ID-DECISION.md`，待单独排期。

## 2.6.1（2026-10-30）

- **文档终审修订（`deepseek-v4-pro` + `glm-5.3` 两席、三轮研判，逐条回代码核对）**：`docs/hooks-guide.md` 修正 9 处——① §2.1 明确 gate 清单**按 harness 分流且互不联动**（Codex/CC 读 `.workspace/gate.custom.json`；Hermes 读 `plugin.custom.json`）；② §2.2 补「结构随 harness 不同、判据相同」（`gate_core` 的 `{anchor,fingerprint,fingerprint_source,coverage,complete}` vs Hermes 的 `read`+`coverage`+`coverage_rev`）；③ §2.2 第 3 点拆清「`sed` 严格区间越界 → `request-past-eof`」与「`read_file` 越界被夹到末尾」；④ §2.5 `unresolved-command` 补 `client`；⑤ §3.1 日志行示例改 `v1.4.3`；⑥ §3.2 补「空 `session_id` 直接跳过守门（trust boundary）」、`pre_llm_call` 按 epoch 提醒、状态文件名消毒；⑦ §4.2 补 `bash -c`/`sh -lc` **最多剥 8 层**、无引号 heredoc 拒 `$`/反引号/**反斜杠**；⑧ §4.3 标明「原生 patch 无 `PostToolUse`」为实测（`ALPHA-HOOK.md` §3 第 6 条）；⑨ §6 表与 Q4 改「Codex 实时拦截 + Post 记账」「包装类已读后直接放行且**无审计事件**」。
- **政策事实修正**：§13 引言「五脚本」→ **六脚本**（补 `cc_transcript`）；R105 的 `unresolved-command` 字段补 `client`（与 `hook.py` 实际落盘一致）。政策 2.6.0 → 2.6.1。
- **配套同步**：`ALPHA-HOOK.md` §3 第 1/2 条改口径（CC Bash 可经转录回放事后归因、包装类已读后无审计）；`hooks-tests/README.md` 政策版本与脚本计数；两份 README 徽章。
- 三轮研判记录见工作区 `s5-261022/DOC-R{1,2,3}-*.md` 与 `DOC-REVIEW-CONVERGENCE.md`。回归 80 例全绿，`verify_rules` clean。

## 2.6.0（2026-10-30，已提交）

- **文档：新增 [`docs/hooks-guide.md`](docs/hooks-guide.md)**（README / README_EN 中链接）——面向小白的三分钟比喻（门禁卡 + 借阅登记）＋ 与 harness 无关的共同机制（治理文件、`c<size>:sha256` 内容指纹、连续覆盖区间、EOF 哨兵、一次性令牌、七型审计事件）＋ **三种 harness 逐项实现拆解**（Hermes 项目插件的 `pre/post_tool_call`；Codex CLI 的事件接线、Bash 分派与 `apply_patch` 可读 deny、fail-open 边界；Claude Code 的写前拦截 + `Stop`/`SessionEnd` 转录回放归因，含跨 Stop 账本、写后消费、同轮二次写三个坑）＋ 真值表 ＋ 排查 FAQ ＋ 接入新 harness 的六步清单 ＋ 术语表。
- **README / README_EN 事实对齐**：政策徽章 `v2.2.0` → `v2.6.0`；CC Bash 覆盖真值表由「**未覆盖、无审计**」改为「**不拦**；`Stop`/`SessionEnd` 转录回放事后归因（`cc-bash-write` / `cc-bash-gap`）」，并同步声明三点限制（`would_deny` 不等于拦截、转录可被同 uid 删改、截断/短读不授证）；`README_EN` 补齐缺失的「Client Governance Hooks」章节；目录树补 `docs/`。
- **死代码清理**：删除 `cc_transcript.py::load_offset()`（无任何调用点，`grep` 零命中）；`hooks-tests/README.md` 按现状重写计数（八套 4/18/9/3/5/4/32/5 = 80 例、`TEMPLATE_FILES` 28 项、审计七型、CC Bash 归因口径）。
- 回归 80 例全绿，`verify_rules --workspace .` clean。

- **对抗复核修复（`1ec4ce1` 之后的 `dev` 修补）**：① 按 `gate.custom.json.gate_files` 逐 gate 记账（不再写死 `AGENT_RULES.md`）；② `is_error` 的 Read 不授证；③ `tool_result` 缺失不再冒充 `result=ok`（新增 `unknown` 并进 pending，跨 `Stop` 补齐后再算）；④ 超限转录不再丢弃全部记录（截断到上限 + `transcript-truncated`）；⑤ 转录路径 `realpath` 校验（挡 projects 内 symlink 逃逸）＋转录自带 `cwd` 与会话 cwd 比对（挡 slug 碰撞误读）；⑥ 读证据参考修订优先取「写前整文件读」，避免「先读全再写」被误判；⑦ `sidechain` 标记入事件。回归 69 例。
- **CC Bash 非订阅归因（新能力，不改变拦截面）**：新增 `.workspace/hooks/cc_transcript.py` —— CC 仍**不订阅** Bash hook，但在 `Stop` / `SessionEnd` 用平台 payload 里的 `transcript_path` 做**转录回放**：解析 `Bash.tool_use.input.command`，用 `wtarget` 分类静态可判目标，用转录里的 `Read.tool_use + tool_result` 重建读证据，复算出 `would_deny`，并与 `git status` 差集交叉。新增审计事件 `cc-bash-write`（digest / targets / class / would_deny / read_state / result）与 `cc-bash-gap`（`unresolved-command` / `no-transcript-path` / `transcript-*` / `changed-but-untargeted` / `transcript-too-large`）。
- **只读边界**：仅读 payload 精确给出的转录路径，先校验 project slug == cwd 拉平值、文件名 == session_id，否则记 gap 并拒读；**不遍历 `~/.claude/projects/**`**（本机实测该目录含 22 个项目、104 MB 转录，含其他私人项目）。偏移按会话 sidecar 增量保存（`cc-transcript-<session>.json`）。单文件 > 8 MB 或 > 20000 条 tool_use 记 `transcript-too-large`，不做无限解析。
- **读证据只认可校验内容**：CC 的 Read 结果会附带 `<system-reminder>` 尾注、超长时截断；剥掉尾注后必须与治理文件对应行逐行相等才授证，末尾出现 `... [truncated]`、短读未到 EOF、内容与磁盘不符一律不授证（否则「截断读」会被当成「读全」）。
- **语义**：事后归因，**不拦截**；`would_deny=true` 表示「按当时证据本该 deny」，不表示写入被阻止；转录可被同 uid 删改，故只是审计复算。命令原文默认不落盘（只记 digest），需 `.workspace/gate.custom.json` 的 `audit_command_text=true` 显式开启。
- **政策**：§13 R104 改写为「不订阅、不拦截，但可经转录回放做命令级事后归因」；R105 审计事件五型 → 七型。政策 2.5.2 → 2.6.0。`TEMPLATE_FILES` 27 → 28。
- **测试**：新增 `hooks-tests/test_cc_transcript.py`（12 例：路径三重校验、增量与半行容错、would_deny 矩阵、**截断/注入/内容不符不授证**、原文开关、差集缺口、偏移幂等）与 `hooks-tests/test_cc_wiring.py`（3 例：真实启动 hook.py 的 cc stop/session_end、无转录 gap、跨事件偏移幂等）。八套合计 69 例。
- **收口修复（`c14f5d4` 之后的 `dev` 修补，两席独立研判一致）**：① **跨 Stop 读证据持久化**——`analyze()` 原先每轮重建 `books`、sidecar 只存 `offset/stats/pending`，而 CC 的 `Stop` 每轮触发，于是「Stop 轮 1 读全 gate → 轮 2 写同一 gate」会复算出 `read_state=missing` / `would_deny=true`（假阳性）。现 sidecar 增加 `books`（`{gate 相对路径: {coverage, revision, total, sha}}`，旧 sidecar 无该键按空字典迁移），`analyze(..., books=...)` 以它为本轮基线、`hook.py` 读出后传入并写回；**修订变化即撤销**：`sha` 是忽略文末单个 LF 的内容指纹，指纹变化（或行数变化）该 gate 的 coverage 清空后按本轮重算，旧修订的读账不得贴到新修订上。② **无 cwd 转录 fail-closed**——整条转录一条 `cwd` 都没有时无法排除 slug 碰撞（`/a-b/c` 与 `/a/b-c`），记 `cc-bash-gap(transcript-cwd-missing)` 并**跳过本轮复算**（`pending={}`），`offset` 仍前进，避免同一批记录每轮重放。③ 清理：删除 `hook.py` 中死代码 `load_offset` 调用；`cc_transcript.py` 两处未关闭的 `open(...).read()` 改为 `with`（消 ResourceWarning）。回归：`test_cc_transcript` 22 → 25（`test_read_survives_across_stops` / `test_revision_change_revokes_across_stops` / `test_missing_cwd_is_gap_not_analyzed`），`test_cc_wiring` 4 → 5（`test_read_survives_across_stops` 走真实 `hook.py` 两轮 `stop`）；八套合计 69 → 73 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`hook.py` / `cc_transcript.py`）。
- **F3 修（真机 CC 复现，`VERIFY-UNVERIFIED-261030.md` §1.1）**：跨 `Stop` 的 gate 写会**自我失效**——`analyze()` 建 books 时若本轮没有该 gate 的读，就拿**磁盘当前内容**当参考修订，而写命令刚刚改过磁盘 ⇒ 指纹与持久化账本（写前）不符 ⇒ coverage 被清空 ⇒ `read_state=missing` / `would_deny=true`（假阳性：会话 A「同轮读+写」通过、会话 B「轮 1 读 / 轮 2 写」失败）。现改为**先预扫本轮 Bash 的静态写目标**（`wtarget.classify`）得到 `written_gates`，再建 books，参考修订三选一：① 本轮有该 gate 的整文件写前读 → `transcript`；② 本轮**被写且无读** → `persisted`（账本里的 sha/total/coverage，**不重新哈希磁盘**，total 与覆盖区间同源故原样继承，不 `_carry_coverage`）；③ 本轮既未读也未写 → `disk`（外部改动检测）。账本里没有该 gate（首轮就写）仍走磁盘、coverage 空 ⇒ deny。事件 `revision` 如实输出三值，`persisted` 已写入模块与 `analyze()` docstring。**能力边界**：外部改动与会话写落在同一轮时转录里没有可区分信号，一律按账本放行；gate 文件的外部改动由 `Stop`/`SessionEnd` 的 `unsanctioned-gatefile-change`（文件级指纹锚）兜底，不由本复算负责。回归：`test_cc_transcript` 25 → 28——`test_read_survives_across_stops` 改为「Stop#2 的写真的改磁盘」并断言 `would_deny=false`/`read_state=complete`/`revision=persisted`/账本仍锚写前修订；新增 `test_write_without_prior_read_across_stops`（首轮只写无读 ⇒ 两轮均 deny/missing）、`test_read_and_write_same_round_still_allows`（同轮读+写 ⇒ `transcript`）、`test_external_change_in_write_round_uses_ledger`（固化上述边界）；`test_revision_change_revokes_across_stops` 改为「该轮**不写** gate」的可区分形态（原形态与新语义互斥：同轮外部改动无法归因），撤销后仍断言一次真实 deny。八套合计 73 → 76 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`cc_transcript.py`）。
- **F3b 修（写即消费，主席探针 `chair_f3_probe.py` R3 复现）**：F3 之后「轮 1 读全 → 轮 2 写」已放行，但**写那一轮没有消费许可**——账本仍锚**写前** sha 与完整 coverage，于是之后每轮「被写且无读」都继续以该账本为参考 ⇒ `would_deny` 永为 false（写完不重读仍 allow，真机假阴性）。现按 §12 R98「写治理文件即消费许可」改写账本：① 该轮某 gate **被写**时，写事件本身仍按**写前**账本判定（`revision=persisted`，F3 修复不变）；② 该轮结束时把该 gate 的账本改写为**写后修订**——`sha` = 当前磁盘内容指纹、`total` = 当前行数、`coverage = []`（证据已消费），账本 `revision` 新增第四值 `post-write`；③ **顺序敏感**：只有**最后一次写之后**的完整读能把 coverage 重建回来（同一轮「写 → 读」的读证据属于写后修订；写中间的读属于已不存在的中间修订，后续再有写即作废）；④ 下一轮「未读直接写」⇒ 账本 coverage 空 ⇒ `would_deny=true` / `read_state=missing`，重读后恢复 allow。**已知边界**：`interpreter_wrapper` / `unresolved` 形态的写目标不可静态确定，不消费任何 gate 账本（否则一次无关的 `python3 script.py` 会消耗全部读许可）；同一轮内的第二次写仍按该轮参考判定，消费只发生在轮末。回归：`test_cc_transcript` 28 → 30——新增 `test_gate_write_consumes_evidence`（R1 读全 → R2 写 false/complete → R3 写 **true/missing** → R4 重读 → R5 写 false/complete）与 `test_read_after_write_same_round_rebuilds_coverage`（同轮写后重读重建 coverage）；`test_read_survives_across_stops` / `test_external_change_in_write_round_uses_ledger` 的**边界语义不变**（该轮仍按账本放行、靠 `unsanctioned-gatefile-change` 兜底），但末尾断言改为「写后账本 = 写后 sha + coverage 空」。八套合计 76 → 78 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`cc_transcript.py`）。
- **F4 修（同一轮第二次写仍 allow，真机 CC 转录 `211c332c` @ `/tmp/cc-e2e-stops4` 复现）**：F3b 只在**轮末**清 coverage，于是同一轮内「读一次 → 连发两条 `printf >> gate`」的第二条仍看到读时的完整 coverage，两条都记 `would_deny=false / read_state=complete / revision=transcript`（真机假阴性，R98 要求第一条写即消费许可）。现改为**每处理完一条命中 gate 的写步骤就立即消费**（`_consume_book()`：`coverage=[]`、`revision=post-write`、`consumed=True`，`raw/total` 置空——本轮磁盘存的是**最后一次**写的结果，不能当中间修订），该写自身仍按**写前**证据判定（第一写 allow 不变）；之后同轮的写 ⇒ `read_state=missing / would_deny=true`。**写之后的读仍可重建 coverage**：被消费的账本遇到 `offset==1` 且未给 `limit` 的整文件读时，以该读内容为新的参考修订（`_adopt_post_write_revision()`；中间修订无权威副本可对照，是本轮唯一可用证据，局部读不采用 ⇒ fail-closed deny，已入 docstring）。**轮末写后账本逻辑保留**（`sha`/`total` = 磁盘现状、`coverage=[]`、`revision=post-write`，供跨 `Stop` 使用），因此 sidecar 持久化语义与 F3b 完全一致。真机回放核对：同一转录现在给出「第一条 `false/complete/transcript`、第二条 **`true/missing/post-write`**」，其后那次 `Read POLICY2.md` 仍把 coverage 重建为 complete。回归：`test_cc_transcript` 30 → 32——新增 `test_second_write_same_round_denied`（读 + 写 + 写 ⇒ 第一条 `false/complete`、第二条 `true/missing`，并断言轮末账本仍为写后修订）、`test_read_between_writes_same_round_allows`（读 + 写 + 读 + 写 ⇒ 两条都 `false/complete`）；新增渲染辅助 `_numbered()`。八套合计 78 → 80 例全绿，`verify_rules` clean。

## 2.5.2（2026-10-22，未提交）

- **原生 payload 形态错误也走可读 deny**：`hook.py` 的 `_write_targets()` / `_absolute()` 对「patch 缺 Begin/End 外壳」「文件头不是 Update/Add/Delete File」「目标路径含首尾空格/控制字符/`~`」「`tool_input` 或 `file_path` 缺失」原先抛裸 `ValueError` → 渲染成「内部错误」；现统一改抛 `_PatchParseError`，输出可执行文案（指出缺什么、该怎么改）。与 F1 同族问题（GLM 终验 unverified 项），fail-closed 不变。`test_bash_dispatch` 新增 W10 三条回归（共 18 例；六套合计 43 例）。
- **政策 §13 R105 明确审计归因边界**：`unresolved-command` 仅落 `tool_use_id` + `digest`（命令 sha256 前 16）+ `class`，不落命令原文、不做 native tool-use 对账，因此审计自身**不可直读归因**（只能以候选命令重算 digest 比对）；需要命令级可读归因须另行实现对账。政策 2.5.1 → 2.5.2。
- **文档措辞与事实对齐（LUNA 终验提出）**：删除 `ALPHA-HOOK.md` / 本文件里「R1/R2/R3 三轮全 pass」的过度声明，改为按轮记录（R1 链未闭合、R2 deny 腿未触发、R3 pass）；`ALPHA-HOOK.md` 测试数 12 → 15（共 40 例）；重写 `hooks-tests/README.md`（删除「wtarget 未接入」「审计只有 6 字段」「W2/W3 未实现」等过期表述，按 2.5.2 现状描述）。

## 2.5.1（2026-10-22，未提交）

- **政策 §13 R105 补边界**：`worktree-change` 的文件粒度受 git 限制——会话中新出现的**未跟踪目录**在 `git status --porcelain` 中折叠为 `?? dir/`，故 `files` 只到目录级且 `diff_stat` 为空，文件级归因须由 `completion-gate`/artifact 侧补证，不得据目录级条目推断具体写入文件。政策 2.5.0 → 2.5.1。
- **修复（客户端 3 轮受管真机测试发现，F1）**：`.workspace/hooks/hook.py` 的 `_bash_patch()` 对显式 `apply_patch` 的不可解析形态（`mkdir … && apply_patch <<'PATCH'` 等 `&&`/`;`/`cd` 串联、非单字面 patch 参数、多 heredoc 终止符、会展开的 heredoc、命令缺失）原先抛裸 `ValueError`，被 `main()` 泛异常分支渲染成 `Workspace gate 内部错误；请人工检查。(ValueError)`；现改为抛 `_PatchParseError`，输出可读 deny（说明该形态目标不可静态确定、请单独调用 `apply_patch`），fail-closed 语义不变。`hooks-tests/test_bash_dispatch.py` 新增 W8/W9 两条回归：复合形态 deny 文案不含「内部错误」、且已完整读取后仍 deny 但可读。
- **F2（实现文档补记）**：`ALPHA-HOOK.md` §3 增第 9 条边界说明（与 R105 同义），§4 增「受管 3 轮」迭代行。
- 3 轮受管真机测试：被测 `deepseek-v4-flash` @ `codex-cli 0.163.0`（6 次 run，每轮独立夹具/会话）。**结论按轮记录，不作「三轮全 pass」**：三次未读写均 deny（R1-A 首写文案为「内部错误」→ F1，deny 本身成立）、读全后写 allow（R1-B/R3-A）、令牌复用 deny（R2-A）、`touch` 记 `suspect` 不撤销（R2-B）、脏夹具 `completion-gate ok=false`（R3-B）。看管席（`deepseek-v4-pro`）判 R1 链未闭合，`glm-5.3` 有条件接受，`gpt-5.6-luna` 判不可接受（F1 阻断）；F1/F2 已在 `a3c4811` 闭合。
- **C9 文案口径修正**：deny 文案的「共 N 行」用 `len(bytes.split(b"\n"))` 计数，对以 LF 结尾的治理文件比 `wc -l` 多 1（`AGENT_RULES.md`：文案 188 / `wc -l` 187），会让模型误以为「还差第 188 行」而无效重读（真机 R2 已复现）。现改为在该行追加 `wc -l` 对照：`共 N 行（wc -l = M，差 1 行为文末空行）`——**语义/判定不变**（`缺失 x..y` 与总行数仍按 `split` 口径），仅多一行对照；`gate_core.py` 新增 `_physical_lines()`，两个 deny 调用点传入 `physical`。`hooks-tests/test_bash_dispatch.py` 新增回归（在未读 gate 的 `pre_write` 上断言文案同时含 `共 ` 与 `wc -l = `，且 `wc -l` 数值等于真实文件的 `len(bytes.split(b"\n")) - 1`，按文件现算、不硬编码 187）。
- **S5 单轮完整链真机重跑记录**：R1/R2 观测不成立、R3 pass（`release_gate=true`），逐轮独立看管 + 两席研讨；详见工作区 `s5-261022/S5-REPORT.md`。

## 2.5.0（2026-10-22，未提交）

- **客户端治理 hook 首版**：新增 Codex CLI / Claude Code 写前守门——`.workspace/hooks/{hook,gate_core,wtarget,authz,template_anchor}.py` + `.codex/hooks.json` + `.claude/settings.json`；`TEMPLATE_FILES` 20 → 27（五脚本 + 两配置）。
- **行为要点**：Codex `printf >>`/`sed -i` 等静态可判写在未读时 deny（真机验证）；`apply_patch $'…'` ANSI-C 规范化；C9 deny 文案带覆盖/缺失/总行数；`SCHEMA 4→5`；W2 指纹锚 + 豁免令牌 + W4 完成声明门禁（advisory）。
- **边界**：**CC 的 Bash 写不订阅、不拦截、无归因**；W2 的 `suspect` 仅 mtime 兜底；W4 不进 deny；clone 未 pin。
- 政策 2.4.0 → 2.5.0（新增 §13，R101–R107：标题 R101 + 六条 R102–R107）。

## 2.4.3（2026-09-16，未提交）

- **workspace-gate plugin v1.4.2 → v1.4.3**：按 v1.4.2 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v142.md`）处置 V142-1/V142-2。⚠️ **尚未经新一轮独立复核。**
- **V142-1（高/发布阻塞 → 修复）**：`_find_workspace` 的工作区身份标记改为**持久的 `.workspace/bootstrap.json`**——v1.4.2 要求 `AGENT_RULES.md` 同时存在，导致「合规读取后 gate 文件被删除（外部同步/误操作/已获许可的工具调用）→ 工作区发现失效 → 后续写入**静默脱离守门**（实测 fail-open：真实分发链 `write_file` 成功创建文件）」。修复后：bootstrap 存在即工作区；gate 文件缺失由写前复核处理（指纹 None → unread → BLOCK + 撤销留档 `to=null`）；`AGENT_RULES.md` 存在但缺 bootstrap 仍保留 near-miss 日志（J2 语义不变）。v1.4.2 的「不可读 → fail-closed」从此真正覆盖「不存在」形态。
- **V142-2（低 → 修复）**：模块 docstring 两处残留的「≤256KB/大文件 stat 弱指纹」描述更正为「一律全量 SHA-256、失败即 None」；记账窗口声明同步补 V142-1 语义。
- 自检 122 → **128 项**（新增 **P9 组** 6 项，全部来自复核方指定的最低验收：合规读后删除 gate → write/patch 均 BLOCK + 撤销留档 `to=null, was_read=true`；无许可 + gate 缺失 → BLOCK；重建 gate + 完整重读 → 恢复放行）。ext4 与真 DrvFs 各 **128/128** 全绿。
- **v1.4.2 冻结哈希（已作废存档）**：`1653bb24…`/`1fc6fd5b…`/`c75373a7…`。

## 2.4.2（2026-09-16，未提交）

- **workspace-gate plugin v1.4.1 → v1.4.2**：按 v1.4.1 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v141.md`）处置 V141-1–V141-4。⚠️ **尚未经新一轮独立复核。**
- **V141-1（致命 → 修复）**：`_mark_read` 的撤销条件去掉 `was_read` 前提——**任何**已记账文件（含只有部分覆盖、`read=false` 的）遇到不同指纹都先留档撤销（`was_read` 降级为纯审计字段），再按本次证据重建。关闭「旧修订部分覆盖 + 新修订部分覆盖拼成全文」的回归（v1.4.1 在修 V140-3 时重新打开）。
- **V141-2（高 → 修复）**：`_post_tool_call` 在接收读证据前调用 `_backend_container_mode(task_id)`——容器 backend 的读**一律不向主机工作区记账**（读操作本身不拦）。关闭「容器绝对读污染主机证据 → backend 切回本地后放行」的通路（v1.4.1 只拦了容器写）。
- **V141-3（中 → 修复）**：`_revision_fingerprint` **取消 256KB 阈值**——gate 文件一律全量内容 SHA-256；不可读 → 无指纹 → 证据不入账、写前 fail-closed。stat 弱绑定路径整体移除（此前「弱绑定仅限非治理文件」的声明不成立，因全部生产调用点都是治理文件）。
- **V141-4（低 → 修复）**：CHANGELOG 2.4.0 的「窗口由写前复核闭合（只可能多拦）」原句**就地显式撤回**（标注被 v1.4.1 确认轮证伪）；2.4.1 条目补「正文同一性不可证」「弱绑定边界声明不成立」的修正。
- 自检 113 → **116 项**（新增 **P8 组**：V141-1 跨修订部分拼接仍拦 ×2、V141-2 容器绝对读不入主机账 + 容器相对读不入账、V141-3 大 gate 文件同长度替换被拦；并修正 P1 夹具为「旧文件 4 行只读 1–2」，确保旧修订未读完即换版的路径被覆盖）。ext4 与真 DrvFs 各 **116/116** 全绿。
- **v1.4.1 冻结哈希（已作废存档）**：`c6a5294f…`/`7f49a90b…`/`08531883…`。

## 2.4.1（2026-09-16，未提交）

- **workspace-gate plugin v1.4.0 → v1.4.1**：按 v1.4.0 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v140.md`）处置 V140-1–V140-4。⚠️ **尚未经新一轮独立复核——不得声称「经独立复测通过」。**
- **V140-1（致命 → 插件侧 fail-closed 兜底）**：新增 `_backend_container_mode(task_id)`（复用核心 `tools.file_tools_paths._uses_container_paths` 权威判定）——容器 backend 的受管写入**一律拦截**（容器路径无法在主机上映射回真实工作区；主机侧会话不受影响）。**根治仍需核心提供 backend→host 身份映射**（已列核心契约需求）。
- **V140-2（致命 → 内容自证指纹）**：修订指纹对 ≤256KB 文件改为**全部字节的 SHA-256**（`c<size>:<digest>`），stat 元数据（size+mtime_ns+ino）只作大文件/不可读文件的弱绑定回退（`s…`）。语义修正：v1.4.0 的「read→post-stat 竞态由写前复核闭合、只可能多拦」被确认轮实测**证伪**（漏拦），v1.4.1 改为「许可绑定**写入时磁盘内容**」——「旧正文绑新许可」的放行前提被逐字节一致性要求关闭；utime 伪造 stat 对内容哈希无效。**残余边界（如实声明）**：插件层仍不能证明「返回给会话的正文 == 记账时磁盘内容」——需核心在 `ReadResult` 原子携带快照哈希（核心契约需求）。**【v1.4.1 确认轮修正】「旧正文绑新许可已关闭」表述过宽**：正文同一性仍不可证（上句边界）；且「≤256KB/大文件弱绑定仅限非治理文件」不成立——gate 文件调用点全部走本指纹函数，v1.4.2 起**取消大小阈值、gate 文件一律全量哈希、不可读即无指纹 fail-closed**（见 2.4.2）。
- **V140-3（误拦 → 修复）**：`_mark_read` 的指纹比较移到 `read=true` 短路之前——已读文件遇到不同指纹立即撤销旧许可（留档 `was_read=true`）并用本次证据重建；「外部改版 → 完整重读新版 → 首次写」不再被拦。
- **V140-4（审计 → 修复）**：schema 迁移按**来源分层**留档——schema≥2 写 `read_legacy_s2`/`coverage_legacy_s2`/`coverage_rev_legacy_s2`，不覆盖 schema<2 的 generic `*_legacy`；docstring 与实现对齐。
- 自检 99 → **113 项**（新增 **P7 组**：P7-1 内容指纹/同长度内容替换被拦/utime 伪造免疫、P7-2 重读新版首写放行、P7-3 容器 fail-closed ×3 + 非Container 对照、P7-4 分层留档 ×3；P7-3 以 sys.modules 桩注入 `_uses_container_paths`）。ext4 与真 DrvFs 各 **113/113** 全绿。
- **v1.4.0 冻结哈希（已作废存档）**：`83b78a6c…`/`611f97e3…`/`ebdb5390…`。

## 2.4.0（2026-09-16，未提交）

- **workspace-gate plugin v1.3.2 → v1.4.0**（`.hermes/plugins/workspace-gate/`），处置独立确认轮（GPT-6 high，2026-09-16）裁决的 N1–N3 与 N5/N6；**N4 文档化为已知保守误拦**。⚠️ **该轮同时裁决 v1.3.2「不可以发布」，v1.4.0 尚未经过新一轮独立复核——不得声称已通过独立复测。**
- **N1（致命 → 修复）**：相对路径解析改用**核心同源 task cwd 锚点**（`tools.terminal_tool.get_session_cwd` / `resolve_task_overrides`，与核心 `_authoritative_workspace_root` 的真实锚点一致，不用其进程 cwd 兜底）；hook 回调显式消费 `task_id`；**state.db 的历史 cwd 彻底退出许可判定**（写无锚点 → 保守拦截，读无锚点 → 不记账）——DB cwd 与 task 运行时 cwd 错位时的双向跨区冒领（确认轮实测的 `dispatch-victim` 与反向 decoy 路径）均关闭；DB cwd 仅存留于 pre_llm 提醒与会话审计的归属面。
- **N2（重要 → 修复 + 诚实边界）**：读证据绑定**修订指纹**（size+mtime_ns+inode）；同指纹才允许跨调用累计，指纹变化撤销旧覆盖（留档 `revocations`）并在**写前复核**时撤销已读许可——跨修订拼接（读旧版部分行 + 读新版部分行凑满 `1..total`）失效。诚实声明：插件侧 stat 相对核心读取时刻存在 TOCTOU 窗口（核心 `ReadResult` 不携带机器可读 revision），窗口由写前复核闭合（只可能多拦）；核心提供真快照版本号后应替换指纹方案。**【2026-09-16 撤回】上句「窗口由写前复核闭合（只可能多拦）」已被 v1.4.1 确认轮实测证伪（read→post-stat 竞态把旧正文绑到新指纹，写前复核无法发现，属漏拦）——v1.4.1 起改为内容自证指纹，见 2.4.1 条目。**
- **N3（重要 → 修复）**：新增生命周期 `epoch`——`pre_llm_call` / `on_session_end` 等真实活动点持锁把 `archived` 翻回 `resumed` 并递增 epoch（纯问答/零回合的恢复也触发）；`on_session_finalize` 按 `(epoch, turns_at_close)` 去重：同一阶段重复收尾幂等覆盖，**恢复后的新阶段追加新边界**（v1.3.2 会把上一条边界覆盖掉）。
- **N5（低）**：`_missing_ranges` 拒绝非整数端点（float / 数字字符串 / bool 不再被强转成合法覆盖提示）。
- **N6（低）**：docstring/CHANGELOG 诚实化——删除已不存在的「无 result 兼容路径」表述；「F1–F8 已全部处置」改述为「代码已改，独立复核仍有未决项」。
- `AGENT_RULES.md` §12 `R98` 同步重写（锚点解析 + 修订绑定 + epoch 审计 + N4 保守误拦声明）；版本 `2.3.0 → 2.4.0`；条款 ID 不变（注册表实测：100 条、高水位 R100，仅 R98 文本与插件文件哈希变更）。注册表 `.workspace/rule-clauses.json` 重建。
- 自检 `selfcheck.py` 79 → **99 项**（新增 **P 确认轮回归组 20 项**：P1 跨修订拼接+写前复核撤销、P2 旧许可撤销与重读解锁、P3 锚点同源正向（DB cwd 故意错误仍以锚点为准）、P4 反向 decoy 读不冒领、P5 零回合恢复的新 epoch 与边界追加、P6 端点严格性；E5/O3 按新语义更新）。ext4 与真 DrvFs（`TMPDIR=/mnt/c`）各 99/99 全绿。
- **v1.3.2 冻结哈希（已作废，仅存档）**：`__init__.py` `41b4296422aad5b0c4dfe0c7551b82d571398e5f59ebbfafe15d7b1b92853f1e` / `plugin.yaml` `ae8d011c…` / `selfcheck.py` `d4647f85…`；其被否决报告见工作区 `gate-v130-260915/confirmation-review.md`。

## 2.3.0（2026-09-15）

- **workspace-gate plugin v1.2.1 → v1.3.1**（`.hermes/plugins/workspace-gate/`），处置 v2.2.0 交付时列出的未决项，并按独立审查裁决逐条修复。
- **审查过程与结论（如实记录，不构成通过背书）**：GLM-5.3 用量达上限，本轮改用 **GPT-6（`gpt-6-astra-900k`，reasoning=high）** 做独立对抗审查。v1.3.0 候选被该审查**否决发布**（报告与全部原始证据见工作区 `gate-v130-260915/independent-review.md`）：F1（`result` 线上类型致判据静默失效）、F2（端点包含被当成集合覆盖 → 只读首尾两行即放行）、F3（空正文/EOF/dedup 存根仍解锁）、F4（用猜到的另一工作区配置豁免整次多目标调用 → 真实漏拦）、F5（行内截断被当作完整读取）、F6（审计覆盖与统计字段失真）、F7（历史授权状态永久继承）、F8（展示与登记问题）。**F1–F8 已全部处置**，形成 v1.3.1；该审查**未对 v1.3.1 背书**，其复测要求见项目内 notes。
- 修复要点：`result` 同时接受 JSON 字符串与 dict（线上契约）；`_coverage_complete` 改为「单个 1..total 连续区间」且 `_read_evidence` 按行号连续段切分（绝不跨洞合并）；无可信证据（空正文/EOF/dedup/行内截断/缺 total/解析失败）一律**不改状态**；`pre_tool_call` 先判定所有已确定目标再处理未知相对目标（未知目标回到保守拦截，custom 不能豁免）；会话边界写独立 `boundaries` 缓冲区、回合记录补 `turn_id`/`failed`/`reason`、`total_turns` 单调计数、finalize 幂等、恢复后标记 `resumed`；新增 `evidence_schema` 迁移，旧「读过即已读」不再视为完整读取（原始值留档 `*_legacy`）。
- **I3 跨进程丢更新**：状态读改写叠加 `fcntl.flock` 文件锁（无 `fcntl` 的平台降级线程锁）；自检 N 组 4 子进程 × 25 次自增 → 计数必须恰为 100（v1.2.x 下会丢更新，GLM 曾实测 n=43/100）。
- **I5-ext 会话级审计**：注册 `on_session_finalize`（payload 契约核对 `hermes_cli/hooks.py`／`cli_session_mixin.py`）。
- **I7 DrvFs**：自检在真 `/mnt/c`（DrvFs）上复跑全绿；大小写探针无误拦。
- `AGENT_RULES.md` §12 的 `R98` 同步重写（逐目标判定 + 未知目标保守拦截 + 完整读取的可验证定义 + 审计覆盖范围与 `evidence_schema` 迁移）；`2.2.0` → `2.3.0` 为条款文本级 MINOR，条款 ID 与 `overrides`/`extends` 引用不受影响（注册表实测：100 条不变、仅 R98 文本变更、高水位仍为 R100）。
- 自检 `selfcheck.py` 覆盖 48 → **79 项**（新增 K 读取覆盖 8 / L 会话边界 6 / M 未知目标 3 / N 跨进程 1 / O 审查回归 11），`TEMPLATE_FILES` 清单不变（仍 20 项）。
- **v1.3.2（真机验证抓到的假阳性修复）**：F5 的行内截断判定改为「**行尾**匹配」——v1.3.1 用「正文包含 `... [truncated]`」判定时，`AGENT_RULES.md` 的 R98 正文本身写着这个标记，于是「读完全文仍被拦、报错一直说尚缺 11–176」（全错误拦）。自检补 O11（正文中间提到该标记不得使证据失效）；覆盖 **79 项**。
- **验证**：79/79 自检全绿（ext4 与真 DrvFs 各一轮）+ 真机会话验证（未读→拦 / 只读前 10 行→仍拦且列出「尚缺行 11–176」/ **读全→放行** / 区外→放行）+ 模板仓库自校验 `clean: true`。
- **教训（写进 plugin docstring 与自检注释）**：合成 payload 全绿 **≠** 线上契约通过——v1.3.0 的覆盖率判据首版就是被真实会话验证抓出「只解析 dict、而线上传 JSON 字符串」而静默失效；实现 hook 前必须读核心发射点的原始 kwargs，不要凭字段名猜类型。

## 2.2.0（2026-09-15）

- 新增**工作区级治理 hook**：Hermes 项目级 plugin `.hermes/plugins/workspace-gate/`（`plugin.yaml` + `__init__.py` v1.2.1 + `selfcheck.py`），在派生工作区内机械守门「写工作区文件前必须先 `read_file` 读取 `AGENT_RULES.md`」——把 §2 的「任务开始前读规则」从软提醒变成机械拦截。事件：`pre_tool_call` 守门（内部异常也 block，fail-closed）、`post_tool_call` 记账（仅 `status=ok` 且 realpath 严格相等）、`pre_llm_call` 提醒（每会话至多一次）、`on_session_end` 回合级审计（有界追加）。守门判据是**写入目标路径**是否在工作区内，与「会话 cwd」解耦。
- `AGENT_RULES.md` 新增 §12「工作区治理 hook（机械守门）」（`R97`–`R100`，文末追加、不打乱既有 ID）：声明守门判据、skill（治心）与 hook（治手）的分工、以及「调参写 `plugin.custom.json`、不得改锁定脚本」的边界。已有 custom `overrides`/`extends` 引用不受影响（纯新增条款）。
- `TEMPLATE_FILES` 收录 3 个 plugin 文件（17 → 20 项）；`.gitignore` 增补 `.workspace/.hook-state/`（守门状态文件不入库，既有实例需自行补该行——`.gitignore` 不在 `TEMPLATE_FILES` 内，同步不会覆盖它）；`check_template.py` 的 `TEMPLATE_OWNED` 与 template-update skill 的清单计数同步。
- README 双语新增「Hook 治理（工作区级机械守门）」节：守门判据、事件、启用方式（`HERMES_ENABLE_PROJECT_PLUGINS=true` **且** `plugins.enabled: [workspace-gate]`）、`plugin.custom.json` 调参、`selfcheck.py` 自检、信任边界，以及**在场核验方法**（实测 `hermes plugins list` 不列项目插件，改用 `$HERMES_HOME/logs/workspace-gate-plugin.log` 的 `active` 行或行为验证）。
- 实现经**跨模型交叉审查**（老大要求「任何误差都会导致行为的错误」）：GLM-5.3 独立审查 v1.0.0 抓出两个致命项——patch 的 `Delete / Move / 无空格` 头整类漏守（可无守门删除工作区文件、含删 `AGENT_RULES.md` 使全区永久脱防）、`$HERMES_HOME`/profile 下 cwd 反查失效致相对路径写入静默放行——以及 I1–I7/S1–S6 共 13 项；逐条处置为 v1.2.x。**GLM 复审查结论：可作为随模板分发的治理守门发布**（F1/F2(b)/I1/I2/I4/I6/I7/S1–S5 到位），并按复审查要求前置补两处：patch `mode=patch` 不再收集 `path`（与核心 `_extract_file_mutation_targets` 完全对齐）、`$HERMES_HOME` 改用 `hermes_constants.get_hermes_home()`（含 ContextVar override）。验证：48 项合成 payload 自检全绿 + GLM 真实会话八项断言全过（未读→拦、相对路径→拦、`*** Delete File:` →拦、区外→放行、读后→放行）。已知残留（跨进程状态丢更新、DrvFs 侧未实测等）见本工作区 `notes/workspace-gate-hook-260915.md` 未决项。

## 2.1.0（2026-09-01）

- 新增 `delivery-closure` skill（`.workspace/skills/delivery-closure/`）：把 §9 完成标准落成六步机械流程（产物清点 → 查 INDEX → note 三选一 → `rebuild_index` 回写 → 部署与机械验证 → 固定格式完成声明）。动因：散文式完成标准（"需要的 note 和索引写回已完成"）在同模型同夜的 A/B 中被自评跳过，而 skill 化的机械步骤（template-update 七步）被完整遵循三次——表示形式决定遵循率。
- `AGENT_RULES.md` 新增 `§11 交付闭环 skill 指针`（`R95`/`R96`，文末追加、不打乱既有 ID）：任务模式声明完成前必须执行该 skill；`R87` 的具体执行含义以 skill 正文为准。已有 custom `overrides`/`extends` 引用不受影响（纯新增条款）。
- `TEMPLATE_FILES` 收录 `.workspace/skills/delivery-closure/SKILL.md`（16 → 17 项）；template-update §3 清单与第 4 步计数、workspace-init 第 7 步登记枚举（三 → 四入口）、README 双语 badge/生命周期表/skill 表/目录树同步更新。

## 2.0.0（2026-08-31）

- **破坏性变更**：git 决策条款（原编号 `R90–R94`）随 `§0.3` 物理归位，现为 `R14–R18`，其后所有条款顺延。工作区 custom 文件中的 `overrides:`/`extends:` 引用需按新 ID 迁移。

### 发布前修复（2026-08-31，第二轮评估 REPORT-2）

- template-update 同步清单改为以 `scripts/verify_rules.py` 的 `TEMPLATE_FILES` 为唯一事实源（纳入 workspace-init / git-init），消除手维护清单漂移导致的「校验失败 → 回滚」死循环。
- README 替换豁免进入 `clean` 判定：`REPLACED` / `HASH-DIFF`（README）计入 `info`，不再导致 `clean: false`；`EXTRA` 检测死代码修复（现迭代 `TEMPLATE_FILES` 与注册表的差集）。
- 条款 ID 改为**跨文件全局唯一、永不复用**（mark_rules 全局高水位 + verify 跨文件撞名检测）。
- 修复 workspace-init 与 skills/REGISTRY.md 中失效的 `TOOLS.md §4.5` 引用（改引小节标题）。
- HERMES.md 人格节改指 `HERMES.custom.md`，移除会制造 TAMPERED 的自毁式引用块槽位。
- 新增 GitHub Actions CI（`mark_rules.py check` + `verify_rules.py`），锁住模板仓库自身不漂移。

## 1.5.0（2026-08-31）

- 三个客户端入口（`HERMES.md` / `CLAUDE.md` / `AGENTS.md`）新增对各自 `.custom.md` 的读取约束——读完 `AGENT_RULES.md` 及 `AGENT_RULES.custom.md` 后，须继续读取对应客户端的 `.custom.md`；规则冲突时 `AGENT_RULES.md` 及其 custom 文件优先级永远最高。
- 变更记录从 `AGENT_RULES.md` 独立为 `CHANGELOG.md`。

## 1.4.0（2026-08-31）

- 新增 `HERMES.md` 客户端入口与 §9.1「记忆边界」；§2 新增入口防分叉条款；§7 新增版本控制操作授权要求；§8 中央文件清单纳入全部客户端入口。
- 新增 §9.2「notes 索引分层与策展节奏」；§9 新增记忆卫生规则（合并优先与 superseded 归档）；索引改为 INDEX.md（路由）+ `_INVERTED.md`（按需详情）双层。
- 新增 §10「规则分层与 custom 覆写」——模板规则文件锁定只读，本地规则一律写入 `<file>.custom.md` 并以 custom 优先；配套条款 ID 标记与双级校验（`scripts/mark_rules.py`、`scripts/verify_rules.py`）。
- 新增 §0.3「git 仓库决策」与 workspace-init / git-init skills——首次初始化须抹除指向模板库的 `.git` 并询问用户是否建仓；venv 为必须项，git 为可选项，决策记录于 bootstrap。
