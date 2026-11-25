<!-- gen:policy-facts:begin -->
<!-- doc-fact:tools-md-heading -->
# 工具（scripts/，23 个）
<!-- gen:policy-facts:end -->

> 实现说明，不构成政策。所有脚本只依赖标准库，要求 Python ≥ 3.10。**`--json` 语义各脚本不同**：`verify_rules.py` 的 `--json` 是**输出文件路径**，`skill_audit.py`/`workspace_preflight.py`/`docs_*.py`/`template_sync.py`/`pyenv_init.py` 的 `--json` 是「打印 JSON 到 stdout」，`check_all.py --json` 同样是「stdout 只放 JSON，人读进度走 stderr」，`mark_rules.py` 无此参数——以各条示例与 `--help` 为准。

## 0. 一览

| 脚本 | 作用 | 典型用法 | 退出码 |
|---|---|---|---|
| `verify_rules.py` | 模板层完整性：文件哈希 + 条款 ID + custom 引用；`--vs-template` 做字节级比对 + git 信任锚 | `--workspace . [--vs-template <克隆>] [--json]` | 0 clean / 1 有 findings |
| `mark_rules.py` | 给条款打稳定 ID（`<!-- id:Rn -->`）并重建注册表 | `registry AGENT_RULES.md` | 0 / 1 |
| `inspect_skills.py` | 只读盘点 skills 目录（入口、name/description、嵌套） | `<skills-dir> [--include-nested]` | 0 / 1 |
| `version_check.py` | 版本三元组：政策 ↔ CHANGELOG ↔ bootstrap | `[--json]` | 0 / 1 |
| `policy_facts.py` | 政策事实生成器：从权威源渲染「生成块」（政策/插件版本、`TEMPLATE_FILES`/skills/scripts 计数、条款范围），并判「未登记的政策版本文面量」（历史标记只豁免它声明的版本、不得登记当前版本；`CHANGELOG` 条目须为 `## x.y.z`）。另有 dev 双账本判据：`.workspace/release_dev.json` 末条 == `CHANGELOG_DEV.md` 首个四段标题、两文件必须成对、dev 版本严格递增；**三段判据不套 `CHANGELOG_DEV.md`**（否则四段版本被误杀） | `[--check] \| [--write] \| [--print]` | 0 一致 / 1 有 finding（`--write` 缺标记时为 2） |
| `release.py` | 发版事务：版本/CHANGELOG/bootstrap/生成块/账本同步，失败整体回滚；tag 单独执行，绝不 push。`--dev` 走**四段 dev 账本**（`NN.X.Y.Z`）：只写 `CHANGELOG_DEV.md` + `.workspace/release_dev.json`，不写 `releases.json`、不改政策头、不 tag/push；若 `.workspace/iterations/R<round>.md` 存在，条目追加 `iterations`（节数）与 `iterations_sha256`（文件 sha256）作为 S12 冻结锚 | `--version X.Y.Z --summary "…" [--dry-run\|--apply]`；`--verify`；`--tag X.Y.Z --apply`；`--dev [--round NN\|--version NN.X.Y.Z] [--dry-run\|--apply]`；`--dev --verify`；`--dev --list` | 0 / 1 |
| `notes_index_check.py` | notes 索引与重建结果一致（防手改/过期） | `[--json]` | 0 / 1 |
| `secret_scan.py` | 高置信度明文凭据扫描（只打印脱敏片段） | `[--root PATH] [--json]` | 0 / 1 |
| `clause_lint.py` | 条款属性门：正文用词 ↔ `clauses.meta.json` 声明一致；附带条款引用门（E12/E13：仓内文档 `§<n> R<m>` 的条款须存在且章节号对得上） | `[--strict] [--report] [--json]` | 0 / 1 |
| `skill_audit.py` | skills 契约节 / 双登记 / 计数与名称集合 / 引用路径 | `[--strict] [--sync-registry] [--tools-snippet] [--json]` | 0 / 1 |
| ↳ `--sync-registry` 契约 | 写完**回读校验**（名称集合 + 入口节计数）；不一致或 I/O 失败 → exit 1 并输出 `registry_problems`。入口行按列结构解析（首列名必须与第三列路径自洽），字符宽度与发现器一致；目录名不符 lowercase-kebab 只在 `--strict` 提示 | 同上 | 0 / 1 |
| `docs_index.py` | docs 确定性索引：`build` / `check` / `query` | `build`；`query <词> --json --top 2` | 0 / 1(stale) / 2(缺索引) / 3(stale 查询) |
| `docs_hit_report.py` | 判据集命中报告（`tests/docs_probe.json`） | `--min 9 --top 2 --json` | 0 达标 / 1 未达标 / 2 stale |
| `docs_alias_audit.py` | 别名审计：死指针/指纹漂移/来源合规/留一验证 | `[--strict] [--restore] [--json]` | 0 / 1 |
| `template_sync.py` | 按 ref 同步模板层：dry-run / apply / prune / rollback；**写盘与校验都是事务性的**（失败自动回滚，`--keep-on-fail` 保留现场） | `--workspace . --ref alpha [--apply --prune --verify --json]`；`--rollback <备份目录>`；`--keep-on-fail` | 0 / 1 前置失败 / 2 校验失败（已回滚）/ 3 写盘或校验器异常（已回滚）/ 4 回滚失败（需按 MANIFEST 手工恢复） |
| `workspace_preflight.py` | 跨设备预检：逐项「登记 vs 本机实际」（条目以 `--json` 的 `items` 为准）；`--layout-only` 只读诊断 venv 布局 | `[--workspace .] [--json] [--strict] [--harness …] [--layout-only]` | 0 无 mismatch / 1 有 mismatch / `--layout-only`：0 找到解释器 / 0 mcp（`layout=n/a`，不探测）/ 3 没有、未配置或缺 identifier |
| `pyenv_init.py` | Python 运行时初始化：uv / 手动 venv / MCP | `--mode uv\|venv\|mcp [--apply] [--json]` | 0 / 1 / 2 参数 / 3 需装 uv / 4 校验失败 / 5 已配置拒绝 |
| `repo_identity.py` | 仓库身份三段纯函数流水线：编码规范化 → 路径格式转换 → 白名单判定 | `from repo_identity import normalize_encoding, convert_format, repair, classify` | 库：非法或不可证明输入返回 `None` |
| `gate_status.py` | harness 接线自检：各客户端有没有**运行期** gate 证据 | `[--require cc,codex,hermes,dsh] [--ttl-minutes N] [--json]` | 0 有证据 / 1 `--require` 缺证据 |
| `local_state.py` | 本机状态按 context 分片（库，无 CLI）：`read_state` / `read_status` / `bind` / `resolve_identity` / `detect_platform_info` | `import local_state` | 库：不变量失败抛 `ValueError`/`TypeError`；判读结果在返回字典的 `status`/`reason` |
| `osnorm.py` | 路径交换格式与文本 I/O 的**唯一实现**（库，无 CLI）：`canonical_path` / `family` / `read_text` / `write_text` / `run_text` / `atomic_write_text` / `replace_retry` / `configure_stdio` / `BOM_POLICY` | `import osnorm` | 库：路径形式不可接受抛 `PathFormError`（`ValueError` 子类） |
| `encoding_guard.py` | 文本编码机械统一：扫模板层 + 用户可编辑清单的 BOM / 纯 CRLF / 混合换行；`--fix` 备份后剥 BOM + 统一 LF（幂等） | `--check [--json]`；`--fix`；`[--root PATH]` | `--check`：0 干净 / 1 有发现；`--fix`：0 全部修好 / 1 有文件修复失败 |
| `sequence_check.py` | 计划侧 ↔ 事实侧互校：`.workspace/SEQUENCE.md` §4 的 S1–S13（轮次递增；**模板仓 dev 缺计划文件 = 红**（SKIP 只留给实例侧/非模板仓）/ 版本首段==轮次 / landed↔账本双向+`land_status` 三值+§1↔§2 一致+**历史锚**（含 `landed-no-ledger` 轮次）+**落地即入账**（首次标 landed 的提交必须已含该轮账本）/ 依赖不倒置 / 范围引用 WORKLIST（无文件 = 声明过的 SKIP；**显式 `--worklist` 指错/不可读 = 红**）/ 计数+历史下界 / 每条账本 summary 与 `CHANGELOG_DEV.md` 逐字**且**与 git 锚一致（`commit: null` → 独立 `DEGRADED`）/ 已落地版本不可变 + **已提交历史版本全字段严格 append-only**（summary 改写也红）/ **`landed` 与 `landed-no-ledger` 都要三席确认**（`legacy` 白名单 + 未知席位必红；中英文方括号/全角逗号都解析）/ `_DEV` 文件分支归属（**模板身份只用 git 锚**，改 `bootstrap.json` 关不掉；非模板仓只 WARN）/ **S11 远端锚**：`origin/<分支>` 是 HEAD 祖先，不可用时 WARN 并明示「仅有本地锚」/ **S12 迭代日志冻结锚**：账本 `iterations_sha256` 必须等于 `.workspace/iterations/R<round>.md` 当前 sha256（落地后回改 = 红）、`iterations` 必须等于 `## it<N>` 节数且编号 1..N 连续，两者都无 = 不判 / **S13 §1 landed 行整行冻结**：`git log -- .workspace/SEQUENCE.md` 各历史 blob 的 §1 **数据行**并集必须在当前版本里**逐字**仍在（`范围`/`备注`/`确认` 说明文字改写、`提交` 换成另一真实提交、整行删除 → 红并打印缺行原文与来源提交；当前**追加**新行不红；判据建立前已发生的改写走代码白名单 `S13_PREEXISTING_EDITS`））。**S10/S11 无论是否 skip 都执行**；`--json` 在任何结局都只输出一个 JSON 对象（`ok`/`checks[]`/`counts`/`degraded`/`warned`/`skipped`） | `[--json] [--root DIR] [--worklist PATH]` | 0 全绿或显式 skip（可含 DEGRADED/WARN）/ 1 有 findings / 2 用法或 IO |
| `check_all.py` | 提交前统一门禁：check 模式 11 段（sequence 是第 10）；`--fix` 模式共 15 段（4 修复 + 11 检查；`--full` 再加 tests / hooks-tests）。全部阶段跑完后若有未推送提交且 dev 账本非空，打一行「尚未由用户放行（未 push）」WARN，**不改退出码** | `[--fix] [--check] [--full] [--json] [--only a,b]` | 0 全绿 / 1 任一阶段失败 / 2 用法错误 |

