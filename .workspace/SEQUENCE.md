# SEQUENCE — 交付顺序与版本号（计划侧）

> 本文件是**计划侧**事实源；**事实侧**是 `.workspace/release_dev.json` + `CHANGELOG_DEV.md` + git 提交。
> 两者由 `scripts/sequence_check.py` 机械互校（S1–S13）。校验的是**一致性**，不是「计划不可改」。
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

**边界**：`CHANGELOG_DEV.md` 与 `.workspace/release_dev.json` **在 `TEMPLATE_FILES` 清单内**（模板同步会分发它们），
但**只存在于 dev 分支**；main 分支上必须删除，由 S10 校验。`SEQUENCE.md` 是计划侧文件，不在同步清单里，
也不随模板同步到用户实例。

**迭代日志**：`.workspace/iterations/R<round>.md` 每轮一个文件、append-only，一次尝试追加一节
（`## it<N> · YYYY-MM-DD` + 触发 / 改动 / 证伪 / 结论 / 遗留）。它承载每次尝试的设计取舍与遗留项，
不替代本文件的轮次计划。`.workspace/iterations/` **不进 `TEMPLATE_FILES`**（dev 专属：实例不需要研发日志，
它只解释「这一轮为什么这样改、哪条判据把它挡住了」）。
`release.py --dev` 写账本时若该文件存在，会把 `iterations`（节数）与 `iterations_sha256`（文件 sha256）
冻结进条目，由 S12 对账：landed 后回改日志 = 红。

每轮的**每次尝试**（含被三席否决的）都要追加一节——否决理由与遗留编号是下一轮设计的输入，
只留一行 planned 块会在上下文压缩后丢掉它们。

> 顺序不可颠倒：②在③之前，③在④之前。先记账后确认 = 账本记录了自己没验过的事。

## 1. 已落地（landed）

| round | version | 提交 | 范围 | 确认（verified_by / verdict） | 备注 |
|---|---|---|---|---|---|
| 44 | `44.3.8.0` | `e3ec3a5` | R35–R44 全集（见 `CHANGELOG_DEV.md` §1–§10） | `legacy`（机制建立前，未做轮次级三席确认；R44a 为双修复席产出 + 控制席复验） | 首条 dev 版本；同时承接原 `CHANGELOG.md` 的 `## Unreleased` |
| 45 | （并入 44 条） | `f0ed3fd` | dev 四段版本机制本身（`release.py --dev`、双账本、`policy_facts` dev 判据） | `legacy`（机制建立前；本副本以完整树基线提交为 git 对象锚） | 机制轮不单独占版本号：它写的正是 `44.3.8.0` 这一条 |
| 46 | `46.3.8.0` | `0d3185d` | R46 计划互校机制：S1–S13（R46a–R46g；含 S12 迭代日志冻结锚、S13 §1 行冻结与建前改写白名单、放行 WARN） | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass verified_at=2026-11-24 | 迭代粒度机制：每轮一次尝试一节（it1–it7）；v54 三席唯一必修已修 `0d3185d` + v54b 复核通过 |
| 47 | `47.3.8.0` | `a6e8620` | stdio 三件套（R4 归零）：osnorm read_stdin_text/emit_stdout/emit_stderr、OSNORM_API 3、hook.py 收敛、entries 归零 | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass-with-notes verified_at=2026-11-25 notes=候选②冷启动可于定位前拒非法字节，已装载时由 strict 拒，两条路径都 deny；真 Windows ①②④ 原生复跑全过 | 迭代日志 it1–it3（R46f 机制） |
| 48 | `48.3.8.0` | `949b470` | R5 迁移前置（只建判据不改调用点）：比较基准统一开关、sidecar 三段兼容读、gate_key oracle 四条、12 条 a 类 subdomain、expires=R39 过期判定、C5/C8 收敛 | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass-with-notes verified_at=2026-11-25 notes=SOL 必修 illegal 子例已由二修席补入，三态配方验证；S7 三条 commit:null 为基线遗留 | 迭代日志 it1–it4（R46f 机制） |
| 49 | `49.3.8.0` | `20ef01c` | R5 语义迁移（账本归零）：15 处迁移（12 normpath_join→交换形态 + 3 relpath_sep_key→新键）、逐项删 structural、比较两侧统一口径、wtarget 无盘契约 | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass verified_at=2026-11-26 notes=真 Windows SidecarCompatR48 10/10，双端键集合 11 项逐字相同；二修为测试层自适应（零产品代码） | 迭代日志 it1–it3（R46f 机制） |
| 50 | `50.3.8.0` | `b1a6e02` | 真机闭环+治理单源：C6 Windows 五项复验、C10 dsh 双证（glob 修复）、C2 bootstrap.gate 化石块可恢复迁移、C3/C4/C7/C9 治理收敛 | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass verified_at=2026-11-26 notes=Windows 原生五项+GBK 字节一致复跑，dsh 双证与 Windows 原生 dsh 端到端，三端对照一致，证据归档 ~/.cache/seat-logs/r50-50.3.8.0/ | 迭代日志 it1–it4（R46f 机制） |
| 51 | `51.3.8.0` | `ee02480` | 闸门转 blocking（空白名单）：新增违规直接红且逐处定位、allowlist 空白名单、抬 baseline 不可解红 | verified_by=[SOL, GPT-SOL-6, PRO] verdict=pass-with-notes verified_at=2026-11-27 notes=v60/v61/v62/v63 四轮核验+Astra 完备性审查四必修闭环，Windows 原生复跑，守护链边界随裁决入账 | 迭代日志 it1–it6（R46f 机制） |

