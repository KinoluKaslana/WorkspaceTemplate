# CHANGELOG — WorkspaceTemplate 变更记录

> 本文件只记录模板的政策/功能变更历史，供模板开发者与维护者查阅。工作区 Agent 无需读取本文件——实际生效规则以 `AGENT_RULES.md` 及其 custom 文件（`AGENT_RULES.custom.md`）为准。

## 2.5.2（2026-10-22，未提交）

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
