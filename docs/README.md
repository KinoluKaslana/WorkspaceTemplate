# 文档索引

> 本目录只讲**实现事实与操作步骤**，不是政策。授权、禁止、例外与判定标准只写在 [`AGENT_RULES.md`](../AGENT_RULES.md)；冲突以政策为准（R100）。

## 按读者选入口

| 你是 | 先读 | 再读 |
|---|---|---|
| 第一次用模板建工作区 | [`../README.md`](../README.md) 的「快速开始」 | 本页「三条必跑命令」 |
| 想把模板同步到某个分支/标签 | [`skills.md`](skills.md) → `template-update` | [`tools.md`](tools.md) → `template_sync.py` |
| 要给 DeepSeek Harness（dsh）接守门 | [`dsh.md`](dsh.md) | [`../.dsh/workspace-gate/plugin.js`](../.dsh/workspace-gate/plugin.js) |
| 要给 Codex / Claude Code 接守门 | [`hooks-guide.md`](hooks-guide.md) | [`hooks-catalog.md`](hooks-catalog.md) |
| 要接入新的 harness | [`harness-onboarding.md`](harness-onboarding.md)（测试清单 + 经验库） | [`hooks-catalog.md`](hooks-catalog.md) §3（7 步接线） |
| 要维护 skills 或新增 skill | [`skills.md`](skills.md) §契约 | `.workspace/skills/_TEMPLATE.md` |
| 工作区来自很旧的模板、连当前 skill 都没有 | [`../UPGRADE.md`](../UPGRADE.md)（自举） | `skills.md` → `legacy-workspace-upgrade` |
| 跨设备（两台机器）用同一个工作区 | [`skills.md`](skills.md) → `workspace-repo-sync` | [`tools.md`](tools.md) → `workspace_preflight.py` |
| 想理解整体分层 | [`architecture.md`](architecture.md) | 本页「三条必跑命令」 |
| 想知道政策为什么是「中文正文 + 机器属性」双轨 | [`policy-language.md`](policy-language.md) | `AGENT_RULES.md` §14 |

## 三条共用验收命令

**前提**：完成 Python 初始化（`bootstrap.python.mode` 不再是 `unconfigured`）之后，凡改动都至少跑这三条；未初始化时先按 §0 选运行时，预检报 `python` mismatch 属预期。

```bash
python3 scripts/mark_rules.py check AGENT_RULES.md        # 条款标记完整（改过政策必跑）→ exit 0
python3 scripts/verify_rules.py --workspace .             # 模板层完整性 → clean: true
python3 scripts/skill_audit.py --strict                   # skills 契约与双登记 → exit 0
python3 scripts/workspace_preflight.py                    # 登记事实 vs 本机实际 → mismatch=0
python3 scripts/clause_lint.py                            # 条款属性与用词一致 → 0 errors
```

分场景再叠加：

| 场景 | 追加命令 |
|---|---|
| 改过 `docs/*.md` | `python3 scripts/docs_index.py build && python3 scripts/docs_hit_report.py --min 9` |
| 改过 hook / 插件 | `python3 -B .hermes/plugins/workspace-gate/selfcheck.py` |
| 跑过同步/迁移 | 单元测试：`python3 -B -m unittest discover -s tests -p 'test_*.py'`；hook 测试逐个跑：`for f in hooks-tests/test_*.py; do python3 -B "$f" || break; done` |

## 本目录文件

| 文件 | 讲什么 | 不讲什么 |
|---|---|---|
| [`skills.md`](skills.md) | 6 个 workspace skill 的触发、边界、验收与契约要求 | 不定义义务 |
| [`tools.md`](tools.md) | 10 个脚本的用途、命令、退出码 | 不代替脚本 `--help` |
| [`architecture.md`](architecture.md) | 分层模型、数据流、失败姿态、跨设备模型 | 不重复政策条文 |
| [`hooks-guide.md`](hooks-guide.md) | 三个 harness 的接线方法（用户视角） | 不列事件清单 |
| [`hooks-catalog.md`](hooks-catalog.md) | 事件/拒绝码/审计/新 harness 照搬清单 | 不写义务句 |
| [`docs-retrieval.md`](docs-retrieval.md) | docs 检索工具与实测结论 | 不作为读证据 |
| [`harness-onboarding.md`](harness-onboarding.md) | 接入新 harness 的分层判据、测试矩阵、平台坑与实测记录 | 不写接线步骤细节（见 hooks-catalog §3） |

## 一句话分层

```
AGENT_RULES.md   政策（唯一权威，锁定只读）
      │  约束
      ▼
hooks / plugin   机械守门（读前写后，advisory）
      │  兜底
      ▼
skills           流程（8 维契约，Agent 执行）
      │  调用
      ▼
scripts          工具（确定性、可复跑、有退出码）
```

细节见 [`architecture.md`](architecture.md)。