> **`verified_by` 取值**：`[SOL, GPT-SOL-6, PRO]`（三席轮次确认）或 `legacy`（机制建立前，S9 豁免但必须注明原因）。
> 自 R46 起，所有 `landed` 轮次必须是前者。

> R45 不单列版本的理由：它的产物就是「让 44.3.8.0 存在」，再给它一个 `45.3.8.0` 会与 S2（NN==round）和账本自指冲突。
> 该轮 `land_status = landed-no-ledger`，`sequence_check` 对这类轮次只校验「提交存在」，不要求账本条目。

## 2. 计划（planned）

> 每条计划项至少含：`round` / `version` / `范围` / `依赖` / `判据` / `风险` / `note`。
> 落地时追加：`land_status` / `verified_by` / `verdict` / `verified_at`（三席确认后填写，见 §0 第②步）。
> `land_status` 取值：`planned`（未做）/ `landed`（账本 + changelog + 三席确认齐全）/ `landed-no-ledger`（机制轮，见 R45）。







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
| S1 | 轮次严格递增、无重复；**dev 分支缺 `SEQUENCE.md` = 红**（删计划不能把 S1–S9 关成 SKIP；SKIP 只留给实例侧 / 非模板仓） | 解析表格 round 列，比较相邻；计划文件缺失时按当前分支 + template 身份（git 历史里有无 `_DEV` 文件）判 | 哪两轮冲突 / 计划文件被删 |
| S2 | `version` 首段 == round | 拆 `.` 取 [0] 与 round 比 | 哪轮版本与轮次脱钩 |
| S3 | `land_status` 只能是 `planned|landed|landed-no-ledger`；`landed` 必须在 dev 账本有条目；`landed-no-ledger` 的提交必须是 git commit；**历史锚**：历史账本 / 历史 SEQUENCE 里出现过的落地轮次必须仍在（`landed` 或 `landed-no-ledger`）；**落地即入账**：某轮首次在历史 SEQUENCE 里标成 `landed` 的那个提交，账本必须同时已含该轮条目 | 双向比对 + `git cat-file -e <sha>^{commit}` + `git log -- .workspace/release_dev.json` / `-- .workspace/SEQUENCE.md` 的历史版本 + `git show <首次落地 sha>:.workspace/release_dev.json` | 非法状态 / 缺账本 / 假提交 / 成对删除 / planned→landed 但账本不动 |
| S4 | `依赖` 必须在表中存在且排在其前 | 依赖号集合校验 + 位置比较 | 哪条依赖倒置或悬空 |
| S5 | 范围引用的 WORKLIST 条目必须存在；**没传 `--worklist` 且无候选文件 = 声明过的 SKIP 豁免**；**显式传入不存在/不可读的路径 = 红**（拼错不许退化成 skip） | `--worklist <path>` 或候选查找；读取失败也判红 | 悬空引用 / 显式路径指错 |
| S6 | 计划 `landed` 数 == 账本条目数，且两者都不得低于**历史最大值**（只增不减） | 计数比较 + `git log` 历史版本的最大计数 | 差值 / 成对删除 |
| S7 | 每条账本 `summary` == `CHANGELOG_DEV.md` 对应段落；**工作区腿 + git 锚腿**（条目 `commit`）两腿逐字一致；`commit: null`（未提交）时状态为独立的 **`DEGRADED`** | 工作区段落逐字比较 + `git show <commit>:CHANGELOG_DEV.md` 逐字比较；无锚时降级但**显式报出来**（不算 FAIL，也不冒充 PASS） | 自证漂移 / 降级未声明 |
| S8 | `release_dev.json` 只能 append，且版本严格递增；**已提交的历史版本之间是全字段严格前缀比较**（`summary` / `files_sha256` 也在 append-only 范围内：任何改写/删除 = 红，不给 `prose_note` 放行） | `git log --format=%H -- .workspace/release_dev.json`；相邻历史与当前逐条**全字段**比较 | 历史条目改写/删除/回退 |
| **S9** | **`landed` 与 `landed-no-ledger` 轮次都必须有三席确认记录**：`verified_by` 为 `[SOL, GPT-SOL-6, PRO]` 三席齐全（中英文方括号、全角逗号、有无空格都解析）、`verdict` 为 `pass`（或 `pass-with-notes` 且 `notes` 非空），且 `verified_at` 不晚于账本条目的 `date`。**`legacy` 轮次豁免，但必须在 `note` 写明原因**（R44/R45 属此列）；**只有 `legacy` 白名单里的轮次可用 `legacy`**（`LEGACY_ROUNDS = {44, 45}`，写死在脚本里：扩白名单必须改代码、在 diff 里留痕，不留可编辑的旁路），新轮次自称 `legacy` 一律红；`verified_by` 出现白名单外的席位名（如 `INTRUDER`）→ 红 | 字段存在性与取值校验 + 日期先后比较 + 白名单比对 | 哪轮缺哪一席 / 新轮次伪称 legacy / 未知席位 |
| **S10** | **`_DEV` 文件只属于 dev 分支**：模板身份**只用 git 锚判定**（`git log -- _DEV 文件` 非空），**不读** `.workspace/bootstrap.json` 等可编辑 JSON；分支为 `dev` 时两文件必须存在；为 `main` 时**必须不存在**（存在即红）；其他分支告警；非模板仓（历史里从未出现 `_DEV` 文件）只 WARN，不判红。**无 `SEQUENCE.md` 时本条仍须执行**（main 的合法态正是没有计划文件） | `git log --oneline -- CHANGELOG_DEV.md` / `-- .workspace/release_dev.json` + `git rev-parse --abbrev-ref HEAD` + 文件存在性 | 分支与 `_DEV` 文件不匹配 / 靠改 JSON 关判据 |
| **S11** | **远端锚对账（增强层）**：`origin/<当前分支>` 存在且可读时，本地历史必须是它的**超集**（`origin/x` 是 HEAD 祖先）；否则红。远端不可用（离线/无远端）→ **WARN 并明示「仅有本地锚」**，不判红 | `git merge-base --is-ancestor origin/x HEAD`；失败分支区分「不是超集」与「远端不可用」 | 本地历史被重写而远端未同步 / 仅本地锚 |
| **S12** | **迭代日志冻结锚**：`.workspace/iterations/R<round>.md` 每轮一个、append-only。账本条目带 `iterations_sha256` ⇒ 当前文件 sha256 必须一致（**landed 后回改日志 = 红**）；带 `iterations` ⇒ 文件必须存在、`## it<N>` 节数一致、编号 **1..N 连续**（缺节/跳号/乱序都红）；**两者都没有 ⇒ 不判**（历史条目 legacy 豁免，不回填） | `hashlib.sha256(读字节)` 与账本 `iterations_sha256` 比对 + 逐行解析 `## it<N>` 得节数与编号序列，与 `iterations` 比对 | 日志落地后被改/被删、节数或编号漂移 |
| **S13** | **§1 landed 行整行冻结（append-only rows）**：`git log -- .workspace/SEQUENCE.md` 取各历史版本 blob，解析 §1 表格**数据行**（不含表头与分隔行），历史全集必须在当前版本里**逐字**仍在；缺任一行 = 红（贴出**缺行原文**与该行**首次出现的提交**）。当前**新增**行（追加）不红。`范围` / `备注` / `确认` 说明文字改写、`提交` 列换成另一真实提交、整行删除都在射程内。**判据建立前已发生的合法改写**（`e895711` 的 R45 确认列在 `464e0c9` 被澄清）由代码内白名单 `S13_PREEXISTING_EDITS` 豁免（键 = 行原文 sha256[:16]，扩名单必须改代码、diff 留痕；只豁免旧行缺失，当前行被改仍红），豁免条数在 detail 里打印 | `git log --format=%H -- .workspace/SEQUENCE.md` 逐个 `git show <sha>:.workspace/SEQUENCE.md` → `section1_rows()` 取行原文（仅 `rstrip`）→ 历史并集 vs 当前 §1 行集合 | 已落地行的非锚列被改写 / 历史行被删 / 只改当前文件或改完再普通 commit |

