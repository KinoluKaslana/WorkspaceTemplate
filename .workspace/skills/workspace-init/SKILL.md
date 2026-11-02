---
name: workspace-init
description: "Use when initializing or adopting a workspace created from an OLD WorkspaceTemplate version (pre-v2.0), or any workspace whose governance files predate the locked-template + custom-override architecture. 初始化/接管老版本模板工作区时使用。"
version: 1.2.0
---

# Workspace Init — 老版本工作区初始化 skill

把一个**老版本模板工作区**（v2.0 之前创建，或结构不明）安全带入当前模板体系。老工作区可能已有大量本地改动、`.git` 可能仍指向模板库——本 skill 的每一步都围绕"不丢用户语义"设计。

## 触发条件

- 用户说"初始化这个工作区 / 接管这个工作区 / 把 XX 工作区升到新模板体系"；
- 或 Agent 检测到：`bootstrap.json` 缺 `template` 节 / `AGENT_RULES.md` 无条款 ID 标记 / `.git` remote 指向 `KinoluKaslana/WorkspaceTemplate`；
- 或**全新实例化**（无 `bootstrap.json` 且无 `AGENT_RULES.md`）：跳过第 4 步迁移，直接按模板复制 + 第 5–8 步落地。

**误触发**：工作区已是当前版本（有条款标记且 `bootstrap.template.current_policy_version` 与目标一致）——那是 `template-update` 的活。
**漏触发**：只有 `bootstrap.json` 缺失、其余文件齐全的"半初始化"工作区。

## 职责边界

- **归本 skill**：老版本/半初始化工作区的接管与迁移；首次实例化落地；bootstrap 血缘回写。
- **不归**：日常模板同步 → `template-update`；建仓 → `git-init`；跨设备拉推 → `workspace-repo-sync`。
- **不动**：用户业务文件、私有目录、`notes/` 正文。

## 前置事实（先读，不可跳过）

1. 目标模板源可读（`TEMPLATE_DIR` 或 `template_sync.py --clone-url` 可克隆）；
2. 工作区在可写路径，且**动手前**已确认无其他 Agent 正在写（同 uid 无锁，靠人工确认）；
3. 用户已同意迁移（涉及覆盖模板层与删除 `.git`，属 R59 高影响动作）；
4. 任一不成立 → **停下报告**，不降级执行。

## 总原则

1. **先盘点、后动手**：任何写操作前完成第 1 步全量盘点。
2. **用户语义零丢失**：本地规则迁入 custom 层；本地独有 commit 导出 patch；备份先行。
3. **venv 必须、git 可选**：Python 运行时按 §0.1/§0.2 必须落地；git 按 §0.3 由用户决策。
4. **失败即停**：任一步校验不过，停下报告，不带病前进。

## 详细流程（九步）

### 第 1 步：全量盘点（只读）

```bash
WS=<工作区绝对路径>
ls -la "$WS"; cat "$WS/.workspace/bootstrap.json" 2>/dev/null || echo "无 bootstrap"
grep -c 'id:R[0-9]' "$WS/AGENT_RULES.md" 2>/dev/null || echo 0   # >0 = 已有条款标记（全文件计数，勿只看前 N 行）
git -C "$WS" remote -v 2>/dev/null; git -C "$WS" status --short 2>/dev/null | head; git -C "$WS" log --oneline -5 2>/dev/null
ls "$WS"/*.custom.md 2>/dev/null; ls "$WS/notes/" 2>/dev/null | head
```

记录到初始化报告：模板版本线（版本头）、是否有条款标记、`.git` 状态与 remote、既有 custom 文件、notes 规模、工作树脏文件数。

### 第 2 步：定位模板源并确认目标版本

```bash
TEMPLATE_DIR=~/Github/WorkspaceTemplate   # 本地克隆；否则浅克隆最新
git -C "$TEMPLATE_DIR" pull --ff-only 2>/dev/null; grep -o '模板政策版本：`[0-9.]*`' "$TEMPLATE_DIR/AGENT_RULES.md" | head -1
```

### 第 3 步：差异判定（新旧注册表对比）

