# ALPHA — 客户端写前守门落地说明（alpha 分支，W2/W4 完成）

> 分支用途：把「Codex / Claude Code 工作区写前守门」落到模板仓，供真实模型试跑与迭代。
> 状态：**alpha，未发布**。已登记 `TEMPLATE_FILES`（hooks 五文件 + 两配置）；`verify_rules --workspace .` = clean；`--vs-template`（clean 克隆）= clean / `template_clean: true`。

## 1. 已落地

| 路径 | 说明 |
|---|---|
| `.workspace/hooks/hook.py` | 适配器：Bash 分派、W1 接线（`wtarget`）、W6 `$'…'`、C9 文案、W2 锚点接线、W3 差集、W4 stop advisory |
| `.workspace/hooks/gate_core.py` | 判定核心：`SCHEMA=5`、审计 `events`、`evaluate_read_required`（B′）、`consume_authz`（豁免）、`anchor_check`（W2）、`worktree_audit`（W3） |
| `.workspace/hooks/wtarget.py` | 窄识别器：`class=interpreter_wrapper|opaque|static`，白名单由命令形态表投影 |
| `.workspace/hooks/authz.py` | 豁免令牌 CLI：`mint/list/revoke`（单次、精确集合、无通配） |
| `.workspace/hooks/template_anchor.py` | W4 门禁：`verify_rules --vs-template` 三键判定（`exit==0 ∧ clean ∧ template_clean`） |
| `.codex/hooks.json` | Codex：`^Bash$` / `apply_patch` / Stop / SessionEnd |
| `.claude/settings.json` | CC：只订阅 `Write\|Edit`（**不做 Bash 订阅**）+ Stop（lifecycle，已原生验证） |
| `hooks-tests/` | 6 个测试文件（识别器 86 例、分派 12、ANSI-C 9、W2 锚 3、authz 5、template_anchor 4） |

## 2. 已实测

**真机 Codex 0.163.0 / deepseek-v4-flash**
- 未读 `apply_patch` → deny；未读 `printf >> AGENT_RULES.md` → **deny**（W1 后；W1 前放行）。
- 读全（3 段 `sed`）→ 两类写均 allow；追加写后再 patch → deny（`缺失 1..178；共 178 行`）。
- `git status` 未读 → allow + `unresolved-command` 审计。
- Stop → `worktree-change` + `completion-gate`（`ok=true`，clean）审计。

**真机 Claude Code 2.1.87-dev**
- `claude -p "Say exactly: OK"`：Stop hook 触发；`completion-gate ok=true` 落 `audit-cc.jsonl`；`PreToolUse` 仍只 `Write|Edit`。

**主理人独立探针（进程级）**
- W2：改字节 → `unsanctioned-gatefile-change`（prev/cur）+ 读许可撤销 + 后续写 deny；仅 `touch` → `suspect:true` 且**不撤销、不 deny**。
- 豁免令牌：mint → 未读写 gate **allow** + `authorized-gatefile-write`（nonce）；同令牌复用 → deny；目标不匹配 → deny；通配 mint → rc 2。
- W4：clean → 0；受比文件脏 → 1；**只脏在非受比文件（`clean:true` / `template_clean:false`）→ 1**；clone 缺失 → 1；`bootstrap.template.clone_path` 与 `WORKSPACE_TEMPLATE_CLONE` 两条解析路径均端到端可用（exit 0）。

## 3. 不在覆盖内（必须与能力一起声明）

1. **CC 经 Bash 的一切写入不拦截、无逐条归因**；只能由 Stop/SessionEnd 的差集审计事后发现「哪些文件变了」。
2. `python -c` / `sh -c` / `perl -e` / `env|sudo|xargs` 包装等：未读时按 B′ **拒绝**；已读后审计放行（不解析内层）。
3. **W2 的 `suspect` 只能靠 `mtime_ns` 兜底**：同一命令内「改完改回」且 `mtime_ns` 也被伪造（`touch -r`）时不可见——属主动绕过（`AGENT_RULES.md` §12.3 边界外）。
4. **W4 只做 advisory + 审计，不在 `stop` 上 deny**：完成声明的机械阻断靠流程（Stop 输出 `systemMessage`、`completion-gate` 落盘），不是硬门禁。
5. **clone 未 pin 版本**：`template_anchor` 记录 `template_head` 供审计，但不会拒绝「指向旧/可疑克隆」；同 uid 下无法更强。
6. Codex heredoc patch 无 PostToolUse；写后撤销靠下一次 Pre / Stop / SessionEnd 对账。
7. 同 uid 下不存在安全边界；自授权闭环仍成立（只能「必被发现」，不能「被阻止」）。
8. 未测：`apply_patch $'…'` 真机形态；Windows 原生；多客户端并发。

## 4. 迭代记录

| 轮 | 提交 | 关键结果 |
|---|---|---|
| R1 | `1c83ac8` | 拦截 1 / 放行 3 / 盲区 1（`printf >>`）/ 误拒 0；研判可进 R2，GLM 有条件接受 |
| R2 | `2a63405` | W-schema/W1(B′)/C9/W6/W3 + 登记；`printf >>` 未读 deny、读后 allow、追加后 deny；GLM 终验有条件接受，4 条件补 3 |
| W2/W4 | `ca36b4d` | 指纹锚 + 豁免令牌 + 完成声明门禁；三态 fail-closed；Codex/CC 真机 `completion-gate ok=true` |
