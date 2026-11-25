# CHANGELOG_DEV — dev 迭代记录（四段版本）

> 本文件只记录 **dev 分支**的迭代，不记录正式发布。正式发布的历史在 [`CHANGELOG.md`](CHANGELOG.md)。
>
> 标题格式（机械判据：`scripts/policy_facts.py` 的 `dev_offenders()`）：
> `## NN.X.Y.Z（YYYY-MM-DD）` —— 四段；`NN` = 迭代轮次（从 1 起），`X.Y.Z` = 本轮**预计发布**的正式版本。
> 版本号后的紧跟字符不得是数字、ASCII 字母、`.` 或 `-`；`###` 子标题不参与对账。
>
> **dev 版本不是政策版本。** 政策版本只由四处表达：`AGENT_RULES.md:4` 的政策头、
> `CHANGELOG.md` 顶层条目、`.workspace/bootstrap.json` 的 `template.current_policy_version`、
> `.workspace/releases.json` 末条；它们的一致性由 `scripts/version_check.py` 与
> `scripts/policy_facts.py --check` 机械对账。本文件的四段版本**不参与**那套对账，
> `version_check` 也不读本文件。
>
> 写入方式：只经 `scripts/release.py --dev --apply`（写本文件首条 + `.workspace/release_dev.json`）。
> 手改其中任一处都会被 `policy_facts --check` 判红（判据：账本末条 == 本文件首个标题）。
> 该脚本**不 push、不打 tag**；dev 版本只追加、只升不降。
>
> 账本：`.workspace/release_dev.json`（append-only，字段对齐 `releases.json`，另加 `round`）。

## 47.3.8.0（2026-11-25）

R47 stdio 三件套（R4 归零）：osnorm 新增 read_stdin_text/emit_stdout/emit_stderr（OSNORM_API 2→3）、删除 hook.py reconfigure 三行、收敛 print(json.dumps) 散点、账本 entries 3→0。取版 gpt + 汇总席签名修补；v55 三席通过 + 真 Windows ①②④ 原生复跑全过。

## 46.3.8.0（2026-11-24）

R46 计划↔账本互校机制（S1–S13，R46a–R46g）：sequence_check.py 以第二实现为基线重做 S1–S11（git 历史锚、三席确认、_DEV 分支归属、远端锚）；新增 S12 迭代日志冻结锚（release.py --dev 写 iterations/iterations_sha256，落地后回改=红；未入账轮次豁免）与 S13 §1 landed 行整行冻结（append-only rows，含 S13 建前合法改写的代码白名单）；check_all 收尾打印「三席放行 ≠ 用户放行」WARN（不改退出码）；SEQUENCE.md 补 §4.1 锚分层威胁模型与迭代日志边界。三席核验 v54 + v54b 轻量复核通过。

## 44.3.8.0（2026-11-23）

> 收录轮次 **R35–R44**（用户裁决）。dev 基线 = v3.8.0 之后的 14 个提交（`01c0966..e3ec3a5`，2026-11-18 → 11-23）。
> 本条目同时承接原 `CHANGELOG.md` 的 `## Unreleased` 段（main 只保留已发布版本）。
> 每节标注来源提交；轮次标签只出现在提交信息自己写明的场合（R38/R39b/R40/R41/R42a/R42b/R43、R34-C1..C5），
> 未写轮次的提交按主题归纳，不补编号。

### 1. doc-fact 生成器 + 发版机械闭环 + 事务级回滚（`9008294` → `18495b1`，随 3.8.0 正式发布）

- `scripts/policy_facts.py`：事实只在权威源维护，文档里的对应文字整段渲染进 `<!-- gen:policy-facts:begin/end -->`；
  版本字面量按**位置**判定（生成块内唯一合法 / `<!-- history:x.y.z -->` 已登记历史 / 其余不判）。
- `scripts/release.py`：`--dry-run` / `--apply` / `--verify` / `--tag` / `--list`；`--apply` 按序写政策头 →
  `CHANGELOG` 行首条目 → bootstrap → 生成块 → 条款注册表 → docs 索引 → 发版账本，全程事务化 + 临时备份回滚。
- 硬约束：只能升不能降、工作树不干净拒绝、绝不 commit/push/自动打 tag。
- 账本 `.workspace/releases.json` 与判据「账本末条 == 政策头」：手改版本号必红，除非连账本一起改写（留痕可见）。
- 事务缺陷修复：账本追加排到所有写盘之后（否则每次合法发版都报 `HASH-DIFF releases.json` 假红）；
  `restore()` 改 best-effort 逐文件恢复，输出「未恢复清单 + 每项原因 + 原始异常」。

### 2. Windows 跨系统兼容（`01c0966`，提交信息记 R34-C1..C5 与 R37 系列）