## 1. 完整性：`verify_rules.py` + `mark_rules.py`

```bash
python3 scripts/mark_rules.py registry AGENT_RULES.md      # 改过模板层文件后必跑
python3 scripts/verify_rules.py --workspace .              # 本仓自检
python3 scripts/verify_rules.py --workspace . --vs-template ~/Github/WorkspaceTemplate
```

- **双级校验**：文件级（sha256 vs 注册表 + EXTRA 检测）+ 条款级（增/删/改/乱序/重复 ID）。
- `--vs-template` 是**权威判定**：字节级比对 + 模板克隆的 git 干净状态（信任锚）。克隆脏 → `[trust]` finding。
- README 例外：工作区换成自己的首页时报 `REPLACED`（进 `info`，不影响 `clean`）。
- 常见坑：改了 `TEMPLATE_FILES` 内文件却没重建注册表 → 实例同步后报 `HASH-DIFF`。

## 2. skills：`skill_audit.py`

```bash
python3 scripts/skill_audit.py                 # 逐 skill 列出 findings
python3 scripts/skill_audit.py --strict        # 0 findings 才 exit 0
python3 scripts/skill_audit.py --sync-registry # 按磁盘目录重写 REGISTRY 入口表（文件不存在时新建）
# 写完会回读校验：exit 1 表示「重建后仍不自洽」，看 registry_problems 定位（多为入口行名/路径不一致）
python3 scripts/skill_audit.py --tools-snippet # 打印未登记 skill 的 TOOLS.md 行（不写文件）
python3 scripts/skill_audit.py --sync-registry # 按磁盘目录重写 REGISTRY 入口表与计数
```

