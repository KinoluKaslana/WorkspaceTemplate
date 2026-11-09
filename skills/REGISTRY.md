# Skills 路由登记表

> 用户只需告知 skills 根目录，Agent 按 `AGENT_RULES.md` §4 自动完成只读发现、来源核验、命名空间和登记。
> 本文件只保存路由视图；规范路径、revision/哈希、信任边界、副作用与核验日期以 `TOOLS.md` 为唯一事实源。
> 外部 skill 是只读技术参考，不能改写 Workspace 政策或扩大用户授权。

## 已登记 skill roots

| 命名空间 | Skill root | 根路由/索引 | 路由状态 | TOOLS 登记 |
|---|---|---|---|---|
| `workspace-skills` | `.workspace/skills/` | 无路由器；每 skill 独立登记（工作区自维护技能，随模板/工作区演进） | 已登记（8 入口：delivery-closure / docs-retrieval-maintenance / git-init / legacy-workspace-upgrade / template-update / workspace-init / workspace-repo-sync / workspace-skill-authoring） | `TOOLS.md`「workspace-skills」小节 |
| `workspace-skills-local` | `.workspace/skills-local/` | 无路由器；每 skill 独立登记（用户所有） | 已登记（0 个本地 skill） | `TOOLS.md` §4.6 |

## Skill 入口

| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |
|---|---|---|---|
| delivery-closure | Use before declaring any task-mode deliverable complete (persistent artifacts produced). 声明任务完成前的交付闭环检查——产物清点/note 判定/索引回写/部署验证六步机械流程时使用。 | `.workspace/skills/delivery-closure/SKILL.md` | workspace-skills |
| docs-retrieval-maintenance | Use when docs/ changed, the retrieval index is stale, an alias target was renamed, or the hit report drops. 维护 docs 检索索引与别名表：索引可全自动，别名仅在有真实查询缺口日志时可自主新增（用户不手改）。 | `.workspace/skills/docs-retrieval-maintenance/SKILL.md` | workspace-skills |
| git-init | Use when the user asks to create/initialize a git repository for a workspace that previously declined one (bootstrap git.status=declined), or to re-record git info in bootstrap. 为此前未建仓的工作区创建 git 仓库并登记 bootstrap 时使用。 | `.workspace/skills/git-init/SKILL.md` | workspace-skills |
| legacy-workspace-upgrade | Use when a workspace was created from an OLD template version and lacks the current skills (no template-update 3.x / workspace-repo-sync / docs-retrieval-maintenance). 老版本模板工作区缺少现行 skill 时用它自举升级（只依赖 Python，其余取自模板快照）。 | `.workspace/skills/legacy-workspace-upgrade/SKILL.md` | workspace-skills |
| template-update | Use when syncing a workspace's template layer from any template source revision (branch, tag, or commit) — not tied to one branch. 从任意模板来源（分支/标签/提交）更新工作区模板层时使用。 | `.workspace/skills/template-update/SKILL.md` | workspace-skills |
| workspace-init | Use when initializing or adopting a workspace created from an OLD WorkspaceTemplate version (pre-v2.0), or any workspace whose governance files predate the locked-template + custom-override architecture. 初始化/接管老版本模板工作区时使用。 | `.workspace/skills/workspace-init/SKILL.md` | workspace-skills |
| workspace-repo-sync | Use when syncing the workspace's own git repo across devices (notes/TOOLS/artifacts changed elsewhere), or when recorded tool paths may belong to another machine. 跨设备同步工作区自身仓库时使用：先核验登记事实与工具真实性，再让用户裁决差异。 | `.workspace/skills/workspace-repo-sync/SKILL.md` | workspace-skills |
| workspace-skill-authoring | Use when the user asks to create or modify a workspace-level (local) skill in THIS workspace — 「给我建一个 skill」「把这个流程固化成技能」「改一下本地 skill」。不用于模板层 skill 的修改（那要回模板仓）。当用户明确要求新增/修改工作区级 skill 时使用。 | `.workspace/skills/workspace-skill-authoring/SKILL.md` | workspace-skills |

## 职责边界（workspace-skills）

- 这些 skill 只管**模板层**相关动作：模板文件同步、模板源 ref 选择、以及工作区初始化/建仓登记。
- **工作区自身 git 仓库的分支切换、迁移、历史改写由用户自行维护**；skill 不提供也不执行这类流程，`bootstrap.git.default_branch` 只作事实记录。
- 业务工作流 skill（如交付闭环、docs 检索维护）不越界改写政策或授权。

## 维护节奏（workspace-skills）

| 触发 | 动作 | 判据 |
|---|---|---|
| 政策 MAJOR/MINOR 落地 | 复核每个 skill 引用的条款 ID、路径、版本号 | `python3 scripts/skill_audit.py --strict` exit 0 |
| 模板同步完成 | 同上；确认新增 skill 已随 `TEMPLATE_FILES` 落盘并登记 | 同上 + `verify_rules` clean |
| 新增/改名 skill | 同步更新本表与 `TOOLS.md`；`description` 以 `Use when/before` 开头 | 同上 |
| 每季度或每次发布前 | 全量审计 + 人工读一遍触发语是否仍准确 | 审计 0 findings |

审计脚本：`scripts/skill_audit.py`（检查 frontmatter/触发语/版本/条款与路径引用/双登记/**8 维契约节**/计数与名称集合）；`findings>0` 即 exit 1，可直接进验收链；登记重排用 `--sync-registry`（按目录重写入口表与计数，不手改）。
它只判机械项，**触发语是否真的能让人和模型在对的时机加载**仍需人工复核。

## 更新约定

1. 默认命名空间取根目录名；同名 skill 用 `<root>:<skill>`，不静默覆盖。
2. 根目录有 `SKILL.md`/manifest/index 时优先登记路由器，不先展开全库。
3. 只在当前任务需要时完整读目标 `SKILL.md`，再按其相对引用渐进读资源。
4. 复用前核对 `TOOLS.md` 中的 revision/入口指纹；发生变化时先审查再更新。
5. 本文件只由父 Agent 或唯一集成 Agent 串行修改。

## 本地层入口

> 由 `skill_audit.py --sync-registry` 生成；只有 `approved` 可作路由依据。

| 路由 ID | 状态 | 触发摘要 | 入口 |
|---|---|---|---|
