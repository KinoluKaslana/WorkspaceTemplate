---
name: docs-retrieval-maintenance
description: "Use when docs/ changed, the retrieval index is stale, an alias target was renamed, or the hit report drops. 维护 docs 检索索引与别名表：索引可全自动，别名仅在有真实查询缺口日志时可自主新增（用户不手改）。"
version: 1.1.0
---

# docs 检索维护 — 索引与别名表的 Agent 维护规程

维护三件产物：索引 `docs/.index.json`、别名表 `docs/.aliases.json`、缺口日志 `docs/.miss-log.jsonl`。
**目标**：可复现的正确与精确——任何改动都能由本文命令重新跑出同一结果。

**不负责**：写政策（`AGENT_RULES.md`）、写正文（`docs/*.md`）、回答业务问题。
**不代替读取**：检索返回**定位**，拿依据仍须 `read_file` 读整节；gate 的 `coverage` 只认读工具。

## 0. 自主级别（本 skill 的核心裁决）

| 产物 | 自主级别 | 依据 |
|---|---|---|
| 索引 `docs/.index.json` | **全自动**（Agent 自主 rebuild） | 纯派生物，`build` 可重算，错误半径 = 一次重建 |
| 失效指针（目标文件/标题不存在） | **全自动停用** | 停用不丢信息；保留只会返回错定位 |
| 指纹对齐（`sha256` 重签） | **全自动**（改 docs 后） | 归一化动作，语义未变 |
| 别名**新增** | **有条件自主**：必须有真实查询缺口日志 | 见 §3；判据集 MISS **不算**理由 |
| 别名**重指**（标题改名） | **需人工确认** | 新节正文是否仍答该问法，机器判不了 |
| 别名**删除**（无命中记录） | **需人工确认** | 未观察 ≠ 无人使用 |
| 调打分系数 | **有条件自主**：一次一个 + 四条验收 | 影响全局排序，必须留前后对比 |
| 改阈值 / boost 上限 / 上限 8 条 | **需人工** | 改了全链路基线 |
| 别名里写答案、政策句、命令 | **禁止** | 别名只是指针，不是政策载体（R24/R98/R100 不经此表） |

**一句话**：表和索引由 Agent 维护，用户不手改；但「新增一条别名」必须拿得出真实缺口，否则只能提案不能落盘。

## 1. 触发条件（事件 → 动作 → 机械判据）

| 事件 | 动作 | 判据 |
|---|---|---|
| 改了 `docs/*.md` | `build` → `check` → `hit_report` | `check` 返回 `ok:true`；命中数不低于改动前 |
| `check` 报 `unindexed` / `stale` | `build` | 同上 |
| `query` 返回 `stale` | `build`（**不得**临时绕过校验） | 同上 |
| 标题被重命名 | 停用旧指针 → 提案重指 | 新 `path`+`heading` 存在且正文含原 expect |
| `hit_report` < 阈值 9 | 走 §2 诊断（先调参） | 恢复到 ≥9；噪声查询仍空 |
| `docs_alias_audit.py` 报 `DEAD`/`DRIFT` | 停用 / 重签指纹 | 审计 exit 0 |
| 用户报「搜不到」 | 追加一条 `docs/.miss-log.jsonl` | 日志行含 §3 字段，且复算为 MISS |
| 周期审计（每次 docs 发布 / 每月） | `docs_alias_audit.py --strict` | exit 0；`probe_only_debt` 为空 |

## 2. 步骤 0–2：基线、重建、MISS 诊断

```bash
python3 scripts/docs_index.py check
python3 scripts/docs_hit_report.py          # 逐题 HIT/MISS
python3 scripts/docs_alias_audit.py         # 死指针 / 漂移 / 债务 / 冗余
python3 -B tests/test_docs_index.py         # 11 例
```

MISS 处理顺序（越级即违规）：

1. `query "<题面>" --json --top 5` 看分数拆解，**先判断**是切词问题还是排序问题；
2. 排序问题 → 调系数（标题 IDF 权重 3.0 / 覆盖率平方 / 二元权重 1.5 / 阈值 0.6），**一次只动一个**，每次跑 `hit_report` + 噪声对拍；
3. 切词问题且术语确实不在目标节 → 才进入 §3（别名）。

噪声对拍固定两条，任何改动后都要跑：

```bash
python3 scripts/docs_index.py query "今天天气"    # 期望 results: []
python3 scripts/docs_index.py query "hello world" # 期望 results: []
```

## 3. 别名准入：只认真实缺口，不认判据集

**唯一合法来源**：`docs/.miss-log.jsonl` 里一条 `result:"miss"` 的记录，且该记录来自**用户真实提问**（不是 `tests/docs_probe.json` 的题面）。

