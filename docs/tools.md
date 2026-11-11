# 工具（scripts/，16 个）

> 实现说明，不构成政策。所有脚本只依赖标准库，要求 Python ≥ 3.10。**`--json` 语义各脚本不同**：`verify_rules.py` 的 `--json` 是**输出文件路径**，`skill_audit.py`/`workspace_preflight.py`/`docs_*.py`/`template_sync.py`/`pyenv_init.py` 的 `--json` 是「打印 JSON 到 stdout」，`mark_rules.py` 无此参数——以各条示例与 `--help` 为准。

## 0. 一览

| 脚本 | 作用 | 典型用法 | 退出码 |
|---|---|---|---|
| `verify_rules.py` | 模板层完整性：文件哈希 + 条款 ID + custom 引用；`--vs-template` 做字节级比对 + git 信任锚 | `--workspace . [--vs-template <克隆>] [--json]` | 0 clean / 1 有 findings |
| `mark_rules.py` | 给条款打稳定 ID（`<!-- id:Rn -->`）并重建注册表 | `registry AGENT_RULES.md` | 0 / 1 |
| `inspect_skills.py` | 只读盘点 skills 目录（入口、name/description、嵌套） | `<skills-dir> [--include-nested]` | 0 / 1 |
| `version_check.py` | 版本三元组：政策 ↔ CHANGELOG ↔ bootstrap | `[--json]` | 0 / 1 |
| `notes_index_check.py` | notes 索引与重建结果一致（防手改/过期） | `[--json]` | 0 / 1 |
| `secret_scan.py` | 高置信度明文凭据扫描（只打印脱敏片段） | `[--root PATH] [--json]` | 0 / 1 |
| `clause_lint.py` | 条款属性门：正文用词 ↔ `clauses.meta.json` 声明一致 | `[--strict] [--report] [--json]` | 0 / 1 |
| `skill_audit.py` | skills 契约节 / 双登记 / 计数与名称集合 / 引用路径 | `[--strict] [--sync-registry] [--tools-snippet] [--json]` | 0 / 1 |
| ↳ `--sync-registry` 契约 | 写完**回读校验**（名称集合 + 入口节计数）；不一致或 I/O 失败 → exit 1 并输出 `registry_problems`。入口行按列结构解析（首列名必须与第三列路径自洽），字符宽度与发现器一致；目录名不符 lowercase-kebab 只在 `--strict` 提示 | 同上 | 0 / 1 |
| `docs_index.py` | docs 确定性索引：`build` / `check` / `query` | `build`；`query <词> --json --top 2` | 0 / 1(stale) / 2(缺索引) / 3(stale 查询) |
| `docs_hit_report.py` | 判据集命中报告（`tests/docs_probe.json`） | `--min 9 --top 2 --json` | 0 达标 / 1 未达标 / 2 stale |
| `docs_alias_audit.py` | 别名审计：死指针/指纹漂移/来源合规/留一验证 | `[--strict] [--restore] [--json]` | 0 / 1 |
| `template_sync.py` | 按 ref 同步模板层：dry-run / apply / prune / rollback；**写盘与校验都是事务性的**（失败自动回滚，`--keep-on-fail` 保留现场） | `--workspace . --ref alpha [--apply --prune --verify --json]`；`--rollback <备份目录>`；`--keep-on-fail` | 0 / 1 前置失败 / 2 校验失败（已回滚）/ 3 写盘或校验器异常（已回滚）/ 4 回滚失败（需按 MANIFEST 手工恢复） |
| `workspace_preflight.py` | 跨设备预检：11 项「登记 vs 本机实际」；`--layout-only` 只读诊断 venv 布局 | `[--workspace .] [--json] [--strict] [--harness …] [--layout-only]` | 0 无 mismatch / 1 有 mismatch / `--layout-only`：0 找到解释器 / 0 mcp（`layout=n/a`，不探测）/ 3 没有、未配置或缺 identifier |
| `pyenv_init.py` | Python 运行时初始化：uv / 手动 venv / MCP | `--mode uv\|venv\|mcp [--apply] [--json]` | 0 / 1 / 2 参数 / 3 需装 uv / 4 校验失败 / 5 已配置拒绝 |
| `gate_status.py` | harness 接线自检：各客户端有没有**运行期** gate 证据 | `[--require cc,codex,hermes,dsh] [--ttl-minutes N] [--json]` | 0 有证据 / 1 `--require` 缺证据 |
| `local_state.py` | 本机状态按 context 分片（库，无 CLI）：`read_state` / `read_status` / `bind` / `resolve_identity` / `detect_platform_info` | `import local_state` | 库：不变量失败抛 `ValueError`/`TypeError`；判读结果在返回字典的 `status`/`reason` |

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

## 5. 跨设备：`workspace_preflight.py`

11 项：`host / workspace_root / python / gate / template_clone / skills / template_files / notes_index / docs_index / git / readme`。
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

