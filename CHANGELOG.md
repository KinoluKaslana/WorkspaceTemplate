# CHANGELOG — WorkspaceTemplate 变更记录

> 本文件只记录模板的政策/功能变更历史，供模板开发者与维护者查阅。工作区 Agent 无需读取本文件——实际生效规则以 `AGENT_RULES.md` 及其 custom 文件（`AGENT_RULES.custom.md`）为准。

## 3.5.8（2026-11-09）：venv 布局口径收敛到一处 + 只读诊断

- **消掉复制粘贴**：`scripts/pyenv_init.py` 原来自己复制了一份 `VENV_INTERPRETERS` / `venv_interpreter`
  （改动前两份完全相同）。现在改为 `from workspace_preflight import …`——布局知识只有一处，
  "init 能建、preflight 说没有"这类漂移不可能再出现。用例 `test_p12_single_source` 用**函数对象同一性**
  （`is`）把这条钉住，复制回来会直接红。
- **新增只读诊断**：`python3 scripts/workspace_preflight.py --workspace . --layout-only`
  输出 `{venv, layout, interpreter, version, candidates, source}`；`layout` ∈ `posix|windows|none`，
  退出码 0=找到解释器 / 3=没有。给人和 CI 一个"为什么这条 mismatch"的答案，不必读代码。
- **不接受的方案（审议席一致否决）**：把布局当参数传进来（JSON 字符串 / 文件 / 环境变量）。
  理由：布局是本机派生事实，外部传入等于把"现场核验"降级成"信任一份可能过期或被伪造的快照"，
  且与 `bootstrap.python.runtime.identifier`（记 venv 目录）语义重叠。`--layout-only` 只读，**没有 `--apply`**：
  自动改 `identifier` 是替用户做运行时选择。
- **不新增第 16 个脚本**：两个席位都算过账——为 4 个候选路径新增 `scripts/py_layout.py` 会连带
  `TEMPLATE_FILES`、tools 表、architecture 计数、发布锚与测试，收益为负；诊断能力用 `--layout-only` 零成本拿到。
- 用例：`test_p10/p11`（`--layout-only` 的 JSON 形状与退出码、`none` 不伪造版本）、`test_p12`（单源同一性）。
- **按运行时模式分流（列席追问后补）**：`--layout-only` 会读 `bootstrap.python.mode`——
  `mcp` 模式没有本地 venv，输出 `layout=n/a`、`applicable=false`、**exit 0**（不报 3，否则自动化会把"本来就没有 venv"当成故障）；
  `unconfigured` 输出 `applicable=false` + 提示先按 §0 选运行时、exit 3；`uv` 与 `local-venv` 都是本地 venv，
  走同一套布局规则（`uv venv` 在 Windows 同样产出 `Scripts\python.exe`）。
- **同行依赖显式化**：`pyenv_init.py` 现在 import `workspace_preflight.py`；两者必须一起分发。
  单独拷一个时给出明确报错（exit 2 + 一行动因），而不是裸 `ModuleNotFoundError`；测试夹具同步改成两个脚本一起拷。
- 用例追加：`test_p13/p14/p15`（mcp 不适用 / unconfigured / uv 同规则）。
- **bootstrap.json 允许 UTF-8 BOM（真机探针踩到）**：Windows 的 PowerShell 5.1 `Set-Content -Encoding UTF8`
  和记事本都会写 BOM；旧版 `json.loads(read_text("utf-8"))` 直接 `Unexpected UTF-8 BOM`，
  preflight / pyenv_init / gate_status / template_sync 全部读不出 bootstrap。四处统一改成 `utf-8-sig`
  （无 BOM 时行为不变）。用例 `test_p16_bootstrap_with_bom`，并做过"改回 utf-8 → 红"的反证。
- 真机复核（Windows，真 venv）：`local-venv` → `layout=windows` / `Scripts\python.exe` / `3.14.7` / exit 0；
  `mcp` → `layout=n/a` / exit 0；`unconfigured` → exit 3；**带 BOM 的 bootstrap** → 同上正常。

## 3.5.7（2026-11-09）：Windows CI 上的编码崩溃

- **根因**：`scripts/template_sync.py` 在失败路径上 `print` 中文；Windows 的 CI 管道用 cp1252，
  `UnicodeEncodeError` 把真正的失败原因盖掉——v3.5.6 的 Windows 作业就是这个红（`test_verify_exception_rolls_back` ERROR）。
- **修法**：模块导入时对 `sys.stdout` / `sys.stderr` 做 `reconfigure(errors="replace")`，
  不可编码字符用替代符，而不是在报错路径上再抛一次。
- **测试加严**：两处就地调用 `main()` 的用例用 `contextlib.redirect_stdout/stderr` 兜住输出，
  不再受终端编码影响。
- 复现与验证（Windows 真机，`PYTHONIOENCODING=cp1252`）：旧脚本 + 旧测试 → `UnicodeEncodeError`；
  新脚本 → `OK`；新脚本 + 新测试 → `OK`。

## 3.5.6（2026-11-04）：同步改成事务性的——失败不留半套状态

- **`--verify` 不通过默认自动回滚**：以前 verify 报 `clean: false` 只返回 rc=2，工作区停在「模板文件已替换、
  但校验不过」的中间态，用户得自己记住备份目录再 `--rollback`。现在默认按 MANIFEST 回滚到同步前，
  打印回滚结果并返回 2；确实想保留现场（例如要对着半成品排查）用新增的 `--keep-on-fail`。
- **写盘中途异常也自动回滚**：落盘段（复制、`--prune`、bootstrap 更新）整体纳入 try，任一异常按 MANIFEST
  回滚并返回 rc=3，不再留下「一部分文件是新的」的工作区。
- 新增 `tests/test_template_sync_rollback.py::Transactional` 三个用例（verify 失败回滚 / `--keep-on-fail` 保留 /
  写盘异常回滚），并用「关掉自动回滚」做过红灯反证（2 条红）。
- **`preflight` 的 Python 项不再写死 POSIX 路径**：以前只查 `<venv>/bin/python`，Windows / MINGW 的 venv 是 `<venv>/Scripts/python.exe`，于是那条**永远 mismatch**（不是用户配错）。现在按 `bin/python`、`bin/python3`、`Scripts/python.exe`、`Scripts/python3.exe` 顺序取第一个存在的；`pyenv_init.py` 用同一口径判断刚建的 venv 是否可用。新增 3 条用例（两种布局 + 都没有时返回 None）+ 1 条 Windows 布局的端到端用例，并用「只留 bin/python」做过红灯反证（2 条红）。
- **跨平台口径补齐（审议席 must-change）**：R5/R7、`README.md`、`README_EN.md`、`TOOLS.md` 的装包命令原来只写 `<venv>/bin/python`，Windows 上是错的；现在统一注明 `<venv>\Scripts\python.exe`。§13 的「六脚本」按实测改为「七脚本」，与 `docs/hooks-catalog.md` 对齐。
- **回滚失败单独可见**：同步失败且回滚也失败时返回 **rc=4**（需按 `MANIFEST.txt` 手工恢复），不再与「已回滚」共用 rc=3；`--verify` 的校验器本身起不来（抛异常）也走回滚，返回 3。
- 测试加严：回滚后 `bootstrap.json` **逐字节**与同步前一致（不只比版本字段）；新增校验器异常注入用例。
- **接线证据假阴性修复**：`gate.log` 原来只在 `post_write` / 生命周期对账时写 `client=` 行，于是「hook 在跑但整个会话只读」会被 `gate_status` 判成 `未接线`（假阴性）。现在 `session_start` 也写一条 `session_start client=<client> session=<sid>`；用例 `hooks-tests/test_w2_anchor.py` 覆盖，并做过「去掉该行 → 红」的反证。
- **Codex hook 不再写死 `/usr/bin/python3`**：10 条命令统一改为 `GATE_PY` → `/usr/bin/python3` → PATH(`python3`/`python`) 的解析前缀；命令字符串变更使既有 `trusted_hash` 失效，已按 R107 重走 `/hooks` 重新信任（`~/.codex/config.toml` 的 `hooks.state` 哈希已更新）。
- 说明：`--rollback` 备份机制本来就有（4 个既有用例），缺的是「失败时自动用它」；`git` 只用于从模板仓
  按 ref 取文件，与回滚无关——无 git 环境下这几个用例是 skip，不是失败。

## 3.5.5（2026-11-04）：修掉一直 404 的策略徽章

- **根因**：shields 静态徽章是 `/badge/<label>-<message>-<color>`。`policy-v3.5.4-single%20source%20of%20truth-6f42c1`
  里 `v3.5.4` 与 `single` 之间用了裸 `-`，shields 把 `v3.5.4` 当成 message、把 `single source of truth` 当成颜色段，
  于是返回 `404: badge not found`。徽章坏掉但页面照常渲染，只看文字看不出来——之前几轮扫描都在看图片链接的 HTTP 状态，
  没看返回的 SVG 内容，所以一直漏。
- **修法**：message 里的空格编码成 `%20`（不再用裸 `-` 分隔）：`policy-v3.5.5%20single%20source%20of%20truth-6f42c1`。
- **加机械检查**：`tests/test_docs_consistency.py` 新增 D10——解析 README / README_EN 里每个 `img.shields.io/badge/…`，
  要求恰好三段（label / message / color）、`--` 视为合法转义、颜色段是 hex 或已知色名。这条能在离线状态下抓住同类错误。

## 3.5.4（2026-11-04）：实例可用性两处修正

- `tests/no_git_runner.py` 容忍缺失目录：派生实例没有 `hooks-tests/`（不下发），之前在实例上跑会
  `ImportError: Start directory is not importable`。
- `workspace_preflight` 的 `gate` 项区分「证据过期」与「没有任何证据」：前者是工作区闲置的正常形态
  （插件只在其事件发生时写日志），判 `warn` 并提示写入前重跑 `gate_status`；后者才判 `mismatch`。

## 3.5.3（2026-11-04）：把「机械一致性」的承诺补齐

- **git 缺失时的行为与文档一致**：`test_template_anchor` / `test_template_sync_rollback` 整类 skip，
  `test_agent_scope` / `test_bash_dispatch` / `test_cc_transcript` / `test_dsh_post_shell` 的 git 夹具抛 `SkipTest`；
  新增 `tests/no_git_runner.py` 把 `git` 从 `which` 里摘掉跑全量（实测 220 例、0 failure、0 error、17 skip），
  CONTRIBUTING 的声明从此可自证。
- **版本漂移堵住**：`tests/test_docs_consistency.py` 的 D4 现在**检查模板仓 README 的徽章格式**（`policy-vX.Y.Z`），
  实例侧仍跳过用户 README；README/README_EN 徽章同步到 3.5.3。
- **CI 徽章换掉会 404 的形态**：`.../actions/workflows/ci.yml/badge.svg`（无 `?branch=`，缓存过 404 后长期显示
  “badge not found”）改为 shields 动态徽章 `github/actions/workflow/status/.../ci.yml?branch=main&label=CI`。
- **补「什么时候不该用」**：README / README_EN 新增该节（一次性任务、只问答、要强隔离、不接受仓内 hook、不想跟版本）。
- 计数：`TEMPLATE_FILES` 77 → 78（新增 no_git_runner）。

## 3.5.2（2026-11-04）：补上 D7 的实例豁免（3.5.1 漏改一处循环）

- `test_d7_prose_counts_match_disk` 的 README 豁免在 3.5.1 只落到了 D8，D7 仍按模板标准扫实例 README，
  导致同步完的实例继续红。本版把 `user_home` 与 `continue` 补进 D7。
- 验证：模板仓 9/9 通过；实例（TemplateDev）同步后 103 通过（1 skip）。

## 3.5.1（2026-11-04）：模板仓专属检查不再误伤派生实例

- `tests/test_docs_consistency.py` 的 D7/D8 之前对 `README.md` / `README_EN.md` 一律按模板标准查计数与 skill 版本列，
  但实例的 README 是**用户首页**（`template_sync` 明确豁免、不覆盖）——同步完的实例必然红。现在只在模板仓
  （`bootstrap.workspace_status == "template"`）严格检查，实例跳过 README。
- D9 的「`release_anchor.py` 仍在仓内」半边改为仅模板仓断言：`.github/` 整层不下发，实例没有这个文件是正常的。
- 无政策语义变更；`workspace_status` 仍是 3.5.0 的条款集（R1..R118）。

## 3.5.0（2026-11-04）：守门接线核验上升为 §15 必须项（与 Python 同性质）

- **新 §15（R113–R118，§0 的补充）**：gate 是否在跑取决于 **harness 的启动方式**，不是工作区的属性——
  dsh 无项目级自动发现（不带 `--patch` 启动 = 完全没有 gate），hermes 需 `HERMES_ENABLE_PROJECT_PLUGINS=true`
  + config `plugins.enabled`，cc/codex 依赖项目受信。开工前必须对当前 harness 跑
  `gate_status.py --require <harness> --ttl-minutes 30`，**只认运行期证据**；无证据必须在第一条受管写入前
  向用户报告并给出启用命令，由用户裁决，**结论必须落盘** `bootstrap.gate`（`configured`/`unconfigured`）。
  已 `configured` 的工作区只复核 TTL，不重复询问。只读/问答不受阻。
- **`scripts/gate_status.py` 增 `--record` / `--user-decision` / `--force`**：无证据拒绝记录 `configured`；
  `unconfigured` 只能由 `--force --user-decision '<原话>'` 写入（把「用户接受无 gate」变成可核验的事实）。
- **`workspace_preflight.py` 增第 10 项 `gate`**：登记 vs 运行期证据——`configured` 但 TTL 内无证据判
  `mismatch`（声明的状态与事实矛盾）；未记录/记录 unconfigured 判 `warn`。
- **dsh 账本不再回退 mtime**：只有 `events[].ts` / `anchors.*.ts` 算证据（`touch` 不再能伪造活跃）；
  两类负例（空账本、有 events 无 ts）都由 `test_gate_status.py` 钉住。
- **`.github/` 明确不下发**：CI 与 `release_anchor.py` 属模板仓基础设施，不进 `TEMPLATE_FILES`、不随同步下发；
  `tests/test_docs_consistency.py::test_d9_github_layer_is_not_template_layer` 钉边界（此前审计席把它当缺口，实为设计）。
