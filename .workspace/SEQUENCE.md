# SEQUENCE — 交付顺序与版本号（计划侧）

> 本文件是**计划侧**事实源；**事实侧**是 `.workspace/release_dev.json` + `CHANGELOG_DEV.md` + git 提交。
> 两者由 `scripts/sequence_check.py` 机械互校（S1–S11）。校验的是**一致性**，不是「计划不可改」。
> 变更规则：改本文件必须在对应条目 `note` 写明「谁改的、为什么」；落地顺序与计划不同只有两条合法出路——
> 改计划（并登记）或回退落地。**不允许「计划写 A、实际做 B、账本记 C」并存。**

## 0. 生命周期（每一轮的闭环，用户裁决 2026-11-23）

```
① 功能开发完成（代码 + 测试在 dev 分支）
② 三席确认（SOL / GPT-SOL-6 / PRO 各自给判定与证据；记录到本轮 verified_by / verdict）
③ 写 CHANGELOG_DEV.md 条目（按提交号归纳，不许凭记忆编排轮次）
④ release.py --dev --round <NN> --apply → 写 release_dev.json（append-only，含 summary 与 commit）
⑤ dev 工作区显式传工作区级清单路径运行 `python3 scripts/sequence_check.py --worklist <path>`，
   全绿（或无 FAIL）后继续；**不传时脚本内置的 `WORKLIST.md` / `../WORKLIST.md` / `../../WORKLIST.md`
   候选只作兜底**——换个目录层级就会静默变 SKIP，所以 dev 工作区一律显式传
⑥ 推送 dev 分支（推送由用户执行；脚本永不 push）
```

**边界**：`SEQUENCE.md`、`CHANGELOG_DEV.md`、`.workspace/release_dev.json` 均为**开发工作区 / dev 分支特有**，
不进 `TEMPLATE_FILES`，不随模板同步到用户实例；main 分支上不应存在后两者（S10 校验）。

> 顺序不可颠倒：②在③之前，③在④之前。先记账后确认 = 账本记录了自己没验过的事。

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 确认（verified_by / verdict） | 备注 |
|---|---|---|---|---|---|
| 44 | `44.3.8.0` | `e3ec3a5` | R35–R44 全集（见 `CHANGELOG_DEV.md` §1–§10） | `legacy`（机制建立前，未做轮次级三席确认；R44a 为双修复席产出 + 控制席复验） | 首条 dev 版本；同时承接原 `CHANGELOG.md` 的 `## Unreleased` |
| 45 | （并入 44 条） | `f0ed3fd` | dev 四段版本机制本身（`release.py --dev`、双账本、`policy_facts` dev 判据） | `legacy`（机制建立前；本副本以完整树基线提交为 git 对象锚） | 机制轮不单独占版本号：它写的正是 `44.3.8.0` 这一条 |

> **`verified_by` 取值**：`[SOL, GPT-SOL-6, PRO]`（三席轮次确认）或 `legacy`（机制建立前，S9 豁免但必须注明原因）。
> 自 R46 起，所有 `landed` 轮次必须是前者。

> R45 不单列版本的理由：它的产物就是「让 44.3.8.0 存在」，再给它一个 `45.3.8.0` 会与 S2（NN==round）和账本自指冲突。
> 该轮 `land_status = landed-no-ledger`，`sequence_check` 对这类轮次只校验「提交存在」，不要求账本条目。

## 2. 计划（planned）

> 每条计划项至少含：`round` / `version` / `范围` / `依赖` / `判据` / `风险` / `note`。
> 落地时追加：`land_status` / `verified_by` / `verdict` / `verified_at`（三席确认后填写，见 §0 第②步）。
> `land_status` 取值：`planned`（未做）/ `landed`（账本 + changelog + 三席确认齐全）/ `landed-no-ledger`（机制轮，见 R45）。

