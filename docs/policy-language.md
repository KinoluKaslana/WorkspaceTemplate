# 政策双轨：中文义务句 ＋ 机器可读条款属性

> 实现说明，不构成政策；义务与判定口径见 [`AGENT_RULES.md`](../AGENT_RULES.md) §14。

## 1. 为什么是双轨，而不是换语言

三席评审（GLM / SOL / DS）在「要不要把政策改成英文或形式化符号」上结论一致：**不换**。理由不是中文更好，而是：

- 英文省 token（同义段落实测 49 vs 73 tokens，约 −33%），但**裁决权转移**——用户是中文最终裁决人，读英文正文只能依赖 agent 转述；全部条款被哈希锁定，全文翻译等于全量重签 + `custom` 覆写逐条重核。
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

## 5. 当前基线（以 `AGENT_RULES.md` 为准）

```
条款数与情态分布见 `AGENT_RULES.md` 与 `.workspace/clauses.meta.json`（不在此处写死快照）
check 分布以 `python3 scripts/clause_lint.py --report` 的实测输出为准（此处不写死计数）
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

## 7. 范围表：机械保证（G）与不保证（N）

| 机械保证（G） | 不保证（N） |
|---|---|
| G1 政策版本 token == `AGENT_RULES.md:4` | N1 语义是否正确 |
| G2 插件版本 == `plugin.yaml:2` | N2 未注册散文（G5/G6 已覆盖项除外） |
| G3 每个 doc-fact id 在 manifest 存在且 locator 命中一次 | N3 日期/sha/外链 |
| G4 计数事实 == 实测取数；生成块外的派生事实重复位（README* 的插件版本、`docs/architecture.md` 的 scripts 计数）只许写指针（`plugin.yaml` / `scripts/*.py`），写死值即红 | N4 徽章渲染 |
| G5 版本登记与标题/指针扫描（`scripts/policy_facts.py`）：生成块内容 == 权威源渲染；当前政策版本字面量只许在生成块内；历史版本（∈ CHANGELOG `## x.y.z` 账本，标题格式不符即红）只许在 `<!-- history:x.y.z -->` 行上，标记只豁免它声明的版本且不得登记当前版本；历史标记行上的裸版本（不带 `v` 前缀）若既非当前也不在账本 → 红；无结构标记的自然语言版本散文不判（N2）；R-a 标题、R-b 第 N 行指针、R-c §5 无版本快照照旧 | N5 本地 skip 的用例 |
| G6 条款范围由 `scripts/policy_facts.py` 渲染（渲染值 == 政策 min..max；max 是发放高水位，不要求无缺号连续）；条款 ID 语义（文件内/跨文件唯一、正文顺序严格递增、起点 R1）；未登记散文的条款范围连接符为闭式集合（`..` / `–` / `-` / `~` / `至` / `……`，全部可识别），起点为 1（或越界、倒序）视为政策范围、必须 == min..max 且形态为两端 R 号 + `..`（缺尾端 R 算形态违规），其余子集（如 `R2–R8`、`R120-R136`）属功能引用不判 | — |
| G7 未登记 skill 计数禁写（README* / docs/*.md；形态：中文数词（完整十/百/千组合，如 `十四`/`一百`/`三十五`）、阿拉伯数字或英文数词 + `skills?`，以及 `skills（N 个）`；英文数词为闭式集合：个位/十几/整十/hundred/thousand/dozen） | — |

G 列只保证下列固定文档集与识别形态，不代表**全仓库**或**所有散文**都已校验；N 列仍须人工评审，其余语义与措辞继续归 N2。

G5 的版本一致性由生成块对账承接：`scripts/policy_facts.py --check` 对 `README.md`、`README_EN.md`、`docs/architecture.md`、`docs/skills.md`、`docs/tools.md` 的登记块逐字比较；旧的散文版本快照不再另设一套散点正则。

- 未登记字面量扫描只读 `README.md`、`README_EN.md`、`CONTRIBUTING.md`、`SECURITY.md`、`UPGRADE.md` 与 `docs/*.md`，不读 `CHANGELOG.md` 或任意新增 Markdown；跳过代码围栏、生成块内的行，以及派生实例的两份 README。识别形状是三段 `x.y.z`：当前版本不得出现在生成块外；账本内历史版本只允许出现在声明了同值 `<!-- history:x.y.z -->` 的行上；带 `<!-- history:x.y.z -->` 的行上，除标记声明的版本外，一个既非当前、也不在账本里的**裸**版本（不带 `v` 前缀）同样报错——该行已声明是沿革引用，同行另一个未登记版本只可能是拼错或臆造。`v` 前缀与插件/工具/skill 版本不判。

**不覆盖自然语言散文断言**：`本模板现行治理基线为 9.9.9。`、`工具版本 9.9.9；"当前政策版本"只是字段名` 这类句子不由 `scripts/policy_facts.py` 判——没有结构标记的散文归 N2（人工/评审层）。旧实现的开放词表（`当前政策版本|政策版本|policy version`）已整段删除：词表内的字段名误报、词表外的说法照样漏，再用词表补洞只会扩大误报面。

- `CHANGELOG.md` 的账本标题只认行首二级标题 `##`、空白、`x.y.z`，且 `x.y.z` 后紧跟的字符不得是数字、ASCII 字母、`.` 或 `-`；所以日期/说明可以紧跟在全角括号等非保留分隔符后。下列后缀粘连样例会被拒绝：

  ```text
  ## 3.6.1beta
  ## 3.6.1.1
  ## 3.6.1-hotfix
  ```

  `###` 子标题与非版本文案不进入账本；账本存在时，首个条目还必须等于政策头版本。

G6 保留条款 ID 与范围两层语义：范围在同一个固定文档集的每一行（含代码围栏；派生实例 README 除外）中识别，不设登记片段或整行豁免；连接符是**闭式集合**（`..`、`–`（en dash）、`-`、`~`、`至`、`……`，以及 `—`、`到`），全部必须被识别，不得静默漏；判定分两档：起点为 1（或越界 `hi > max`、倒序 `lo > hi`）视为政策范围断言，值必须等于政策 `<!-- id:Rn -->` 的 min..max，形态必须是两端 R 号 + `..`（缺尾端 R、其它连接符都是形态违规）；其余子集（如 `R2–R8`、`R120-R136`）属功能引用，不判值、不报错。ID 语义从 `AGENT_RULES*.md` 及注册表声明含条款的文件重算：文件内/跨文件唯一、单个文件正文顺序严格递增、起点为 R1；缺失 ID 可以退休，max 是发放高水位，不要求编号连续。该扫描不覆盖 `CHANGELOG.md` 或任意新增 Markdown。

G7 只在同一固定文档集的全部行（含代码围栏；派生实例 README 除外）中检查已写死的 skill 计数形状，包括中文/阿拉伯/英文数词与 `skills` 的组合及 `skills（N 个）`；已登记的事实行，以及同一行指向 `docs/skills.md` 或 `skill_audit.py --strict` 的写法豁免。它检查的是计数是否登记，不是任意位置的数字是否正确，也不覆盖整个仓库。

### 7.1 政策版本只从生成块来（G5 的落地形态）

政策版本、插件版本、`TEMPLATE_FILES` 项数、skills/scripts 个数、条款范围（`R1..R136`）都是**派生事实**：
它们各自只有一个权威源，文档里的文字必须由 `scripts/policy_facts.py` 从权威源渲染，
不许人写。落地方式是 HTML 注释围起来的块：

```text
<!-- gen:policy-facts:begin -->
<!-- doc-fact:<id> -->
<由模板渲染的那一行>
<!-- gen:policy-facts:end -->
```

- 块内可以有多行（`doc-fact` 标记也由生成器一并渲染：登记与取值同源，改不到中间态）。
  一个文件最多一个块；块出现在未登记的文件里同样报错。
- `python3 scripts/policy_facts.py --check`：块内容 == 渲染结果？版本字面量都登记了？
  CI 与 `tests/test_docs_consistency.py` 都跑它（同一份实现，两个入口）。
- `python3 scripts/policy_facts.py --write`：按权威源重写块。**它不替你插入标记**——
  位置是人选的，内容不是。
- 生成块外的派生事实副本只许是**指针**：`README.md` 写「插件版本见 `.hermes/plugins/workspace-gate/plugin.yaml`」、`docs/architecture.md` 的架构图写「scripts 以 `scripts/*.py` 为准」。块外出现字面值（哪怕值与权威源相同）即红——冗余副本本身就是漂移源（架构图曾写 16、实测 17）。判据在 `tests/test_docs_consistency.py` 的 `_dup_fact_offenders`。

历史沿革（「某版本起 / 某版本引入」）不删，但要在行尾登记它引用的版本：

```text
政策 x.y.z 起改用本机状态。<!-- history:x.y.z -->
```

登记值必须在 `CHANGELOG.md` 的 `## x.y.z` 账本里，而且**不许是当前版本**——当前版本只能
出现在生成块内，拿它去登记等于给「块外写当前版本」开一条后门。这样账本删条目时引用行
立刻红，而不是静默缩小豁免面。空标记 `<!-- history -->` 不接受：那只是「别查」。
豁免按**标记声明的版本值**算，不按行算：同一行上其余 semver 照判（一行写对一处，
不能给别处的错版本当挡箭牌）。`CHANGELOG.md` 的条目标题也纳入契约：必须写成 `## x.y.z`
（版本号后不得紧跟字母/数字/点/连字符），格式不符的标题不进账本，历史判据随之失效。

不判的东西，以及为什么：插件/工具/skill 版本各有权威源（G2、D2、D8），把它们也纳入「字面量必须登记」会让示例命令、版本列全部需要标记，收益为零；代码围栏里的版本号是命令参数，不是断言。真正的空白有两类，都归 N2：**不带任何结构标记的自然语言版本断言**（如「本模板现行治理基线为 9.9.9。」）与 **正文里随机出现的三段数字**（如「x.y.z 修了个 bug」）。第 13 轮删掉了按词表补洞的做法：开放词表既会误报字段名，也会漏掉词表外说法，再补词表只会扩大误报面。

## 8. 已从 manual 转成命令的条款（判据 C1–C3）

转档标准：违反义务会留下**可静态检查的产物/配置/痕迹**，且能给出**会变红的负例**。

| 条款 | 命令 | 判据 | 能证明 | 不能证明 |
|---|---|---|---|---|
| R2 版本必须更新并记 CHANGELOG | `version_check.py` | C2 静态配置 | 政策版本、CHANGELOG 顶层、bootstrap 记录三者一致 | 该升 PATCH 还是 MINOR/MAJOR |
| R61 凭据不得进普通文件 | `secret_scan.py` | C3 痕迹 | 仓库内没有匹配高置信度模式的明文凭据 | 凭据是否被发到外部（网络行为不可见） |
| R80 索引不得手改 | `notes_index_check.py` | C1 产物 | 两个索引文件与「重新生成」逐字节一致（时间戳除外） | 生成脚本本身是否有 bug |
| R11 MCP 模式不得建本地 venv | `workspace_preflight.py` | C1 产物 | `python.mode=mcp` 时工作区没有 `.venv-*` 目录 | venv 是否建在别处；「shell python ≠ MCP 运行时」这半句仍靠人判 |

仍然保持 `manual` 的那些条款（清单见 `clause_lint.py --report`）都是**意图或过程**判断（用户是否同意、是否属当次范围、合并前是否检查），不满足 C1–C3。**把"用户确认"做成自我声明的日志或环境变量，等于把 manual 换成假绿**——这类转换一律拒绝。

**下一步候选（两席同提，未做）**：R2 / R11 / R61 各自都含「可判事实 + 人的判断」两半——
例如 R2 的「版本号与 CHANGELOG 条目存在」可判，但「该升 PATCH 还是 MINOR」不可判；
R61 的「不得出现明文凭据」可判，但「受控位置是否必要」不可判。
把一个条款拆成两条（各配各的证据）比用 `manual:` 遮住半截可判义务更诚实，属下一次 MINOR，需要改正文与条款号。

## 9. 什么时候该翻盘

如果试点后仍出现「同一句话两个 agent 判出不同结果」，且分歧集中在**强度**而非语境，才值得把个别条款改写成受控英文（条款级替换，不是整体翻译）。这条判据写在这里，避免以后靠印象争论。