检查项：frontmatter（name/description/version）、name 与目录一致、触发语前缀与长度、semver、条款 ID 与路径引用、**8 维契约节**、REGISTRY/TOOLS 双登记、REGISTRY 名称集合与计数自洽。

## 3. docs 检索三件套

```bash
python3 scripts/docs_index.py build                 # 原子重建 docs/.index.json（不存正文）
python3 scripts/docs_index.py check                 # 新鲜度：文件指纹 + 目录清单 + schema
python3 scripts/docs_index.py query R98 委托 --json # 定位（默认 top-2）
python3 scripts/docs_hit_report.py --min 9          # 判据集命中
python3 scripts/docs_alias_audit.py --strict        # 别名：死指针/漂移/来源/冗余
```

- 过期即拒答（stale），不返回旧定位；
- 检索结果**不是读证据**，不入 `coverage`；拿依据仍须 `read_file`；
- 别名只允许由 `docs/.miss-log.jsonl` 的真实缺口驱动（判据集 MISS 不算）。

## 4. 模板同步：`template_sync.py`

```bash
python3 scripts/template_sync.py --workspace . --list-refs
python3 scripts/template_sync.py --workspace . --ref alpha                  # dry-run
python3 scripts/template_sync.py --workspace . --ref alpha --apply --verify
python3 scripts/template_sync.py --workspace . --ref v3.3.1 --apply        # 标签
python3 scripts/template_sync.py --workspace . --ref 5edf554 --apply       # 提交
```

- 用 `git worktree add --detach` 取快照，不动模板克隆的工作树；
- 备份到 `.workspace/backup-<ts>-sync/`（含 `MANIFEST.txt`）；
- 回报 `changed / skipped / identical / removed_upstream / pruned / python_mode_preserved`（后者只针对旧 `bootstrap.python` 节；本机状态不在同步范围）；
- `--prune` 删除上游已移除的模板层文件（先备份，并清理空父目录）。

## 4.5 条款属性：`clause_lint.py`

```bash
python3 scripts/clause_lint.py            # 逐条；0 errors 才 exit 0
python3 scripts/clause_lint.py --strict   # 警告也算失败
python3 scripts/clause_lint.py --report   # severity / check 类型 / manual 清单
```

校验 `AGENT_RULES.md` 正文用词与 `.workspace/clauses.meta.json` 声明是否一致：
键集合、枚举、强条款的 `check`、`manual:` 理由、`reviewed`、以及「可机械判定的触发条件不许标 manual」。
规则本身见 [`policy-language.md`](policy-language.md)。

## 4.6 三条可判定检查（条款属性绑定）

```bash
python3 scripts/version_check.py        # R2：政策版本 = CHANGELOG 顶层 = bootstrap 记录
python3 scripts/notes_index_check.py    # R80：notes 索引必须等于重建结果（防手改/过期）
python3 scripts/secret_scan.py          # R61：仓库内高置信度明文凭据
python3 scripts/workspace_preflight.py  # R11：MCP 模式不得同时存在本地 .venv-*
```

各自的「能证明/不能证明」写在 [`policy-language.md`](policy-language.md) §7；
新检查必须带**会变红的负例**（`tests/test_check_scripts.py`）。

## 4.7 dev 四段版本账本（`CHANGELOG_DEV.md` + `.workspace/release_dev.json`）

政策版本（`X.Y.Z`）只在正式发布时变；dev 迭代用**四段** `NN.X.Y.Z`，两条版本链物理分文件：

| 链 | 事实位 | 写入者 | 对账 |
|---|---|---|---|
| 政策 | `AGENT_RULES.md:4` / `CHANGELOG.md` 顶层 / `bootstrap.current_policy_version` / `.workspace/releases.json` 末条 | `release.py --apply`（事务） | `version_check.py`、`policy_facts.py --check` |
| dev | `CHANGELOG_DEV.md` 首个四段标题 / `.workspace/release_dev.json` 末条 | `release.py --dev --apply`（事务） | `release.py --dev --verify`、`policy_facts.py --check` 的 `dev_offenders()` |

- `NN` = 迭代轮次，`X.Y.Z` = 本轮预计发布的正式版本，必须等于当前政策版本的后三段；
  **dev 版本不改政策版本**——头部、徽章、`version_check` 仍只认三段号。
- 判据（`policy_facts.dev_offenders()`）：两文件必须成对存在；账本末条 == 变更日志首个四段标题；
  账本版本严格递增；`round` 字段必须等于版本首段；dev 文件的版本型标题必须是四段。
