# ALPHA — 客户端写前守门落地说明（alpha 分支）

> 分支用途：把「Codex / Claude Code 工作区写前守门」的当前成果落到模板仓，**供真实模型试跑与两轮迭代**。
> 状态：**alpha，未发布**。不登记 `TEMPLATE_FILES`，不进 `rule-clauses.json`，`verify_rules --vs-template` 对它不作保证。

## 1. 本分支落地的内容

| 路径 | 来源 | 说明 |
|---|---|---|
| `.workspace/hooks/hook.py` | W0 版本（sha `e19b5dc2…`） | Bash 分派同时接受 `cc`/`codex`；cc 的 Bash 读不记账；其余判定未改 |
| `.workspace/hooks/gate_core.py` | 未改（sha `30027b3e…`） | 读证据/修订绑定/deny/生命周期对账 |
| `.workspace/hooks/wtarget.py` | W1b（B′ 识别器，sha `e3a0a0fd…`） | 窄识别器；**尚未接线**，只被测试调用 |
| `.codex/hooks.json` | 既有 | Codex 事件：`^Bash$`、`apply_patch`、Stop、SessionEnd… |
| `.claude/settings.json` | 既有 | **只订阅 `Write\|Edit`**（用户 2026-10-22 约束：不做 CC Bash 订阅） |
| `hooks-tests/` | W0 + W1b 测试 | `test_wtarget.py`（86 例）、`test_bash_dispatch.py`（7 例）、`replay_wtarget.py` |

## 2. 现在能证明什么 / 不能证明什么

**能证明（进程级测试）**
- CC 的 `Write`/`Edit` 与 Codex 的 `apply_patch` 在未完整读 `AGENT_RULES.md` 当前修订时被 deny，读完放行，外部改动后再次 deny（`gate_core` 既有语义）。
- Bash 分派两端可用：`GATE_CLIENT=cc` 的 `ls` 不再触发「内部错误 deny」；`apply_patch` 类 Bash patch 仍按写判定。
- `wtarget` 对 11 类静态写形态的识别、对已知漏判（heredoc-first、`cd`/子 shell、`env` 包装、heredoc 正文假 `apply_patch`、`<` 输入重定向、`perl -e`）的回归全绿。

**不能证明**
- `wtarget` 还没接线：**当前守门实际只覆盖 `apply_patch` 与原生 `Write`/`Edit`**；Bash 的 `>` / `sed -i` / `tee` 等仍不拦（与历史一致）。
- 无原生 Codex / Claude Code 真机会话 E2E；全部是进程级（真实启动 hook + 合成 payload）。
- CC 的 Bash 写不拦截、无逐条归因；会话级差集审计（W3）与指纹锚（W2）尚未实现。
- 无审计槽位：`gate_core._append_audit` 仍写 6 个固定字段，没有 `unresolved-command` / `unsanctioned-gatefile-change`。

## 3. 试跑方式（alpha 测试约定）

```bash
# 0) 依赖：Python ≥ 3.10（系统 python3 即可）
python3 -B hooks-tests/test_wtarget.py
python3 -B hooks-tests/test_bash_dispatch.py

# 1) Codex 侧（可选，真机）：在临时夹具里跑，不要直接在本仓 .hook-state 上做破坏性试验
#    需要 CODEX_HOME 指向隔离目录、并把夹具路径加入 trusted 项目
#    见交付方提供的 run/CODEX_HOME 模板（不在本仓分发）

# 2) Claude Code 侧：只验证 Write/Edit 守门（Bash 不订阅，属已知边界）
```

## 4. 已知风险（alpha 期间）

1. **`apply_patch $'…'`** 仍会 `ValueError → 「内部错误」deny`（W6 未做）。
2. **越界读**（`sed -n 171,180p`，文件 176 行）会拒账 → coverage 不满 → 永久 deny，且文案不指缺行（C9）。
3. **非 patch 写不拦**：`echo >`、`sed -i`、`tee`、`python -c` 等不受管（W1 接线后才会有一半被纳管）。
4. **Codex heredoc 无 PostToolUse**：写后撤销靠下一次 Pre/Stop/SessionEnd 的延迟对账，不是即时。
5. 本分支未注册哈希：任何模板同步都可能覆盖这些文件，升级前需 diff。

## 5. 迭代计划（用户指定）

1. **R1 试跑**（deepseek-flash）：在本分支上跑真实用法，记录「被拦 / 未被拦 / 误拒」样本。
2. **研判**（DeepSeek-V4-Pro）：对 R1 样本与接线必要性做研判。
3. **验收**（GLM）：对研判与代码做验收，列出阻塞项。
4. **问题讨论**（DeepSeek-V4-Pro × LUNA）→ **GLM 拍板**。
5. **落地第二轮**：按拍板修改落回 alpha，重复一轮试跑与验收。