**状态与退出码**：`PASS` / `FAIL` / `SKIP`（声明过的豁免：实例侧 / 非模板仓无 `SEQUENCE.md`、
候选路径无 `WORKLIST.md`）/ `DEGRADED`（锚不可用，已降级但**显式打印**：S7 的未提交条目、无 git 时的 S10/S13）/
`WARN`（不影响结论：非 dev/main 分支的 S10、非模板仓的 S10、远端不可用的 S11）。**只有 `FAIL` 影响退出码**；
`DEGRADED` 与 `WARN` 在汇总行计数（`N DEGRADED(S7)` / `N WARN(S10, S11)`），并在 `--json` 的
`degraded` / `warned` / `counts` 里逐项列出——降级可见，但不被静默当成通过。

### 4.1 锚的分层与威胁模型（三席 2026-11-23 裁决；R46e4 按 v52 三席复跑反例再收窄）

```
适用域  判据以 **dev 分支**为权威域：**计划文件存在时，所有分支都执行现行 S 判据
        （当前 S1–S13）**。main 上 S10 判 _DEV 归属、其余 S 照常互校（实测：main 保留
        计划并移走两份 _DEV 文件 → S3/S6/S8 红）；其他分支上 S10 只对分支归属告警；
        只有**整份计划文件不存在**时 S2–S9、S12 与 S13 才按 SKIP 不判红（S1 在 dev 上
        缺计划仍 FAIL、其他分支只告警；实测：非 dev/main 删整份计划 → 10 SKIP / 3 WARN，
        rc=0；main 缺计划且无 _DEV 残留 → S10 PASS）。计划内容篡改不因分支豁免（实测：
        probe-feature 保留计划注入非法 land_status → S3 红）。

主防线  本地 git 锚（账本 append-only 全字段前缀 + 有 commit 条目的 changelog blob
        + 历史轮次集合 + 落地即入账）
        能挡（每条都有构造测试证明）：
          · **误删 / 误改已提交的历史条目** —— 账本条目字段（S8 全字段前缀）、
            **历史 landed 轮次集合**（S3/S6）、**§1 landed 数据行原文**（S13 整行冻结）
          · §1 landed 行的 `version` 列改写 —— S2/S8（与账本逐轮对账）
          · **§1 landed 行的非锚列** —— S13：`范围` / `备注` / `确认` 列的说明文字改写、
            `提交` 列换成另一真实提交、整行删除 → 红（`version` 列仍由 S2/S8 对账；
            `确认` 列的**结构字段**——`legacy` 白名单 / `verified_by` 席位 / `verdict` /
            `verified_at`——仍由 S9 校验）。判据建立前已发生的改写见 S13 的代码白名单
          · 成对删除（landed 行 + 账本条目同时删） —— S3/S6/S8/S13
          · 有 commit 的账本条目：summary 与历史 CHANGELOG blob 逐字不符 —— S7
          · 依赖倒置、非法 land_status、landed 与账本脱钩 —— S4/S3
          · 未落地轮次的 `依赖` / `land_status` / `version` 首段改写 —— S4/S3/S2/S8
          · dev 分支的**计划文件缺失**（历史有计划却删掉 SEQUENCE.md） —— R46e B 项
        不能挡（**已实测**，不要再当成能力）：
          · **未落地轮次的散文字段**（`范围`、`判据`、`说明`）改写、或整块删除 ——
            rc=0（`依赖` / `land_status` / `version` 首段仍受 S4/S3/S2/S8 校验，改写会红）
          · 非 dev/main 分支上删除整份计划（S2–S9/S12/S13 全部 SKIP，S1/S10/S11 只告警）
          · 追加伪造的轮次（含伪造三席确认）
          · 重写整条可达历史（orphan 分支 / 全链 rebase / filter-branch）
          · 本地改写而远端同谋
          —— 与 `AGENT_RULES.md §12 R100` 同一主体边界：**仓内判据防漂移，不防主动伪造**
             （主动伪造属权限层，不在只读判据的能力范围）

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