- `CHANGELOG_DEV.md` **不在** `_doc_files()` 扫描面内：三段判据（当前政策版本字面量、历史标记、
  标题版本）套上去会把合法的四段标题判成三段而误杀——这条边界的唯一出口是 `dev_offenders()`。
- **已知边界（未在本轮改动）**：CI 的 `.github/scripts/release_anchor.py` 仍只认 `CHANGELOG.md`
  作为「模板层改动必须进账本」的合法落点，因此 dev 分支上「只写 `CHANGELOG_DEV.md`」的提交会被它判红。
  dev 合流到 main 前必须先按 `release.py --apply` 正式发版（写 `CHANGELOG.md`），否则该 CI job 不会放过。

## 5. 跨设备：`workspace_preflight.py`

条目（以 `--json` 的 `items` 为准）：`host / bootstrap_schema / workspace_root / python / gate / template_clone / skills / template_files / notes_index / docs_index / git / readme`。
每项 `recorded / actual / status / action`；`mismatch` 阻塞，`warn` 记录后继续，`n/a` 跳过。
**只报告、不改写记录**——记录是共享资产，改写由用户裁决后执行。

## 5.5 Gate 接线：`gate_status.py`

gate 结论按机器分片落在 `<ws>/.workspace/.hook-state/<scope>/gate.json`（`<scope>` 见 `docs/hooks-catalog.md`）；
`bootstrap.json` 的 `gate` 节是旧位置，只读兼容、不再写入。同一份证据文件（`gate.log`）也按 `<scope>` 分片，
别的机器同步过来的证据不会被当作本机证据。


```bash
python3 scripts/gate_status.py --ttl-minutes 30                  # 四端「有没有运行期证据」
python3 scripts/gate_status.py --require dsh,cc --json           # 缺证据 → 退出码 1
```

政策 §15 把这条核验定为启动前必须项（与 Python 选择同性质），结论落本机状态 `.workspace/.hook-state/<scope>/gate.json`（`bootstrap.gate` 是旧位置，只读兼容）。判据只看运行期证据：`cc`/`codex` 来自 `.workspace/.hook-state/<scope>/gate.log` 的 `client=` 行；`hermes` 来自
`$HERMES_HOME/logs/workspace-gate-plugin.log` 的 `workspace-gate active` 行（机器级，插件日志不绑工作区——这是已知边界）；
`dsh` **必须同时**有 `.workspace/.dsh-gate.log` 的 `apply` 事件与 `.hook-state/<scope>/dsh-session-*.json` 账本。
TTL 必须为正（0 直接拒绝）；时间戳在当前时刻之后超过 2 分钟不算证据（时钟回拨/伪造）。
**文件在不在不算证据**——
dsh 没有项目级自动发现，不带 `--patch` 启动就完全没有 gate；`hermes` 需要 `HERMES_ENABLE_PROJECT_PLUGINS=true`
且 config 里 `plugins.enabled` 含 `workspace-gate`。克隆后先跑这一条，别用「目录长这样」推断「已经受管」。

`--record` 会把结论写进 `bootstrap.gate`（原子替换）：有证据才写 `configured`；`unconfigured` 需要
`--force --user-decision '<用户原话>'`；从 `configured` 翻转或缩小 harness 集合同样需要 `--user-decision`（R117）。

**与预检的失败姿态**：`workspace_preflight` 的 `gate` 项判 `mismatch` 表示「不能进入受管写入/同步」，
不表示只读也被禁止（R118）。预检复核 TTL 固定按 ≤30 分钟算，不会因为 bootstrap 里填了更大的值而放宽。

## 5.6 本机状态分片：`local_state.py`

本机状态（运行时模式、venv、版本、后续的 gate 结论）按**执行上下文**分片存放，不写进随仓库分发的 `bootstrap.json`：

```
<workspace>/.workspace/.local/v1/<context_id>/runtime.json
context_id   = HMAC-SHA256(machine_material, f"{workspace_id}|{realm}|{principal}")
root_binding = HMAC-SHA256(context_id, realpath(workspace))
```

- `machine_material`：Linux/WSL `/etc/machine-id`（回退 `/var/lib/dbus/machine-id`）、Windows `MachineGuid`、macOS `IOPlatformUUID`（Darwin 不走 Linux 的 machine-id 文件）；CI 用 `WORKSPACE_CONTEXT_SEED` 显式给定。采集入口 `detect_platform_info()`（只读、best-effort）。
- `principal`：POSIX `uid` / Windows `SID`（`whoami /user` 解析；取不到返回 `None`，**不回退用户名**）；`realm`：`linux|windows|macos|wsl|container:<rt>|ci:<provider>`（可用 `WORKSPACE_REALM` 覆盖）。
- `mode` 的持久值与历史 `bootstrap.python.mode` 一致：`uv` / `local-venv` / `mcp`（`pyenv_init --mode venv` 只是 CLI 别名）；它现在存在本机状态里，`bootstrap` 不再保存。
- 只读判定（`read_state` / `read_status`）不创建、不修复；只认精确的 `runtime.json`，同步冲突副本与别人的 context 目录一律不采信。
- 状态文件不存明文绝对路径：`root_binding` 是 HMAC，venv 用工作区相对路径；工作区一移动，绑定自动失效（`UNBOUND` + `root-mismatch`）。`venv_relpath` 必须无 drive、无 root、无 `..`、非空。
- 首次绑定把内容写进同级临时目录（`mkdtemp`）→ 文件 `fsync` → **临时目录 `fsync`** → 整体 `rename` → 父目录 `fsync`，读者看不到「目录在、`runtime.json` 不在」的中间态；更新已有 context 用 `mkstemp + fsync + os.replace + 目录 fsync`。
- 写锁在 OS 临时目录（`workspace-template-locks-<uid>/<context_id>.lock`，`0700`），不落在状态树里，不会被同步到别的机器；同 context 的并发写串行化。
- 工作区之下的路径链上任何一段是 symlink 都拒绝写入；`runtime.json` 用 `O_NOFOLLOW` 打开。工作区自身是 symlink 时按 `realpath` 锚定（状态仍落在工作区实体内）。
- `ephemeral` 是调用方显式传入的 `bool`（CI 用），对应目录名带 `-eph` 后缀；传非 `bool` 抛 `TypeError`，避免字符串 `"false"` 被当成真值。

