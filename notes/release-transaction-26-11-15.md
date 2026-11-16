---
name: release-transaction
description: 模板发版应由单入口事务脚本同步版本事实、生成物与账本，并用临时备份实现全量回滚。
category: workspace-governance
techniques:
  - transactional-file-backup
  - append-only-release-ledger
related:
---

# 发版事务机械闭环 (26-11-15)

## 类别

Workspace 治理 / 模板发布。

## 关键特征

- 政策版本、CHANGELOG、bootstrap、生成块、注册表、docs 索引和 tag 分散，顺序曾依赖人工记忆。

## 有效方法

1. 以 `scripts/release.py` 统一 dry-run、apply、verify、tag；apply 前要求干净工作树，子步骤失败即恢复明确的可写集合。
2. `.workspace/releases.json` 只追加发布事实，`policy_facts.py --check` 对账其末条与政策头。
3. 在独立临时 git 副本真跑成功、拒绝与语法错误注入，证明回滚后状态与关键文件哈希恢复。

## 失效方法 / 弯路

- 把异常追加在 `raise SystemExit(main())` 之后不会执行；故障注入应破坏入口执行路径，例如在临时副本制造语法错误。

## 陷阱

- `--apply` 不提交；打 tag 前仍需人工提交并恢复干净工作树。脚本不 push。
- 受限沙箱把 `.git` 设为只读时，依赖 `git worktree add` 的既有测试会失败，不能误归因于发布事务。

## 可复用要点

1. 多生成器发布流程的回滚集合必须覆盖所有直接文件与子进程生成物，并记录原先是否存在以删除本次新增文件。

## 本次查阅的 notes

- `notes/INDEX.md` — ➖ 无既有 note。

## 技术扩展推荐

- 无。

## 敏感信息处理

- 不适用；未记录凭据或外部账户信息。

## 环境

- 使用用户指定的 `.venv/bin/python`（Python 3.14.8）；Codex gate 无 30 分钟内证据，用户明确接受无 gate。