### R46 · `46.3.8.0` · 计划互校机制
- **范围**：`scripts/sequence_check.py`（S1–S11：R46 落 S1–S8；R46b 补 S9/S10；R46c 补 git 历史锚；
  R46d 以第二实现为基线重做——补 A 无计划文件时仍判 S10、B 显式 `--worklist` 指错必红、
  C `legacy` 白名单、D 未知席位必红、E S7 独立 `DEGRADED`、F 历史轮次集合收 `landed-no-ledger`，
  并新增 S11 远端锚）
  + 本文件 + 登记（`TEMPLATE_FILES` / `TOOLS.md` / `check_all` 阶段）
  + 顺手：C11（`release_anchor.py` 进 `TEMPLATE_FILES`）、D5（`docs/tools.md` 补 `collapse_path`/`OSNORM_API`）、
  D6（台账 `R35-C1` 回写已修）
- **依赖**：R45
- **判据**：S1–S11 全绿（S7 的 `DEGRADED`、S10/S11 的 `WARN` 不算失败，但必须在摘要里可见）；证伪必须红：
  ①删 §1 landed 行 + 删账本条目（成对删除）②账本 summary 与 CHANGELOG 同步改写 ③新轮次写 `verified_by: legacy`
  ④`verified_by` 含白名单外席位 ⑤`--worklist` 指向不存在路径 ⑥main 分支留 `_DEV` 文件（且无 `SEQUENCE.md`）
  ⑦删历史 §1 的 R45（landed-no-ledger）行并把 R46 依赖改 R44 ⑧本地历史改写而 origin 未同步 ⑨无 origin 时只 WARN
- **风险**：低（不改产品行为）

### R47 · `47.3.8.0` · stdio 三件套（R4 归零）
- **范围**：`osnorm.read_stdin_text` / `emit_stdout` / `emit_stderr`；删 `hook.py:941-943` 三处 `reconfigure`；
  收敛 `hook.py` 5 处 `print(json.dumps(..., ensure_ascii=False))`
- **依赖**：R46
- **判据**：7 项矩阵（中文 / 非法字节必拒 / BOM 与现状一致 / 空 stdin / >1MB / TTY / 管道）+ 真 Windows 跑 ①②④；
  `entries` 3 → 0，`structural` 保持 15；fail-closed 引导的 ASCII `buffer.write` 路径逐字保留
- **风险**：高（输入解码方向错 = 坏 payload 被放行）

### R48 · `48.3.8.0` · R5 迁移前置（只建判据，不改调用点）
- **范围**：比较基准统一方案（宿主原生 → 交换形态）落地为**可切换的判据与 fixture**；sidecar 三段兼容读
  （新键 → 旧键 → 原始绝对键）；`gate_key` oracle 补「逃逸允许 / 跨盘不抛 / 两 cwd 同键」；
  12 条 a 类理由抽成具名 `subdomain`；`expires=R39` 过期项处置；C5（测试/生产取数双份）、C8（scope 分叉）
- **依赖**：R47
- **判据**：旧/新/原始绝对三路 fixture 都可读；三条 oracle 全绿；`gate_status` 与 `gate_core` 对同机 scope 逐字相同；
  **本轮不减** 15 项 structural
- **风险**：中高（这是 R49 的可证伪基础）

### R49 · `49.3.8.0` · R5 语义迁移（账本归零）
- **范围**：12 处 `normpath_join` → `collapse_path`；3 处 `relpath_sep_key` → `posixpath.relpath(collapse_path, collapse_path)`
  + 显式盘符判定；逐项删 `structural`
- **依赖**：R48
- **判据**：`entries 0 / structural 0`；Linux 与 Windows 同批会话 sidecar 键集合**逐字相同**；
  故障注入证明逃逸 fail-closed、跨盘不抛
- **风险**：**全序最高**（比较基准错 → 静默误 deny 或误放行）

### R50 · `50.3.8.0` · 真机闭环 + 治理单源
- **范围**：C6 Windows 五项真机复验；C10 dsh 会话账本缺失；C2 `bootstrap.gate` 化石块（`gate_status --migrate-legacy`
  + preflight 不读化石 + 报 source）；C3/C4/C7/C9（实例 CHANGELOG 测试、sync 后登记/索引、`--python` 拼接、
  无 `.git` 导出 3 例）
- **依赖**：R49（必须验**迁移后**的实现）
- **判据**：五项有真机命令与绿结果；dsh 同时产生 apply 事件与 `dsh-session-*.json`；Linux/Windows/dsh 对相同目标决策一致
- **风险**：高（平台差异暴露轮）

