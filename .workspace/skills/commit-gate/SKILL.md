---
name: commit-gate
description: "Use when a git commit is imminent — before committing, right after `release.py --apply`, before merging into main, or whenever 「提交前检查」 comes up；不用于发版版本记录（那是 release）。提交前质量门：一条命令跑完全部格式修复与判据检查。"
version: 1.1.0
---

# commit-gate — 提交前质量门

## 职责边界

- **归本 skill**：把「提交前到底要跑哪些脚本」收敛成一条命令（`scripts/check_all.py`），并规定**先修、再查、再全量**的顺序与失败处置口径；提交 / 合并 / 推送仍由用户执行。
- **不归**：版本记录、CHANGELOG 条目、tag 身份 → `.workspace/skills/release/SKILL.md`（release 管「这次发的是哪个版本」，本 skill 管「这份工作树能不能提交」）。
- **不归**：判据本身的内容与失败后的代码修复 → 由人/AI 读 findings 后修，本 skill 只负责跑到红并指名阶段。
- **不动**：`scripts/check_all.py` 的阶段表、`AGENT_RULES.md` 条款正文、`.workspace/rule-clauses.json` 的判据语义；也**不得**在本流程里自行 `git commit` / `git push` / 改判据（R63：版本控制动作只在用户当次明确要求时执行）。

## 触发条件

- 进入：任何 `git commit` 之前；`scripts/release.py --apply` 之后（发版写盘会动模板层文件与生成块）；合并到 `main` 之前；用户说「提交前检查」「跑一遍门禁」「check all」。
- **误触发例**：只想发版/打 tag 本身（走 `release`）；只想查某个单点问题（直接跑那个脚本，别用本 skill 当体检）。
- **漏触发例**：改完 `docs/`、`notes/` 或 `.workspace/skills/` 就提交（索引与注册表最容易漏）；在 CI 之外手工删改生成块后直接提交。

## 前置事实（先读，不可跳过）

1. 已选 Python 运行时（本机状态 `runtime.json` 的 `mode`）与 `.venv` 解释器路径，从 `TOOLS.md` 取；本 skill 全部命令都用**该解释器**的绝对路径，不用裸 `python3`。
2. 工作树里没有未完成的冲突标记与半截事务（`git status` 自己看；本 skill 不改工作树状态）。
3. 事实不成立时：**阻塞**（解释器缺失 → 先按 §0.1 建 venv；冲突未解 → 先解决），不降级、不跳过。

## 输入输出契约

- 读：`scripts/check_all.py` 的阶段表与退出码；`docs/tools.md` 的阶段说明；上一步的 stdout。
- 写：**只由各阶段自己写**（`--fix` 阶段会改编码/注册表/索引）；本 skill 不直接写任何文件。
- 报告：阶段名 + `[ok]/[FAIL]` + 秒数 + 失败阶段输出尾部 20 行；最终 rc。

## 标准流程（3 步，顺序固定）

### 第 1 步：自动修复（格式类）

```bash
<venv-python> scripts/check_all.py --fix      # 阶段 1–15：4 个修复（encoding / registry / docs_index / notes）+ 11 个检查
```

期望：共 **15 个阶段**全 `[ok]`（`--fix` = 1–15：先 4 个修复阶段，再强制重跑 11 个检查阶段），rc=0。修复阶段会**改文件**：跑完必须看一眼 `git diff`，确认改动只是格式/生成物（BOM、注册表哈希、索引），没有语义变化；干净树上这一趟必须是**零 diff**。

### 第 2 步：只查不写

```bash
<venv-python> scripts/check_all.py --check    # 阶段 5–15：11 个检查阶段
```

期望：共 **11 个阶段**（5–15）全 `[ok]`，rc=0。这一步证明「第 1 步修完之后，工作树是干净的」——只有它绿，才说明第 1 步的修复真的收敛了。

### 第 3 步：全量（含单元测试）

```bash
<venv-python> scripts/check_all.py --fix --full   # 阶段 1–17：17 个阶段（慢，约 2 分钟）
```

