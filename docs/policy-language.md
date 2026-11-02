# 政策双轨：中文义务句 ＋ 机器可读条款属性

> 实现说明，不构成政策；义务与判定口径见 [`AGENT_RULES.md`](../AGENT_RULES.md) §14。

## 1. 为什么是双轨，而不是换语言

三席评审（GLM / SOL / DS）在「要不要把政策改成英文或形式化符号」上结论一致：**不换**。理由不是中文更好，而是：

- 英文省 token（同义段落实测 49 vs 73 tokens，约 −33%），但**裁决权转移**——用户是中文最终裁决人，读英文正文只能依赖 agent 转述；112 条条款被哈希锁定，全文翻译等于全量重签 + `custom` 覆写逐条重核。
- 纯符号只对**可判定子集**有效（覆盖区间、TTL、拒绝码、写工具清单）；「确认必要性」「可写集合裁量」形式化后要么不可判定，要么把错误裁量固化成规则。模型能解释符号，但解释不是验证。

因此分层：

| 层 | 载体 | 谁读 | 谁验 |
|---|---|---|---|
| 语义（义务） | `AGENT_RULES.md` 正文 | 用户、主 Agent | 人（裁决） |
| 结构（完整性） | `.workspace/rule-clauses.json`（生成） | 脚本 | `mark_rules.py` / `verify_rules.py` |
| 属性（强度/触发/证据） | `.workspace/clauses.meta.json`（手写，哈希锁定） | 脚本、评审者 | `scripts/clause_lint.py` |

属性**不改变**义务；冲突以正文为准（R111）。

## 2. 受控词表

| 词 | 档位 | 语义 |
|---|---|---|
| 必须 | `MUST` | 无条件义务 |
| 不得 / 禁止 | `MUST_NOT` | 无条件禁令 |
| 应当 | `SHOULD` | 默认遵守，偏离须说明 |
| 可以 / 可 | `MAY` | 授权，不是要求 |

禁用「尽量」「必要时」「建议」「通常」表达义务——条件必须写成**可判定的触发条件**（什么状态、什么动作、什么证据）。当前正文这四词为 0（R110 自身列举除外）。

## 3. 属性文件

`.workspace/clauses.meta.json`，键为 R 号：

```json
"R98": {"severity": "MUST", "trigger": "gate-hermes",
        "check": "python3 .hermes/plugins/workspace-gate/selfcheck.py", "reviewed": true}
```

- `severity`：`MUST` / `MUST_NOT` / `SHOULD` / `MAY` / `INFO`（`INFO` = 描述性，无义务）；
- `trigger`：受控条件码（`python-runtime`、`hook`、`delivery-notes`、`policy-language`…）；
- `check`：可执行命令，或 `manual:<非空理由>`；
- `reviewed`：是否经人工确认强度；**新增强条款必须为 true**，否则 lint 报 E9。

## 4. `clause_lint.py` 判什么

```bash
python3 scripts/clause_lint.py            # findings>0 即 exit 1（含警告）
python3 scripts/clause_lint.py --lenient  # 只把 errors 当失败
python3 scripts/clause_lint.py --report   # 覆盖率：severity / check 类型 / manual 清单
python3 scripts/clause_lint.py --json
```

**错误**：meta 键与正文条款不对应（E1/E2）、枚举或字段非法（E3/E4）、强条款缺 `check`（E5）、`manual:` 理由为空（E6）、正文没有与声明档位相符的情态词（E7）、`check` 不是可执行前缀（E7b）、**可机械判定的触发条件却标 manual**（E8）、强条款未复核（E9）、**条款 ID 重复**（E10）、**文末有未标记的条款正文**（E11，会逃过注册表）。

E8 是防自欺的关键：`hook` / `template-sync` / `skills-registry` / `docs` / `rule-layering` 这些触发条件的事实现成脚本就能查，写 `manual:` 会被判错。

**警告**（同属 findings，默认也失败；`--lenient` 放行）：同句混用必须/不得（W1）、模糊量词（W2）、同句出现「可以」授权（W3）、档位与用词不匹配（W4）、`INFO` 却含强情态词（W5）。

## 5. 当前基线（政策 3.4.0）

```
112 条 / MUST 27 / MUST_NOT 13 / SHOULD 4 / MAY 16 / INFO 52
check：命令 24 / manual 16 / 空 72（SHOULD·MAY·INFO 不强制）
0 errors / 0 warnings（findings>0 即 exit 1）
```

警告按**句子级**判定（同句同时出现必须/不得、同句出现「可以」授权、模糊量词、档位与用词不符）。当前为 0——但这不是「政策无歧义」的证明，只说明用词与声明一致；新增警告必须解释，批量上升说明受控词表被破坏。

## 6. 新增或改写条款的流程

1. 正文用受控词表写义务句，标注 `<!-- id:Rn -->`（新号取全局最大 +1，不复用旧号）；
2. 在 `clauses.meta.json` 加同号条目：`severity` 与用词一致；`check` 优先给命令；
3. `python3 scripts/mark_rules.py registry AGENT_RULES.md` 重建条款注册表；
4. `python3 scripts/clause_lint.py` 必须 0 errors；
5. `python3 scripts/verify_rules.py --workspace .` 必须 clean；
6. 改的是**义务**（不是措辞）→ 按 `CHANGELOG.md` 的语义化规则升版。

## 7. 它不保证什么

- **不证明语义正确**：lint 只校验「用词与声明一致」，不能判断这条义务是否该这样定；
- **不替代 hook**：机械守门在 `.hermes/plugins/workspace-gate/`，clause_lint 是静态属性门；
- **不覆盖 `custom` 层**：`AGENT_RULES.custom.md` 的条款不在此文件管辖，其 `overrides` 由 `verify_rules` 报告；
- **警告不等于有问题**：它们是需要人看一眼的信号，不是失败。

## 8. 已从 manual 转成命令的条款（判据 C1–C3）

转档标准：违反义务会留下**可静态检查的产物/配置/痕迹**，且能给出**会变红的负例**。

| 条款 | 命令 | 判据 | 能证明 | 不能证明 |
|---|---|---|---|---|
| R2 版本必须更新并记 CHANGELOG | `version_check.py` | C2 静态配置 | 政策版本、CHANGELOG 顶层、bootstrap 记录三者一致 | 该升 PATCH 还是 MINOR/MAJOR |
| R61 凭据不得进普通文件 | `secret_scan.py` | C3 痕迹 | 仓库内没有匹配高置信度模式的明文凭据 | 凭据是否被发到外部（网络行为不可见） |
| R80 索引不得手改 | `notes_index_check.py` | C1 产物 | 两个索引文件与「重新生成」逐字节一致（时间戳除外） | 生成脚本本身是否有 bug |
| R11 MCP 模式不得建本地 venv | `workspace_preflight.py` | C1 产物 | `python.mode=mcp` 时工作区没有 `.venv-*` 目录 | venv 是否被建在别处 |

仍然保持 `manual` 的 16 条（R3/R6/R20/R57/R58/R59/R60/R62/R63/R66/R67/R68/R71/R75/R84/R104）都是**意图或过程**判断（用户是否同意、是否属当次范围、合并前是否检查），不满足 C1–C3。**把"用户确认"做成自我声明的日志或环境变量，等于把 manual 换成假绿**——这类转换一律拒绝。

## 9. 什么时候该翻盘

如果试点后仍出现「同一句话两个 agent 判出不同结果」，且分歧集中在**强度**而非语境，才值得把个别条款改写成受控英文（条款级替换，不是整体翻译）。这条判据写在这里，避免以后靠印象争论。
