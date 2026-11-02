# 工具（scripts/，10 个）

> 实现说明，不构成政策。所有脚本只依赖标准库，要求 Python ≥ 3.10。**`--json` 语义各脚本不同**：`verify_rules.py` 的 `--json` 是**输出文件路径**，`skill_audit.py`/`workspace_preflight.py`/`docs_*.py`/`template_sync.py`/`pyenv_init.py` 的 `--json` 是「打印 JSON 到 stdout」，`mark_rules.py` 无此参数——以各条示例与 `--help` 为准。

## 0. 一览

| 脚本 | 作用 | 典型用法 | 退出码 |
|---|---|---|---|
| `verify_rules.py` | 模板层完整性：文件哈希 + 条款 ID + custom 引用；`--vs-template` 做字节级比对 + git 信任锚 | `--workspace . [--vs-template <克隆>] [--json]` | 0 clean / 1 有 findings |
| `mark_rules.py` | 给条款打稳定 ID（`<!-- id:Rn -->`）并重建注册表 | `registry AGENT_RULES.md` | 0 / 1 |
| `inspect_skills.py` | 只读盘点 skills 目录（入口、name/description、嵌套） | `<skills-dir> [--include-nested]` | 0 / 1 |
| `skill_audit.py` | skills 契约节 / 双登记 / 计数与名称集合 / 引用路径 | `[--strict] [--sync-registry] [--json]` | 0 / 1 |
| `docs_index.py` | docs 确定性索引：`build` / `check` / `query` | `build`；`query <词> --json --top 2` | 0 / 1(stale) / 2(缺索引) / 3(stale 查询) |
| `docs_hit_report.py` | 判据集命中报告（`tests/docs_probe.json`） | `--min 9 --top 2 --json` | 0 达标 / 1 未达标 / 2 stale |
| `docs_alias_audit.py` | 别名审计：死指针/指纹漂移/来源合规/留一验证 | `[--strict] [--restore] [--json]` | 0 / 1 |
| `template_sync.py` | 按 ref 同步模板层：dry-run / apply / prune | `--workspace . --ref alpha [--apply --prune --verify --json]` | 0 / 1 前置失败 / 2 校验失败 |
| `workspace_preflight.py` | 跨设备预检：9 项「登记 vs 本机实际」 | `[--workspace .] [--json] [--strict]` | 0 无 mismatch / 1 有 mismatch |
| `pyenv_init.py` | Python 运行时初始化：uv / 手动 venv / MCP | `--mode uv\|venv\|mcp [--apply] [--json]` | 0 / 1 / 2 参数 / 3 需装 uv / 4 校验失败 / 5 已配置拒绝 |

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
- 回报 `changed / skipped / identical / removed_upstream / pruned / python_mode_preserved`；
- `--prune` 删除上游已移除的模板层文件（先备份，并清理空父目录）。

## 5. 跨设备：`workspace_preflight.py`

9 项：`host / workspace_root / python / template_clone / skills / template_files / notes_index / docs_index / git`。
每项 `recorded / actual / status / action`；`mismatch` 阻塞，`warn` 记录后继续，`n/a` 跳过。
**只报告、不改写记录**——记录是共享资产，改写由用户裁决后执行。

## 6. Python 运行时：`pyenv_init.py`

```bash
python3 scripts/pyenv_init.py --mode uv --python 3.12            # dry-run
python3 scripts/pyenv_init.py --mode uv --python 3.12 --apply
python3 scripts/pyenv_init.py --mode venv --python 3.12 --apply
python3 scripts/pyenv_init.py --mode mcp --mcp-server s --mcp-tool t --apply
```

- uv 缺失 → exit 3 + 安装方式；**默认不联网安装**，仅当用户明确要求时才由用户执行安装（或用户自己加 `--allow-install` 跑官方脚本）；
- 已配置的工作区 → exit 5 `already-configured`（保护既有选择；`--force` 才切换）；
- 写 `bootstrap.python`（mode/runtime/version/configured_at），输出一段 `TOOLS.md` 待粘贴行。

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

## 8. 变更记录

- 2026-11-02：首版，对应 10 个脚本与 policy 3.3.1。