`runtime.json` 里的 `capabilities` 只记**能由模式本身推出**的事实：本地 venv / uv 的`workspace_file_access` 与 `dependency_persistence` 为真（结构性），`network_access` 与 MCP 三项一律为 `null`（未探测）；`basis` 取 `structural` / `unverified` 说明这份声明的来源，不要在未探测时写 `true`。

接口：`read_state()` 返回 `{status, reason, context_id, data}`，`BOUND` 时 `data` 是经校验的 payload；`read_status()` 是只含前三键的兼容视图；`bind()` 返回 `read_state()` 的结果。

判读结果：`BOUND`（`ok`）/ `UNBOUND`（`no-local-state` / `no-self-state` / `root-mismatch`）/ `CORRUPT`（`bad-json` / `unknown-schema` / `missing-fields` / `missing-runtime` / `workspace-mismatch` / `context-mismatch` / `realm-mismatch` / `invalid-fields` / `unsafe-path`）/ `IDENTITY_UNAVAILABLE`（`identity-unavailable`）。

契约测试：`tests/test_local_state.py`（29 例：含并发读、首次绑定原子发布、symlink 逃逸、HMAC 固定向量、首绑文件+目录 fsync、多进程写、工作区别名、SID 解析、全分支只读不写盘）。

**存量实例注意**：`.gitignore` 不在 `TEMPLATE_FILES`，同步不会带来 `.workspace/.local/` 这一行，需按 `.workspace/skills/template-update/` 的「同步后检查」手工补一行。

## 6. Python 运行时：`pyenv_init.py`

```bash
python3 scripts/pyenv_init.py --mode uv --python 3.12            # dry-run
python3 scripts/pyenv_init.py --mode uv --python 3.12 --apply
python3 scripts/pyenv_init.py --mode venv --python 3.12 --apply
python3 scripts/pyenv_init.py --mode mcp --mcp-server s --mcp-tool t --apply
```

- uv 缺失 → exit 3 + 安装方式；**默认不联网安装**，仅当用户明确要求时才由用户执行安装（或用户自己加 `--allow-install` 跑官方脚本）；
- 已配置的工作区 → exit 5 `already-configured`（保护既有选择；`--force` 才切换）；
- 写**本机状态** `.workspace/.local/v1/<context_id>/runtime.json`（mode / venv 目录名 / 版本 / runtime 标识 / 能力声明），并翻转 `bootstrap.workspace_status=active`、写入 `workspace_id`、删掉旧的 `python` 节；输出一段 `TOOLS.md` 待粘贴行。

## 6.5 路径归一与文本 I/O：`osnorm.py`

`scripts/osnorm.py` 是**文件系统路径交换格式 + 文本 I/O 的单一实现**：纯 stdlib、叶子模块
（不 import 本仓任何模块）、import 期无全局副作用（`configure_stdio()` 必须显式调用）。
它**不是**业务校验器：git 语义、身份派生、扫描范围、锁与事务、权限 ACL 都不在它里面。

```python
from osnorm import (OSNORM_API, PathFormError, canonical_path, family, read_text, write_text,
                    read_bytes, write_bytes, append_text, run_text, utf8_env, collapse_path,
                    replace_retry, atomic_write_text, configure_stdio,
                    read_stdin_text, emit_stdout, emit_stderr)

canonical_path(r"\\?\UNC\host\share\x")        # '//host/share/x'
canonical_path("a/b", base="/r")               # '/r/a/b'（相对路径必须给 base）
collapse_path("a//b/./c")                     # 'a/b/c'（变换层：纯词法，不抛、不知道平台）
family("/mnt/c/Users")                        # 'wsl-mount'（≠ windows-drive）
read_text(p, bom=True)                        # utf-8-sig（剥 BOM）；bom=False 保 BOM
read_text(p)                                  # bom=None → 取 BOM_POLICY（默认 tolerate：剥 BOM）
write_text(p, "a\r\nb")                       # 磁盘落 'a\nb'（newline='lf' 默认）
append_text(p, "line\n")                      # O_APPEND 并发追加；不重写整文件
run_text(["git", "-C", ws, "status"])         # proc.stdout 恒为 str，永不是 None
OSNORM_API                                    # 3（hooks 装载器的版本判据）
```

**两层分工（`collapse_path` 与 `canonical_path`）**：路径写法的**裸折叠**只有一份实现——
`collapse_path(p) -> str`（消 `''`/`.` 段、并多余 `/`、去尾斜杠；`..` 可消则消，绝对形态
不可消即折到根、相对形态保留前导 `..`；不抛、不判越界、**不认 `\`**、不查文件系统）。
`canonical_path` 内部**不再自己折叠**，只做形态判定 / 平台语义（反斜杠、盘符、UNC share
边界）/ 组装 / 逃逸检查，全部折叠都委托 `collapse_path`。所以 `C:/a/../../x` → `C:/x`
（盘符族越界静默折到盘符根），而 `//h/s/../x` → `PathFormError`（UNC 的 `..` 折到 share
即逃出共享）——两处语义差异由各自的判定层保留，折叠层对两者一视同仁。