期望：共 **17 个阶段**（1–17，含 `tests` 与 `hooks-tests`）全 `[ok]`，rc=0。**提交前必须跑过这一步**：`--check` 不覆盖测试，测试红却提交等于把红灯推给 CI。

CI 侧同一条命令：`.github/workflows/ci.yml` 的治理 job 跑 `scripts/check_all.py --check`；本地三步都要过，不是「CI 会兜」。

## 失败与回滚

| 失败特征（exit code / 输出特征） | 处置 |
|---|---|
| rc=1 且失败阶段 ∈ {`encoding`, `encoding_check`, `docs_index_fix`, `docs_index_check`, `notes_fix`, `notes_check`, `registry`, `registry_check`} | **格式类**：先跑 `--fix` 再复查 `--check`；仍未收敛说明有阶段互相打架（例如手改了生成块），按失败阶段名去读对应脚本 docstring |
| rc=1 且失败阶段 ∈ {`verify_rules`, `clause_lint`, `policy_facts`, `sequence`, `osnorm_gate`, `skill_audit`} | **判据类**：禁止用 `--fix` 蒙混（这些阶段本来就不在修复段）。读 findings 原文，由人/AI 判断后改正文/注册表/调用点，再整轮重跑。`sequence` 红 = 计划侧（`SEQUENCE.md`）与账本/`CHANGELOG_DEV.md` 不互认：改计划并登记，或回退落地，**不许**改判据迁就现状 |
| rc=1 且失败阶段 ∈ {`tests`, `hooks`} | 单个测试红：先看尾部 20 行里的 `FAIL:`/`ERROR:` 行名，再单跑该用例定位；**不得**把测试改绿当修复 |
| rc=2 | 用法错误（`--only` 里有未知阶段等）：改命令行，不是改判据 |
| `--fix` 改出意外 diff（语义变化） | `git checkout -- <path>` 回到提交前状态，再查是哪个阶段的生成逻辑出了问题；回滚自验 = 重跑 `--check` 必须复现同样的红 |

## 禁止

- **禁止手挑脚本跑**：`mark_rules` + `verify_rules` + `clause_lint` 各跑一遍不等于跑过门禁——阶段之间有顺序依赖（修复在前、检查在后）。
- **禁止 FAIL 仍提交**：任一阶段 rc≠0 就不提交，也不许「先提交再修」。
- **禁止用 `--only` 绕过必需阶段**：`--only` 是调试口；提交前只认完整的 `--fix` → `--check` → `--full` 三连。
- **禁止**在本流程里 `git commit` / `git push` / 打 tag（R63）。

## 验收（命令 + 期望输出，可被他人复跑）

```bash
<venv-python> scripts/check_all.py --check; echo rc=$?        # 期望：全 [ok]，rc=0
<venv-python> scripts/check_all.py --check --json | head -c 400  # 期望：{"ok": true, "stages": [...]
<venv-python> scripts/check_all.py --fix --full; echo rc=$?   # 期望：17 阶段（1–17）全 [ok]，rc=0
```

负向自检（证明门禁真的抓得住）：给任一 `.md` 加 UTF-8 BOM → `--check` 必须 rc=1 且点名 `encoding_check`；`--fix` 后复跑必须 rc=0。

## 维护

- 阶段表变更（新增/重排/改参数）：同步改 `scripts/check_all.py` 的 `STAGES`、`docs/tools.md` 的阶段表与 `CHANGELOG.md`，再跑 `--full`。
- 改本 skill 后：`<venv-python> scripts/skill_audit.py --strict`；登记在 `skills/REGISTRY.md` 与 `TOOLS.md` 两处（`--sync-registry` 可重排，但描述手写）。
- 版本语义：PATCH=措辞；MINOR=新增步骤/阶段约束；MAJOR=改变提交门口径（例如并入测试）。
- 与 `release` 的分工：release 管版本记录与 tag，commit-gate 管提交前质量门；`release --apply` 之后仍必须走本 skill 三连。