- **测试文件读写编码**：hooks-tests 里 `read_text()/write_text()` 未指定编码，Windows 上按 cp1252 处理含中文的
  状态/审计账本会抛 `UnicodeDecodeError`（`test_w2_anchor` / `test_w3_worktree_change_audit` 因此红）；统一显式 UTF-8。
- **`.gitattributes` 强制 LF**：Windows 默认检出 CRLF，会让「完整读取」的行证据与字节指纹判据失效。
- **测试子进程编码**：Windows 上 Python 子进程 stdout 用 locale（cp1252/GBK），父进程用 `text=True` 解码会抛
  `UnicodeDecodeError`——所有 spawn 统一 `encoding="utf-8", errors="replace"`，并在测试进程里 `PYTHONUTF8=1` 让子进程按 UTF-8 输出。
- **Windows CI 修复（同一发布线）**：CC project slug 在 Windows 上产出 `-C:\Users\…` 非法目录名（hooks-tests 39 error 的根因）→ 分隔符归一化 + 冒号替换；
  `test_wtarget` 命令串里的盘符路径改用 `/`（bash 里反斜杠是转义，属 shell 语义）；`test_cc_transcript` 的产物路径断言先归一化；
  dsh harness 用 `pathToFileURL()` 加载插件（裸盘符路径在 Windows 触发 `ERR_UNSUPPORTED_ESM_URL_SCHEME`）；
  `template_sync` 用本地目录当 clone URL 时先转 `file://`（Git for Windows 的 MSYS 路径转换会把 destination 变成 `/d/…`）。
  CI 的 unittest 步骤失败时会把红掉的测试写成 GitHub annotation，便于无 admin 权限时直接定位。
- **`bootstrap.json` 增 `gate` 节**（status / harness / evidence / ttl_minutes / decision_source / checked_at），
  与 `python` 节同级。

## 剩余风险处置：发布锚 + 并发加压（2026-11-04，dev，不改政策版本）

- **发布锚 `.github/scripts/release_anchor.py` + CI 步骤**：本次提交动了 `TEMPLATE_FILES` 里的文件、却没动
  `CHANGELOG.md` 时，governance job 直接红。根因是 `verify_rules` 的信任源为仓库内**可被
  `mark_rules.py registry` 重新生成**的注册表——「改文件 + 重建注册表」在机械门禁里不可见。
  `--self-test` 用真 git 仓库做负向自检（仅改模板层 → rc=1；同提交带 CHANGELOG → rc=0），CI 每次先跑自检。
  **挡不住**「文件与 CHANGELOG 一起改」——那是评审责任，不是机器能判的。
- **Windows 5 会话并发加压**：1 读 + 4 未读（`write` / `pwsh` 混合）同一工作区同时起——只有读者落地，
  5 份账本互不串号，恰好 1 份含完整读证据并带 `gate-intent`/`gate-commit`。首轮 3 会话的结论在 5 会话下仍成立。
- **dsh 生命周期探针（新事实）**：dsh 不向插件暴露会话结束钩子；`session/flush` 会触发（一次会话约 12 次）
  且载荷含会话 id，但它是**耐久检查点**，不是会话结束。
- **`scripts/gate_status.py`（新增）**：判「clone 下来到底有没有 gate 在跑」。dsh **没有项目级自动发现**——
  不带 `--patch .dsh/workspace-gate/cordis.yml` 启动，就等于完全没有 gate（读证据、写前拦截、事后审计都不发生），
  而工作区文件一个不少；`hermes` 需要 `HERMES_ENABLE_PROJECT_PLUGINS=true` + config `plugins.enabled`；
  `cc`/`codex` 依赖项目受信。判定只看**运行期证据**（`gate.log` 的 `client=` / `.dsh-gate.log` 的 `apply` /
  `dsh-session-*.json`），`--require dsh,cc` 缺证据 → 退出码 1。`tests/test_gate_status.py` 22 例。
- **盲区事后可见（插件侧，含边界拓宽）**：既然没有收尾事件，就由插件在**工具调用后**触发差集审计——
  触发面是 shell（`post_shell`）与「不在只读集合里的未知工具」（`post_tool`）。判定口径：
  git 仓库用 `git status`；**非 git 工作区用 mtime 水位线 + 哈希兜底**（水位线 O(1) 状态、不存全量清单；
  哈希兜底覆盖 `cp -p` / `tar -x` 这类保留 mtime 的写，按 1 MiB/文件、32 MiB/轮的预算，超预算记
  `snapshot-incomplete`）。审计记录追加到 `.workspace/.hook-state/audit-dsh.jsonl`（256 KiB 轮转），
  不再只留在 50 条的事件窗口里。节流 `WORKSPACE_GATE_DSH_AUDIT_MS`（默认 5000 ms）。
  真机实测：`& ('Set'+'-Content') -Path blind3.txt -Value ok` 被静态分类器放行、文件真的落地，
  账本随后记到 `worktree-change(source=post_shell)` 与 `unobserved-write → blind3.txt`。
  测试：`hooks-tests/test_dsh_post_shell.py`（11 例，含「700 文件干净树无假阳性」「保 mtime 改写被哈希抓到」两条负控）。
- **审计齐平**：文档里的脚本数 / skill 数 / 条款范围 / skill 表版本列 / 断链全部对齐磁盘；新增
  `test_d7`（散文计数）、`test_d8`（skill 表版本），`test_d1` 的扫描面扩到 CONTRIBUTING / SECURITY / UPGRADE。

## dsh Windows 多会话并发取证（2026-11-04，dev，不改政策版本）

上一轮发布研判里，deepseek-v4-pro 的唯一硬阻塞是「Windows 真实多会话并发无真机证据」。本轮补上：

- **三个 headless 会话同秒起**（同一工作区）：整读后 `write` 放行并留 `gate-intent`/`gate-commit`；未读的 `write` 与 `pwsh` 各被拒（`[workspace-gate:no-read]`，文件均未落地）；三份 `dsh-session-*.json` 互不串号、无撕裂。
- **确定性并发探针**（3 进程直调 `hook.py`，不经模型）6/6 PASS：只有读者账本含 `coverage [[1,204]]`。
- **两条通道各跑一轮**：win-host 加密通道（`/exec`）与 WSL interop 直调，结论同形。
- 边界照实记：同父会话的并发子会话、>3 会话压力未测；B 会话账本另有一条 `unobserved-write`（dsh 在 deny 后仍发 post 事件），不代表写入发生。
- 证据目录（工作区侧，不随模板分发）：`dsh-win-concurrency-261104/run1-interop/`、`run2-winhost/`。

## 对外发布补齐（2026-11-03，dev，政策 3.4.0 + 本条目）

依据一份 0 上下文的外部评审（8.5/10 自用、6/10 公开模板），按主理席研讨结论落地：

- **LICENSE**：改用 MIT（用户拍板），`LICENSE` 进 `TEMPLATE_FILES`（75 项），README/README_EN 加徽章与许可段。
- **Windows 路径 bug**：`docs_index.py` / `secret_scan.py` 的 `str(relative_to(...))` 改 `as_posix()`，
  否则 Windows 下索引键与 `.secretscanignore` 匹配都会带反斜杠。
- **可跳过用例显式化**：`test_workspace_preflight` 的 git/symlink 依赖改为 `SkipTest` 并给出原因，
  不再在无开发者模式/无 git 的机器上变红。
- **CI**：新增 `.github/workflows/ci.yml`——治理 job（mark_rules / verify_rules / clause_lint / skill_audit /
  docs 检索）+ 测试 job 矩阵（ubuntu / windows；macos 未纳入矩阵，仅在本机手测），跑 `unittest discover` 两个目录与 dsh harness。
- **社区文件**：`SECURITY.md`（含 advisory 边界说明）、`CONTRIBUTING.md`、issue/PR 模板。
- **未采纳**：政策 30KB 拆分（主理席一致认为会引入「附件漏挂 → 政策静默失效」风险，列为发布后单独评估）。
- 遗留：GitHub 仓库 description/topics、tag 需在合并 main 后设置（本机无 gh CLI）。

## docs 检索判据补齐（2026-11-03，dev，不改政策版本）

`hit08` 是长期唯一的判据缺口（9/10）。根因不是索引坏了，而是**判据标题被当检索词用**：原标题「新 harness 几步」
里的「几步」在文档中不存在（只有「7 步」），字面匹配必然落空。

- 标题改为「新 harness 事件类型」（与内容一致，问题文本不变）；
- 新文档 `docs/harness-onboarding.md` §2.1 写出该题要的答案（`SessionStart` / `PreToolUse` / `Stop`）；
- **没有加别名**：别名表仍为空，符合「判据集 MISS 不算加别名的理由」；
- 判据集现在 **10/10**（`--min 10` 通过）。
- 另测：用完整 `question` 检索只有 7/10（hit06/07/10 落空），记为长问句的待改进项，不与本次混为一谈。

## Windows 其余面（2026-11-03，dev，不改政策版本）

在真实 Windows 上补齐：`edit` 未读拒 / 读后放行；只读前 50 行 → 写被拒（理由精确到区间）；子 agent 未读被拒；
读后治理文件被外部修订 → 再写被拒（hook 级）；hook 缺失与 hook 超时 30s 均按 advisory 放行并落日志。

- 插件 spawn hook 时注入 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`（Windows 不加这组，中文 deny 文案会退化）。
- 中文文案乱码的根因确认在 **PowerShell 的解码**，不是守门：设置 `[Console]::OutputEncoding=UTF8` + `chcp 65001`
  后复跑，deny 文案完整可读（`decision=deny`、文件未落地不变）。
- 模型会自己按 R90 拒绝「用 Add-Content 改 AGENT_RULES.md」，因此修订撤销改用 hook 级构造验证。

## Windows 真机端到端（2026-11-03，dev，不改政策版本）

真实模型回合（`deepseek-flash` + `dsh 0.2.2-alpha.2` + `D:\wt\ws`）跑通两侧：

- 未读写（`pwsh` / `write`）→ **deny**，文件不存在；
- 整读 204 行后写 → **allow**，文件落地（coverage 1..204 complete）。

修掉四个只有 Windows 才出现的缺陷：

1. `gate_core` 的 `import fcntl` 在 Windows 直接崩（hook 退出 → 插件 advisory 放行）→ POSIX `fcntl` / Windows `msvcrt.locking` 双实现；
2. 插件硬编码 `python3`（Windows 只有 `python`）→ 按平台探测；
3. `python` 可能命中 WindowsApps 的 Store 存根（rc=0、无输出）→ 用 `-c print(sys.version_info[0])` 实测必须输出 `3`；
4. **hook 的 stdin/stdout 用本地码页（GBK）**，客户端送 UTF-8 → `JSONDecodeError`，读证据永远记不上、deny 文案乱码 → `reconfigure(encoding="utf-8")`。

外部依赖 `win-host` 也修了两处：`subprocess.run(text=True)` 的 GBK 解码异常（读取线程崩 → 连接重置），以及 `/exec` 的 socket 超时没跟随 `--timeout`。

## Windows 侧 dsh 落地（2026-11-03，dev，不改政策版本）

- Windows 装 `@deepseek-ai/dsh` 0.2.2-alpha.2（npm 全局 → `C:\wt-dsh`，615 包）；Node 24.22.2 便携版（`D:\wt`）。
- 发现即修：插件硬编码 `python3`，Windows 只有 `python` → spawn 失败后**静默放行**；改为按平台探测 + 环境变量覆盖，
  找不到 Python 时写 `python-missing` 审计并放行（advisory 口径，不声称已拦）。
- `--dump-config` 在 Windows 上确认插件接线：`workspace-gate-dsh → file:///D:/wt/ws/.dsh/workspace-gate/plugin.js`。
- 注：Node < 24.9 时 `dsh` 静默退出（rc=0、无输出），曾被误判为"安装失败"。

## pwsh 写路径补齐（2026-11-02，dev，不改政策版本）

在真实 Windows（PowerShell 5.1，经 win-host 远程执行）验证：`Set-Content` / `Out-File` / `Add-Content` /
`New-Item -Value` / `>` / `>>` / `Tee-Object` / `[IO.File]::WriteAllText` / `Copy-Item`
**9 种写法真的创建了文件**（`cmd /c` 那条未实测成功，见本条目末的反例说明）。而旧分类器只认 POSIX，这 10 种全部被判「无写目标」→ **dsh 的 pwsh 工具等于完全不设防**。

- 新增 `wtarget.classify_pwsh()`：识别上述 10 类写形态、`Remove-Item`/`Clear-Content` 等破坏性 cmdlet、
  `[IO.File]::*` 写 API；写意图存在但目标不可静态确定（`iex`/变量目标）→ **fail-closed**。
- `hook.py` 在 `dsh_tool=pwsh` 时走该分类器；顺带修 `res["class"]` 在 pwsh 分支的 KeyError
  （会把只读命令误判成「内部错误 deny」）。
- 新增 `tests/test_pwsh_writes.py`：10 种写形态 deny、4 种只读 allow、`iex` fail-closed、读后放行。
- `TEMPLATE_FILES` 70 → 71。
- **未做**：Windows 上没有安装 dsh，所以「dsh pwsh 工具 → 本 hook」这一跳是按契约接线 + 两侧各自实测，
  不是端到端；已在 `docs/dsh.md` 写明。
- SOL 复核席抓出 5 类问题并全部修掉：`sc -Path` 别名、`& 'Set-Content'` 调用运算符、`% { }`/`foreach { }`
  脚本块、`cmd /c "..."` 内层命令、以及**误报**（注释/引号里的 `>`、`2>&1` 流合并）。
  最终形态是逐字符分词（引号与注释先屏蔽），不再用正则猜整条命令。
- Windows 二次取证（别名/调用运算符/脚本块/.NET 写对象 7 种）全部真的建了文件；同时抓到两个**不能想当然**的反例：
  `Set-Item` 在 FileSystem provider 上 `NotSupported`（不会写文件，已从写表移除，避免误报）；
  `cmd /c …` 在本机 PATH 命中 msys64 的 `cmd`，PowerShell 拒绝在管道中运行 → **cmd 内层未实测**，
  分类器按写意图拦但文档不声称已验证。

## dsh 未验证项逐条实证（2026-11-02，dev，不改政策版本）

按预注册判据（SOL 主理、GLM 辅）实跑 V1–V9，并把结果写回 `docs/dsh.md`：