**canonical 判定（唯一判据表，全部由 `canonical_path` 实现）**

| 输入 | 结果 |
|---|---|
| `C:\a\b` / `C:/a/b` / `c:/A/b` | `C:/a/b` / `C:/a/b` / `C:/A/b`（盘符大写、段保留） |
| `/c:/a/b`（msys） | `C:/a/b` |
| `\\?\C:\a\b` / `//?/C:/a/b` | `C:/a/b`（剥前缀） |
| `\\?\UNC\host\share\x` / `\\host\share\x` | `//host/share/x` |
| `\\wsl.localhost\<distro>\…` | `//wsl.localhost/<distro>/…` |
| `/mnt/c/...` | 原样（**不**改写成 `C:`；wsl 挂载与盘符不同族） |
| `file:///C:/x` / `file://localhost/x` / `file://host/share/x` | `C:/x` / `/x` / `//host/share/x` |
| `\\.\PhysicalDrive0`、`\\?\GLOBALROOT\…`、`CON`/`NUL`/`COM1` | 抛 `PathFormError`（设备命名空间与保留名不是文件） |
| `C:relative`、裸 `C:`、`//host`（无 share）、`""`/全空白、含 NUL | 抛 `PathFormError` |
| `a/b`（无 base）、`../x`（逃出 base） | 抛 `PathFormError`（**没有 cwd 隐式回退**） |
| `a/b`（base=`/r`） | `/r/a/b`，与 `Path(...).as_posix()` 逐字一致（键口径） |

**换行与解码契约（API 与性质测试共用同一契约）**：`write_text(newline="lf")`（默认）先把
`\r\n`/`\r` 归一为 `\n`，磁盘恒为 LF；`newline="preserve"` 原样。`read_text` 的 `bom=True`
用 `utf-8-sig` 剥 BOM，`bom=False` 把 `\ufeff` 留给调用方；**不传 `bom`（`None`）时取模块级
`BOM_POLICY`**（默认 `tolerate` = 剥 BOM，见下节 §6.6）。`errors` 按场景显式声明：
治理/JSON 用 `strict`，日志用 `replace`，`secret_scan` 用 `ignore`；**不猜编码**——
UTF-16/GBK 在 `strict` 下上抛。

**追加与原子发布**：日志/账本（`gate.log`、`audit-*.jsonl`、`commits.jsonl`、委托账本）是
**多进程并发追加**的，`append_text` 用 `O_APPEND` 单次续写（语义等同 `open(..., "a")`），
**不能**拿 `write_text` 代替——原子发布是「重建整个文件」，会覆盖别的进程刚写的行。

**原子发布与子进程**：`atomic_write_text` 走「同目录临时文件 → fsync → `replace_retry`
（有界退避，Windows 读侧持句柄时 `os.replace` 会失败）→ 目录 fsync」，读者看不到半截文件；
预算耗尽时原样抛出，**绝不退化成非原子写**。`run_text` 固定 `capture_output=True` +
UTF-8 + `errors`，并把 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` 注入子进程环境
（`os.environ` → `utf8_env()` → 调用方 `env`，调用方优先），因此 `proc.stdout` 恒为 `str`。
流式/交互式子进程不在它范围内（调用方自行 `Popen`，且须进门禁 allowlist 登记）。

**门禁（blocking）**：`tests/test_osnorm_single_source.py` 由 `tests/` discovery 与 CI 执行——
`scripts/`、`.workspace/hooks/`、`tests/` 里再出现自带 `encoding=` 的文本 I/O（R1）、
自带 `text=`/`encoding=` 的子进程（R2）、直接 `os.replace`（R3）、直接 `reconfigure`（R4）、
自写归一化函数/内联键（R5）、`os.path.normcase`（R6）即红。规则级结构性豁免只有 osnorm 自身
（`canonical_path` / `run_text` / `replace_retry` / `configure_stdio`）与 `local_state` 的 no-follow
发布路径；两个 hooks adapter 的**定义**（`hook._absolute` / `gate_core._norm_join`，只剩 R5 语义的
路径拼接）按 R7 的 adapter oracle 钉在声明子域上；`release._utf8_stdio` 现在只是
`osnorm.configure_stdio` 的转调，不再是豁免。**hooks 侧一律经装载器使用 osnorm，禁止裸 import**：
`.workspace/hooks/osnorm_loader.py` 按「① hook 自身树 → ② 请求期 `ws_root` → ③ fail-closed」
解析，进程内只解析一次并钉住指纹（路径 + size + mtime_ns + sha256），
`OSNORM_API` 在 `exec_module` 之后、任何 API 调用之前校验；候选必须是非 symlink 的普通文件。
hooks 用 `read_stdin_text` 严格解码管道 stdin，并经 `emit_stdout` / `emit_stderr` 写 UTF-8 字节。
TTY 沿用流的编码，编解码错误采用 `replace`。
账本（`tests/osnorm_allowlist.json`）的三个段各有独立用途：

- `entries`（兜底存量）：`(file, rule, target, line_kind)` 节点级表项。`count` 必须**精确等于**
  实测（`>` 或 `<` 都红；归零必须删表项）；`baseline` 记录每个 `(file, rule)` 的历史最低值，
  `count` 不得高于它。R47 移除了 `hook.py` 的三处 `reconfigure`，`entries` 已归零。
- `structural`（结构性豁免，落 S3）：位置永久保留，但**理由由一条可执行 oracle 持续验证**。
  登记粒度 `(file, rule, target)`，`target` = `函数名#序数`（序数 = 该函数内 R5 命中按源码顺序
  的第几处，**不写行号**——行号会随上面任意一行改动漂移，漂移即红就把门禁变成噪音）。
  每条至少含 `file / rule / target / expr / verdict(a|b) / count_baseline / reason / oracle`，
  门禁机械校验四项：① `oracle` 指向的用例真实存在且被 `unittest` 收集（跑真实子进程，
  出现 `_FailedTest` 即「查无此例」；`oracle` 可为字符串或字符串列表，列表逐条都要收集且通过）；
  ② 该用例当前通过（rc=0）；③ 该 `(file, rule)` 实测计数不得高于 `count_baseline` 之和（未覆盖即红）；
  ④ 目标能在真实代码里定位——函数存在、该序数处确实是声明的表达式类。此外 15 条与实测 15 处
  **严格一一对应**：删条目或加一处 R5 都红。
  判定的两个方向都有 oracle：`(a)` 可等价 = 在声明的子域内与 `canonical_path` 得出**同一路径
  标识**（原生形态 + 回喂 canonical 往返）且出界就分叉；`(b)` 不同域 = 证明 osnorm 根本产不出
  该处产物（例如相对键：osnorm 没有 `relpath`，`canonical_path` 对无 base 的相对路径直接拒）。
  R48 起 `verdict=a` 的 12 条公共边界抽到顶层 `subdomains`（`host-native-join`）：条目只写
  `subdomain` 名 + 本条特有理由，门禁校验名在注册表、reason 引用了它、且边界文本没有被复制
  进 reason——避免 11 条文案各自漂移。比较基准从「宿主原生串」切换到「交换形态」由
  `OSNORM_COMPARE_BASELINE=native|exchange` 控制（默认 `exchange`，两侧一起 canonical），
  保证 R49 改调用点后判据不假绿/假红；`entries` 的 `expires` 采用**收紧**口径：早于当前 dev
  轮次即红（R39 批次已在 R44–R47 迁完，不再续期）。