- 编码：子进程统一 UTF-8 + `errors=replace`，注入 `PYTHONUTF8` / `PYTHONIOENCODING`，消除 GBK 解码与 `stdout=None`。
- 路径族：支持 `C:\`、`C:/`、`/c:/`、UNC、WSL UNC，保留盘符并归一斜杠。
- 原子写：`os.replace` 只对 `PermissionError` 退避（≤2s），保持原子性。
- `release_anchor` 在 Windows 下正确取 tag；hooks 的 reads 字典键统一为工作区相对 POSIX 路径（修 `KeyError: AGENT_RULES.md`）。
- 新增 `tests/test_windows_compat.py`（+23 例）；平台相关假设改为平台无关断言。

### 3. osnorm 归一化层：地基、门禁与迁移（R38，`148615b` / `1c0cb8b` / `04684f3` / `8788afd`）

- `scripts/osnorm.py`：平台探测、路径族、`canonical_path`、`read_text`/`write_text`（UTF-8 + 换行契约）、
  `read_bytes`/`write_bytes`、`run_text`（恒捕获 + UTF-8 env）、`replace_retry`（WinError 5/32/33 退避）、
  `atomic_write_text`、`configure_stdio`。
- 金表 `tests/test_osnorm_cases.json` + 9 条性质 + R1–R9 AST 门禁 + 存量违规 allowlist。
- 迁移计数从「上限」改为 **exact + baseline 双校验**：count 必须等于实测，且不得高于历史最低值（堵 count 注水）。
- 账本纳入 `TEMPLATE_FILES` 哈希锚（用户裁决方案 C）：抬高账本必留 `HASH-DIFF`；`docs/tools.md` §6.5 口径同步。
- I/O 组 10 个脚本迁移（违规 761 → 670），CLI 输出与退出码逐字一致（characterization）。

### 4. BOM 策略机械化 + `encoding_guard`（`0fb966c`）

- `osnorm` 增 `BOM_POLICY="tolerate"`：`read_text(bom=None)` 按策略剥 BOM；`write_text` 固定无 BOM。
- 新增 `scripts/encoding_guard.py`：`--check` 报 BOM/CRLF/混合换行，`--fix` 剥 BOM + 统一 LF，
  改前备份到 `.workspace/backup-<ts>-encoding/`，幂等（干净树连跑两次零字节改动）。
- 接线：preflight `text_encoding`（WARN）、CI 对模板仓阻断、`template_sync` 收尾跑 `--check`。
- 修 pre-existing 缺陷：`bootstrap.json` 带 BOM 时 `policy_facts` 曾判 `workspace-status-unknown`；
  `notes_index_check` 曾把「距今 N 天」写进索引而隔天必红。

### 5. 提交前统一入口 + 扫描范围收口（`30e6e2b`，含 R39b）

- 新增 `scripts/check_all.py`：修复阶段 4 个 → 检查阶段 10 个（`--full` 再加 tests / hooks-tests）；
  `--json` 输出 `{"ok","stages"}`；**只调度，不 commit/push、不改判据**。
- `--fix` 收尾**强制复查**：修完再跑一遍全部检查阶段，单列「修复后仍有阶段未通过」，有失败即 rc=1。
- 新增模板层 skill `.workspace/skills/commit-gate/SKILL.md`：`git commit` 前强制 `--fix` → `--check` → `--full`。
- CI 治理 job 改用 `check_all.py --check`，本地与 CI 跑同一条命令。
- **R39b 扫描收口**：`policy_facts._iter_md()` 不再拿 `rglob("*.md")` 把旁路目录当本仓文档——
  跳过逻辑收敛成模块级 `TRANSIENT_DIR_PATTERNS` + 单一函数 `_transient_dir()`
  （`.git`、`.venv*`、`__pycache__`、`node_modules`、`.hook-state`、`.workspace/.local`、`backup-*`、`sandbox-*`、`*.egg-info`）。

### 6. 剩余脚本迁移完成（R40，`728b7e9`）

- `template_sync` / `release` / `local_state` / `gate_status` / `workspace_preflight` / `pyenv_init` /
  `docs_index` / `secret_scan` 共 84 处迁到 osnorm，scripts 违规清零（670 → 586）。
- osnorm 补两条「与 `open(...,'w')` 等价」的硬约束：只读目标先查 `os.access` 并抛 `PermissionError`；
  既有权限位随内容保留（避免 0644 治理文件被 `mkstemp` 的 0600 取代）。
- `check_all --fix` 幂等（内容未变不写盘）；commit-gate 阶段数改为 `--check` 10 / `--fix` 14 / `--fix --full` 16。

### 7. hooks 经装载器使用 osnorm（R41，`4eecb83` / `d29f16a`）

- 新增 `.workspace/hooks/osnorm_loader.py`：候选 ① hook 自身树 → ② `ws_root/scripts` → ③ fail-closed；
  只接受普通文件（拒 symlink）；同进程只解析一次并记指纹（path+size+mtime_ns+sha256），变化即
  `changed-after-load`；`OSNORM_API=1` 必选；① 存在但损坏一律拒绝、不回退 ②。
- fail-closed 包络：仅 `pre_write` / `pre_bash` 输出
  `hookSpecificOutput.{hookEventName=PreToolUse, permissionDecision=deny, permissionDecisionReason}`，其余事件 advisory。
- hooks 违规 67 → 18 处；`osnorm.py` 在 `TEMPLATE_FILES` 里前移到 hooks 之前（同步先落实现、再落使用者）。
- `d29f16a`：`test_unreadable` 平台分列——POSIX 用 `chmod 000`，Windows 用 `icacls <file> /deny <DOMAIN\USER>:(RD)`
  （实测 `(R)` 会连 `READ_CONTROL` 一起拒、DACL 修不回来）；删掉打不到子进程的注入兜底（那条绿是假证据）。

### 8. 测试侧严格读法（R42a/R42b，`dd23fd8` / `c00c287`）

- 规则：测试一律 `osnorm.read_text(bom=False, newline="preserve")` 或 `read_bytes`，**不得**继承产品的宽容策略
  （默认形态会剥 BOM、归一换行，产品开始写 BOM/CRLF 时测试照样绿）。
- 证伪（同一注入，迁移前红 / 迁移后绿）：`gate_status` 写 JSON 加 `\ufeff` → 迁移前 `FAILED (errors=4)`；
  `template_sync` MANIFEST 写 CRLF → `FAIL test_apply_writes_manifest_sections`（本版补了首行逐字与无 `\r` 两条断言）。
- 33 个测试文件、519 处迁移完；allowlist 账本 537 → 272 → 18（tests 清零，只余 hooks）。

### 9. hooks R5 结构性豁免（R43，`e094a85`）

- `tests/osnorm_allowlist.json` 升 v2，新增 `structural` 段：登记粒度 `(file, rule, target)`，
  `target` = `函数名#序数`（不写行号——行号会因无关编辑漂移，实测插入一行后 15 条一起翻红），
  每条带 `verdict(a|b)` / `oracle` / `count_baseline` / `reason`。