- **V1 子 agent**：子 `agent.id` 独立、账本独立、父证据不继承、子写被拒（无委托机制，属已知差异）。
- **V2 分段读**：只读 1..50/204 → 写拒；补齐 → 放行。
- **V3 hook 缺失**：放行 + `hook-missing`。
- **V4 不可判 bash**：实测推翻旧文档——解释器写 **拒**；变量间接与 `awk` 重定向 **放行**（真盲区，真实回合确认文件被创建）。
- **V5 超时**：20s 放行 + `hook-timeout`。
- **V6 失败 read**：不记账，随后写仍拒。
- **V7 修订失效**：写后 `post-write-invalidation`，第二次写被拒。
- **V8 `edit`**：未读即拒，文件未变。
- **V9 会话隔离**：前序会话有完整读，新会话仍拒。

同时修掉一处**假拒绝**：`pre_bash` 只接受 `cc/codex`，dsh 的 bash 一律走 `ValueError` → 被包装成 "内部错误 deny"。现在 dsh 的 `bash/pwsh` 归一为 hook 的 `Bash` 走同一套静态分析，拒绝理由也从"内部错误"变成真实的「未读」文案。

仍**无法验证**：`pwsh`（需 Windows 主机）；`str_replace_editor`（**该构建未提供此工具**，模型明确回报不存在；映射留作防御性分支）。
## dsh 真实回合验证与修正（2026-11-02，dev，不改政策版本）

账号登录 dsh 后跑真实模型回合（`deepseek-flash`），暴露出四个只有实跑才会发现的问题，全部修复：

1. **插件永不激活**：`inject: ['tools','session']` —— dsh 没有 `session` 服务，条目一直 pending，守门等于没装。改为 `['tools']`，会话键取实测的 `exec.agent.id`（形如 `session-<uuid>`）。
2. **会话键双拼**：`agent.id` 既是会话 id 又被当 `agent_id` 传入 → 状态文件变成 `a.a`。同值时不再传 `agent_id`。
3. **read 正文解析不了**：dsh 的 read 结果带 `<path>/<type>/<content>` 包装、`N: ` 行号前缀，以及 `(Showing lines A-B of N…)` / `(End of file - total N lines)` 尾注。新增 `_dsh_read_body`：剥包装、校验行号连续、剥尾注；`end<total` 时补尾 LF 以匹配 `gate_core` 期望片段。
4. **EOF 哨兵**：按物理行数（去掉末尾空行）计算读取区间，避免「读完整个文件仍被判不完整」。

实测结果：未读先写 → 拒绝、文件未创建；先读后写 → 放行；`bash` 重定向写 → 拒绝；非写工具放行。
`tests/test_dsh_wiring.py` 改用真实形态载荷（`file_path` + 行号 + 尾注），合成用例 8 条；`tests/dsh_harness.mjs` 同步。
文档补 `headless + 账号登录` 的 profile 补丁写法（`docs/dsh.md` §5.1）。

## 工具新增：DeepSeek Harness（dsh）接入（2026-11-02，dev，不改政策版本）

dsh 是 Cordis 插件内核（`@deepseek-ai/dsh` 0.2.1-rc.4 实测；0.2.2-alpha.2 复验插件加载与 8 条合成用例），拦截点是 `tools/pre-execute` / `tools/post-execute` 事件而非 JSON 配置，因此新增**原生插件**而不是复用 Codex/CC 的桥。

- `.dsh/workspace-gate/`：`plugin.js`（把 `ToolExecution` 翻译成工作区 hook 载荷、把决策映射回 `{kind:'deny', reason}`）+ `cordis.yml`（`insert` 补丁）+ `package.json`。
- `hook.py` / `gate_core.py` 增加 `dsh` 客户端分支：`write|edit|str_replace_editor` → `pre_write`（`file_path` / `path` 容错，二者皆缺即拒）；`bash|pwsh` → `pre_bash`；`read` → `post_read`（正文用启发式提取，取不到不记账）；拒绝理由补 `[workspace-gate:no-read]` 前缀。
- 失败姿态：插件异常 / hook 超时（20s）/ spawn 失败 → 放行 + `.workspace/.dsh-gate.log`；**拿不到会话身份 → fail-closed**。
- 证据等级：配置挂载（`--dump-config`）✓、真实 `dsh web` 启动的 `apply` ✓、真实 plugin+hook 的 7 条合成用例 ✓；**真实模型回合拦截未跑**（无 API key），未验证项写在 `docs/dsh.md` §5。
- 同步修掉两个真 bug：`gate_core._state_path` 只接受 `cc/codex`（dsh 直接 ValueError）、插件 spawn 未写 stdin 导致 hook 阻塞超时。
- `TEMPLATE_FILES` 64 → 70；测试 171 → 174。

## 工具新增：把 4 条 manual 转成可判定命令（2026-11-02，不改政策版本）

按预注册判据 C1（产物）/C2（静态配置）/C3（痕迹）逐条筛，只转「违反会留下可检产物」的四条；意图/过程类一律保持 manual，拒绝"自我声明式假绿"。

- `scripts/version_check.py` → R2：政策版本 = CHANGELOG 顶层 = `bootstrap.template.current_policy_version`。
  落地即抓到真实漂移：模板自己的 bootstrap 记录停在 **3.3.1**，已修正为 3.4.0。
- `scripts/notes_index_check.py` → R80：把 `notes/` 复制到临时目录重跑 `rebuild_index.py`，与仓库的 `INDEX.md`/`_INVERTED.md` 比对（生成时间戳行归一化后逐字节）。
- `scripts/secret_scan.py` → R61：私钥块 / AWS AKID / GitHub·Slack token / Bearer / password 赋值；只打印脱敏片段。
- `workspace_preflight.py` 新增 → R11：`python.mode=mcp` 时不得同时存在 `.venv-*` 目录。
- 结果：command 20 → **24**，manual 20 → **16**；`tests/test_check_scripts.py` 10 例（每个检查都带会变红的负例）。
- `TEMPLATE_FILES` 60 → 64。

## 3.4.0（2026-11-02，MINOR）政策双轨：受控词表 + 条款属性

三席（GLM/SOL/DS）同判「不换语言」后的落地。新增 §14（R109–R112）：正文只用四个情态词、条款属性存 `.workspace/clauses.meta.json`、`check` 必须是命令或 `manual:<理由>`、`clause_lint` 独立成门。正文顺带消掉 3 处模糊量词（「通常」×2、「确有必要时」），并把 R8/R24/R94 的义务句补成显式「必须」。

- 新增 `scripts/clause_lint.py`（E1–E9 错误 / W1–W5 警告；`--strict`、`--report`、`--json`），E8 专门拦「机器可查却标 manual」。
- 新增 `.workspace/clauses.meta.json`：112 条全量覆盖，MUST 27 / MUST_NOT 13 / SHOULD 4 / MAY 16 / INFO 52；20 条绑可执行命令、20 条 manual（全为意图/裁决类：版本语义、首启询问、安全域范围判断、委派过程等）。
- 新增 `docs/policy-language.md`（为什么双轨、判定表、基线、翻盘条件）与 `tests/test_clause_lint.py`（9 例）。
- `clause_lint` 默认 findings>0 即失败（`--lenient` 才只看 errors），并新增 E10（条款 ID 重复）/ E11（文末未标记正文）两项漏检修复。
- 基线 0 errors / 0 warnings；同时把 §0.1/§0.2/§1/§7/§8/§9 里 19 处「不<动词>」「只…」式隐式义务改写成显式「不得/必须」（同一义务，措辞可判定）。
- `TEMPLATE_FILES` 56 → 60。

## 工具新增：docs 检索（2026-11-02，不改政策版本）

- 新增 `scripts/docs_index.py`（build / check / query）与 `docs/docs-retrieval.md`：`docs/*.md` 的**确定性**定位索引，按标题切节，不存正文，文件指纹变化即拒答（fail-closed）。
- 定位为**成本工具**，不是 token 工具，也不构成「已读」：检索结果不入 gate `coverage`，需要依据仍须 `read_file`。
- 实测（10 题，deepseek-v4-flash）：正确率 10/10 vs 10/10；total tokens 68,955 → 107,021（+55%）；未缓存 input −52%；单题成本 −39%；检索命中依据 6/10。
- 裁定与判据：`mc-261030/RAG-VERDICT.md`（GLM/SOL 双主写 + DS 与主理人裁判）。政策正文与 R 号不变，故不升政策版本。
- 新增回归：`tests/test_docs_index.py`（7 例）。

### 维护自主权与别名纪律（skill 1.1.0）

- 明确**自主分级**：索引 `build/check` 全自动；失效指针自动停用；指纹自动对齐；**别名新增仅在有真实查询缺口日志时可自主**；重指/删除/改阈值/改 boost 上限需人工。
- 新增 `scripts/docs_alias_audit.py`：死指针、指纹漂移、来源合规（`source` + `miss_id` 必须能在 `docs/.miss-log.jsonl` 命中）、**留一验证**（禁用后仍达标即判冗余）、`--restore` 快照回滚。
- 新增缺口日志 `docs/.miss-log.jsonl`（只追加，字段固定）；**判据集 MISS 不得作为新增别名的理由**。
- 清空历史 `probe-only` 别名（该条由判据题引入且留一验证为冗余）：别名 0 条，判据集 **9/10**（hit08 缺口如实保留，待真实查询驱动）。
- 新增回归 `tests/test_alias_audit.py`（5 例）；`TEMPLATE_FILES` 33 → 35。

### 检索命中修复（同日）

- 诊断：4/10 漏检全部源于「标题命中常数 +10/词、不乘 IDF」，叠加中文二元功能词碎片与缺失覆盖率因子（`mc-261030/RETRIEVAL-DIAGNOSIS.md`）。
- 打分重构：`score = C × (BM25加权 + 3.0·Σ w·IDF_标题) + 100·条款 + 20·别名`，`C = 0.25 + 0.75·(命中词权重/查询词权重)²`；中文二元权重 1.5；查询侧剔除功能词（怎么/哪些/几步/只认/…）；<0.6 且无条款/别名命中返回空。
- 新增 `docs/.aliases.json`：只存「口语短语 → 已有节」指针（当前 1 条），目标标题或文件指纹不符即忽略。
- 结果：10 题命中 **6/10 → 10/10**；`R98` 仍排第一；噪声查询仍为空；测试 **7 → 11 例**。token/成本结论不变（打分只改排序，不改查询轮次）。

## 自举流程加固（2026-11-02，三席评审后）

评审席位：GLM、SOL 各出一份独立意见，DS 裁决、主理人复核。**两席独立命中同一 P0**：`UPGRADE.md` 的回滚脚本只放回旧文件、不删除本次新增，重犯了 3.2.0 已修的同款问题。

- `template_sync.py`：`--apply` 写 `MANIFEST.txt`（`[replaced]` / `[added]` / `[preserved]`；`--prune` 另记 `[pruned]`）；新增 `--rollback <备份目录>`（放回 replaced/pruned、删除 added）与 `--purge-generated`（清除升级前不存在的 `skills/REGISTRY.md`、`notes/INDEX.md`、`docs/.index.json` 等引导期生成物）。
- `--keep-readme` 语义修正：只保留用户已有的 `README.md`；`README_EN.md` 缺失时仍安装（否则 `verify_rules` 恒报 `MISSING README_EN.md`）。
- 标签/裸提交 ref 也回写 `last_synced_branch`（原来只在 commit 分支下写，下次缺省会退回 main）。
- `skill_audit.py`：TOOLS 登记判定从「全文子串」改为「表格行 `| **name**`」（散文提到不再算已登记）；`--sync-registry` 新建 `REGISTRY.md` 时写入入口计数行。
- `UPGRADE.md`：补 R59 授权句、`--keep-readme` 必带说明、`$TPL` 跨 shell 处理、备份目录选择、`--purge-generated`、浅克隆与标签限制、TOOLS 按完整 snippet 抄。
- 文档计数修正（7 skills / 56 files），新增 D6 计数一致性测试；README/README_EN 增加「旧版本工作区怎么升级」可复制 prompt 与新版目录树。
- 实测：伪造 2.4.0 工作区 → 54 文件 → `--rollback --purge-generated` 后仅剩 `AGENT_RULES.md` 与 `.workspace/`；146 例测试全 OK。

## 旧版本工作区自举（2026-11-02，policy 3.3.1 内含，不改政策文本）

问题：由 2.x 模板创建的工作区，`.workspace/skills/` 里没有现行 skill，也没有 `scripts/template_sync.py`，因此**无法用工作区自身能力升级**。

- 新增 [`UPGRADE.md`](UPGRADE.md)：只依赖 Python 的自举入口（克隆模板快照 → `template_sync --apply` → 补登记 → 四条验收 → 回滚）。
- 新增 skill `legacy-workspace-upgrade` v1.0.0（8 维契约；与 `workspace-init` 分界：有血缘但版本旧 vs 无血缘接管）。
- `template-update` 增加「旧工作区没有本 skill 新版时」的转交说明。
- `skill_audit.py`：`--sync-registry` 在 `skills/REGISTRY.md` **不存在时新建**（旧工作区本来就没有该文件）；新增 `--tools-snippet` 打印需补进 `TOOLS.md` 的行。
- 实测：`/tmp/oldws`（伪造 2.4.0、无 scripts、无 REGISTRY）→ 自举后 `verify_rules --vs-template clean=true`、`skill_audit --strict 7/7 0 findings`。
- `TEMPLATE_FILES` 55（+`UPGRADE.md`、+`legacy-workspace-upgrade/SKILL.md`）。

## 合并前修剪：过程类语句（2026-11-02）

原则：只删「我们怎么走到这里」的叙述与逐版本历史；运行所需元数据（当前版本号、条款 ID、frontmatter version、验收命令、行为事实）一律保留。历史唯一归宿是本文件。