- 账本本身进 `TEMPLATE_FILES` 哈希锁定：抬高账本会留下 `HASH-DIFF`，派生实例自行放宽会被
  `verify_rules --vs-template` 暴露。同仓内抬高要同时重建注册表，两次改动都留在 diff 与 CI 记录里。

## 6.6 文本编码：`encoding_guard.py`（BOM / 换行机械统一）

```bash
python3 scripts/encoding_guard.py --check            # 只报告；有发现 rc=1
python3 scripts/encoding_guard.py --check --json     # {"root","scanned","findings":[{"path","problems"}]}
python3 scripts/encoding_guard.py --fix              # 备份后剥 BOM + 统一 LF（幂等）
python3 scripts/encoding_guard.py --check --root /path/to/ws   # 诊断别的树（CI/排查用）
```

**扫什么**：`TEMPLATE_FILES`（唯一事实源 `scripts/verify_rules.py`）+ 用户可编辑清单
（`.workspace/bootstrap.json`、`WORKSPACE.md`、`TOOLS.md`、`notes/**/*.md`、任意 `*.custom.md`）。

**判什么**（只看字节，不解码）：`BOM` / 纯 `CRLF` / 纯 `CR` / 混合换行 `MIXED`。

**为什么是「一处决定 + 一键清理」而不是「报错让用户改」**：BOM 与 CRLF 都是外部工具
（记事本、PowerShell `Set-Content`、Git autocrlf）写的，用户通常不知道 BOM 是什么，
也难判断哪个编辑器会写它。所以四层分工：

| 层 | 做法 | 落点 |
|---|---|---|
| 读 | `osnorm.BOM_POLICY = "tolerate"`：`read_text(bom=None)` 剥 BOM | `scripts/osnorm.py` |
| 写 | `write_text` 固定 UTF-8 无 BOM（永不自产 BOM） | `scripts/osnorm.py` |
| 清理 | `--fix` 备份 → 剥 BOM → 统一 LF；无发现的文件一个字节都不碰 | 本脚本 |
| 门禁 | 模板仓自身 CI **阻断**；实例的 `workspace_preflight` 只 **WARN** | `ci.yml` / `workspace_preflight.py` |

`--fix` 的备份目录是 `.workspace/backup-encoding/<ts>/`（保留工作区相对路径，原始字节），
不覆盖已有备份；写盘走 `osnorm.atomic_write_bytes` 并保留原文件权限位。
`backup-*` 是递归文档扫描的旁路目录（`policy_facts._transient_dir()`），备份副本不会被
当成本仓文档，所以 `--fix` 之后再跑 `--check` 必须 rc=0；再跑一次 `--fix` 必须一个文件都不改（幂等）。

`template_sync.py --apply` 收尾会跑一次 `--check`，把「有 N 个文件带 BOM/换行问题 + 一键命令」
写进报告（`--json` 里的 `text_encoding`），**不自动改用户文件**。

## 6.7 提交前统一门禁：`check_all.py`

```bash
<venv-python> scripts/check_all.py            # = --check：只跑检查（阶段 5–15）
<venv-python> scripts/check_all.py --fix      # 先修（1–4），再查（5–15）
<venv-python> scripts/check_all.py --full     # 再加 tests + hooks-tests（16–17，约 2 分钟）
<venv-python> scripts/check_all.py --check --json | head -c 400
<venv-python> scripts/check_all.py --only encoding_check,version   # 调试单点，不是提交门
```

