# CHANGELOG — WorkspaceTemplate 变更记录

> 本文件只记录模板的政策/功能变更历史，供模板开发者与维护者查阅。工作区 Agent 无需读取本文件——实际生效规则以 `AGENT_RULES.md` 及其 custom 文件（`AGENT_RULES.custom.md`）为准。

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