- **政策**：删 `实测子 agent 会因 §0 询问空转（v2.8.3 补充）` 旁注、`子 Agent 写授权（3.0.0）`、`子 Agent 任务书（3.0.0，extends R66）` 的版本括注。
- **skills**：6 个 skill 的逐版本 `## 变更记录` 全部移除，改由本文件承载；补稳定的 `## 维护` 节（版本规则 + 审计命令）。`_TEMPLATE.md` 同步改为 `## 维护`。
- **契约**：`skill_audit.py` 的必备节由「变更记录」改为「维护」，`tests/test_skill_audit.py` 同步。
- **docs**：删 `docs/README.md` 顶部版本快照、`docs/skills.md`/`docs/tools.md` 的变更记录节、`hooks-catalog.md` 的过期版本串（3.1.0）、`hooks-guide.md` 的三处「vX.Y.Z 起」；`docs/skills.md` 小节标题去掉版本括号。保留 `docs/architecture.md` 的版本锚点行（版本入口）。
- **README**：`## alpha 分支（3.x）新增了什么` → `## 设计要点`（去分支名，保留内容）；去掉「（v1.2.0 起）」。
- **脚本**：`docs_index.py` 的 `policy 3.1.0` 括注、`template_sync.py` 的 `--ref v3.1.0` 示例改为 `<tag>`。
- **删除过程文档** `ALPHA-HOOK.md`（"已并入 dev，未发布"的落地说明）；`hooks-guide.md` 对它的引用改为直接陈述事实（Codex 不为原生 patch 发 `PostToolUse`）。
- **保留**（三席一致）：`实测` 作为来源标记（R102）、`docs-retrieval.md` 的实验日期（界定批次）、`hooks-guide.md` 的「本轮被写/本轮没读」（守门时序语义）、`CHANGELOG.md` 全文。

## 文档体系（2026-11-02，不改政策版本）

- **README 更新**（中英）：alpha 3.x 变化表、三种 Python 模式、6 个 skill 表、目录结构、文档索引入口；修正「venv 是必须项」「守门只需 read_file 过」等过宽表述。
- **新增 docs**：
  - `docs/README.md`：按读者选入口 + 三条共用验收命令 + 分层一句话；
  - `docs/architecture.md`：四层模型、写路径、读证据模型、失败姿态（含 fail-open）、跨设备三类事实、模板演进；
  - `docs/skills.md`：6 个 skill 的触发/边界/组合流程 + 8 维契约 + 维护；
  - `docs/tools.md`：10 个脚本的用法、`--json` 语义差异、退出码约定。
- **新增回归** `tests/test_docs_consistency.py`：链接可解析、skills/scripts 全覆盖、版本号与 `TEMPLATE_FILES` 条数与实际一致、README 提到全部 skill。
- **修脚本缺陷**：`template_sync.py --verify` 原把 `--json` 当开关传（`verify_rules.py` 要求输出路径）→ 校验恒为 `None`；改为写临时文件后读取，现回报 `clean=true`。
- `TEMPLATE_FILES` 44 → 53（docs 7 篇 + 一致性测试）。
- **修政策结构缺陷**：3.3.0 给 R5 加了子列表，被 `mark_rules.py` 判为 3 条未标记条款；已并回单段。验收链补 `mark_rules.py check`（改过政策必跑）。

## 3.3.1（2026-11-02）

**PATCH：既有工作区的 Python 模式不可被模板演进改写**

- R2/R8 补「既有工作区不回改」：`python.mode` 一旦配置（`uv`/`local-venv`/`mcp`），模板升级、迁移、仓库同步都不得改写；uv 默认只作用于 `unconfigured` 的新工作区。
- `scripts/pyenv_init.py` 增加护栏：已配置工作区执行 `--apply` → **exit 5 `already-configured`**，需 `--force` 才切换。
- `scripts/template_sync.py` 回报 `python_mode_preserved`（同步前后 `bootstrap.python` 必须一致）。
- skills：`workspace-init` 1.2.0（先读 mode，已配置则跳过提问/重建）、`template-update` 3.2.1（Python 运行时不动）、`workspace-repo-sync` 1.1.1（不改 mode，登记按主机分列）。

## 3.3.0（2026-11-02）

**MINOR：Python 运行时增加 uv 通道（默认推荐）**

- §0 首次启动询问改为三选一：**uv（默认）/ 手动 venv / MCP**；未表态按 uv，uv 不可用时再回退并由用户选，禁止静默降级。
- §0.1 更名为「创建本地版本化 venv（uv 默认 / 手动）」，R3–R8 覆盖两种创建方式：`uv venv .venv-312 --python 3.12` 与 `<python> -m venv .venv-312`；**uv 不存在时只给安装方式、由用户执行**，不自动联网安装。
- `bootstrap.python.mode` 新增取值 `uv`；记录 `runtime.uv` 路径与版本。
- 新增机械入口 `scripts/pyenv_init.py`（`--mode uv|venv|mcp`，默认 dry-run；`--apply` 才建 venv/写 bootstrap；uv 缺失 exit 3）。
- `workspace_preflight.py` 支持 `uv` 模式（校验 venv 存在 + uv 可用 + 版本一致）。
- `workspace-init` skill 第 5 步改为三模式并给出 `pyenv_init.py` 命令。
- 新增回归 `tests/test_pyenv_init.py`（5 例，含 uv 缺失与 dry-run 不落盘）；`TEMPLATE_FILES` 42 → 44。

## skills 完备性收敛（2026-11-02，不改政策版本）

**来源**：GLM/SOL 双主写辩论 + DS/主理人裁判；判据为「触发/前置/契约/边界/可执行/失败回滚/验收/维护」8 维。

- 新增契约模板 `.workspace/skills/_TEMPLATE.md`：8 维骨架 + frontmatter 规范，新 skill 照此落盘。
- `scripts/skill_audit.py`：新增契约节检查（缺节即 finding）、REGISTRY **名称集合**比对（不只比数量）、`--sync-registry`（按目录重写入口表与计数）；**exit code 改为 `findings>0 → 1`**（原先非 strict 恒 0，脚本无法 gate）。
- 修实际缺陷：
  - `workspace-init`：条款标记检测由 `head -20` 改为全文件计数（原写法对现行模板**必然**误判"无标记"→ 误走迁移路径）；备份清单改为与替换清单同源（原备份 ⊂ 替换，回滚不完整）；登记核验按目录派生；补全新实例化触发/边界/前置/变更记录。
  - `git-init`：顺序改为 init → .gitignore → 登记 → **审查暂存清单** → 提交 → 核对提交树；补失败与回滚（含撤销建仓）、验收命令。
  - `template-update`：备份/回滚改为 `MANIFEST.txt` 驱动（含"删除本次新增文件"）；更正 PATCH「静默」的含义（不逐项确认 ≠ 不报告）。
  - `workspace-repo-sync`：无 `.git` 时不做 git 步骤；补同步前 HEAD 锚点与冲突回滚（merge/rebase abort、reset --hard 前置条件）；复验表逐条给期望输出；补前置与验收。
  - `delivery-closure`：`git status` 为空时改用文件时间戳列举（原逻辑在无仓工作区静默漏盘）；第 5 步补 `docs` 改动行；补「完成 / 已交付带限制 / 阻塞」三档口径与变更记录。
  - `docs-retrieval-maintenance`：补前置；「停用 / 删除 / 重指」三档拆分（消除「90 天无命中即删」与「删除需人工」的矛盾）；系数变更加快照回滚。
- 新增回归 `tests/test_skill_audit.py`（5 例）；`TEMPLATE_FILES` 40 → 42。

## 3.2.0（2026-11-02）

**MINOR：skills 收敛**

- **删除 `workspace-git-branch-upgrade` skill**：与 `template-update` 职责重叠（模板同步已支持按 ref 取用分支/标签/提交），模板只保留 `template-update`。
- **R18 改写**：§0.3 由「三个 skill」改为「两个 skill」（`workspace-init`、`git-init`），并指向 `template-update` 处理模板层同步。
- `TEMPLATE_FILES` 相应移除该 skill；`skills/REGISTRY.md`、`TOOLS.md` 同步去条目。
- `template-update` **3.1.0**：新增 `--prune`，用于删除上游已移除的模板层文件（先备份；默认只报告）。
- 迁移提示：已同步过旧版本的工作区需 `template_sync.py --prune` 才会真正删除本地副本。

## 3.1.0（2026-11-02）

**MINOR：tokens 消耗优化（义务不变）**。基线用 `cl100k_base` 实测（DeepSeek 分词器为代理口径）。

- `AGENT_RULES.md` 15,181 → 14,392 字符（−5.2%）；`cl100k_base` tokens **10,179 → 9,708（−4.6%）**；条款文本总量同步下降。
- 手法：把 R98/R100/R104/R105 里的**机械实现细节**（账本落盘、CC 转录回放、委托记录格式与命令）迁到 `docs/hooks-catalog.md` §2.7，政策只留义务句 + 指针；判据、拒绝码、边界声明一律留在必读文件。
- **不做**：任何形式的读后驱逐、摘要替代、读证明工具（用户裁定 R24 常驻，违反即 MAJOR 削弱）。
- 副作用：`docs/hooks-catalog.md` 升为 3.1.0 口径、hook 脚本数 6 → 7（含 `delegations`）；插件 **1.9.0**。
- 说明：MAJOR（3.0.0）与本次 MINOR 在同一分支连续落地，`verify_rules` 对两者的校验口径相同。

## 3.0.0（2026-11-02）

**MAJOR：子 Agent 授权语义变更**（用户拍板：规则由主 Agent 常驻执行，子 Agent 只做操作辅助）。

- **R24 重写**：主 Agent 每任务完整读取 `AGENT_RULES.md` 与存在的 custom，且全文**全生命周期常驻上下文**（不得驱逐、不得以摘要/读证明替代）；**子 Agent 不重复读取全量政策**。
- **新增 R108「子 Agent 任务书」**：父 Agent 派发前必须给出目标/验收、可写目标集合（逐项绝对路径、禁通配）、只读范围、禁止动作、授权来源，并签发委托记录；集合不得超出父自身授权、不得含治理文件与 R67 中央文件。
- **R98 扩展「子 Agent 写授权」**：子写按序判定「目标精确命中有效委托 → 父会话对当前修订仍有完整读证据」；**子 Agent 自读不构成写授权**；越界一律拦；委托过期/撤销/gate 修订变化/父证据失效即失效。
- **R102 对齐**：改为「账本不继承父读证据；仅经有效委托引用父证据」，消除与 R98 的规范冲突。
- **实现**：新增 `.workspace/hooks/delegations.py`（`mint` / `check` / `revoke`；父无证据拒发、目标必须绝对路径且禁通配、TTL 默认 1800s、绑定 gate 指纹）；`gate_core.evaluate_pre_write` 与 Hermes 插件 `_pre_tool_call` 在 `no-read` 前先查有效委托，命中落 `delegation-allow` 审计。
- **迁移（MAJOR 门槛）**：`verify_rules` 只校验条款 ID 存在、**不检测同 ID 改义**；派生工作区必须 grep `overrides: R24|R98|R102` 逐条人工比对语义并取得用户确认（R92 custom 优先会把旧语义悄悄压回来）。
- 验证：新增 `hooks-tests/test_delegation.py`（7 例）；9 套全量回归与 selfcheck 见本次提交。

## 2.10.0（2026-11-02）

- **MINOR 段：机制外移 + 入口瘦身**（承接 2.9.1 的 PATCH 段）。
  - §12/§13 的**实现细节**移入 `docs/hooks-catalog.md` §2.6（拒绝码表、13 类审计事件、Codex 静态可判写清单、hook 脚本清单、边界审计条目规则）；政策保留义务句与指针。
  - §0/§4/§9 的同义压缩（只删重复限定，不删义务与命令）。
  - `HERMES.md` 1,363 → **986 字符（−27.7%）**：只留定位、子 Agent 传参、记忆边界、`--ignore-rules` 例外与人格归属。
  - 新增边界句（R100）：「实现文档只陈述机械事实，不得出现义务句；授权/禁止/例外/判定口径只在本文件，冲突以本文件为准。」
- **效果（实测字符）**：`AGENT_RULES.md` 16,545 → **14,359（−13.2%）**；`HERMES.md` −27.7%。合计每会话约 **−1.4k tokens**（含 2.9.1 的 −1.5k 字符）。
- **与方案的偏差（如实记录）**：方案估 −45%，实际 −13.2%。原因是三席底线「删掉后只读必读文件的 Agent 是否得出相同决策」——§0 的命令、§4 的登记义务、§9 的完成判据都属于「删了就会改变决策」的部分，不能下沉；能下沉的只有字段/枚举/历史旁注，量比预估小。
- 同步：`docs/hooks-catalog.md` 升到 2.10.0 口径（去「待实测」、补 `gate-deny`、加「本文是实现说明、不构成政策」抬头）；catalog 不在 `TEMPLATE_FILES`，其正确性靠人工同步与本文档记录，不靠 `verify_rules`。
- 验证：107 个条款标记完好；`verify_rules` clean；94 例测试与 selfcheck 全绿；deepseek-flash 行为回归见 `mc-261030/POLICY-TOKENS-REGRESSION.md`。

## 2.9.1（2026-11-02）

- **PATCH 段：删历史旁注 + 同义压缩**（无判定语义变化、R 号零变动、custom 引用不受影响）。
  - 20 条改写：R18/R39/R41/R42/R61/R63/R67/R72/R73/R81/R90/R93/R96/R98/R100/R102/R103/R104/R105/R107。
  - `AGENT_RULES.md` 16,545 → **15,013 字符（−9.3%，≈−0.9k tokens/会话）**。
  - 保留的全部是义务句；删掉的是版本旁注、重复限定语与举例性解释（细节见 `mc-261030/apply_patch_compress.py` 的逐条映射）。
- 验证：107 个条款标记完好；`mark_rules check` 通过；registry 重建后 `verify_rules` clean；94 例测试与 selfcheck 全绿。

## 2.9.0（2026-11-02）

- **新增 skill `workspace-git-branch-upgrade`**（`.workspace/skills/workspace-git-branch-upgrade/SKILL.md`）：把已有仓库从 `main` 迁到 `dev`/目标分支，覆盖前置检查（remote/忽略/子模块/仓库类型）、三态迁移（新建 `switch -c`、远端跟踪 `--track`、仅落后 `merge --ff-only`；**禁止 `switch -C` 重置**）、默认分支指针（只改本地 `origin/HEAD`，不改服务端）、模板校验三条判据（exit 0 + `clean` + `template_clean`）、记录回写（`default_branch` 是记录不是当前分支）、验证清单与双态回滚。
- **方案来源**：GLM-5.3 主笔 → deepseek-v4-pro 与 gpt-6-sol 交叉评审 → 主理人裁决；两席共提 13 处修正（其中 DS 的「TEMPLATE_FILES=20 项」被判错，实测 28 项）。
- **登记**：`TEMPLATE_FILES` 28 → 29（`verify_rules.py`）；`skills/REGISTRY.md` 与 `TOOLS.md` §4.3 各补一条；R18 指向新 skill。
- 政策 2.8.5 → **2.9.0**（MINOR：新增能力，无判定语义变化）。

