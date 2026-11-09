# 架构：政策 · 守门 · 流程 · 工具

> 实现说明，不构成政策。判定口径以 [`AGENT_RULES.md`](../AGENT_RULES.md) 为准。
> 当前对应：政策 **3.5.13** · 插件 **1.9.0** · `TEMPLATE_FILES` **80** 项 · skills **8** 个 · scripts **15** 个。

## 1. 四层模型

```
┌──────────────────────────────────────────────────────────────┐
│ ① 政策层  AGENT_RULES.md（+ *.custom.md）                     │
│    唯一权威；条款 ID R1..R134；模板层锁定只读                  │
└───────────────┬──────────────────────────────────────────────┘
                │ 读（主 Agent 每任务完整读并常驻）
┌───────────────▼──────────────────────────────────────────────┐
│ ② 守门层  .hermes/plugins/workspace-gate（Hermes）            │
│           .workspace/hooks/ + .codex/hooks.json + .claude/…   │
│    机械判据：写工作区前是否有「当前修订的完整读证据」           │
└───────────────┬──────────────────────────────────────────────┘
                │ 兜底（不是替代）
┌───────────────▼──────────────────────────────────────────────┐
│ ③ 流程层  .workspace/skills/（7 个）                          │
│    8 维契约：触发/前置/契约/边界/执行/失败回滚/验收/维护       │
└───────────────┬──────────────────────────────────────────────┘
                │ 调用
┌───────────────▼──────────────────────────────────────────────┐
│ ④ 工具层  scripts/（15 个）                                   │
│    确定性、可复跑、带退出码；不读政策、只认参数与文件事实      │
└──────────────────────────────────────────────────────────────┘
```

**为什么这样分**：政策会演进、守门会失效、流程会写错、工具会有 bug。四层分开后，每层都能被独立验证：
`verify_rules` 验①、`selfcheck` 验②、`skill_audit` 验③、各脚本自带 `--json` + 退出码验④。

## 2. 一次「写文件」的完整路径

```
Agent 决定写 X
  ├─ Hermes：插件 pre_tool_call
  │    ├─ 解析 X 所属工作区（相对路径按 task cwd 锚点；解析不出一律拦）
  │    ├─ 取当前会话账本：是否覆盖 AGENT_RULES.md 当前修订的 1..total？
  │    │    ├─ 是 → 放行，记 intent{target, base_sha}
  │    │    └─ 否 → 子作用域？→ 查有效委托（目标精确命中 + 父证据有效）
  │    │              ├─ 命中 → 放行（记 delegation-allow）
  │    │              └─ 未命中 → block（[workspace-gate:no-read]）
  │    └─ 写后：post_tool_call → commits → commits.jsonl（无 intent 记 unobserved）
  └─ Codex / CC：PreToolUse hook
       ├─ Codex：apply_patch / Bash 静态可判写 → 同样判据
       └─ CC：Write/Edit 受管；Bash 不订阅（只能 Stop/SessionEnd 转录回放事后归因）
```

## 3. 读证据模型（贯穿全系统）

| 概念 | 定义 | 存放 |
|---|---|---|
| 覆盖 | 行区间集合，必须 `1..total` 连续无洞 | `coverage[]` |
| 指纹 | 读时磁盘内容 `c<bytes>:<sha256>` | `fingerprint` / `coverage_rev` |
| 完整 | 覆盖完整 **且** 指纹 == 判定时刻磁盘指纹 | 派生判定 |
| 委托 | 父为子签发的「可写目标集合 + 父证据引用 + TTL + gate 指纹」 | `delegations.jsonl` |
| 三值记账 | `gate-intent` → `gate-commit` → 缺失一侧记 `unobserved-write` | `commits.jsonl` / `audit-*.jsonl` |

**关键性质**：改一次政策 → 指纹变 → 所有旧证据作废 → 必须重读。这是「读完再写」的机械保证，也是为什么「一次性读证明」不被接受。

## 4. 失败姿态（必须分两层说）

| 层 | 行为 |
|---|---|
| 钩子内部异常 | 写前钩子 fail-closed（拒）；观察型钩子（读后/收尾）fail-open（放，但记审计） |
| 客户端不送达钩子 | **fail-open**：守门不生效，只能用 `unobserved` 事后暴露 → 所以整体口径是 **advisory**，不是安全边界 |
| 同 uid 篡改 | 无防护：账本由同一用户在磁盘上，能改就能骗；防篡改属沙箱/权限层 |

## 5. 跨设备模型

工作区在多台机器各自克隆，**三样东西必须分开**：

| 类别 | 例子 | 规则 |
|---|---|---|
| 设备本地 | 根路径、venv 目录、模板克隆路径、HERMES_HOME、python.mode | 按主机分列登记（`TOOLS.md` §1）；**不互相改写** |
| 仓库共享 | `notes/`、`skills/`、`scripts/`、`docs/` | 走 git；冲突由用户裁决 |
| 派生/易变 | `docs/.index.json`、`notes/INDEX.md`、`.hook-state/` | 拉取后重建；`.hook-state/` 必须被 gitignore |

`workspace_preflight.py` 把这三类差异摊开（11 项），`workspace-repo-sync` skill 负责按用户裁决执行。

## 6. 模板演进与实例的关系

```
模板仓库（正主）            工作区实例
  AGENT_RULES.md   ──整文件替换──►  AGENT_RULES.md（锁定）
  scripts/…        ──整文件替换──►  scripts/…（锁定，79 项清单）
  *.custom.md      ──── 永不触碰 ──► 本地规则（custom 优先）
  WORKSPACE/TOOLS  ──── 永不触碰 ──► 业务与环境事实
```

- 同步入口：`scripts/template_sync.py --ref <分支|标签|提交> [--apply] [--prune]`；
- 同步**不改** `bootstrap.python`（`python_mode_preserved=true` 是硬条件）；
- **`.github/` 不下发**：CI 工作流与 `.github/scripts/release_anchor.py` 属模板仓自己的基础设施，不进
  `TEMPLATE_FILES`、不随 `template_sync` 同步到实例；实例不需要它们也能跑全部治理脚本（`tests/test_docs_consistency.py` 的 D9 断言此边界）。
- 清单唯一事实源：`scripts/verify_rules.py` 的 `TEMPLATE_FILES`（当前 79 项；**新增/删除模板层文件时，本页与 `docs/README.md` 的数字必须同步**，`tests/test_docs_consistency.py` 会拦）。

## 7. 扩展点

| 想加 | 改哪里 | 不要改 |
|---|---|---|
| 新 harness | `docs/hooks-catalog.md` §3 七步 + `.codex/hooks.json` 或 `.claude/settings.json` | 不要复制政策正文 |
| 新 skill | 按 `.workspace/skills/_TEMPLATE.md` 落盘 + `skill_audit --sync-registry` | 不要在 skill 里写义务句 |
| 新工具 | `scripts/` + `TOOLS.md` 登记 + `TEMPLATE_FILES`（若是模板层） | 不要绕过 `--json`/退出码约定 |
| 新政策 | 改 `AGENT_RULES.md`（R 号只增）+ `CHANGELOG.md` + 重建注册表 | 不要在 docs 里定义务 |