日志行格式（一行一条，只追加）：

```json
{"id":"m-<ts>-<n>","ts":"2026-11-02 10:12:00","source":"user-query","query":"<用户原话>",
 "result":"miss","top5":[{"path":"docs/x.md","lines":[10,20],"score":1.9}],
 "tried":[{"coef":"heading_weight=2.5","hit":8},{"coef":"threshold=0.4","hit":8}]}
```

新增一条别名必须同时满足：

- [ ] 关联一条 `source:"user-query"` 且 `result:"miss"` 的日志（写进 `miss_id`，并给别名标 `source:"user-query"`）；
- [ ] `tried` 至少两项系数尝试均未解决（证明公式确实排不出，而不是没调）；
- [ ] `phrases` ≤3 条，取自用户原话片段，不含标点/通配；
- [ ] `path`+`heading` 指向存在的节，且该节正文确实包含答案依据；
- [ ] `sha256` 等于目标文件当前指纹；
- [ ] `boost` 默认 20，上限 30（超出需人工）；
- [ ] 加完 `hit_report` **不下降**、噪声查询仍空、`docs_alias_audit.py --strict` exit 0。

**禁止**：用 `tests/docs_probe.json` 的题面做 `phrases`；用判据集 MISS 作为新增理由；把别名当答案库。

删除条件：目标不存在；`audit --strict` 判冗余（留一验证后命中仍达标）；或超过 90 天无任何命中。

## 4. 防自欺（Goodhart）机制

1. **判据集只作回归**：`docs_probe.json` 的作用是「改动后没变坏」，不是「缺什么补什么」。
2. **来源可核验**：`docs_alias_audit.py` 校验每条别名的 `source`/`miss_id` 是否真的存在于日志且为 MISS；缺失即 `SOURCE` 违规。
3. **留一验证**：审计逐条禁用别名后重跑判据集，命中仍达标 → 标 `REDUND`，要求删除。冗余条没有存在价值。
4. **债务计数**：`source:"probe-only"` 的条目一律计入 `probe_only_debt`，`--strict` 下判失败——不允许"为了让 10/10 而留一条"。

## 5. 回滚（不依赖 git）

改表前先快照，文件名含序号：

```bash
cp docs/.aliases.json "docs/.aliases.bak.$(date +%Y%m%d%H%M%S).json"
```

写表必须原子（`tmp` + `os.replace`）。任一条验收红 → 回滚：

```bash
python3 scripts/docs_alias_audit.py --restore     # 取最新快照覆盖回去
python3 scripts/docs_index.py build && python3 scripts/docs_hit_report.py
```

`docs/` 已变更导致旧指针失效时保持 fail-closed（`stale`），交人工处理，不要带病回滚。

## 6. 验收（缺一不可，全绿才算完成）

```bash
python3 scripts/docs_index.py check                    # ok:true
python3 scripts/docs_hit_report.py --min 9             # PASS，且不低于改动前
python3 scripts/docs_alias_audit.py --strict           # exit 0，probe_only_debt 空
python3 -B tests/test_docs_index.py                    # 11 例
python3 -B scripts/verify_rules.py --workspace .       # clean=true
```

## 7. 失败模式与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `query` 返回 `stale` | docs 改了没重建 | `build`；不得禁用校验 |
| 别名加了没生效 | `sha256`/`heading` 与索引不一致 | 对齐指纹、核对标题字面 |
| 命中率整体下滑 | 系数或节被改 | 回看改动，逐项恢复；跑噪声对拍 |
| 噪声查询返回结果 | 阈值/权重问题 | 调回 0.6 / 降标题权重 |
| 审计报 `REDUND` | 别名对判据集无贡献 | 删除；说明当初为何加 |
| 审计报 `SOURCE` | 别名没有真实缺口来源 | 补日志或删除，**不得**补造来源 |

## 8. 交付记录（写进任务汇报，不写政策）

触发原因；`build` 前后节数；`hit_report` 改动前后逐题对比；别名增删清单 + 每条 `miss_id`；
五条验收命令的实际输出摘要。

## 变更记录

- 1.1.0（2026-11-02）：确立**自主分级**（索引全自动 / 别名有条件自主 / 阈值与 boost 需人工）；
  新增 `scripts/docs_alias_audit.py`（死指针、指纹漂移、来源合规、留一验证、`--restore`）；
  新增 `docs/.miss-log.jsonl` 作为别名唯一合法来源；清空历史 `probe-only` 别名（判据集 9/10，缺口如实保留）。
- 1.0.0（2026-11-02）：首版。源自 6/10→10/10 的修复过程；固定「先调参、后别名、上限 8、四条验收」。