## 2.8.5（2026-11-02）

- **`gate-deny` 审计事件**：拒绝本身进审计（`tool` / `tool_use_id` / `targets` / 原因摘要），作用域键含 `.agent_id`，`Stop`/`SubagentStop`/`SessionEnd` flush 到 `audit-<client>.jsonl`。此前拒绝只存在于客户端转录，无法独立核验。`test_cc_wiring` 增 1 例（6 例）。
- **`scoped_id` 幂等**：客户端把 agent 折进 `session_id` 时不再二次拼接（实测出现过 `cc-<sid>.<agent>.<agent>.json`）。
- **活体拒绝腿压测（CC 2.1.87-dev，21 次）**：子 agent `tools: Write`（结构上无读工具）→ **10/10** 被拒且文件未落盘（子转录工具集仅 `Write`，审计有 `gate-deny`）；`tools: [Write, Read]` 对照 **5/5** 放行；父读后子冷写 **3/3** 仍拒（账户隔离）；`Bash` 写治理文件 **3/3 未拦截**（已声明的盲区，记 gap 不算 PASS）。带 `Bash` 的 `[Write, Bash]` 白名单未被客户端应用，该变体作废。
- **口径**：这是「结构性剥读下的 fail-closed 边界测试」，不等于正常配置能兜住对抗性写入。Codex 子 agent 活体拒绝腿仍 NOT-OBSERVED（子 agent 两次都回「没有具体任务」）；Hermes 的拒绝腿属提示词服从型，不得与 CC 等价。
- 政策 2.8.4 → 2.8.5。

## 2.8.4（2026-11-01）

- **CC 子 agent 转录归因接通**：`SubagentStop` 现在按 payload 的 `agent_transcript_path` 单独回放子转录（作用域键含 `.agent_id`，与主会话账本互不覆盖）。`cc_transcript.validate_path` 接受子 agent 形状 `<slug>/<session_id>/subagents/agent-<agent_id>.jsonl`（父会话不匹配仍拒）。新增 `test_cc_transcript` 用例 1 例（33）。
- **确定性验证（不依赖模型是否听话）**：
  - `verify_subagent_attribution.py`：构造「子 agent 转录里有 Bash 写 gate 文件」的样本 → 真实 `hook.py subagent_stop` → 审计产出 `cc-bash-write{would_deny=true}`，子作用域账本成立 → **PASS**。
  - `verify_codex_subagent_scope.py`：父会话读 gate 后，子 agent（`agent_id` 不同）写 → **deny（父读不传子）**；子自己读后写 → 放行 → **PASS**。
- **如实披露**：两次**活体**尝试（CC 子 agent 执行 Bash 写、Codex 子 agent 未读直接写）模型都没有照指令做（CC 子 agent 只读文件、Codex 子 agent 回「没有任务」），因此「真模型回合里的子 agent 拒绝腿」仍记为 **NOT-OBSERVED**，上述结论只覆盖确定性路径。
- 政策 2.8.3 → 2.8.4（R104 补子 agent 转录回放口径）。

## 2.8.3（2026-11-01）

- **插件 v1.8.0：与客户端 hooks 记账对齐**。放行写时落 `intents{target, base_sha}`；`post_tool_call` 处理写工具后落 `commits` 并追加工作区级 `commits.jsonl`（`client="hermes"`，字段与 hooks 同构）；无 intent 的写落 `unobserved{reason="no-intent"}`。`selfcheck` 增 P13-1..5。此前 Hermes 侧只记「读过没有」，无法与 CC/Codex 的 intent/commit 对账。
- **新增 `docs/hooks-catalog.md`**：三 harness 事件矩阵（何时触发 / 为何必须 / 缺了会怎样）、新 harness 接入 7 步与三条验收断言、6 条已知坑。经 LUNA + GLM 逐条对照 `file:line` 核验。
- **Codex 子 agent 排查结论**：「workspace Python mode is unconfigured」**不是 Codex 运行时错误**，而是子 agent 读到 §0 首次启动协议后按规则要求用户选择 Python 模式；子 agent 没有用户通道 → 反复空转、零工具调用。修复：① 子 agent 沿用父会话已记录模式，父也未配置则报告阻塞并停止（R2 补充）；② 压测夹具先把 `python.mode` 配置好再测子 agent。
- **政策 2.8.2 → 2.8.3**：R2 补「子 Agent 无用户通道时不得反复追问」；R98 补插件三值记账口径。

## 2.8.2（2026-10-31）

- **拒绝可观测（插件 v1.7.0）**：`_block()` 统一加 `[workspace-gate:<code>]` 机器可读前缀（`no-session` / `container-unmapped` / `no-read` / `unresolved-target` / `internal-error`），并落一条 best-effort 审计（`BLOCK code=… session=… tool=… target=…`）。**只加可观测性，不动判定**：`action` 仍为 `block`，消息仍指示先读哪份文件；审计写失败不影响 block。`selfcheck` 增 P12-1..3。
- **拒绝腿（客户端派发）实测**：新增 `deny_leg_probe.py` 走真实分派链 `model_tools._pre_dispatch_guards(skip=False)` → `_dispatch_pre_tool_call_hooks` → 插件 block：未读写 → `error_type=plugin_block`、目标文件未创建；真实读一次后同调用放行 → **PASS**。这是「客户端尊重 block 且不执行工具」的首个确定性证据（不含 LLM）。
- **真模型回合的自然拒绝：未观测到（如实记录）**。预登记探针（`DENY-PRE-OVERSIZE.md`，N=5，>100KB gate 文件迫使分页）**5/5 次模型仍完整读**后合规放行 → K=0，不追加轮次、不换提示词，也不把它写成命中率。
- **同轮验证「不影响其他 harness」**：CC/Codex 不加载本插件；其既有控制运行复跑（`test_cc_wiring` 5、`test_cc_transcript` 32 等九套 92 例全绿）、Codex 真机 `apply_patch` 写 smoke 通过、`verify_rules` clean。
- **四席研判收敛**（GLM / LUNA / deepseek-v4-pro / 主理人）：O2（无模型真实分派）为主证 + O5（机器码与审计）为辅证；O1（toolset 去读工具）经实测不可行（`-t write_file` → `Unknown toolsets`，`file` 组打包读写）；超长文件夹具降级为「预登记、全量披露的存在性探针」。三方一致要求：不得把插件层结论写成客户端层。

## 2.8.1（2026-10-30）

- **Codex 读证据被 shell 包装击穿（D3）修复**：Codex 每条 Bash 都是 `/usr/bin/zsh -lc "…"`，读识别只认裸 `cat`/`sed -n a,bp` → 读证据永远是 0，模型只能自铸令牌或停摆（真机压测复现：读后写仍 deny）。新增 `hook._strip_shell_wrap()`：整串字面解析**恰 3 词**、`exe ∈ {bash, sh, zsh}`、`flag ∈ {-c, -lc}`、递归 ≤3；读识别与 `_bash_patch` 共用。剥壳失败（含 `$`/反引号/反斜杠/引号不闭合）一律原样返回 → 不授证。新增 `test_bash_dispatch` W11/W12/W13（wrapped cat 授证、wrapped 越界不授证、含展开不剥）。
- **Hermes 插件生命周期（v1.5.0 → v1.6.0）**：注册 `on_session_start` / `subagent_start` / `subagent_stop`；主会话与子 agent 各按自身 `session_id` 建账（`agents` 映射 + `agent_events` 有界缓冲），子 agent **不继承**父会话读证据。`selfcheck` 新增 P11（父子登记、agent-start/stop、父读后子写仍 BLOCK）。
- **压测（同工作区并发）**：
  - Hermes 插件层 2 主 × 2 子：reader 子允许、未读子 BLOCK；父账本登记 2 个子身份，`agent-start/stop` 成对。
  - Codex 2 会话并发（修复后）：读后写 allow + `gate-intent`/`gate-commit`，未读写 deny；两者互不影响。
  - **Hermes 客户端层（重启后）部分成立**：根因是陈旧的 `.hermes/plugins/workspace-gate/__pycache__/__init__.cpython-311.pyc`（10-22 生成）被加载，日志一直打 `v1.4.3`；清掉 `__pycache__` 并重启 gateway 后加载 **v1.6.0**。2 主 × 各 2 子并发：父账本登记子身份、`agent-start/stop` 成对、子读证据互不继承。**客户端级 deny 腿仍未验证**（未读子由模型自行拒绝写入，不是 hook 拦截）。
- **仍未闭合**：CC 子 agent 的 Bash 写只有会话级转录归因；Hermes 插件不记录 worktree 基线/未观测写（与客户端 hooks 的 `unobserved` 不对等）；hook 未送达仍为客户端 fail-open。政策 2.8.0 → 2.8.1。

## 2.8.0（2026-10-30）

- **agent 作用域账本（P1）**：状态键 = `session_id`，子 agent 再拼 `.agent_id`（`gate_core.scoped_id/split_scoped_id`，`hook.py::_session` 从 payload 取 `agent_id`）。CC 主线程与子 agent 共用同一 `session_id`（实测 dump：`SessionStart` 无 `agent_id`、`SubagentStart`/子调用带 `agent_id`），因此**父会话的读证据不再授予子 agent**。
- **子 agent 生命周期**：`.claude/settings.json` 与 `.codex/hooks.json` 增订 `SubagentStart` / `SubagentStop` → `hook.py subagent_start/subagent_stop` 落 `agent-start` / `agent-stop`，子 agent 状态在 SubagentStop 时冲刷进审计。
- **intent / commit 两阶段**：allow 时落 `gate-intent{target, base_sha}`，写后 `evaluate_post_write` 落 `gate-commit{target, new_sha}`（sha 与磁盘一致才算）；无对应 intent 的写落 `unobserved-write{reason, detected_by}`，**不表示通过**。工作区级 `commits.jsonl` 记录 commit（按 sha 精确匹配；轮转时按 target 压缩保留最新，不丢历史判定所需的最新记录）。
- **worktree 差集按会话基线**：`SessionStart` 记 `worktree_baseline`，`Stop/SessionEnd` 只报相对基线变化；跨会话/跨 agent 的合法写通过全局 commit 账本排除，避免把别人的写误报成自己的 `unobserved`。
- **审计事件 7 → 12 型**：新增 `gate-intent` / `gate-commit` / `unobserved-write` / `agent-start` / `agent-stop`。政策 2.7.0 → 2.8.0（§13 R102 身份粒度、R105 事件与对账口径）。
- **回归**：新增 `hooks-tests/test_agent_scope.py`（8 例：作用域键、父子隔离双向、intent 的 base_sha、commit 的 new_sha、无意图写落 unobserved、worktree 差集、hook 子进程按 agent 落状态）；九套合计 **89 例** 全绿，`verify_rules` clean，`selfcheck` 全绿。
- **压测（CC 侧）**：同工作区 2 主会话 × 2 子 agent 并发——子 A（读后写）allow + intent/commit；子 B（未读）**deny**；`agent-start/stop` 各 4 条；主会话 worktree 审计不再把子写误报为 `unobserved`；另以 Bash 旁路写 `rogue.txt` 产出真实 `unobserved-write`。
- **未完成/边界**：Codex 子 agent payload 与压测受本地代理网络故障阻塞（仅确认二进制含 `SubagentStart`）；CC 子 agent 的 **Bash 写**仍只有会话级转录归因（`agent_transcript_path` 未回放）；Hermes 插件未接 `on_session_start`/`subagent_start`；hook 未送达仍为客户端 fail-open。


## 2.7.0（2026-10-30）

- **空 `session_id` 改为 fail-closed（行为变更）**：Hermes 项目插件原先在 `session_id` 为空时**跳过守门**（日志 `trust boundary`）。多 Agent/多 harness 并发测试证实这条通道会被用作无身份写入的口子（CC 主线程与子 agent 共用同一 `session_id`、Hermes persistence-disabled fork 与父共享 id、身份本身是客户端自报），因此改为：**写工具 + 目标落在工作区内（或相对目标无法解析 / 目标缺失）→ 一律 deny**；非写工具与工作区外绝对路径写入不受影响。插件 `1.4.3 → 1.5.0`（`plugin.yaml` 与激活日志同步）。
- **政策 §12 R98 补边界句**：会话身份缺失时不放行，写入工作区文件一律拦截（fail-closed），非写工具与工作区外写入不受影响；R99 措辞同步为「被拦截/放行时义务不变」。政策 `2.6.1 → 2.7.0`。
- **回归**：`selfcheck.py` 旧用例 `A3 empty session_id -> no gate` 改判为 **BLOCK**，新增 `P10-1a..1e`（空 id + 工作区写 / patch / 未知相对路径 → BLOCK；空 id + `read_file` / 工作区外绝对路径 → 放行）。全部通过。
- **文档**：`docs/hooks-guide.md` §3.2 与 §6 真值表、`hooks-tests/README.md`、两份 README 徽章同步到 2.7.0。
- **未包含（仍为提案）**：agent 粒度账本（`(harness, session_id, agent_id?)`）、`intent/commit` 两阶段与 `unobserved` 三值记账——见工作区 `mc-261030/SESSION-ID-DECISION.md`，待单独排期。

## 2.6.1（2026-10-30）

