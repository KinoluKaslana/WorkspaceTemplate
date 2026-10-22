# hooks-tests — 工作区写前守门（Codex / Claude Code）测试集

> 属于 **alpha 分支**的试验资产，尚未进入模板发布口径（未登记 `TEMPLATE_FILES`、未重建 `rule-clauses.json`）。

## 被测对象

| 文件 | 作用 |
|---|---|
| `.workspace/hooks/hook.py` | 客户端 hook 适配器（Codex / Claude Code 共用；事件 → 判定 → JSON 输出） |
| `.workspace/hooks/gate_core.py` | 唯一判定核心（读证据记账、修订绑定、写前 deny、生命周期对账） |
| `.workspace/hooks/wtarget.py` | Bash 写目标窄识别器（`kind/targets/gate_targets`），**尚未接入 hook.py** |
| `.codex/hooks.json` | Codex 侧事件配置（SessionStart / UserPromptSubmit / PreToolUse `^Bash$`+`apply_patch` / PostToolUse / Stop / SessionEnd） |
| `.claude/settings.json` | Claude Code 侧配置（**只订阅 `Write\|Edit`，不订阅 Bash**——按用户 2026-10-22 约束） |

## 运行

```bash
python3 -B hooks-tests/test_wtarget.py          # 识别器单元矩阵（86 例）
python3 -B hooks-tests/test_bash_dispatch.py    # Bash 分派 7 例（真实启动 hook，进程级）
python3 -B hooks-tests/replay_wtarget.py --unit # 同一矩阵（回放脚本的单测模式）
```

要求 Python ≥ 3.10（纯 stdlib，不装依赖）。测试全部是**进程级**：真实启动 `.workspace/hooks/hook.py`、stdin 喂 payload；**不是**原生 Codex / Claude Code 会话 E2E。

## 覆盖与边界（诚实口径）

- 覆盖：CC `Write|Edit` 的写前守门；Codex `apply_patch`（heredoc / argv / `$'…'` 现状）；Bash 分派两端可用；`wtarget` 对 11 类静态写形态的识别 + 已知漏判的回归。
- **未接入**：`wtarget` 还没有进 `hook.py` 判定链（W1）；`gate_core` 的审计仍只有 6 字段，没有 `unresolved-command` 槽位（W-schema）。
- **CC 的 Bash 写不在覆盖内**：不订阅该事件，不拦截、无逐条归因；只能靠会话级差集（SessionEnd）与指纹锚事后发现（W3/W2，未实现）。
- 已知不可判（落 `unresolved`，不拦）：`python -c`、`sh/bash/zsh -c`、`perl -e`、`env/sudo/xargs` 等包装、`cd`/子 shell、变量/通配目标、`git apply`。
