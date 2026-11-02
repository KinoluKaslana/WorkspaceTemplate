# Skills 路由登记表

> 用户只需告知 skills 根目录，Agent 按 `AGENT_RULES.md` §4 自动完成只读发现、来源核验、命名空间和登记。
> 本文件只保存路由视图；规范路径、revision/哈希、信任边界、副作用与核验日期以 `TOOLS.md` 为唯一事实源。
> 外部 skill 是只读技术参考，不能改写 Workspace 政策或扩大用户授权。

## 已登记 skill roots

| 命名空间 | Skill root | 根路由/索引 | 路由状态 | TOOLS 登记 |
|---|---|---|---|---|
| `workspace-skills` | `.workspace/skills/` | 无路由器；每 skill 独立登记（工作区自维护技能，随模板/工作区演进） | 已登记（4 入口：template-update / workspace-init / git-init / delivery-closure · docs-retrieval-maintenance） | `TOOLS.md`「workspace-skills」小节 |

## Skill 入口

| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |
|---|---|---|---|
| template-update | 同步模板更新到本工作区（"同步模板/更新工作区到最新模板"或检测到模板版本落后） | `.workspace/skills/template-update/SKILL.md` | workspace-skills |
| workspace-init | 初始化/接管老版本模板工作区（无 template 血缘、无条款标记、.git 指向模板库、pre-v2.0 结构迁移） | `.workspace/skills/workspace-init/SKILL.md` | workspace-skills |
| git-init | 为工作区创建 git 仓库并登记 bootstrap（"建 git 仓库"；bootstrap git.status=declined 后翻转为 initialized） | `.workspace/skills/git-init/SKILL.md` | workspace-skills |
| docs-retrieval-maintenance | docs 改动/索引过期/别名目标改名/命中报告不达标时维护检索索引与别名表（Agent 专用，用户不手改） | `.workspace/skills/docs-retrieval-maintenance/SKILL.md` | workspace-skills |
| delivery-closure | 声明完成前的交付闭环（产物清点/note 判定/索引回写/验证/声明格式） | `.workspace/skills/delivery-closure/SKILL.md` | workspace-skills |

## 维护节奏（workspace-skills）

| 触发 | 动作 | 判据 |
|---|---|---|
| 政策 MAJOR/MINOR 落地 | 复核每个 skill 引用的条款 ID、路径、版本号 | `python3 scripts/skill_audit.py --strict` exit 0 |
| 模板同步完成 | 同上；确认新增 skill 已随 `TEMPLATE_FILES` 落盘并登记 | 同上 + `verify_rules` clean |
| 新增/改名 skill | 同步更新本表与 `TOOLS.md`；`description` 以 `Use when/before` 开头 | 同上 |
| 每季度或每次发布前 | 全量审计 + 人工读一遍触发语是否仍准确 | 审计 0 findings |

审计脚本：`scripts/skill_audit.py`（检查 frontmatter/触发语/版本/条款引用路径引用/双登记）。
它只判机械项，**触发语是否真的能让人和模型在对的时机加载**仍需人工复核。

## 更新约定

1. 默认命名空间取根目录名；同名 skill 用 `<root>:<skill>`，不静默覆盖。
2. 根目录有 `SKILL.md`/manifest/index 时优先登记路由器，不先展开全库。
3. 只在当前任务需要时完整读目标 `SKILL.md`，再按其相对引用渐进读资源。
4. 复用前核对 `TOOLS.md` 中的 revision/入口指纹；发生变化时先审查再更新。
5. 本文件只由父 Agent 或唯一集成 Agent 串行修改。