- **文档终审修订（`deepseek-v4-pro` + `glm-5.3` 两席、三轮研判，逐条回代码核对）**：`docs/hooks-guide.md` 修正 9 处——① §2.1 明确 gate 清单**按 harness 分流且互不联动**（Codex/CC 读 `.workspace/gate.custom.json`；Hermes 读 `plugin.custom.json`）；② §2.2 补「结构随 harness 不同、判据相同」（`gate_core` 的 `{anchor,fingerprint,fingerprint_source,coverage,complete}` vs Hermes 的 `read`+`coverage`+`coverage_rev`）；③ §2.2 第 3 点拆清「`sed` 严格区间越界 → `request-past-eof`」与「`read_file` 越界被夹到末尾」；④ §2.5 `unresolved-command` 补 `client`；⑤ §3.1 日志行示例改 `v1.4.3`；⑥ §3.2 补「空 `session_id` 直接跳过守门（trust boundary）」、`pre_llm_call` 按 epoch 提醒、状态文件名消毒；⑦ §4.2 补 `bash -c`/`sh -lc` **最多剥 8 层**、无引号 heredoc 拒 `$`/反引号/**反斜杠**；⑧ §4.3 标明「原生 patch 无 `PostToolUse`」为实测（`ALPHA-HOOK.md` §3 第 6 条）；⑨ §6 表与 Q4 改「Codex 实时拦截 + Post 记账」「包装类已读后直接放行且**无审计事件**」。
- **政策事实修正**：§13 引言「五脚本」→ **六脚本**（补 `cc_transcript`）；R105 的 `unresolved-command` 字段补 `client`（与 `hook.py` 实际落盘一致）。政策 2.6.0 → 2.6.1。
- **配套同步**：`ALPHA-HOOK.md` §3 第 1/2 条改口径（CC Bash 可经转录回放事后归因、包装类已读后无审计）；`hooks-tests/README.md` 政策版本与脚本计数；两份 README 徽章。
- 三轮研判记录见工作区 `s5-261022/DOC-R{1,2,3}-*.md` 与 `DOC-REVIEW-CONVERGENCE.md`。回归 80 例全绿，`verify_rules` clean。

## 2.6.0（2026-10-30，已提交）

- **文档：新增 [`docs/hooks-guide.md`](docs/hooks-guide.md)**（README / README_EN 中链接）——面向小白的三分钟比喻（门禁卡 + 借阅登记）＋ 与 harness 无关的共同机制（治理文件、`c<size>:sha256` 内容指纹、连续覆盖区间、EOF 哨兵、一次性令牌、七型审计事件）＋ **三种 harness 逐项实现拆解**（Hermes 项目插件的 `pre/post_tool_call`；Codex CLI 的事件接线、Bash 分派与 `apply_patch` 可读 deny、fail-open 边界；Claude Code 的写前拦截 + `Stop`/`SessionEnd` 转录回放归因，含跨 Stop 账本、写后消费、同轮二次写三个坑）＋ 真值表 ＋ 排查 FAQ ＋ 接入新 harness 的六步清单 ＋ 术语表。
- **README / README_EN 事实对齐**：政策徽章 `v2.2.0` → `v2.6.0`；CC Bash 覆盖真值表由「**未覆盖、无审计**」改为「**不拦**；`Stop`/`SessionEnd` 转录回放事后归因（`cc-bash-write` / `cc-bash-gap`）」，并同步声明三点限制（`would_deny` 不等于拦截、转录可被同 uid 删改、截断/短读不授证）；`README_EN` 补齐缺失的「Client Governance Hooks」章节；目录树补 `docs/`。
- **死代码清理**：删除 `cc_transcript.py::load_offset()`（无任何调用点，`grep` 零命中）；`hooks-tests/README.md` 按现状重写计数（八套 4/18/9/3/5/4/32/5 = 80 例、`TEMPLATE_FILES` 28 项、审计七型、CC Bash 归因口径）。
- 回归 80 例全绿，`verify_rules --workspace .` clean。

- **对抗复核修复（`1ec4ce1` 之后的 `dev` 修补）**：① 按 `gate.custom.json.gate_files` 逐 gate 记账（不再写死 `AGENT_RULES.md`）；② `is_error` 的 Read 不授证；③ `tool_result` 缺失不再冒充 `result=ok`（新增 `unknown` 并进 pending，跨 `Stop` 补齐后再算）；④ 超限转录不再丢弃全部记录（截断到上限 + `transcript-truncated`）；⑤ 转录路径 `realpath` 校验（挡 projects 内 symlink 逃逸）＋转录自带 `cwd` 与会话 cwd 比对（挡 slug 碰撞误读）；⑥ 读证据参考修订优先取「写前整文件读」，避免「先读全再写」被误判；⑦ `sidechain` 标记入事件。回归 69 例。
- **CC Bash 非订阅归因（新能力，不改变拦截面）**：新增 `.workspace/hooks/cc_transcript.py` —— CC 仍**不订阅** Bash hook，但在 `Stop` / `SessionEnd` 用平台 payload 里的 `transcript_path` 做**转录回放**：解析 `Bash.tool_use.input.command`，用 `wtarget` 分类静态可判目标，用转录里的 `Read.tool_use + tool_result` 重建读证据，复算出 `would_deny`，并与 `git status` 差集交叉。新增审计事件 `cc-bash-write`（digest / targets / class / would_deny / read_state / result）与 `cc-bash-gap`（`unresolved-command` / `no-transcript-path` / `transcript-*` / `changed-but-untargeted` / `transcript-too-large`）。
- **只读边界**：仅读 payload 精确给出的转录路径，先校验 project slug == cwd 拉平值、文件名 == session_id，否则记 gap 并拒读；**不遍历 `~/.claude/projects/**`**（本机实测该目录含 22 个项目、104 MB 转录，含其他私人项目）。偏移按会话 sidecar 增量保存（`cc-transcript-<session>.json`）。单文件 > 8 MB 或 > 20000 条 tool_use 记 `transcript-too-large`，不做无限解析。
- **读证据只认可校验内容**：CC 的 Read 结果会附带 `<system-reminder>` 尾注、超长时截断；剥掉尾注后必须与治理文件对应行逐行相等才授证，末尾出现 `... [truncated]`、短读未到 EOF、内容与磁盘不符一律不授证（否则「截断读」会被当成「读全」）。
- **语义**：事后归因，**不拦截**；`would_deny=true` 表示「按当时证据本该 deny」，不表示写入被阻止；转录可被同 uid 删改，故只是审计复算。命令原文默认不落盘（只记 digest），需 `.workspace/gate.custom.json` 的 `audit_command_text=true` 显式开启。
- **政策**：§13 R104 改写为「不订阅、不拦截，但可经转录回放做命令级事后归因」；R105 审计事件五型 → 七型。政策 2.5.2 → 2.6.0。`TEMPLATE_FILES` 27 → 28。
- **测试**：新增 `hooks-tests/test_cc_transcript.py`（12 例：路径三重校验、增量与半行容错、would_deny 矩阵、**截断/注入/内容不符不授证**、原文开关、差集缺口、偏移幂等）与 `hooks-tests/test_cc_wiring.py`（3 例：真实启动 hook.py 的 cc stop/session_end、无转录 gap、跨事件偏移幂等）。八套合计 69 例。
- **收口修复（`c14f5d4` 之后的 `dev` 修补，两席独立研判一致）**：① **跨 Stop 读证据持久化**——`analyze()` 原先每轮重建 `books`、sidecar 只存 `offset/stats/pending`，而 CC 的 `Stop` 每轮触发，于是「Stop 轮 1 读全 gate → 轮 2 写同一 gate」会复算出 `read_state=missing` / `would_deny=true`（假阳性）。现 sidecar 增加 `books`（`{gate 相对路径: {coverage, revision, total, sha}}`，旧 sidecar 无该键按空字典迁移），`analyze(..., books=...)` 以它为本轮基线、`hook.py` 读出后传入并写回；**修订变化即撤销**：`sha` 是忽略文末单个 LF 的内容指纹，指纹变化（或行数变化）该 gate 的 coverage 清空后按本轮重算，旧修订的读账不得贴到新修订上。② **无 cwd 转录 fail-closed**——整条转录一条 `cwd` 都没有时无法排除 slug 碰撞（`/a-b/c` 与 `/a/b-c`），记 `cc-bash-gap(transcript-cwd-missing)` 并**跳过本轮复算**（`pending={}`），`offset` 仍前进，避免同一批记录每轮重放。③ 清理：删除 `hook.py` 中死代码 `load_offset` 调用；`cc_transcript.py` 两处未关闭的 `open(...).read()` 改为 `with`（消 ResourceWarning）。回归：`test_cc_transcript` 22 → 25（`test_read_survives_across_stops` / `test_revision_change_revokes_across_stops` / `test_missing_cwd_is_gap_not_analyzed`），`test_cc_wiring` 4 → 5（`test_read_survives_across_stops` 走真实 `hook.py` 两轮 `stop`）；八套合计 69 → 73 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`hook.py` / `cc_transcript.py`）。
- **F3 修（真机 CC 复现，`VERIFY-UNVERIFIED-261030.md` §1.1）**：跨 `Stop` 的 gate 写会**自我失效**——`analyze()` 建 books 时若本轮没有该 gate 的读，就拿**磁盘当前内容**当参考修订，而写命令刚刚改过磁盘 ⇒ 指纹与持久化账本（写前）不符 ⇒ coverage 被清空 ⇒ `read_state=missing` / `would_deny=true`（假阳性：会话 A「同轮读+写」通过、会话 B「轮 1 读 / 轮 2 写」失败）。现改为**先预扫本轮 Bash 的静态写目标**（`wtarget.classify`）得到 `written_gates`，再建 books，参考修订三选一：① 本轮有该 gate 的整文件写前读 → `transcript`；② 本轮**被写且无读** → `persisted`（账本里的 sha/total/coverage，**不重新哈希磁盘**，total 与覆盖区间同源故原样继承，不 `_carry_coverage`）；③ 本轮既未读也未写 → `disk`（外部改动检测）。账本里没有该 gate（首轮就写）仍走磁盘、coverage 空 ⇒ deny。事件 `revision` 如实输出三值，`persisted` 已写入模块与 `analyze()` docstring。**能力边界**：外部改动与会话写落在同一轮时转录里没有可区分信号，一律按账本放行；gate 文件的外部改动由 `Stop`/`SessionEnd` 的 `unsanctioned-gatefile-change`（文件级指纹锚）兜底，不由本复算负责。回归：`test_cc_transcript` 25 → 28——`test_read_survives_across_stops` 改为「Stop#2 的写真的改磁盘」并断言 `would_deny=false`/`read_state=complete`/`revision=persisted`/账本仍锚写前修订；新增 `test_write_without_prior_read_across_stops`（首轮只写无读 ⇒ 两轮均 deny/missing）、`test_read_and_write_same_round_still_allows`（同轮读+写 ⇒ `transcript`）、`test_external_change_in_write_round_uses_ledger`（固化上述边界）；`test_revision_change_revokes_across_stops` 改为「该轮**不写** gate」的可区分形态（原形态与新语义互斥：同轮外部改动无法归因），撤销后仍断言一次真实 deny。八套合计 73 → 76 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`cc_transcript.py`）。
- **F3b 修（写即消费，主席探针 `chair_f3_probe.py` R3 复现）**：F3 之后「轮 1 读全 → 轮 2 写」已放行，但**写那一轮没有消费许可**——账本仍锚**写前** sha 与完整 coverage，于是之后每轮「被写且无读」都继续以该账本为参考 ⇒ `would_deny` 永为 false（写完不重读仍 allow，真机假阴性）。现按 §12 R98「写治理文件即消费许可」改写账本：① 该轮某 gate **被写**时，写事件本身仍按**写前**账本判定（`revision=persisted`，F3 修复不变）；② 该轮结束时把该 gate 的账本改写为**写后修订**——`sha` = 当前磁盘内容指纹、`total` = 当前行数、`coverage = []`（证据已消费），账本 `revision` 新增第四值 `post-write`；③ **顺序敏感**：只有**最后一次写之后**的完整读能把 coverage 重建回来（同一轮「写 → 读」的读证据属于写后修订；写中间的读属于已不存在的中间修订，后续再有写即作废）；④ 下一轮「未读直接写」⇒ 账本 coverage 空 ⇒ `would_deny=true` / `read_state=missing`，重读后恢复 allow。**已知边界**：`interpreter_wrapper` / `unresolved` 形态的写目标不可静态确定，不消费任何 gate 账本（否则一次无关的 `python3 script.py` 会消耗全部读许可）；同一轮内的第二次写仍按该轮参考判定，消费只发生在轮末。回归：`test_cc_transcript` 28 → 30——新增 `test_gate_write_consumes_evidence`（R1 读全 → R2 写 false/complete → R3 写 **true/missing** → R4 重读 → R5 写 false/complete）与 `test_read_after_write_same_round_rebuilds_coverage`（同轮写后重读重建 coverage）；`test_read_survives_across_stops` / `test_external_change_in_write_round_uses_ledger` 的**边界语义不变**（该轮仍按账本放行、靠 `unsanctioned-gatefile-change` 兜底），但末尾断言改为「写后账本 = 写后 sha + coverage 空」。八套合计 76 → 78 例全绿，`verify_rules` clean，`TEMPLATE_FILES` 指纹同步（`cc_transcript.py`）。
- **F4 修（同一轮第二次写仍 allow，真机 CC 转录 `211c332c` @ `/tmp/cc-e2e-stops4` 复现）**：F3b 只在**轮末**清 coverage，于是同一轮内「读一次 → 连发两条 `printf >> gate`」的第二条仍看到读时的完整 coverage，两条都记 `would_deny=false / read_state=complete / revision=transcript`（真机假阴性，R98 要求第一条写即消费许可）。现改为**每处理完一条命中 gate 的写步骤就立即消费**（`_consume_book()`：`coverage=[]`、`revision=post-write`、`consumed=True`，`raw/total` 置空——本轮磁盘存的是**最后一次**写的结果，不能当中间修订），该写自身仍按**写前**证据判定（第一写 allow 不变）；之后同轮的写 ⇒ `read_state=missing / would_deny=true`。**写之后的读仍可重建 coverage**：被消费的账本遇到 `offset==1` 且未给 `limit` 的整文件读时，以该读内容为新的参考修订（`_adopt_post_write_revision()`；中间修订无权威副本可对照，是本轮唯一可用证据，局部读不采用 ⇒ fail-closed deny，已入 docstring）。**轮末写后账本逻辑保留**（`sha`/`total` = 磁盘现状、`coverage=[]`、`revision=post-write`，供跨 `Stop` 使用），因此 sidecar 持久化语义与 F3b 完全一致。真机回放核对：同一转录现在给出「第一条 `false/complete/transcript`、第二条 **`true/missing/post-write`**」，其后那次 `Read POLICY2.md` 仍把 coverage 重建为 complete。回归：`test_cc_transcript` 30 → 32——新增 `test_second_write_same_round_denied`（读 + 写 + 写 ⇒ 第一条 `false/complete`、第二条 `true/missing`，并断言轮末账本仍为写后修订）、`test_read_between_writes_same_round_allows`（读 + 写 + 读 + 写 ⇒ 两条都 `false/complete`）；新增渲染辅助 `_numbered()`。八套合计 78 → 80 例全绿，`verify_rules` clean。

## 2.5.2（2026-10-22，未提交）

- **原生 payload 形态错误也走可读 deny**：`hook.py` 的 `_write_targets()` / `_absolute()` 对「patch 缺 Begin/End 外壳」「文件头不是 Update/Add/Delete File」「目标路径含首尾空格/控制字符/`~`」「`tool_input` 或 `file_path` 缺失」原先抛裸 `ValueError` → 渲染成「内部错误」；现统一改抛 `_PatchParseError`，输出可执行文案（指出缺什么、该怎么改）。与 F1 同族问题（GLM 终验 unverified 项），fail-closed 不变。`test_bash_dispatch` 新增 W10 三条回归（共 18 例；六套合计 43 例）。
- **政策 §13 R105 明确审计归因边界**：`unresolved-command` 仅落 `tool_use_id` + `digest`（命令 sha256 前 16）+ `class`，不落命令原文、不做 native tool-use 对账，因此审计自身**不可直读归因**（只能以候选命令重算 digest 比对）；需要命令级可读归因须另行实现对账。政策 2.5.1 → 2.5.2。
- **文档措辞与事实对齐（LUNA 终验提出）**：删除 `ALPHA-HOOK.md` / 本文件里「R1/R2/R3 三轮全 pass」的过度声明，改为按轮记录（R1 链未闭合、R2 deny 腿未触发、R3 pass）；`ALPHA-HOOK.md` 测试数 12 → 15（共 40 例）；重写 `hooks-tests/README.md`（删除「wtarget 未接入」「审计只有 6 字段」「W2/W3 未实现」等过期表述，按 2.5.2 现状描述）。

## 2.5.1（2026-10-22，未提交）

- **政策 §13 R105 补边界**：`worktree-change` 的文件粒度受 git 限制——会话中新出现的**未跟踪目录**在 `git status --porcelain` 中折叠为 `?? dir/`，故 `files` 只到目录级且 `diff_stat` 为空，文件级归因须由 `completion-gate`/artifact 侧补证，不得据目录级条目推断具体写入文件。政策 2.5.0 → 2.5.1。
- **修复（客户端 3 轮受管真机测试发现，F1）**：`.workspace/hooks/hook.py` 的 `_bash_patch()` 对显式 `apply_patch` 的不可解析形态（`mkdir … && apply_patch <<'PATCH'` 等 `&&`/`;`/`cd` 串联、非单字面 patch 参数、多 heredoc 终止符、会展开的 heredoc、命令缺失）原先抛裸 `ValueError`，被 `main()` 泛异常分支渲染成 `Workspace gate 内部错误；请人工检查。(ValueError)`；现改为抛 `_PatchParseError`，输出可读 deny（说明该形态目标不可静态确定、请单独调用 `apply_patch`），fail-closed 语义不变。`hooks-tests/test_bash_dispatch.py` 新增 W8/W9 两条回归：复合形态 deny 文案不含「内部错误」、且已完整读取后仍 deny 但可读。
- **F2（实现文档补记）**：`ALPHA-HOOK.md` §3 增第 9 条边界说明（与 R105 同义），§4 增「受管 3 轮」迭代行。
- 3 轮受管真机测试：被测 `deepseek-v4-flash` @ `codex-cli 0.163.0`（6 次 run，每轮独立夹具/会话）。**结论按轮记录，不作「三轮全 pass」**：三次未读写均 deny（R1-A 首写文案为「内部错误」→ F1，deny 本身成立）、读全后写 allow（R1-B/R3-A）、令牌复用 deny（R2-A）、`touch` 记 `suspect` 不撤销（R2-B）、脏夹具 `completion-gate ok=false`（R3-B）。看管席（`deepseek-v4-pro`）判 R1 链未闭合，`glm-5.3` 有条件接受，`gpt-5.6-luna` 判不可接受（F1 阻断）；F1/F2 已在 `a3c4811` 闭合。
- **C9 文案口径修正**：deny 文案的「共 N 行」用 `len(bytes.split(b"\n"))` 计数，对以 LF 结尾的治理文件比 `wc -l` 多 1（`AGENT_RULES.md`：文案 188 / `wc -l` 187），会让模型误以为「还差第 188 行」而无效重读（真机 R2 已复现）。现改为在该行追加 `wc -l` 对照：`共 N 行（wc -l = M，差 1 行为文末空行）`——**语义/判定不变**（`缺失 x..y` 与总行数仍按 `split` 口径），仅多一行对照；`gate_core.py` 新增 `_physical_lines()`，两个 deny 调用点传入 `physical`。`hooks-tests/test_bash_dispatch.py` 新增回归（在未读 gate 的 `pre_write` 上断言文案同时含 `共 ` 与 `wc -l = `，且 `wc -l` 数值等于真实文件的 `len(bytes.split(b"\n")) - 1`，按文件现算、不硬编码 187）。
- **S5 单轮完整链真机重跑记录**：R1/R2 观测不成立、R3 pass（`release_gate=true`），逐轮独立看管 + 两席研讨；详见工作区 `s5-261022/S5-REPORT.md`。

## 2.5.0（2026-10-22，未提交）

- **客户端治理 hook 首版**：新增 Codex CLI / Claude Code 写前守门——`.workspace/hooks/{hook,gate_core,wtarget,authz,template_anchor}.py` + `.codex/hooks.json` + `.claude/settings.json`；`TEMPLATE_FILES` 20 → 27（五脚本 + 两配置）。
- **行为要点**：Codex `printf >>`/`sed -i` 等静态可判写在未读时 deny（真机验证）；`apply_patch $'…'` ANSI-C 规范化；C9 deny 文案带覆盖/缺失/总行数；`SCHEMA 4→5`；W2 指纹锚 + 豁免令牌 + W4 完成声明门禁（advisory）。
- **边界**：**CC 的 Bash 写不订阅、不拦截、无归因**；W2 的 `suspect` 仅 mtime 兜底；W4 不进 deny；clone 未 pin。
- 政策 2.4.0 → 2.5.0（新增 §13，R101–R107：标题 R101 + 六条 R102–R107）。

## 2.4.3（2026-09-16，未提交）

- **workspace-gate plugin v1.4.2 → v1.4.3**：按 v1.4.2 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v142.md`）处置 V142-1/V142-2。⚠️ **尚未经新一轮独立复核。**
- **V142-1（高/发布阻塞 → 修复）**：`_find_workspace` 的工作区身份标记改为**持久的 `.workspace/bootstrap.json`**——v1.4.2 要求 `AGENT_RULES.md` 同时存在，导致「合规读取后 gate 文件被删除（外部同步/误操作/已获许可的工具调用）→ 工作区发现失效 → 后续写入**静默脱离守门**（实测 fail-open：真实分发链 `write_file` 成功创建文件）」。修复后：bootstrap 存在即工作区；gate 文件缺失由写前复核处理（指纹 None → unread → BLOCK + 撤销留档 `to=null`）；`AGENT_RULES.md` 存在但缺 bootstrap 仍保留 near-miss 日志（J2 语义不变）。v1.4.2 的「不可读 → fail-closed」从此真正覆盖「不存在」形态。
- **V142-2（低 → 修复）**：模块 docstring 两处残留的「≤256KB/大文件 stat 弱指纹」描述更正为「一律全量 SHA-256、失败即 None」；记账窗口声明同步补 V142-1 语义。
- 自检 122 → **128 项**（新增 **P9 组** 6 项，全部来自复核方指定的最低验收：合规读后删除 gate → write/patch 均 BLOCK + 撤销留档 `to=null, was_read=true`；无许可 + gate 缺失 → BLOCK；重建 gate + 完整重读 → 恢复放行）。ext4 与真 DrvFs 各 **128/128** 全绿。
- **v1.4.2 冻结哈希（已作废存档）**：`1653bb24…`/`1fc6fd5b…`/`c75373a7…`。

## 2.4.2（2026-09-16，未提交）

- **workspace-gate plugin v1.4.1 → v1.4.2**：按 v1.4.1 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v141.md`）处置 V141-1–V141-4。⚠️ **尚未经新一轮独立复核。**
- **V141-1（致命 → 修复）**：`_mark_read` 的撤销条件去掉 `was_read` 前提——**任何**已记账文件（含只有部分覆盖、`read=false` 的）遇到不同指纹都先留档撤销（`was_read` 降级为纯审计字段），再按本次证据重建。关闭「旧修订部分覆盖 + 新修订部分覆盖拼成全文」的回归（v1.4.1 在修 V140-3 时重新打开）。
- **V141-2（高 → 修复）**：`_post_tool_call` 在接收读证据前调用 `_backend_container_mode(task_id)`——容器 backend 的读**一律不向主机工作区记账**（读操作本身不拦）。关闭「容器绝对读污染主机证据 → backend 切回本地后放行」的通路（v1.4.1 只拦了容器写）。
- **V141-3（中 → 修复）**：`_revision_fingerprint` **取消 256KB 阈值**——gate 文件一律全量内容 SHA-256；不可读 → 无指纹 → 证据不入账、写前 fail-closed。stat 弱绑定路径整体移除（此前「弱绑定仅限非治理文件」的声明不成立，因全部生产调用点都是治理文件）。
- **V141-4（低 → 修复）**：CHANGELOG 2.4.0 的「窗口由写前复核闭合（只可能多拦）」原句**就地显式撤回**（标注被 v1.4.1 确认轮证伪）；2.4.1 条目补「正文同一性不可证」「弱绑定边界声明不成立」的修正。
- 自检 113 → **116 项**（新增 **P8 组**：V141-1 跨修订部分拼接仍拦 ×2、V141-2 容器绝对读不入主机账 + 容器相对读不入账、V141-3 大 gate 文件同长度替换被拦；并修正 P1 夹具为「旧文件 4 行只读 1–2」，确保旧修订未读完即换版的路径被覆盖）。ext4 与真 DrvFs 各 **116/116** 全绿。
- **v1.4.1 冻结哈希（已作废存档）**：`c6a5294f…`/`7f49a90b…`/`08531883…`。

## 2.4.1（2026-09-16，未提交）

- **workspace-gate plugin v1.4.0 → v1.4.1**：按 v1.4.0 确认轮（GPT-5.6-SOL high，报告见派生工作区 `gate-v130-260915/confirmation-review-v140.md`）处置 V140-1–V140-4。⚠️ **尚未经新一轮独立复核——不得声称「经独立复测通过」。**
- **V140-1（致命 → 插件侧 fail-closed 兜底）**：新增 `_backend_container_mode(task_id)`（复用核心 `tools.file_tools_paths._uses_container_paths` 权威判定）——容器 backend 的受管写入**一律拦截**（容器路径无法在主机上映射回真实工作区；主机侧会话不受影响）。**根治仍需核心提供 backend→host 身份映射**（已列核心契约需求）。
- **V140-2（致命 → 内容自证指纹）**：修订指纹对 ≤256KB 文件改为**全部字节的 SHA-256**（`c<size>:<digest>`），stat 元数据（size+mtime_ns+ino）只作大文件/不可读文件的弱绑定回退（`s…`）。语义修正：v1.4.0 的「read→post-stat 竞态由写前复核闭合、只可能多拦」被确认轮实测**证伪**（漏拦），v1.4.1 改为「许可绑定**写入时磁盘内容**」——「旧正文绑新许可」的放行前提被逐字节一致性要求关闭；utime 伪造 stat 对内容哈希无效。**残余边界（如实声明）**：插件层仍不能证明「返回给会话的正文 == 记账时磁盘内容」——需核心在 `ReadResult` 原子携带快照哈希（核心契约需求）。**【v1.4.1 确认轮修正】「旧正文绑新许可已关闭」表述过宽**：正文同一性仍不可证（上句边界）；且「≤256KB/大文件弱绑定仅限非治理文件」不成立——gate 文件调用点全部走本指纹函数，v1.4.2 起**取消大小阈值、gate 文件一律全量哈希、不可读即无指纹 fail-closed**（见 2.4.2）。
- **V140-3（误拦 → 修复）**：`_mark_read` 的指纹比较移到 `read=true` 短路之前——已读文件遇到不同指纹立即撤销旧许可（留档 `was_read=true`）并用本次证据重建；「外部改版 → 完整重读新版 → 首次写」不再被拦。
- **V140-4（审计 → 修复）**：schema 迁移按**来源分层**留档——schema≥2 写 `read_legacy_s2`/`coverage_legacy_s2`/`coverage_rev_legacy_s2`，不覆盖 schema<2 的 generic `*_legacy`；docstring 与实现对齐。
- 自检 99 → **113 项**（新增 **P7 组**：P7-1 内容指纹/同长度内容替换被拦/utime 伪造免疫、P7-2 重读新版首写放行、P7-3 容器 fail-closed ×3 + 非Container 对照、P7-4 分层留档 ×3；P7-3 以 sys.modules 桩注入 `_uses_container_paths`）。ext4 与真 DrvFs 各 **113/113** 全绿。
- **v1.4.0 冻结哈希（已作废存档）**：`83b78a6c…`/`611f97e3…`/`ebdb5390…`。

## 2.4.0（2026-09-16，未提交）

- **workspace-gate plugin v1.3.2 → v1.4.0**（`.hermes/plugins/workspace-gate/`），处置独立确认轮（GPT-6 high，2026-09-16）裁决的 N1–N3 与 N5/N6；**N4 文档化为已知保守误拦**。⚠️ **该轮同时裁决 v1.3.2「不可以发布」，v1.4.0 尚未经过新一轮独立复核——不得声称已通过独立复测。**
- **N1（致命 → 修复）**：相对路径解析改用**核心同源 task cwd 锚点**（`tools.terminal_tool.get_session_cwd` / `resolve_task_overrides`，与核心 `_authoritative_workspace_root` 的真实锚点一致，不用其进程 cwd 兜底）；hook 回调显式消费 `task_id`；**state.db 的历史 cwd 彻底退出许可判定**（写无锚点 → 保守拦截，读无锚点 → 不记账）——DB cwd 与 task 运行时 cwd 错位时的双向跨区冒领（确认轮实测的 `dispatch-victim` 与反向 decoy 路径）均关闭；DB cwd 仅存留于 pre_llm 提醒与会话审计的归属面。
- **N2（重要 → 修复 + 诚实边界）**：读证据绑定**修订指纹**（size+mtime_ns+inode）；同指纹才允许跨调用累计，指纹变化撤销旧覆盖（留档 `revocations`）并在**写前复核**时撤销已读许可——跨修订拼接（读旧版部分行 + 读新版部分行凑满 `1..total`）失效。诚实声明：插件侧 stat 相对核心读取时刻存在 TOCTOU 窗口（核心 `ReadResult` 不携带机器可读 revision），窗口由写前复核闭合（只可能多拦）；核心提供真快照版本号后应替换指纹方案。**【2026-09-16 撤回】上句「窗口由写前复核闭合（只可能多拦）」已被 v1.4.1 确认轮实测证伪（read→post-stat 竞态把旧正文绑到新指纹，写前复核无法发现，属漏拦）——v1.4.1 起改为内容自证指纹，见 2.4.1 条目。**
- **N3（重要 → 修复）**：新增生命周期 `epoch`——`pre_llm_call` / `on_session_end` 等真实活动点持锁把 `archived` 翻回 `resumed` 并递增 epoch（纯问答/零回合的恢复也触发）；`on_session_finalize` 按 `(epoch, turns_at_close)` 去重：同一阶段重复收尾幂等覆盖，**恢复后的新阶段追加新边界**（v1.3.2 会把上一条边界覆盖掉）。
- **N5（低）**：`_missing_ranges` 拒绝非整数端点（float / 数字字符串 / bool 不再被强转成合法覆盖提示）。
- **N6（低）**：docstring/CHANGELOG 诚实化——删除已不存在的「无 result 兼容路径」表述；「F1–F8 已全部处置」改述为「代码已改，独立复核仍有未决项」。
- `AGENT_RULES.md` §12 `R98` 同步重写（锚点解析 + 修订绑定 + epoch 审计 + N4 保守误拦声明）；版本 `2.3.0 → 2.4.0`；条款 ID 不变（注册表实测：100 条、高水位 R100，仅 R98 文本与插件文件哈希变更）。注册表 `.workspace/rule-clauses.json` 重建。
- 自检 `selfcheck.py` 79 → **99 项**（新增 **P 确认轮回归组 20 项**：P1 跨修订拼接+写前复核撤销、P2 旧许可撤销与重读解锁、P3 锚点同源正向（DB cwd 故意错误仍以锚点为准）、P4 反向 decoy 读不冒领、P5 零回合恢复的新 epoch 与边界追加、P6 端点严格性；E5/O3 按新语义更新）。ext4 与真 DrvFs（`TMPDIR=/mnt/c`）各 99/99 全绿。
- **v1.3.2 冻结哈希（已作废，仅存档）**：`__init__.py` `41b4296422aad5b0c4dfe0c7551b82d571398e5f59ebbfafe15d7b1b92853f1e` / `plugin.yaml` `ae8d011c…` / `selfcheck.py` `d4647f85…`；其被否决报告见工作区 `gate-v130-260915/confirmation-review.md`。

## 2.3.0（2026-09-15）

- **workspace-gate plugin v1.2.1 → v1.3.1**（`.hermes/plugins/workspace-gate/`），处置 v2.2.0 交付时列出的未决项，并按独立审查裁决逐条修复。
- **审查过程与结论（如实记录，不构成通过背书）**：GLM-5.3 用量达上限，本轮改用 **GPT-6（`gpt-6-astra-900k`，reasoning=high）** 做独立对抗审查。v1.3.0 候选被该审查**否决发布**（报告与全部原始证据见工作区 `gate-v130-260915/independent-review.md`）：F1（`result` 线上类型致判据静默失效）、F2（端点包含被当成集合覆盖 → 只读首尾两行即放行）、F3（空正文/EOF/dedup 存根仍解锁）、F4（用猜到的另一工作区配置豁免整次多目标调用 → 真实漏拦）、F5（行内截断被当作完整读取）、F6（审计覆盖与统计字段失真）、F7（历史授权状态永久继承）、F8（展示与登记问题）。**F1–F8 已全部处置**，形成 v1.3.1；该审查**未对 v1.3.1 背书**，其复测要求见项目内 notes。
- 修复要点：`result` 同时接受 JSON 字符串与 dict（线上契约）；`_coverage_complete` 改为「单个 1..total 连续区间」且 `_read_evidence` 按行号连续段切分（绝不跨洞合并）；无可信证据（空正文/EOF/dedup/行内截断/缺 total/解析失败）一律**不改状态**；`pre_tool_call` 先判定所有已确定目标再处理未知相对目标（未知目标回到保守拦截，custom 不能豁免）；会话边界写独立 `boundaries` 缓冲区、回合记录补 `turn_id`/`failed`/`reason`、`total_turns` 单调计数、finalize 幂等、恢复后标记 `resumed`；新增 `evidence_schema` 迁移，旧「读过即已读」不再视为完整读取（原始值留档 `*_legacy`）。
- **I3 跨进程丢更新**：状态读改写叠加 `fcntl.flock` 文件锁（无 `fcntl` 的平台降级线程锁）；自检 N 组 4 子进程 × 25 次自增 → 计数必须恰为 100（v1.2.x 下会丢更新，GLM 曾实测 n=43/100）。
- **I5-ext 会话级审计**：注册 `on_session_finalize`（payload 契约核对 `hermes_cli/hooks.py`／`cli_session_mixin.py`）。
- **I7 DrvFs**：自检在真 `/mnt/c`（DrvFs）上复跑全绿；大小写探针无误拦。
- `AGENT_RULES.md` §12 的 `R98` 同步重写（逐目标判定 + 未知目标保守拦截 + 完整读取的可验证定义 + 审计覆盖范围与 `evidence_schema` 迁移）；`2.2.0` → `2.3.0` 为条款文本级 MINOR，条款 ID 与 `overrides`/`extends` 引用不受影响（注册表实测：100 条不变、仅 R98 文本变更、高水位仍为 R100）。
- 自检 `selfcheck.py` 覆盖 48 → **79 项**（新增 K 读取覆盖 8 / L 会话边界 6 / M 未知目标 3 / N 跨进程 1 / O 审查回归 11），`TEMPLATE_FILES` 清单不变（仍 20 项）。
- **v1.3.2（真机验证抓到的假阳性修复）**：F5 的行内截断判定改为「**行尾**匹配」——v1.3.1 用「正文包含 `... [truncated]`」判定时，`AGENT_RULES.md` 的 R98 正文本身写着这个标记，于是「读完全文仍被拦、报错一直说尚缺 11–176」（全错误拦）。自检补 O11（正文中间提到该标记不得使证据失效）；覆盖 **79 项**。
- **验证**：79/79 自检全绿（ext4 与真 DrvFs 各一轮）+ 真机会话验证（未读→拦 / 只读前 10 行→仍拦且列出「尚缺行 11–176」/ **读全→放行** / 区外→放行）+ 模板仓库自校验 `clean: true`。
- **教训（写进 plugin docstring 与自检注释）**：合成 payload 全绿 **≠** 线上契约通过——v1.3.0 的覆盖率判据首版就是被真实会话验证抓出「只解析 dict、而线上传 JSON 字符串」而静默失效；实现 hook 前必须读核心发射点的原始 kwargs，不要凭字段名猜类型。

## 2.2.0（2026-09-15）

- 新增**工作区级治理 hook**：Hermes 项目级 plugin `.hermes/plugins/workspace-gate/`（`plugin.yaml` + `__init__.py` v1.2.1 + `selfcheck.py`），在派生工作区内机械守门「写工作区文件前必须先 `read_file` 读取 `AGENT_RULES.md`」——把 §2 的「任务开始前读规则」从软提醒变成机械拦截。事件：`pre_tool_call` 守门（内部异常也 block，fail-closed）、`post_tool_call` 记账（仅 `status=ok` 且 realpath 严格相等）、`pre_llm_call` 提醒（每会话至多一次）、`on_session_end` 回合级审计（有界追加）。守门判据是**写入目标路径**是否在工作区内，与「会话 cwd」解耦。
- `AGENT_RULES.md` 新增 §12「工作区治理 hook（机械守门）」（`R97`–`R100`，文末追加、不打乱既有 ID）：声明守门判据、skill（治心）与 hook（治手）的分工、以及「调参写 `plugin.custom.json`、不得改锁定脚本」的边界。已有 custom `overrides`/`extends` 引用不受影响（纯新增条款）。
- `TEMPLATE_FILES` 收录 3 个 plugin 文件（17 → 20 项）；`.gitignore` 增补 `.workspace/.hook-state/`（守门状态文件不入库，既有实例需自行补该行——`.gitignore` 不在 `TEMPLATE_FILES` 内，同步不会覆盖它）；`check_template.py` 的 `TEMPLATE_OWNED` 与 template-update skill 的清单计数同步。
- README 双语新增「Hook 治理（工作区级机械守门）」节：守门判据、事件、启用方式（`HERMES_ENABLE_PROJECT_PLUGINS=true` **且** `plugins.enabled: [workspace-gate]`）、`plugin.custom.json` 调参、`selfcheck.py` 自检、信任边界，以及**在场核验方法**（实测 `hermes plugins list` 不列项目插件，改用 `$HERMES_HOME/logs/workspace-gate-plugin.log` 的 `active` 行或行为验证）。
- 实现经**跨模型交叉审查**（老大要求「任何误差都会导致行为的错误」）：GLM-5.3 独立审查 v1.0.0 抓出两个致命项——patch 的 `Delete / Move / 无空格` 头整类漏守（可无守门删除工作区文件、含删 `AGENT_RULES.md` 使全区永久脱防）、`$HERMES_HOME`/profile 下 cwd 反查失效致相对路径写入静默放行——以及 I1–I7/S1–S6 共 13 项；逐条处置为 v1.2.x。**GLM 复审查结论：可作为随模板分发的治理守门发布**（F1/F2(b)/I1/I2/I4/I6/I7/S1–S5 到位），并按复审查要求前置补两处：patch `mode=patch` 不再收集 `path`（与核心 `_extract_file_mutation_targets` 完全对齐）、`$HERMES_HOME` 改用 `hermes_constants.get_hermes_home()`（含 ContextVar override）。验证：48 项合成 payload 自检全绿 + GLM 真实会话八项断言全过（未读→拦、相对路径→拦、`*** Delete File:` →拦、区外→放行、读后→放行）。已知残留（跨进程状态丢更新、DrvFs 侧未实测等）见本工作区 `notes/workspace-gate-hook-260915.md` 未决项。

## 2.1.0（2026-09-01）

- 新增 `delivery-closure` skill（`.workspace/skills/delivery-closure/`）：把 §9 完成标准落成六步机械流程（产物清点 → 查 INDEX → note 三选一 → `rebuild_index` 回写 → 部署与机械验证 → 固定格式完成声明）。动因：散文式完成标准（"需要的 note 和索引写回已完成"）在同模型同夜的 A/B 中被自评跳过，而 skill 化的机械步骤（template-update 七步）被完整遵循三次——表示形式决定遵循率。
- `AGENT_RULES.md` 新增 `§11 交付闭环 skill 指针`（`R95`/`R96`，文末追加、不打乱既有 ID）：任务模式声明完成前必须执行该 skill；`R87` 的具体执行含义以 skill 正文为准。已有 custom `overrides`/`extends` 引用不受影响（纯新增条款）。
- `TEMPLATE_FILES` 收录 `.workspace/skills/delivery-closure/SKILL.md`（16 → 17 项）；template-update §3 清单与第 4 步计数、workspace-init 第 7 步登记枚举（三 → 四入口）、README 双语 badge/生命周期表/skill 表/目录树同步更新。

## 2.0.0（2026-08-31）

- **破坏性变更**：git 决策条款（原编号 `R90–R94`）随 `§0.3` 物理归位，现为 `R14–R18`，其后所有条款顺延。工作区 custom 文件中的 `overrides:`/`extends:` 引用需按新 ID 迁移。

### 发布前修复（2026-08-31，第二轮评估 REPORT-2）

- template-update 同步清单改为以 `scripts/verify_rules.py` 的 `TEMPLATE_FILES` 为唯一事实源（纳入 workspace-init / git-init），消除手维护清单漂移导致的「校验失败 → 回滚」死循环。
- README 替换豁免进入 `clean` 判定：`REPLACED` / `HASH-DIFF`（README）计入 `info`，不再导致 `clean: false`；`EXTRA` 检测死代码修复（现迭代 `TEMPLATE_FILES` 与注册表的差集）。
- 条款 ID 改为**跨文件全局唯一、永不复用**（mark_rules 全局高水位 + verify 跨文件撞名检测）。
- 修复 workspace-init 与 skills/REGISTRY.md 中失效的 `TOOLS.md §4.5` 引用（改引小节标题）。
- HERMES.md 人格节改指 `HERMES.custom.md`，移除会制造 TAMPERED 的自毁式引用块槽位。
- 新增 GitHub Actions CI（`mark_rules.py check` + `verify_rules.py`），锁住模板仓库自身不漂移。

## 1.5.0（2026-08-31）

- 三个客户端入口（`HERMES.md` / `CLAUDE.md` / `AGENTS.md`）新增对各自 `.custom.md` 的读取约束——读完 `AGENT_RULES.md` 及 `AGENT_RULES.custom.md` 后，须继续读取对应客户端的 `.custom.md`；规则冲突时 `AGENT_RULES.md` 及其 custom 文件优先级永远最高。
- 变更记录从 `AGENT_RULES.md` 独立为 `CHANGELOG.md`。

## 1.4.0（2026-08-31）

- 新增 `HERMES.md` 客户端入口与 §9.1「记忆边界」；§2 新增入口防分叉条款；§7 新增版本控制操作授权要求；§8 中央文件清单纳入全部客户端入口。
- 新增 §9.2「notes 索引分层与策展节奏」；§9 新增记忆卫生规则（合并优先与 superseded 归档）；索引改为 INDEX.md（路由）+ `_INVERTED.md`（按需详情）双层。
- 新增 §10「规则分层与 custom 覆写」——模板规则文件锁定只读，本地规则一律写入 `<file>.custom.md` 并以 custom 优先；配套条款 ID 标记与双级校验（`scripts/mark_rules.py`、`scripts/verify_rules.py`）。
- 新增 §0.3「git 仓库决策」与 workspace-init / git-init skills——首次初始化须抹除指向模板库的 `.git` 并询问用户是否建仓；venv 为必须项，git 为可选项，决策记录于 bootstrap。
