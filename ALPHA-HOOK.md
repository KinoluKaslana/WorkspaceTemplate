# ALPHA — 客户端写前守门落地说明（alpha 分支，round 2）

> 分支用途：把「Codex / Claude Code 工作区写前守门」落到模板仓，供真实模型试跑与两轮迭代。
> 状态：**alpha，未发布**。已登记 `TEMPLATE_FILES`（hooks 三文件 + 两配置），`verify_rules --workspace .` = clean；`--vs-template`（对 clean 克隆）已验证 clean。

## 1. 已落地

| 路径 | 说明 |
|---|---|
| `.workspace/hooks/hook.py` | 适配器：Bash 分派（cc/codex）、W1 接线（`wtarget`）、W6 `$'…'` 规范化、C9 deny 文案、stop/session_end 差集审计 |
| `.workspace/hooks/gate_core.py` | 判定核心：`SCHEMA=5`、审计 `events` 槽位、`evaluate_read_required`（B′）、`worktree_audit`（W3）、C9 文案 |
| `.workspace/hooks/wtarget.py` | 写目标窄识别器：`class=interpreter_wrapper|opaque|static`；`WHITELIST` 由命令形态表投影 |
| `.codex/hooks.json` | Codex：`^Bash$` / `apply_patch` / Stop / SessionEnd |
| `.claude/settings.json` | CC：**只订阅 `Write\|Edit`**（不做 Bash 订阅）+ **Stop**（lifecycle，已原生验证触发） |
| `hooks-tests/` | `test_wtarget`（86 例 + 白名单恒等/正负例）、`test_bash_dispatch`（12 例）、`test_apply_patch_ansi`（9 例）、`replay_wtarget.py`（`--corpus` 可选） |

## 2. 已实测（真机 + 进程级）

**真机 Codex 0.163.0 / deepseek-v4-flash（两轮，共 5 次 run）**
- 未读 `apply_patch` → **deny**，文案含「已覆盖 无；缺失 1..177；共 177 行」。
- 未读 `printf … >> AGENT_RULES.md` → **deny**（round 1 时此路径放行——W1 接线后关闭）。
- 全读（`sed -n` 三段）→ `apply_patch` allow；随后 `printf >>` allow。
- 追加写后修订变化 → 下一次 `apply_patch` **deny**，模型准确复述「缺失 1..178；共 178 行」。
- `git status --porcelain` 未读 → allow，并落 `unresolved-command`（class=opaque）审计。
- `stop` / `session_end` → `worktree-change` 审计记录（`audit-codex.jsonl` 的 `events`）。

**真机 Claude Code 2.1.87-dev**
- `claude -p "Say exactly: OK"` 在含本仓 `.claude/settings.json` 的夹具中运行 → **Stop hook 触发**（观察器记录 `STOP_FIRED`）；`PreToolUse` 仍只 `Write|Edit`。

**进程级**
- 三套测试全绿；`replay_wtarget.py --corpus <历史语料>`：误拒 0、漏判 0、单元矩阵 86/86。
- `grep -rn "/home/kinolu" hooks-tests/` 为空；任意 cwd 可跑。

## 3. 不在覆盖内（必须与能力一起声明）

1. **CC 经 Bash 的一切写入不拦截、无逐条归因**（不订阅该事件）；只能由 Stop/SessionEnd 的差集审计事后发现「哪些文件变了」，不能归因到具体命令。
2. `python -c` / `sh -c` / `perl -e` / `env|sudo|xargs` 包装等：未读时按 B′ **拒绝**；已读后属「未判形态」，按审计放行（不逐条解析内层）。
3. `W2`（治理文件指纹锚）与 `W4`（`verify_rules --vs-template` 完成声明门禁）**未实现**；`authorized-gatefile-write` / `unsanctioned-gatefile-change` 两个审计槽位已定义、未发射。
4. Codex heredoc patch 无 PostToolUse；写后撤销靠下一次 Pre / Stop / SessionEnd 对账，非即时。
5. 同 uid 下不存在安全边界；自授权闭环（改治理文件 → 下次 Pre 拒 → 重读自改版 → 放行）仍成立，只能「必被发现」，不能「被阻止」。
6. 未测：`apply_patch $'…'` 的真机形态（只有进程级 9 例）；Windows 原生；多客户端并发。

## 4. 两轮迭代记录

| 轮 | 提交 | 试跑 | 研判/验收 |
|---|---|---|---|
| R1 | `1c83ac8` | 拦截 1 / 放行 3 / 盲区 1（`printf >>`）/ 误拒 0 | 研判：可进 R2；验收（GLM）：有条件接受 |
| R2 | `2a63405` | 未读 `printf >>` deny、读后 allow、追加后 deny（C9 文案）、审计/差集成立 | 讨论（DS×LUNA）→ GLM 拍板 → 终验：有条件接受（4 条件已补 3 项，余 1 项属 W2） |