### R51 · `51.3.8.0` · 闸门转 blocking（空白名单）
- **范围**：规则从「只许减不许增」→「新增违规直接红」；`allowlist` 空白名单
- **依赖**：R49 + R50
- **判据**：空账本基线全绿；注入 R4/R5 各一处违规必红且定位准确；移除后恢复绿；不得靠抬 baseline 解红
- **风险**：高（过早 blocking 会误 deny 正常开发）

### R52 · 发布里程碑（非 dev 轮次）
- **范围**：D3 — main 重新发版/合并，windows-latest CI 34 例历史红全部重跑清零；dev/main 账本对账
- **依赖**：R47–R51 全绿
- **判据**：CI 绿 + 政策头/`CHANGELOG.md`/`bootstrap` 三方一致 + dev 账本与 SEQUENCE 互校通过
- **说明**：正式版本号（3.8.1 / 3.9.0）与 tag/push/合并由用户另行授权；脚本永不自动 push

## 3. 版本号再规划表（SEQUENCE 首次落盘时一次性执行）

| 旧编号（WORKLIST 草案） | 新编号 | dev 版本 | 变更原因 |
|---|---|---|---|
| R47 stdio | R47 | `47.3.8.0` | 三家一致：stdio 先行（只动 R4，不动路径语义） |
| —（新增） | R48 | `48.3.8.0` | 三席两家主张把 R5 前置拆出：先建判据再迁移，否则迁移不可验 |
| R48 R5 迁移 | R49 | `49.3.8.0` | 承接上条拆分 |
| —（新增） | R50 | `50.3.8.0` | 真机闭环与治理单源合并成一轮（GPT-SOL-6 主张二者早于 blocking） |
| R49 blocking | R51 | `51.3.8.0` | 账本归零 + 真机绿之后才能开硬门 |
| R46 发布准备 | R52 | （非 dev 轮次） | 发布是里程碑不是迭代轮次，不占四段版本 |

## 4. 机械校验判据（`sequence_check.py` 实现）

| # | 判什么 | 怎么判 | 失败指向 |
|---|---|---|---|
| S1 | 轮次严格递增、无重复 | 解析表格 round 列，比较相邻 | 哪两轮冲突 |
| S2 | `version` 首段 == round | 拆 `.` 取 [0] 与 round 比 | 哪轮版本与轮次脱钩 |
| S3 | `land_status` 只能是 `planned|landed|landed-no-ledger`；`landed` 必须在 dev 账本有条目；`landed-no-ledger` 的提交必须是 git commit；**历史锚**：历史账本 / 历史 SEQUENCE 里出现过的落地轮次必须仍在（`landed` 或 `landed-no-ledger`） | 双向比对 + `git cat-file -e <sha>^{commit}` + `git log -- .workspace/release_dev.json` / `-- .workspace/SEQUENCE.md` 的历史版本 | 非法状态 / 缺账本 / 假提交 / 成对删除 |
| S4 | `依赖` 必须在表中存在且排在其前 | 依赖号集合校验 + 位置比较 | 哪条依赖倒置或悬空 |
| S5 | 范围引用的 WORKLIST 条目必须存在；**没传 `--worklist` 且无候选文件 = 声明过的 SKIP 豁免**；**显式传入不存在/不可读的路径 = 红**（拼错不许退化成 skip） | `--worklist <path>` 或候选查找；读取失败也判红 | 悬空引用 / 显式路径指错 |
| S6 | 计划 `landed` 数 == 账本条目数，且两者都不得低于**历史最大值**（只增不减） | 计数比较 + `git log` 历史版本的最大计数 | 差值 / 成对删除 |
| S7 | 每条账本 `summary` == `CHANGELOG_DEV.md` 对应段落；**工作区腿 + git 锚腿**（条目 `commit`）两腿逐字一致；`commit: null`（未提交）时状态为独立的 **`DEGRADED`** | 工作区段落逐字比较 + `git show <commit>:CHANGELOG_DEV.md` 逐字比较；无锚时降级但**显式报出来**（不算 FAIL，也不冒充 PASS） | 自证漂移 / 降级未声明 |
| S8 | `release_dev.json` 只能 append，且版本严格递增 | `git log --format=%H -- .workspace/release_dev.json`；相邻历史与当前逐条前缀比较 | 历史条目改写/删除/回退 |
| **S9** | **`landed` 轮次必须有三席确认记录**：`verified_by` 为 `[SOL, GPT-SOL-6, PRO]` 三席齐全、`verdict` 为 `pass`（或 `pass-with-notes` 且 `notes` 非空），且 `verified_at` 不晚于账本条目的 `date`。**`legacy` 轮次豁免，但必须在 `note` 写明原因**（R44/R45 属此列）；**只有 `legacy` 白名单里的轮次可用 `legacy`**（`LEGACY_ROUNDS = {44, 45}`，写死在脚本里：扩白名单必须改代码、在 diff 里留痕，不留可编辑的旁路），新轮次自称 `legacy` 一律红；`verified_by` 出现白名单外的席位名（如 `INTRUDER`）→ 红 | 字段存在性与取值校验 + 日期先后比较 + 白名单比对 | 哪轮缺哪一席 / 新轮次伪称 legacy / 未知席位 |
| **S10** | **`_DEV` 文件只属于 dev 分支**：分支为 `dev` 时两文件必须存在；为 `main` 时**必须不存在**（存在即红）；其他分支告警。**无 `SEQUENCE.md` 时本条仍须执行**（main 的合法态正是没有计划文件） | `git rev-parse --abbrev-ref HEAD` + 文件存在性（不依赖计划文件） | 分支与 `_DEV` 文件不匹配 |
| **S11** | **远端锚对账（增强层）**：`origin/<当前分支>` 存在且可读时，本地历史必须是它的**超集**（`origin/x` 是 HEAD 祖先）；否则红。远端不可用（离线/无远端）→ **WARN 并明示「仅有本地锚」**，不判红 | `git merge-base --is-ancestor origin/x HEAD`；失败分支区分「不是超集」与「远端不可用」 | 本地历史被重写而远端未同步 / 仅本地锚 |