```bash
<py> "$TEMPLATE_DIR/scripts/verify_rules.py" --workspace "$WS" --registry "$TEMPLATE_DIR/.workspace/rule-clauses.json" --json /tmp/init-diff.json
```

registry 用**模板最新版**：报告 `[file] MISSING`（工作区缺 v2.0 新文件）与 custom 引用问题。若工作区 AGENT_RULES.md 无标记 → 判定为"pre-v2.0 迁移"路径（第 4 步），否则为"直接同步"路径（跳到第 5 步）。

### 第 4 步：pre-v2.0 → v2.0 迁移（本地语义外置）

1. **备份（必须覆盖"将被替换的全部文件"，不是挑几个）**：
   ```bash
   BK="$WS/.workspace/backup-init-<date>"; mkdir -p "$BK"
   python3 - "$TEMPLATE_DIR" "$WS" "$BK" <<'PY'
   import shutil, sys
   from pathlib import Path
   sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
   from verify_rules import TEMPLATE_FILES
   tpl, ws, bk = (Path(x) for x in sys.argv[1:4])
   for rel in TEMPLATE_FILES:                 # 与第 4 步替换清单同源
       src = ws / rel
       if src.is_file():
           (bk / rel).parent.mkdir(parents=True, exist_ok=True)
           shutil.copy2(src, bk / rel)
   for extra in ("TOOLS.md", ".workspace/bootstrap.json"):
       p = ws / extra
       if p.is_file():
           (bk / extra).parent.mkdir(parents=True, exist_ok=True)
           shutil.copy2(p, bk / extra)
   PY
   diff -r "$BK" "$WS" >/dev/null 2>&1 && echo "警告：备份为空，停止" && exit 1
   ```
   备份集必须 ⊇ 第 4 步的替换集（历史上漏备份 `scripts/`、`skills/`、`rule-clauses.json` 导致回滚不完整）。
2. **提取本地差异**：`diff "$WS/AGENT_RULES.md" "$TEMPLATE_DIR/AGENT_RULES.md" > /tmp/rules.diff`（逐 hunk 人工审读——老工作区的本地章节如人格定义、路径约定都在这里）。
3. **写入 custom 层**：把每个"工作区独有"的规则块整理进 `$WS/AGENT_RULES.custom.md`，每条标注 `overrides: R<id>`（改写了哪条模板条款）或 `extends:`（纯新增）。不确定对应关系时标 `extends:` 并在报告注明。
4. **模板层还原**：从 `$TEMPLATE_DIR` 复制全部模板层文件（AGENT_RULES.md、AGENTS/CLAUDE/HERMES、README*、scripts/*、notes/rebuild_index.py、_TEMPLATE.md、.workspace/skills/、.workspace/rule-clauses.json），`diff` 验证逐字节一致。
5. **bootstrap 血缘回写**：`template.source_repo`、`current_policy_version`、`last_synced_at`。

### 第 5 步：Python 运行时（必须项，AGENT_RULES §0）

**先判断，再问**：读 `bootstrap.python.mode`——
- 已配置（`uv` / `local-venv` / `mcp`）→ **保持原样**，不提问、不重建 venv、不切 uv；只在报告里记录"沿用既有模式 X"。
- `unconfigured` 或缺失 → 按三选一询问（默认 uv）。

三种模式由用户选：**uv（默认推荐）** / 手动 venv / MCP。机械入口（已配置工作区会被 exit 5 拒绝，需 `--force`）：

```bash
python3 scripts/pyenv_init.py --mode uv   --python <ver>            # dry-run
python3 scripts/pyenv_init.py --mode uv   --python <ver> --apply    # uv（默认）
python3 scripts/pyenv_init.py --mode venv --python <ver> --apply    # 手动 venv
python3 scripts/pyenv_init.py --mode mcp  --mcp-server <s> --mcp-tool <t> --apply
```
uv 未安装时脚本 exit 3 并给安装方式；**不得**替用户联网安装（除非用户明确要求并加 `--allow-install`）。


读 `bootstrap.json` 的 `python.mode`：

- `unconfigured` → 按 §0 询问用户：本地版本化 venv（推荐，`§0.1` 流程）或 MCP Python（`§0.2` 流程）。完成后 `workspace_status: active`。
- `local-venv`/`mcp` → 核验登记信息仍有效（venv 存在、解释器版本一致），失效则按上条重新配置。
- 验证：`"$WS/.venv-*/bin/python" --version`（或 MCP 工具实测），结果写入 TOOLS.md。