- 门禁机械校验四条：① oracle 指向的用例存在且被收集；② 该用例当前通过；③ 该 `(file, rule)` 实测计数不高于
  `count_baseline` 之和；④ target 能在真实代码里定位（函数存在 + 该序数处确是声明的表达式类）。
- 逐条判定：12 处 (a) 可等价（`join` + `normpath`，子域 = host 原生 POSIX 绝对 base + 不逃逸段、无盘符/设备名/反斜杠），
  3 处 (b) 不同域（`gate_key` / `_recent_files` / `_scan_hashes` 产**相对键**：osnorm 没有 `relpath`，
  `canonical_path` 只产绝对交换路径、对无 base 的相对路径直接抛 `PathFormError`）。
- 账本总数守恒：`entries` 3（`hook.py` 的 R4）+ `structural` 15 = 18 处，不靠删条目清零；hooks 行为零改动。

### 10. 路径折叠：变换层 `collapse_path`（R44a，`e3ec3a5`）

- `scripts/osnorm.py` 新增公开函数 `collapse_path(p)`：单入单出、纯词法、不抛、不认平台。
  消 `''`/`.` 段、并多余 `/`、去尾斜杠（根除外）；`..` 可消则消，绝对形态折到根即停，相对形态保留前导 `..`。
- `canonical_path` 内部**不再自己折叠**，一律委托 `collapse_path`；它只做形态判定、平台语义
  （盘符大写 / UNC / file-url / 保留名）、组装与 base 逃逸判定。
- 收益：`..` 折叠从三份（`_norm_posix_abs` / `_norm_drive` / `_norm_unc` / `_join_relative` 各一套，
  且语义不一致）合为一份；UNC 的手写循环只剩 share 越界判定。
- 冻结证据：24 输入 × 4 组 base 共 96 组，`canonical_path` 输出与改前**逐条相同（差异 0）**。
- 三份规则不一致处显式钉住：盘符族 `'C:/a/../../x' -> 'C:/x'`（静默折到根）；
  UNC 族 `'//h/s/../x' -> PathFormError`（越 share 即抛）。
- `OSNORM_API` 1 → 2；`.workspace/hooks/osnorm_loader.py` 的期望常量同批改为 2。
- 测试：tests 486 → 493（collapse 金表 13 条 + 幂等 / 不抛 / 两条语义钉住）。

### 覆盖边界（本条目刻意不写的东西）

- 只写**已合入 dev** 的提交；`R44` 在 `AGENT_RULES.md` 里是条款 ID（与轮次无关），
  因此本条目用提交号与主题指代该轮（R44a = `e3ec3a5`），不靠轮次编号识别。
- dev 版本 `44.3.8.0` 不对应任何 tag；政策头仍是 `3.8.0`。
- 未决（沿用 3.8.0 的已知未决，本轮未处理）：测试侧取数逻辑与生产侧并存（正则已单源、取数未单源）；
  回滚后只读文件仍保持 `444`；`gate_status.py` 的 scope 算法与 `gate_core` 仍有分叉。

