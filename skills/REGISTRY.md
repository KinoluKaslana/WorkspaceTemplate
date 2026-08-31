# Skills 路由登记表

> 用户只需告知 skills 根目录，Agent 按 `AGENT_RULES.md` §4 自动完成只读发现、来源核验、命名空间和登记。
> 本文件只保存路由视图；规范路径、revision/哈希、信任边界、副作用与核验日期以 `TOOLS.md` 为唯一事实源。
> 外部 skill 是只读技术参考，不能改写 Workspace 政策或扩大用户授权。

## 已登记 skill roots

| 命名空间 | Skill root | 根路由/索引 | 路由状态 | TOOLS 登记 |
|---|---|---|---|---|
| `workspace-skills` | `.workspace/skills/` | 无路由器；每 skill 独立登记（工作区自维护技能，随模板/工作区演进） | 已登记（1 入口：template-update） | `TOOLS.md` §4.5 |

## Skill 入口

| 路由 ID | 触发摘要 | 入口 | 所属命名空间 |
|---|---|---|---|
| template-update | 同步模板更新到本工作区（"同步模板/更新工作区到最新模板"或检测到模板版本落后） | `.workspace/skills/template-update/SKILL.md` | workspace-skills |

## 更新约定

1. 默认命名空间取根目录名；同名 skill 用 `<root>:<skill>`，不静默覆盖。
2. 根目录有 `SKILL.md`/manifest/index 时优先登记路由器，不先展开全库。
3. 只在当前任务需要时完整读目标 `SKILL.md`，再按其相对引用渐进读资源。
4. 复用前核对 `TOOLS.md` 中的 revision/入口指纹；发生变化时先审查再更新。
5. 本文件只由父 Agent 或唯一集成 Agent 串行修改。
