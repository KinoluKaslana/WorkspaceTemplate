# hooks-tests — 工作区写前守门（Codex / Claude Code）测试集

> 模板层资产（`dev` 分支，政策 2.5.2）。`hooks-tests/` 本身**不登记** `TEMPLATE_FILES`；被测的五个 hook 脚本与两份客户端配置已登记（共 27 项）。

## 被测对象

| 文件 | 作用 |
|---|---|
| `.workspace/hooks/hook.py` | 客户端 hook 适配器（Codex / Claude Code 共用；事件 → 判定 → JSON 输出；Bash 分派含 W1 接线、W6 `$'…'` 解码、C9 文案、W2 锚点、W3 差集、W4 stop advisory） |
| `.workspace/hooks/gate_core.py` | 唯一判定核心（`SCHEMA=5`、读证据记账、修订绑定、写前 deny、`consume_authz`、`anchor_check`、`worktree_audit`、生命周期对账） |
| `.workspace/hooks/wtarget.py` | Bash 写目标窄识别器（`kind/targets/class`），**已接入** `hook.py` 的 `pre_bash` 判定链 |
| `.codex/hooks.json` | Codex 侧事件配置（SessionStart / UserPromptSubmit / PreToolUse `^Bash$`+`apply_patch` / PostToolUse / Stop / SessionEnd；8 个 definition） |
| `.claude/settings.json` | Claude Code 侧配置（**只订阅 `Write\|Edit` 与 `Read`，不订阅 Bash**——按用户 2026-10-22 约束） |

## 运行

```bash
for t in test_wtarget test_bash_dispatch test_apply_patch_ansi test_w2_anchor test_authz test_template_anchor; do
  python3 -B hooks-tests/$t.py
done
python3 -B hooks-tests/replay_wtarget.py --unit   # 同一识别器矩阵（回放脚本的单测模式）
```

当前规模：`test_wtarget` 4 类（内含 86 条断言矩阵）、`test_bash_dispatch` 15、`test_apply_patch_ansi` 9、`test_w2_anchor` 3、`test_authz` 5、`test_template_anchor` 4——**共 40 例**，全绿。

要求 Python ≥ 3.10（纯 stdlib，不装依赖）。测试全部是**进程级**：真实启动 `.workspace/hooks/hook.py`、stdin 喂 payload；**不是**原生 Codex / Claude Code 会话 E2E（原生行为证据见各轮 `*-TEST-REPORT.md` 与 `ROUNDS-FACTS.md`）。

## 覆盖与边界（诚实口径）

- 覆盖：CC `Write|Edit` 写前守门；Codex `apply_patch`（heredoc / 双引号 / `$'…'`）；Codex Bash 静态可判写（`>`/`>>`/`tee`/`sed -i`/`perl -i`/`truncate`/`cp`/`mv`/`install`/`dd of=`）；解释器/包装类未读 deny（B′）；不可判形态审计放行（`unresolved-command`）；W2 指纹锚（含 `suspect`）；豁免令牌（单次、精确集合）；W4 门禁三键判定。
- 审计事件五型：`unresolved-command` / `authorized-gatefile-write` / `unsanctioned-gatefile-change` / `worktree-change` / `completion-gate`。
- `unresolved-command` **只记** `tool_use_id`、`digest=sha256(command)[:16]`、`class`；不落命令正文，也不做 native tool-use 对账——因此审计本身不可直读归因，只能拿候选命令重算 digest 比对。
- **CC 的 Bash 写不在覆盖内**：不订阅该事件，不拦截、无逐条归因；只能靠会话级差集（Stop/SessionEnd 的 `worktree-change`）与治理文件指纹锚事后发现。
- 已知不可判但**不拦**（`unresolved` 且非 `interpreter_wrapper`）：变量/通配目标、子 shell 首词元、`git apply`、`cd`/`pushd` 串联等；`python*`/`sh`/`bash`/`perl`/`make`/`env`/`sudo` 等包装属 B′，未读即 deny。