**状态与退出码**：`PASS` / `FAIL` / `SKIP`（声明过的豁免：实例侧无 `SEQUENCE.md`、候选路径无 `WORKLIST.md`、
非 template 工作区的 S10）/ `DEGRADED`（锚不可用，已降级但**显式打印**：S7 的未提交条目、无 git 时的 S10）/
`WARN`（不影响结论：非 dev/main 分支的 S10、远端不可用的 S11）。**只有 `FAIL` 影响退出码**；
`DEGRADED` 与 `WARN` 在汇总行计数（`N DEGRADED(S7)` / `N WARN(S10, S11)`），并在 `--json` 的
`degraded` / `warned` / `counts` 里逐项列出——降级可见，但不被静默当成通过。

### 4.1 锚的分层与威胁模型（三席 2026-11-23 裁决）

```
主防线  本地 git 锚（账本 append-only 前缀 + changelog blob + 历史轮次集合）
        能挡：误删、误改、三文本同步改写、landed 与账本脱钩、历史行被删
        挡不住：蓄意重写**整条可达历史**（orphan 分支 / 全链 rebase / filter-branch）
                —— 与 §12 R100 同类：守门防「无意识跳过」，不防「主动绕过」（属权限层）

增强层  S11 远端锚：已推送的历史由远端固定；能挡住「本地重写但远端未变」
        局限：离线 / 无远端 / 尚未推送 → 只能 WARN，输出必须**明示**当前只有本地锚

结论    优先级是「本地锚为主 + 远端锚增强」，不是反过来：
        远端经常不可用，不能当主防线；但一旦可用就必须用。
```

## 5. 与 WORKLIST 的关系

`WORKLIST.md`（工作区级，`/home/kinolu/Workspaces/TemplateDev/`）是**待办全量清单**，含未进入本计划的项
（C5 之外的可选维护项、D4 的其余 20 处 advisory 输出等）。本文件只承载**已排期的轮次**。
两者关系：`WORKLIST` 记「有哪些事」，`SEQUENCE` 记「按什么顺序做、哪个版本」。

运行时以 `--worklist <工作区根>/WORKLIST.md` 显式指路（§0 第⑤步）：脚本的候选路径只作兜底，
找不到就报 SKIP（§4 声明过的豁免，不报红）；但**显式传了一个不存在或不可读的路径一定报红**
——「拼错一个字母」不该等价于「关掉 S5」。
