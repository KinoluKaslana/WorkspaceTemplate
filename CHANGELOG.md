# CHANGELOG — WorkspaceTemplate 变更记录

> 本文件只记录模板的政策/功能变更历史，供模板开发者与维护者查阅。工作区 Agent 无需读取本文件——实际生效规则以 `AGENT_RULES.md` 及其 custom 文件（`AGENT_RULES.custom.md`）为准。

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