### 第 6 步：git 标记处置 + 建仓决策（AGENT_RULES §0.3）

**6a. 抹除模板 git 标记**（仅当 `.git` 存在且 remote 指向模板库）：

```bash
cd "$WS"
git remote -v          # 必须显示 KinoluKaslana/WorkspaceTemplate
git log origin/main..HEAD --oneline | wc -l   # 本地独有 commit 数
# 若 >0：git format-patch origin/main..HEAD -o .workspace/backup-init-<date>/patches/  （先导出再删）
rm -rf .git
```

remote 指向他处 / 历史无法确认 → **暂停，向用户展示发现并请裁决**，绝不擅自删除。

**6b. 询问建仓决策**（抹除后必问，一次）：

> 模板 git 标记已清除。是否把本工作区初始化为独立 git 仓库（可配你自己的 remote 做多设备同步）？现在建 / 暂不建（以后随时可建）。

- **现在建** → 执行 git-init skill 全流程；
- **暂不建** → bootstrap `git.status: "declined"`，报告注明"后续说'建 git 仓库'即可触发 git-init skill"。

### 第 7 步：skills 登记核验

确认 `skills/REGISTRY.md` 有 `workspace-skills` 命名空间，且**入口集合 = `.workspace/skills/*/SKILL.md` 目录集合**（不要写死名单）；跑 `python3 scripts/skill_audit.py --strict`，需要重排时用 `--sync-registry`；`bootstrap.json` 的 `skills.registered_roots` 含 `workspace-skills`。缺则补登记（TOOLS.md 同步「workspace-skills」小节条目）。

### 第 8 步：全量验证

```bash
<py> "$WS/scripts/verify_rules.py" --workspace "$WS" --vs-template "$TEMPLATE_DIR" --json /tmp/init-verify.json   # 权威校验（含信任锚）
<py> "$WS/notes/rebuild_index.py"                                                                                   # 索引跑通
```

两项任一不过 → 按备份回滚对应文件，报告后停。注意：验证用的 scripts 与注册表以**模板最新版**为准（第 4 步已随模板层替换）。

### 第 9 步：初始化报告

`盘点结果 → 迁移清单（迁入 custom 的规则块）→ Python 模式 → git 决策与 patch 备份位置 → 校验三项结果 → 版本（工作区旧版 → 模板版）`。建 note 记录初始化结论（按 AGENT_RULES §9）。

## 变更记录

- 1.2.0（2026-11-02）：明确「已配置的 `python.mode` 不因迁移而改变」，只有 `unconfigured` 才问（默认 uv）。
- 1.1.0（2026-11-02）：修完备性缺口——条款标记检测改全文件计数（原 `head -20` 对现行模板必然误判为"无标记"）；备份清单改为与替换清单同源；登记核验按目录派生；补"全新实例化"触发、职责边界、前置事实。
- 1.0.0：首版。

## 陷阱

- **不要用工作区老版 verify_rules.py 校验自己**——老版注册表与模板新版不一致，会全屏假阳性；registry 必须指向模板最新版。
- pre-v2.0 工作区的 `.workspace/template-base/`（v1.x BASE 机制）在 v2.0 下无用途：保留作历史备份即可，不再更新。
- `git log origin/main..HEAD` 在 remote 已失联时会报错——先 `git fetch` 失败则按"历史无法确认"走暂停路径。
- 用户在工作区根放的私有文件（override.txt、任务目录等）不属治理范围，盘点时列出但不动。
- MCP Python 模式下无法运行本地脚本的，文件操作走 Agent 文件工具，脚本校验由 Agent 手工比对哈希替代并在报告注明限制。
