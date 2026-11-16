---
name: release
description: "Use when any request asks to raise the policy version, create a release tag, or publish WorkspaceTemplate. 任何升政策版本、打 tag、发版请求都必须使用。"
version: 1.0.0
---

# Release — 模板发版机械闭环

## 职责边界

- **归本 skill**：政策版本、CHANGELOG、bootstrap、生成物、账本和 tag 的顺序化发布。
- **不归**：push 与实例同步；二者由人工在本地事务全绿后执行。
- **不动**：remote、分支和提交历史；脚本绝不 push。

## 触发条件

- 进入：任何「升政策版本 / 打 tag / 发版」请求。
- **误触发例**：只改业务文档但不发版，应走对应开发流程。
- **漏触发例**：只说「把政策号改成 X.Y.Z」也属于发版，不得直接编辑。

## 前置事实（先读，不可跳过）

1. 工作树必须干净，`.workspace/releases.json` 末条必须是当前政策版本；否则阻塞。
2. 新版本必须是严格递增 semver；不成立时阻塞，不降级、不跳过。

## 输入输出契约

- 读：用户给出的版本与摘要、git 状态、政策头、bootstrap、发版账本。
- 写：仅由 `scripts/release.py` 写政策头、CHANGELOG、bootstrap、生成块、注册表、docs 索引和账本。
- 报告：dry-run 计划、11 项清单、人工 tag/push/实例同步命令。

## 标准流程（7 步，顺序固定）

```bash
.venv/bin/python -B scripts/release.py --version X.Y.Z --summary "…" --dry-run
# 人工确认后：
.venv/bin/python -B scripts/release.py --version X.Y.Z --summary "…" --apply
.venv/bin/python -B scripts/release.py --verify
.venv/bin/python -B scripts/release.py --tag X.Y.Z --apply
git push origin HEAD && git push origin vX.Y.Z
# 再按 template-update skill 从 vX.Y.Z 做实例同步
```

强制顺序：`--dry-run` → 人工确认 → `--apply` → `--verify` → `--tag --apply` → 人工 push → 实例同步。禁止手改版本号、CHANGELOG 条目或 `releases.json`；任一步失败立即停工，不许跳过继续。

## 失败与回滚

| 失败特征（exit code / 输出特征） | 处置（回滚到哪个状态 + 自验命令） |
|---|---|
| `--apply` 非零 | 脚本从临时备份恢复全部事务文件；跑 `git status --short`，必须回到执行前基线 |
| 回滚后仍有差异 | 停工；用 `git diff -- <文件>` 定位，仅恢复本次差异，再重跑 dry-run；不得手改三处发版事实 |
| tag 身份校验失败 | 脚本删除刚建的本地 tag；修复后从 `--verify` 重来，不得 push |

## 验收（命令 + 期望输出，可被他人复跑）

```bash
.venv/bin/python -B scripts/release.py --verify  # 期望 exit 0
.venv/bin/python -B scripts/release.py --list    # 期望末条版本等于政策头
```

## 维护

- 改本 skill 后：`python3 scripts/skill_audit.py --strict`；登记重排用 `--sync-registry`。
- 同步 `skills/REGISTRY.md` 与 `TOOLS.md`；版本历史见 `CHANGELOG.md`。
- 版本语义：PATCH=措辞；MINOR=新增步骤；MAJOR=改变发布边界。