**为什么要它**：格式修复、注册表重建、判据检查、索引校验分散在十几个脚本里，靠人「记得逐个跑」
必然漏；本地跑一套、CI 跑另一套还会漂移。`check_all.py` 把顺序固定成一张表，本地提交前与
CI 治理 job 跑**同一条命令**。

| 序 | 阶段 | 命令 | 归类 |
|---|---|---|---|
| 1 | `encoding` | `scripts/encoding_guard.py --fix` | 仅 `--fix` |
| 2 | `registry` | `scripts/mark_rules.py registry AGENT_RULES.md` | 仅 `--fix` |
| 3 | `docs_index_fix` | `scripts/docs_index.py build` | 仅 `--fix` |
| 4 | `notes_fix` | `notes/rebuild_index.py` | 仅 `--fix` |
| 5 | `encoding_check` | `scripts/encoding_guard.py --check` | 检查 |
| 6 | `registry_check` | `scripts/mark_rules.py check AGENT_RULES.md` | 检查 |
| 7 | `verify_rules` | `scripts/verify_rules.py --workspace .` | 检查 |
| 8 | `clause_lint` | `scripts/clause_lint.py` | 检查 |
| 9 | `skill_audit` | `scripts/skill_audit.py --strict` | 检查 |
| 10 | `policy_facts` | `scripts/policy_facts.py --check` | 检查 |
| 11 | `docs_index_check` | `scripts/docs_index.py check` | 检查 |
| 12 | `notes_check` | `scripts/notes_index_check.py` | 检查 |
| 13 | `version` | `scripts/version_check.py` | 检查 |
| 14 | `sequence` | `scripts/sequence_check.py` | 检查 |
| 15 | `osnorm_gate` | `python -m unittest tests.test_osnorm_single_source` | 检查 |
| 16 | `tests` | `python -m unittest discover -s tests -p 'test_*.py'` | 仅 `--full` |
| 17 | `hooks` | `python -m unittest discover -s hooks-tests -p 'test_*.py'` | 仅 `--full` |

**为什么顺序不能重排**：修复阶段必须在检查阶段之前（否则查的是修之前的状态）；`osnorm_gate`
在 unittest 派发之前（它的职责是钉住「唯一实现」这条约束，红了就不该再往下跑全量测试）。
`sequence` 排在 `version` 之后：先确认政策/CHANGELOG/bootstrap 三元组自洽，再问「计划（`SEQUENCE.md`）
与账本是否互认」；它仍是判据检查，所以在 `osnorm_gate` 之前。实例侧 / 非模板仓没有 `.workspace/SEQUENCE.md`
（研发路线图不随模板下发），该阶段**显式 skip 且 rc=0**；但模板仓的 dev 分支删掉计划文件是 **FAIL**，
且 S10/S11 无论是否 skip 都执行。

**行为契约**（机械可判）：

- 每阶段打一行 `[ok] <stage> <秒>` / `[FAIL] <stage> <秒>`；失败时回显该阶段输出**尾部 20 行**。
- 任一阶段失败 → rc=1；`--fix` 里修复阶段失败**不短路**，后面的检查照跑（一次看到全貌）。
- `--fix` 收尾**强制复查**：修复阶段跑完后再跑一遍全部检查阶段，单独打印 `修复后复查` 段，
  末尾汇总写「修复后仍有阶段未通过：<names>」；有失败即 rc=1（修复是否收口只看这次复查）。
- `--json` 的 stdout 只有 `{"ok": bool, "stages": [{"name","ok","rc","seconds","tail"}]}`，人读进度走 stderr。
- **放行提示（只提示，不改退出码）**：全部阶段跑完后，若当前分支**有上游**且 `ahead > 0`（有未推送提交）、
  且 dev 账本 `.workspace/release_dev.json` **非空**，打印一行
  `WARN: 本轮已由三席放行，但尚未由用户放行（未 push）；全绿 ≠ 已放行`。push 永远由用户执行，
  所以「全绿但没 push」是正常中间态、不判红；但它必须出现一次，免得把三席放行读成用户放行。
  `--json` 下这行走 stderr，stdout 仍只有一个 JSON 对象；退出码不受影响。
- 子进程一律走 `osnorm.run_text`（恒捕获 + utf-8），解释器用**当前** `sys.executable`。
- **只调度，不改判据**：不解释 findings、不自动 `git commit` / `git push`、不把失败改判成通过。
- `--only a,b` 精确覆盖模式过滤（按阶段表顺序执行）——调试口；提交前只认完整三连。

**它不覆盖什么**：`.github/scripts/release_anchor.py`（模板层改动必须进 `CHANGELOG`、tag 身份）
留在 CI 里单独跑；`docs_hit_report.py` 的命中率判据、`docs_alias_audit.py` 的别名审计不在阶段表内
（前者的判据集与阈值是独立口径，后者按需人工触发）。

## 7. 退出码约定

| 码 | 含义 |
|---|---|
| 0 | 成功 / 达标 |
| 1 | 有 findings 或前置检查失败（可用，但需处理） |
| 2 | 参数错误 / 缺索引 / 校验失败（视脚本） |
| 3 | 需要外部条件（如安装 uv）/ stale 查询 |
| 4 | 创建或校验失败 |
| 5 | 拒绝改写（已配置的 Python 模式） |

带 `--json` 的脚本把机器可读结果放 stdout；`verify_rules.py --json <path>` 写文件；人类提示在同段 JSON 的 `hint` 字段或 stderr。

