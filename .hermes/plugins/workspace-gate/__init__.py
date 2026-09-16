"""workspace-gate — WorkspaceTemplate 工作区级治理 hook（Hermes 项目级 plugin）。

随工作区分发（`.hermes/plugins/workspace-gate/`），在 WorkspaceTemplate 派生工作区
强制「先读治理文件，再写工作区文件」。启用方式：`HERMES_ENABLE_PROJECT_PLUGINS=true`。

核心判据：守门看「写入目标路径是否在工作区内」，而非「会话 cwd 是否在工作区」。

事件：
- pre_tool_call   守门（write_file/patch 的任一目标在工作区内且未读治理文件 → block）
- post_tool_call  记账（read_file status=="ok" 且 realpath 严格命中治理文件 → 累计读取覆盖率）
- pre_llm_call    提醒（每个会话至多注入一次未读清单）
- on_session_end  审计（回合级事件：有界追加，条目带 scope="turn"）
- on_session_finalize  会话级收尾审计（payload 契约已核对 Hermes 源码 hooks.py：
  `{"session_id": …}`）：追加 scope="session" 边界条目，并把状态文件标记
  status="archived" 留档（已读/覆盖率证据保留）

读取记账的判定：按 read_file 返回的行号区间**累计覆盖率**，覆盖全文才算「已读」——
与 block 文案「完整读取」对齐。**没有可信证据就不改状态**（绝不隐式解锁）。

相对路径解析（v1.4.0 起，审查 N1）：**只认核心同源的 task cwd 锚点**——
`tools.terminal_tool.get_session_cwd(task_id)` / `resolve_task_overrides(task_id)["cwd"]`
（与核心 `file_tools_paths._authoritative_workspace_root` 的前两个真实锚点一致；
不使用它的进程 cwd/`$TERMINAL_CWD` 兜底——那不是会话可证明的锚点）。
**state.db 里的历史 cwd 彻底退出许可判定**（核心在 ACP/desktop 切换运行时 cwd 时
不会同步 DB，DB cwd 与真实 task cwd 可以错位，双向都能冒领）：相对**写**无锚点按
未知目标保守拦截；相对**读**无锚点不授予任何证据（只限制相对写、继续给相对读记账
不足以修复 N1 的反向路径）。DB cwd 仍用于 pre_llm 提醒 / 会话审计的**归属面**——
那里只是提醒与留痕，不发许可。

容器 backend（v1.4.1 起，审查 V140-1）：核心把主机 cwd bind-mount 成容器路径并回写
session cwd，插件在主机上无法把容器路径映射回真实工作区——**容器 backend 的受管写入
一律 fail-closed**（复用核心 `_uses_container_paths` 权威判定）。这是有意的过度拦截；
主机侧会话（本地 backend）不受影响。

完整读取的修订绑定（v1.4.0 引入，v1.4.1/V140-2 与 v1.4.2/V141-3 按确认轮强化）：每份可信
读取证据绑定目标文件的**修订指纹 = 全部字节的 SHA-256**（`c<hexdigest>`，无大小阈值、
无 stat 分量）——「read→post-stat 竞态把旧正文绑到新许可」「同 stat tuple 内容替换」
「utime 回拨」等形态被内容自证关闭：放行前提是「**写入时磁盘内容 == 记账时内容**」。
诚实边界：插件层无法证明「返回给会话的正文 == 记账时磁盘内容」（需核心在 ReadResult
原子携带快照哈希，属核心契约需求）。指纹变化（外部编辑/模板同步/文件被删）→ 旧覆盖
撤销留档、已读许可在写前复核与重读时一并撤销——跨修订拼接不算完整。工作区身份由
持久的 `.workspace/bootstrap.json` 承担（V142-1）：gate 文件被删除后写前复核得到
None → 状态 unread → BLOCK，工作区不会脱离守门。

守门判据的落点：
- 绝对路径目标：直接按目标路径找工作区（与「会话 cwd」无关）→ 会话在区外、却写区内
  文件同样被拦。
- 相对路径目标：需要**核心同源的 task cwd 锚点**才能解析（见上）；解析不出
  （standalone、task 无锚点、profile/DB 不匹配）时**不放行**——按 fail-closed 拦截并
  给出可执行的替代方案（改用绝对路径）。懒 agent 最爱用相对路径，静默放行等于守门失效。
- 提醒（pre_llm_call）以「会话 DB cwd 所属工作区」为对象（审计/提醒面，不发许可），
  与守门的「目标路径」判据不完全一致：cwd 在区外但写区内文件的会话「有拦截、无
  提醒」——这是有意为之，不是 bug。

信任边界（有意为之）：守门防「无意识跳过」，不防「主动绕过」。拥有 terminal /
execute_code 的 agent 可以直接改状态文件、改配置或绕过工具层；审计结论对拥有写权限
的 agent 只具参考性。空 session_id 时不守门（无法归属状态），并在日志中记录。状态文件
本身是工作区内普通文件，可被 agent 伪造——真要防恶意绕过需沙箱/OS 权限层。

工作区级覆写：`.hermes/plugins/workspace-gate/plugin.custom.json`（工作区所有，
不随模板分发/sync）可设 `enabled` / `gate_files` / `write_tools`——本脚本属模板层
锁定文件（哈希校验），要改行为请写 custom，别改脚本。

修订记录（v1.0.0 → v1.2.0）：
- v1.1.0（实现者自审 + 合成 payload 攻击测试 28 项）：
  * patch V4A 的 `*** Delete File:` 纳入守门
  * parent_session_id 链兜底（delegate/cron 子会话）
  * state.db / 日志路径跟随 $HERMES_HOME（原硬编码 ~/.hermes 在 profile/Windows 下查错库）
  * _load_state 键归一化（缺键/损坏状态文件不再 KeyError 或静默失效）
  * pre_llm_call 去重（原实现每回合重复注入）
  * on_session_end 改为有界追加（原实现每回合覆盖 last_end）
  * 新增 plugin.custom.json 覆写通道
- v1.2.0（GLM-5.3 独立审查 F1/F2、I1-I7、S1-S6 全部处置）：
  * F1 补齐 patch 头三类漏网：`Move File: src -> dst`（双目标）、无空格 `***Update File:`、
    并照抄核心 `agent/tool_dispatch_helpers.py::_extract_file_mutation_targets` 的
    mode 语义（mode=replace 只看 path，mode=patch 才解析正文）
  * F2(b) 相对路径 + cwd 不可得 → fail-closed 拦截（原静默放行）
  * I1 工作区判定改双判据（词法路径优先，再 resolved），堵符号链接逃逸
  * I3 状态读改写加线程锁 + mkstemp 原子写（同进程并发不再丢更新/互相践踏）
  * I4 提醒改为「每个会话至多一次」，不再逐回合唠叨
  * I6 近失配留痕：有 AGENT_RULES.md 但缺 .workspace/bootstrap.json 的工作区记日志
  * I7 `/mnt/*`（DrvFs 大小写不敏感）下 read 记账改用 casefold 比较，避免永封
  * S1 register() 写「active」日志；S2 状态文件名消毒 session_id
- v1.2.1（复审查后的两处前置修复，均由 GLM-5.3 指定）：
  * N1：patch `mode=patch` 时不再收集 `path` 字段（核心该模式完全忽略 path）——消除唯一的语义错位，
    现在「插件目标集 ⊇ 核心实际写入集」在所有 mode 下成立且不超集
  * F2(a) 补完：优先复用进程内的 `hermes_constants.get_hermes_home()`（含 ContextVar override →
    ``$HERMES_HOME`` → 平台默认），import 不可用时退回本地镜像（env → 平台默认）
- v1.3.0（候选版本；**独立审查否决，未发布**——由 GPT-6(reasoning=high) 对抗审查，报告与证据见
  工作区 `gate-v130-260915/`）：
  * I3 跨进程：状态读改写叠加 `fcntl.flock` 文件锁（无 fcntl 的平台降级为线程锁）。
  * I5-ext：注册 `on_session_finalize`；`on_session_end` 条目补 `scope`。
  * N2：cwd 不可得的 fail-closed 分支尝试读 `plugin.custom.json` 后判定工具归属
    （**审查 F4 判定该做法引入新漏拦，v1.3.1 已回退**）。
  * 读取覆盖度：`post_tool_call` 解析 read_file 返回的行号区间累计覆盖。
- v1.3.1（按上述审查逐条修复；F1–F8 全部处置）：
  * F1 契约：`result` 在线上是 **JSON 字符串**（`model_tools.py::_emit_post_tool_call_hook` 直传），
    现同时接受 str/dict；否则覆盖率判定在生产中静默失效
  * F2 端点包含 ≠ 集合覆盖：`_coverage_complete` 改为「**单个** 1..total 的连续区间」；
    `_read_evidence` 按返回内容的行号连续段切分 runs，**绝不跨洞合并**（读首尾两行不再算读完）
  * F3 无正文不给证据：空正文 / EOF 越界 / dedup 存根 / 解析失败 / 缺 `total_lines`
    → `_read_evidence` 返回 None，`_post_tool_call` **不改状态**（不再有「无 result 即隐式解锁」）
  * F4 目标顺序：先逐个判定**所有已确定目标**（任一未读即拦），再处理未知相对目标——
    未知目标回到保守边界（默认写工具集一律拦，不接受别的工作区 custom 发豁免）
  * F5 行内截断：正文含 `... [truncated]` 的返回不产生证据（长行被截半不算完整读取）
  * F6 审计：会话边界写**独立** `boundaries` 缓冲区（不再被回合环形缓冲裁掉）、
    回合记录补 `turn_id`/`failed`/`reason`、新增单调 `total_turns`、finalize 幂等、
    恢复后状态标签改 `resumed`；并如实声明「边界记录只归属会话 cwd 所属工作区」
  * F7 历史污染：新增 `evidence_schema`；<2 的旧状态在持锁迁移时清空 `read`/`coverage`
    （原始值留档 `read_legacy`/`coverage_legacy`），v1.2.x 的「读过即已读」不再被当作完整读取
  * F8 展示与登记：`_missing_ranges` 裁剪到 1..total、丢弃非法区间、超过三段提示「其余略」；
    自检补 O 组（首尾两行、空正文、dedup、行内截断、混合绝对/相对 patch、目标顺序置换）
    与 L 组审计断言——**合成 payload 全绿 ≠ 线上契约通过，真机会话验证不可省**
- v1.3.2（v1.3.1 真机验证抓到的假阳性修复）：
  * F5 的行内截断判定从「正文**包含** `... [truncated]`」改为「**行尾**匹配」——v1.3.1 用包含
    判定时 `AGENT_RULES.md` 的 R98 正文本身写着这个标记，导致「读完全文仍被拦、报错一直说
    尚缺 11–176」的**全错误拦**；核心的标记确实追加在行尾（`file_operations.py::_add_line_numbers`）
  * 自检补一例：正文**中间**提到该标记不得使证据失效（O11）
- v1.4.1（按 v1.4.0 确认轮（GPT-5.6-SOL high）处置 V140-1–V140-4；**未经新一轮独立复核不得发布**）：
  * V140-1 容器路径：新增 `_backend_container_mode(task_id)`（复用核心 `_uses_container_paths`
    权威判定）——容器 backend 的受管写入**一律 fail-closed**（容器路径无法在主机上映射回
    真实工作区，v1.4.0 实测被当区外放行）；主机侧绝对路径写入不受影响
  * V140-2 修订身份：指纹升级为**内容自证**——≤256KB 的文件直接 SHA-256 全部字节
    （`c<size>:<digest>`）；stat 元数据只作大文件/不可读文件的弱绑定回退（`s…` 前缀）。
    语义从「许可绑定 stat 快照」改为「**许可绑定写入时的磁盘内容**」：read→post-stat
    竞态把 OLD 正文绑到 NEW 许可的形态被内容自证关闭。诚实边界（复核方裁决保留）：
    仍不能证明「返回给会话的正文 == 记账时磁盘内容」——那需要核心在 ReadResult 原子
    携带快照哈希；同 size+mtime 的 utime 伪造对内容哈希无效（威胁模型内的主动绕过除外）
  * V140-3 重读误拦：`_mark_read` 的指纹比较移到 read=true 短路**之前**——已读文件
    遇到不同指纹立即撤销旧许可（留档 `was_read=True`）并用本次新证据重建；
    「外部改版 → 完整重读新版 → 首次写」不再被拦
  * V140-4 迁移留档：按**来源 schema 分层**——schema≥2 迁移写 `read_legacy_s2`/
    `coverage_legacy_s2`/`coverage_rev_legacy_s2`（不覆盖 schema<2 的 generic
    `*_legacy`）；docstring/CHANGELOG 与实现对齐
  * N6 余项：删除「TOCTOU 由写前复核闭合/只可能多拦」的不实声明（V140-2 实测为漏拦），
    改为如实陈述内容自证语义与其边界
- v1.4.2（按 v1.4.1 确认轮（GPT-5.6-SOL high）处置 V141-1–V141-4；**未经新一轮独立复核不得发布**）：
  * V141-1 拼接回归：`_mark_read` 撤销条件去掉 `was_read` 前提——任何已记账文件遇不同
    指纹先留档撤销再重建（`was_read` 降为审计字段）；关闭「旧修订部分覆盖 + 新修订部分
    覆盖拼成全文」的路径（v1.4.1 修 V140-3 时重新打开）
  * V141-2 容器读污染：`_post_tool_call` 接收证据前判定 `_backend_container_mode`，
    True 一律不向主机工作区记账（读不拦，但容器读不发主机许可）——关闭「容器绝对读
    绑主机哈希 → backend 切回本地放行」的通路
  * V141-3 大文件弱绑定：`_revision_fingerprint` 取消 256KB 阈值，gate 文件一律全量
    SHA-256；不可读 → None → 证据不入账、写前 fail-closed；stat 弱绑定路径整体移除
  * V141-4 文案：CHANGELOG 2.4.0 的已证伪断言就地显式撤回；2.4.1 条目补边界修正
  * N1 权威解析：相对路径只认核心同源 task cwd 锚点（`get_session_cwd` /
    `resolve_task_overrides`，与核心 `_authoritative_workspace_root` 的真实锚点一致，
    不用其进程 cwd 兜底）；hook 显式消费 `task_id`；**state.db 历史 cwd 退出许可判定**
    （写无锚点→拦，读无锚点→不记账）；DB cwd 仅存留于提醒/审计归属面
  * N2 修订绑定：证据带修订指纹（size+mtime_ns+ino）；同指纹才跨调用累计；指纹变化
    撤销旧覆盖（留档 `revocations`）并在**写前复核**时撤销已读许可；跨修订拼接失效
  * N3 生命周期 epoch：`pre_llm_call`/`on_session_end` 等真实活动点持锁把
    archived→resumed 并递增 `epoch`（纯问答/零回合的恢复也会触发）；finalize 按
    `(epoch, total_turns)` 去重——不同阶段的边界**追加**，不再覆盖上一条
  * N5：`_missing_ranges` 拒绝非整数端点（不再截断 float / 接受数字字符串）
  * N6：docstring 与 CHANGELOG 诚实化（删除已不存在的「无 result 兼容路径」表述；
    「已处置」≠「已通过独立复核」）
  * `evidence_schema` 2→3：schema=2 状态可能已含 N1/N2 污染的错区读许可，迁移清空
    并留档（`read_legacy_s2`/`coverage_legacy_s2`）
  * N4（中，文档化）：合法正文恰以裸 `... [truncated]` 结尾时该次读取不产生证据
    （保守假阳性，上一轮已知、本轮确认；治理文件应由维护者改写该行或加包裹）
"""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

try:  # POSIX 文件锁：跨进程串行化状态读改写（Windows 无此模块 → 降级为线程锁）
    import fcntl
except Exception:  # pragma: no cover - win32
    fcntl = None

RULES = ["AGENT_RULES.md", "AGENT_RULES.custom.md"]
BOOTSTRAP = [".workspace/bootstrap.json"]
PREP = ["notes/INDEX.md", "skills/REGISTRY.md", "TOOLS.md"]
ALL_GOV = RULES + BOOTSTRAP + PREP

# Tools gated by default; a workspace may narrow/widen via plugin.custom.json.
WRITE_TOOLS = {"write_file", "patch"}

GATE_FILE = "AGENT_RULES.md"

# Workspace-level override (workspace-owned file, never distributed/synced):
#   .hermes/plugins/workspace-gate/plugin.custom.json
#   {"enabled": true, "gate_files": ["AGENT_RULES.md"], "write_tools": ["write_file", "patch"]}
# The script itself is template-layer (locked, hash-verified) — tune behaviour
# here instead of editing it, so the next template sync never fights your edits.
CUSTOM_CONF_REL = ".hermes/plugins/workspace-gate/plugin.custom.json"

STATE_DIRNAME = ".hook-state"
MAX_PARENT_HOPS = 8
MAX_TURN_RECORDS = 20
MAX_BOUNDARY_RECORDS = 10

# Mirrors agent/tool_dispatch_helpers.py::_extract_file_mutation_targets:
# `\s*` (not `\s+`) after `***` because the parser accepts `***Update File:` too.
_PATCH_FILE_RE = re.compile(
    r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+)$", re.MULTILINE
)
_PATCH_MOVE_RE = re.compile(
    r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$", re.MULTILINE
)

_LOCK = threading.Lock()
_NEAR_MISS_LOGGED: set = set()

try:  # Loaded in-process as a Hermes plugin → reuse the authoritative resolver
    from hermes_constants import get_hermes_home as _get_hermes_home
except Exception:  # standalone (e.g. selfcheck.py) → local mirror below
    _get_hermes_home = None

# N1：核心同源的 task cwd 锚点。插件与核心文件工具**必须用同一个 cwd 权威源**，否则
# DB 记录的 cwd 与 task 运行时 cwd 错位时，读证据与写许可都能跨工作区冒领（确认轮
# N1 的双向复现）。这两个函数与核心 `file_tools_paths._authoritative_workspace_root`
# 的前两个锚点完全一致（register_task_env_overrides 会把 cwd override 同步进
# _session_cwd，所以 get_session_cwd 已覆盖 override 通道）；**故意不用**核心的
# 进程 cwd / `$TERMINAL_CWD` 兜底——那不是会话可证明的锚点。
try:  # in-process (real runtime)
    from tools.terminal_tool import get_session_cwd as _core_get_session_cwd
    from tools.terminal_tool import resolve_task_overrides as _core_resolve_task_overrides
except Exception:  # standalone (selfcheck / broken install) → both None
    _core_get_session_cwd = None
    _core_resolve_task_overrides = None


def _task_cwd_anchor(task_id, session_id=None, parent_session_id=None):
    """N1 权威锚点：返回该 task 真实读写所用的 cwd（绝对路径字符串）或 None。

    与核心 `_resolve_path_for_task` 的锚点同源（顺序：recorded → registered
    override），因此「插件按锚点解析的目标」⊆「核心实际读写的目标」。取不到
    可证明的锚点时返回 None——调用方按 fail-closed 处理（写→拦，读→不记账），
    绝不用 state.db 的历史 cwd 充当许可来源。
    """
    tid = str(task_id or "").strip() or None
    if tid is not None:
        if _core_get_session_cwd is not None:
            try:
                cwd = _core_get_session_cwd(tid)
                if isinstance(cwd, str) and cwd.strip():
                    return cwd
            except Exception as e:
                _log(f"task anchor (get_session_cwd) error: {e!r}")
        if _core_resolve_task_overrides is not None:
            try:
                ov = _core_resolve_task_overrides(tid)
                cwd = (ov or {}).get("cwd") if isinstance(ov, dict) else None
                if isinstance(cwd, str) and cwd.strip():
                    return cwd
            except Exception as e:
                _log(f"task anchor (resolve_task_overrides) error: {e!r}")
    return None


def _backend_container_mode(task_id):
    """V140-1：该 task 是否走**容器侧路径**（核心权威判定）。

    返回 True/False（核心可判定）或 None（standalone/核心不可导入）。容器 backend
    把主机 cwd bind-mount 成容器路径（如 `/workspace`）并回写 session cwd——
    插件在主机上无法把容器路径映射回真实工作区，v1.4.0 确认轮实测该形态下
    受管写入被当「区外」放行。True 时：写一律 fail-closed，读不授予主机证据。
    """
    if not str(task_id or "").strip():
        return None
    try:
        from tools.file_tools_paths import _uses_container_paths

        return bool(_uses_container_paths(str(task_id)))
    except Exception:
        return None


def _log(msg):
    try:
        log_dir = _hermes_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "workspace-gate-plugin.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _hermes_home():
    """Hermes home: authoritative resolver when importable, else a local mirror.

    Inside the Hermes process we reuse `hermes_constants.get_hermes_home()`, whose
    resolution order is ContextVar override → ``$HERMES_HOME`` → platform default
    (so per-task/profile scopes are honoured). Standalone (selfcheck, or a broken
    install) we mirror the process-scope part: env → platform default. Never log
    from here — `_log` itself calls `_hermes_home()` (would recurse).
    """
    if _get_hermes_home is not None:
        try:
            return Path(_get_hermes_home())
        except Exception:
            pass
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def _resolve_session_cwd(session_id, parent_session_id=None):
    """会话 cwd：sessions.cwd 列 → model_config.cwd → parent_session_id 链。"""
    db = _hermes_home() / "state.db"
    return _session_cwd_from_db(db, session_id, parent_session_id)


def _session_cwd_from_db(db, session_id, parent_session_id=None):
    seen = set()
    sid = session_id
    for _ in range(MAX_PARENT_HOPS):
        if not sid or sid in seen:
            break
        seen.add(sid)
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                row = con.execute(
                    "SELECT cwd, model_config, parent_session_id FROM sessions WHERE id = ?",
                    (sid,),
                ).fetchone()
            finally:
                con.close()
        except Exception as e:
            _log(f"resolve_session_cwd error: {e!r}")
            return None
        if not row:
            return None
        cwd, model_config, parent = row
        if cwd:
            return cwd
        if model_config:
            try:
                mc = json.loads(model_config)
                if isinstance(mc, dict) and mc.get("cwd"):
                    return mc["cwd"]
            except Exception:
                pass
        sid = parent
    return None


def _ws_root(d):
    """True when *d* carries both WorkspaceTemplate workspace markers."""
    try:
        return (d / GATE_FILE).is_file() and (d / ".workspace" / "bootstrap.json").is_file()
    except OSError:
        return False


def _find_workspace(start_path):
    """向上遍历找 WorkspaceTemplate 工作区根。

    双判据：先按**词法**路径（normpath，不穿透符号链接）判定，再按 resolved 路径判定。
    词法分支堵住「工作区内 symlink → 区外文件」的逃逸（写该 symlink 仍视为写工作区文件）；
    resolved 分支保持原有语义。`.git` 为**目录**才是 git 边界（普通仓库 root）；
    为文件（submodule / worktree 的 gitdir 链接）不是边界，继续向上。

    V142-1（v1.4.2 确认轮）：工作区身份标记改为**持久的 `.workspace/bootstrap.json`**——
    此前要求 `AGENT_RULES.md` 同时存在，导致「合规读取后 gate 文件被删除（外部同步/
    误操作/已获许可的工具调用）→ 工作区发现失效 → 后续写入静默脱离守门」（实测
    fail-open）。bootstrap 是工作区的持久身份；gate 文件缺失由**写前复核**处理
    （指纹 None → 状态 unread → BLOCK），不再由工作区发现处理。
    `AGENT_RULES.md` 存在但缺 bootstrap 仍保留 near-miss 日志（疑似残缺工作区）。
    """
    if not start_path:
        return None
    raw = os.path.normpath(os.path.expanduser(str(start_path)))
    try:
        resolved = str(Path(raw).resolve())
    except Exception:
        resolved = None
    for cand in (raw, resolved):
        if not cand:
            continue
        d = Path(cand)
        for anc in [d] + list(d.parents):
            try:
                has_bootstrap = (anc / ".workspace" / "bootstrap.json").is_file()
                has_gate = (anc / GATE_FILE).is_file()
            except OSError:
                continue
            if has_bootstrap:
                try:
                    return anc.resolve()
                except Exception:
                    return anc
            if has_gate:
                key = str(anc)
                if key not in _NEAR_MISS_LOGGED:
                    _NEAR_MISS_LOGGED.add(key)
                    _log(
                        f"near-miss: {key}/{GATE_FILE} exists but "
                        f"{key}/.workspace/bootstrap.json is missing — workspace NOT gated"
                    )
            try:
                if (anc / ".git").is_dir():
                    break
            except OSError:
                break
    return None


def _absolutize(path_arg, session_id, parent_session_id=None, task_id=None):
    """绝对化；相对路径经 **N1 权威 task cwd 锚点** 解析。

    解析不出返回 None（由调用方决定 fail-closed）。**不回退到 state.db**——
    DB cwd 与 task 运行时 cwd 可错位，双向都能冒领（确认轮 N1）。
    """
    if path_arg is None:
        return None
    p = os.path.expanduser(str(path_arg))
    if not p:
        return None
    if not os.path.isabs(p):
        anchor = _task_cwd_anchor(task_id, session_id, parent_session_id)
        if not anchor:
            return None
        p = os.path.join(anchor, p)
    return os.path.normpath(p)


def _raw_targets(args, tool_name):
    """工具调用声明的原始目标（未绝对化）。语义对齐核心同款函数：

    mode = args.get("mode") or "replace"；replace 只看 args["path"]，
    patch 才解析 V4A 正文（否则会出现核心不写、插件却按正文拦的错位）。
    """
    if not isinstance(args, dict):
        return []
    out = []
    if tool_name == "write_file":
        if args.get("path"):
            out.append(str(args["path"]))
    elif tool_name == "patch":
        mode = args.get("mode") or "replace"
        if mode == "patch":
            body = args.get("patch")
            if isinstance(body, str) and body:
                for m in _PATCH_FILE_RE.finditer(body):
                    p = m.group(1).strip()
                    if p:
                        out.append(p)
                for m in _PATCH_MOVE_RE.finditer(body):
                    for g in (1, 2):
                        p = m.group(g).strip()
                        if p:
                            out.append(p)
        elif args.get("path"):
            # Core ignores `path` entirely in patch mode — mirror that exactly.
            out.append(str(args["path"]))
    else:
        for key in ("path", "file_path", "target_path"):
            if args.get(key):
                out.append(str(args[key]))
    return out


def _resolve_target_paths(args, tool_name, session_id, parent_session_id=None, task_id=None):
    """返回 (已解析绝对路径列表, 无法解析的相对路径列表)。"""
    resolved, unresolved = [], []
    for raw in _raw_targets(args, tool_name):
        abs_path = _absolutize(raw, session_id, parent_session_id, task_id)
        if abs_path:
            resolved.append(abs_path)
        else:
            unresolved.append(raw)
    out, seen = [], set()
    for p in resolved:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out, unresolved


def _state_path(ws, session_id):
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id)) or "unknown"
    return ws / ".workspace" / STATE_DIRNAME / f"{sid}.json"


@contextlib.contextmanager
def _state_lock(ws, session_id):
    """状态文件的互斥区：线程锁 +（POSIX）`fcntl.flock` 跨进程锁。

    两个进程同时写同一 session（两个终端 resume 同一会话、gateway 与 CLI 并行）时，
    仅靠 `threading.Lock` 会丢更新（GLM 实测 4 进程 × 25 次读改写 → n=43/100）。flock
    提供跨进程串行化；无 fcntl 的平台（Windows）降级为线程锁——已知残留，方向 fail-safe
    （丢的是已读标记，只会多拦不会漏拦）。
    """
    lock_path = _state_path(ws, session_id).with_name(
        _state_path(ws, session_id).name + ".lock"
    )
    with _LOCK:
        if fcntl is None:
            yield
            return
        fh = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_path, "a+")
        except OSError as e:
            _log(f"state lock unavailable ({lock_path}): {e!r} — thread lock only")
        if fh is None:
            yield
            return
        try:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError as e:
                _log(f"flock failed ({lock_path}): {e!r} — thread lock only")
                yield
                return
            try:
                yield
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                fh.close()
            except OSError:
                pass


def _load_state(ws, session_id):
    state = None
    sp = _state_path(ws, session_id)
    if sp.is_file():
        try:
            loaded = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except Exception as e:
            _log(f"load_state corrupt ({sp}): {e!r} — reinitialising")
    if state is None:
        state = {"session_id": session_id, "workspace_root": str(ws)}
    state["session_id"] = session_id
    state.setdefault("workspace_root", str(ws))
    state.setdefault("task_slug", None)
    state.setdefault("closure", False)
    state.setdefault("status", "active")
    state.setdefault("reminder_sent", False)
    read = state.get("read")
    if not isinstance(read, dict):
        read = {}
    for f in ALL_GOV:
        read.setdefault(f, False)
    state["read"] = read
    if not isinstance(state.get("ends"), list):
        state["ends"] = []
    if not isinstance(state.get("coverage"), dict):
        state["coverage"] = {}
    if not isinstance(state.get("boundaries"), list):
        state["boundaries"] = []
    try:
        state["total_turns"] = int(state.get("total_turns") or 0)
    except Exception:
        state["total_turns"] = 0
    # 证据迁移（审查 F7）：EVIDENCE_SCHEMA < 2 的旧状态里，`read=True` 来自 v1.2.x 的
    # 「读过即已读」，且旧 `coverage` 可能被 min/max 填过洞——两者都**不可信**。按审查裁决
    # 默认不 grandfather：清空 read（连同旧 coverage 一起留档到 *_legacy 供审计），
    # 让证据按新规则重新积累。保留原始文件，不删除会话状态。
    try:
        schema = int(state.get("evidence_schema") or 0)
    except Exception:
        schema = 0
    if schema < EVIDENCE_SCHEMA:
        # V140-4：按来源 schema 分层留档，不覆盖更早的 generic 留档
        if schema >= 2:
            state["read_legacy_s2"] = dict(state["read"])
            state["coverage_legacy_s2"] = dict(state.get("coverage") or {})
            state.setdefault("coverage_rev_legacy_s2", dict(state.get("coverage_rev") or {}))
        else:
            state["read_legacy"] = dict(state["read"])
            state["coverage_legacy"] = dict(state.get("coverage") or {})
        state["read"] = {f: False for f in ALL_GOV}
        state["coverage"] = {}
        state["coverage_rev"] = {}
        state["evidence_schema"] = EVIDENCE_SCHEMA
        _log(
            f"state migrated to evidence_schema={EVIDENCE_SCHEMA} (from {schema}) ({sp}): "
            "legacy read flags and coverage cleared (kept in "
            + ("*_legacy_s2" if schema >= 2 else "*_legacy") + " for audit)"
        )
    return state


def _save_state(ws, session_id, state):
    sp = _state_path(ws, session_id)
    sp.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(sp.parent), prefix=sp.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, sp)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _paths_equal(a, b) -> bool:
    """路径相等判定；`/mnt/*`（DrvFs 大小写不敏感）降级为大小写不敏感比较。"""
    sa, sb = str(a), str(b)
    if sa.startswith("/mnt/") or sb.startswith("/mnt/"):
        return sa.casefold() == sb.casefold()
    return sa == sb


_LINE_NUM_RE = re.compile(r"^(\d+)\|", re.MULTILINE)

# 行内截断标记（核心 tools/file_operations.py 超长行的处理）：
#   f"{i}|{line[:max_line_length]}... [truncated]"
# 只匹配**行尾**的标记——正文中间提到这个字符串（例如 R98 自己就写着它）不算被截断，
# 否则治理文件本身就永远读不完整（v1.3.1 真机验证抓到的假阳性）。
_INLINE_TRUNC_RE = re.compile(r"\.\.\.\s*\[truncated\]\s*$", re.MULTILINE)

# 已读证据模式版本：schema<3 的旧状态不可信——<2 是 v1.2.x「读过即已读」；==2 可能已含
# N1（错区 cwd 冒领）与 N2（跨修订拼接）产生的污染许可（确认轮 2026-09-16 裁决：
# 修复发布必须清除 schema=2 的既有许可，不能仅改 resolver 后继承 true）。加载时迁移：
# 清空 read/coverage，原始值留档供审计，证据按新规则重新积累。
# V140-4：按**来源 schema 分层**留档——schema<2 → `read_legacy`/`coverage_legacy`；
# schema==2 → `read_legacy_s2`/`coverage_legacy_s2`（不覆盖更早的 generic 留档）。
# V141-4（v1.4.1 确认轮）/ V142-2（v1.4.2 确认轮）：文案与实现一致——
# 指纹为 **gate 文件一律全量内容 SHA-256**（`c<hexdigest>`，无大小阈值、无 stat 回退）；
# 读取失败/文件缺失 → None（证据不入账、写前 fail-closed）。
EVIDENCE_SCHEMA = 3


def _revision_fingerprint(path):
    """N2/V140-2/V141-3 修订指纹：**gate 文件一律全量内容 SHA-256**（内容自证）。

    V141-3（v1.4.1 确认轮）：v1.4.1 的「>256KB 退回 stat 弱指纹」被证伪为治理文件
    上的弱绑定绕过（320KB 夹具同长度替换 + mtime 回拨后放行）——本函数的全部生产
    调用点都服务于治理/gate 文件，因此**不再设大小阈值**：所有可读文件全量哈希；
    只有读取失败（权限/IO 竞态）时返回 None（无指纹 → 证据不入账、写前 fail-closed）。
    stat 元数据不再是指纹的一部分。诚实边界保留：仍不能证明「会话读到的正文 ==
    记账时磁盘内容」（核心契约需求）；对超大治理文件的哈希开销是接受的成本。
    """
    try:
        return "c" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return None


def _read_evidence(result):
    """从 read_file 返回里提取**可信**已读证据 → `(runs, total)`，否则 None。

    `runs` = `[[start, end], …]`：**严格按返回内容里行号的连续段**切分，绝不跨洞合并
    （旧实现取 min/max 会把中间没读到的行「涂满」，审查 F2）。
    `total` = 工具声明的文件总行数，必须是正整数（缺失即无可信证据，审查 F3）。

    返回 None（＝不增加任何已读证据，也不撤销既有状态）：
    - result 非 str/dict / 解析失败（线上是 JSON 字符串，见下）；
    - content 为空或空白（EOF 越界、dedup 存根、无正文）；
    - 正文行尾含截断标记 `... [truncated]`（该行不完整，审查 F5；**已知保守假阳性
      N4**：合法正文恰以裸标记结尾时同样拒绝，属有意保留，见 R98/v1.4.0 记录）；
    - `total_lines` 缺失或非正整数；
    - 解析不出行号。

    成功时返回 `(runs, total, fingerprint)`：`fingerprint` 是读取后目标的修订指纹
    （N2；调用方 stat 失败会得到 None，`_mark_read` 拒收无指纹证据）。

    **线上契约（实测）**：`post_tool_call` 的 `result` 是**原始工具结果的 JSON 字符串**
    （`model_tools.py::_emit_post_tool_call_hook` 直传；`_tool_result_observer_fields` 先
    `json.loads` 再派生 status），所以必须同时接受 str 与 dict——只认 dict 会让判据在生产
    里静默失效（v1.3.0 首版即踩此坑，审查 F1）。
    """
    if isinstance(result, (str, bytes)):
        try:
            result = json.loads(result)
        except Exception:
            return None
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    if _INLINE_TRUNC_RE.search(content):
        return None
    total = result.get("total_lines")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return None
    nums = sorted({int(n) for n in _LINE_NUM_RE.findall(content)})
    if not nums:
        return None
    runs: list = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append([start, prev])
        start = prev = n
    runs.append([start, prev])
    if result.get("next_offset") or result.get("truncated"):
        # 分页只给了一部分：显式把 total 抬到取到范围之外（严格 complete 判定已隐含，
        # 这里再兜一层，防止将来误放宽 complete 判定时把边界页当全文）。
        total = max(total, nums[-1] + 1)
    return runs, total, None  # fingerprint 由调用方 stat 后补（需要目标路径）


def _evidence_with_fingerprint(evidence, abs_path):
    """N2：给 `_read_evidence` 的结果补修订指纹；取不到指纹 → None（不收账）。"""
    if evidence is None:
        return None
    runs, total, _ = evidence
    fp = _revision_fingerprint(abs_path)
    if not fp:
        return None
    return runs, total, fp


def _coverage_merge(spans, new_span):
    """把新区间并入已覆盖区间列表（合并相邻/重叠），返回排序后的 [[s,e], …]。"""
    items = [
        [int(s[0]), int(s[1])]
        for s in (spans or [])
        if isinstance(s, (list, tuple)) and len(s) == 2
    ]
    items.append([int(new_span[0]), int(new_span[1])])
    items.sort()
    merged: list = []
    for start, end in items:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _coverage_complete(spans, total) -> bool:
    """严格完整判定：**必须是单个覆盖 1..total 的连续区间**，不允许中间有洞。

    旧实现只比较「全局最小起点 / 全局最大终点」（审查 F2）：读第 1 行 + 最后一行即可通过。
    """
    return (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and len(spans) == 1
        and int(spans[0][0]) <= 1
        and int(spans[0][1]) >= total
    )


def _missing_ranges(spans, total):
    """未覆盖的行区间列表（block 文案用）。区间裁到 1..total，丢弃非法/反向区间。

    N5：端点必须**严格是 int**（bool 排除、float/数字字符串拒绝）——旧实现对
    `[1.9, 3.8]` / `["4","8"]` 强转后当作合法覆盖，提示失真。
    """
    total = total if type(total) is int and total > 0 else 0
    clean = []
    for s in spans or []:
        if not isinstance(s, (list, tuple)) or len(s) != 2:
            continue
        a, b = s[0], s[1]
        if type(a) is not int or type(b) is not int:
            continue
        if b < a or b < 1:
            continue
        if total and a > total:
            continue
        clean.append([max(1, a), min(b, total) if total else b])
    missing, cursor = [], 1
    for start, end in sorted(clean):
        if start > cursor:
            missing.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if total and cursor <= total:
        missing.append((cursor, total))
    return missing


def _file_total_lines(path) -> int:
    try:
        return len(Path(path).read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        return 0


def _unread_detail(state, ws, unread):
    """已读但未覆盖全文时，给 block 文案补「尚缺哪些行」的可执行信息（最多列 3 段）。"""
    cov = state.get("coverage") or {}
    parts = []
    for f in unread:
        spans = cov.get(f)
        if not spans:
            continue
        total = _file_total_lines(ws / f)
        miss = _missing_ranges(spans, total) if total else []
        if miss:
            shown = ", ".join(f"{s}–{e}" for s, e in miss[:3])
            more = f"（其余 {len(miss) - 3} 段略）" if len(miss) > 3 else ""
            parts.append(f"{f} 已部分读取，尚缺行 {shown}{more}；")
    return ("".join(parts) + " ") if parts else ""


def _revoke_if_stale(state, ws, fname, now_fp):
    """N2 写前复核：治理文件指纹与会话记录的不一致 → 撤销该文件的许可与覆盖。

    返回 (撤了没有, 撤销明细)。指纹取不到（文件消失/不可 stat）同样视为失效——
    防止「读完 → 文件被换 → 直接写」绕过修订绑定。
    """
    rec = (state.get("coverage_rev") or {}).get(fname)
    if rec is None:
        return False, None
    if rec == now_fp:
        return False, None
    revoked = {
        "from": rec,
        "to": now_fp,
        "coverage": (state.get("coverage") or {}).get(fname),
        "was_read": bool((state.get("read") or {}).get(fname)),
    }
    state.setdefault("revocations", [])
    state["revocations"].append(
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "file": fname, **revoked}
    )
    del state["revocations"][:-MAX_TURN_RECORDS]
    if state.get("read", {}).get(fname):
        state["read"][fname] = False
    if isinstance(state.get("coverage"), dict):
        state["coverage"].pop(fname, None)
    state.setdefault("coverage_rev", {})[fname] = now_fp
    _log(f"coverage revoked (revision changed): {fname} {revoked['from'][:24]}… → {str(now_fp)[:24]}…")
    return True, revoked


def _mark_read(state, ws, abs_path, evidence):
    """严格匹配：realpath 与治理文件 realpath 相等才记账。abs_path 必须已解析。

    `evidence` 是 `_read_evidence()` 的返回值 `(runs, total, fp)`；**None 表示没有可信证据**
    （空正文 / EOF 越界 / dedup 存根 / 行内截断 / 缺 total / 解析失败），此时**不增加证据、
    也不撤销既有状态**——绝不隐式解锁（审查 F3）。
    N2：`fp` 为本次读取后目标的修订指纹。同指纹才累计；与既有覆盖指纹不同 → 旧覆盖
    撤销留档后从本次区间重新积累（跨修订拼接失效）。指纹取不到（stat 失败）时不记账
    （无法绑定修订的证据不收）。
    """
    if not abs_path or evidence is None:
        return False
    runs, total, fp = evidence
    if not fp:
        return False
    try:
        target = Path(abs_path).expanduser().resolve()
    except Exception:
        return False
    changed = False
    cov = state.setdefault("coverage", {})
    rev_map = state.setdefault("coverage_rev", {})
    for f in ALL_GOV:
        try:
            if not _paths_equal(target, (ws / f).resolve()):
                continue
        except Exception:
            continue
        # V140-3：指纹比较必须发生在 read=true 短路**之前**——旧版「已读即跳过」导致
        # 「外部改版 → 完整重读新版 → 首次写仍被拦」（确认轮实测的合法路径误拦）。
        # V141-1（v1.4.1 确认轮）：撤销条件**不得**以 was_read 为前提——旧修订只有部分
        # 覆盖时（read=false 但已有 coverage），不同指纹同样必须先清空旧 coverage 再用
        # 本次证据重建，否则「旧修订读 1–2 + 新修订读 3–4」会拼成 [[1,4]] 放行。
        # was_read 只作审计字段。
        was_read = bool(state["read"].get(f))
        old_fp = rev_map.get(f)
        if old_fp is not None and old_fp != fp:
            state.setdefault("revocations", []).append(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "file": f,
                    "from": old_fp,
                    "to": fp,
                    "coverage": cov.get(f),
                    "was_read": was_read,
                }
            )
            del state["revocations"][:-MAX_TURN_RECORDS]
            if was_read:
                state["read"][f] = False
            cov.pop(f, None)
            _log(f"coverage revoked on revision change (was_read={was_read}): {f}")
        elif was_read:
            continue  # 同指纹且已读：无需重复记账
        spans = cov.get(f) or []
        for run in runs:
            spans = _coverage_merge(spans, run)
        cov[f] = spans
        rev_map[f] = fp
        changed = True
        if _coverage_complete(spans, total):
            state["read"][f] = True
    return changed


def _block(reason):
    return {"action": "block", "message": reason}


def _load_conf(ws):
    """Workspace-level override from `plugin.custom.json` (fail-safe defaults)."""
    conf = {"enabled": True, "gate_files": [GATE_FILE], "write_tools": sorted(WRITE_TOOLS)}
    p = ws / CUSTOM_CONF_REL
    if not p.is_file():
        return conf
    try:
        user = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"plugin.custom.json ignored ({p}): {e!r}")
        return conf
    if not isinstance(user, dict):
        return conf
    if isinstance(user.get("enabled"), bool):
        conf["enabled"] = user["enabled"]
    gf = user.get("gate_files")
    if isinstance(gf, list) and gf and all(isinstance(x, str) and x.strip() for x in gf):
        conf["gate_files"] = [x.strip() for x in gf]
    wt = user.get("write_tools")
    if isinstance(wt, list) and all(isinstance(x, str) and x.strip() for x in wt):
        conf["write_tools"] = [x.strip() for x in wt]
    return conf


def _pre_tool_call(
    tool_name=None,
    args=None,
    session_id="",
    parent_session_id=None,
    **kwargs,
):
    try:
        if not tool_name:
            return None
        if not session_id:
            _log("pre_tool_call: empty session_id — gate skipped (trust boundary)")
            return None
        task_id = kwargs.get("task_id")
        # V140-1：容器侧路径在主机上无法映射回真实工作区（核心把主机 cwd bind-mount 成
        # `/workspace` 并回写 session cwd）——受管写入 fail-closed，这是有意的过度拦截。
        container = _backend_container_mode(task_id)
        if container and tool_name in WRITE_TOOLS:
            return _block(
                "WorkspaceTemplate 守门：容器 backend 的路径无法在主机上映射回真实工作区，"
                "无法证明写入目标的归属（V140-1，fail-closed）。请在主机侧会话中用"
                "绝对路径写入，或由模板维护者在核心提供 backend→host 映射后放开。"
            )
        targets, unresolved = _resolve_target_paths(
            args, tool_name, session_id, parent_session_id, task_id
        )
        if not targets and not unresolved:
            return None
        # 1) **先逐个判定所有已确定的目标**（任一未读即拦）——审查 F4：旧实现先用「按进程 cwd
        #    猜到的某个工作区配置」决定整次调用豁免，会跳过已经明确解析出来的绝对目标，
        #    造成「混合相对/绝对多目标 patch 写进严格工作区」的漏拦，且判定随目标顺序变化。
        for target in targets:
            ws = _find_workspace(target)
            if ws is None:
                continue
            conf = _load_conf(ws)
            if not conf["enabled"] or tool_name not in conf["write_tools"]:
                continue
            with _state_lock(ws, session_id):
                state = _load_state(ws, session_id)
                # N2 写前复核：任一 gate 文件修订变化 → 撤销许可/覆盖（持久化）再判定
                for fname in conf["gate_files"]:
                    _revoke_if_stale(state, ws, fname, _revision_fingerprint(ws / fname))
                _save_state(ws, session_id, state)
                unread = [f for f in conf["gate_files"] if not state["read"].get(f)]
                if unread:
                    detail = _unread_detail(state, ws, unread)
                    return _block(
                        f"WorkspaceTemplate 守门（{ws}）：写工作区文件前请先 read_file 完整读取 "
                        f"{'、'.join(unread)}（治理规则入口）。{detail}目标：{target}"
                    )
        # 2) 无法解析的相对目标：回到保守边界（审查 F4 的短期安全回退）——默认写工具集一律拦，
        #    不接受「另一个工作区的 custom 配置」给未知目标发豁免（custom 的缩窄/放宽只适用于
        #    能确定归属的目标，即上面第 1 步）。这是**有意保留的过度拦截**，见 R98。
        if unresolved:
            if tool_name in WRITE_TOOLS:
                return _block(
                    "WorkspaceTemplate 守门：无法从核心 task 锚点解析相对路径写入目标"
                    f"（{', '.join(unresolved[:3])}）所属工作区（未知目标按保守策略拦截，"
                    "工作区级 custom 不适用于无法归属的目标）。请改用绝对路径，"
                    f"或先 read_file 完整读取 {GATE_FILE} 后改用绝对路径写入。"
                )
            return None
        return None
    except Exception as e:
        _log(f"pre_tool_call error (fail-closed): {e!r}")
        return _block(
            "WorkspaceTemplate 守门内部错误（fail-closed）：请人工检查 workspace-gate plugin。"
        )


# N2 记账窗口的诚实声明（v1.4.1 按确认轮 V140-2 重写；v1.4.2 按 V141-3/V142-2 再修正）：
# v1.4.0 的「post 一次 stat + 写前复核」被确认轮实测证伪——read→post-stat 竞态把旧正文
# 绑到新指纹，写前复核看到的是同一新指纹，无法发现（漏拦，不是「只可能多拦」）。
# v1.4.2 起为**内容自证指纹**：对治理文件**一律**哈希全部字节（无 256KB 阈值、无 stat
# 分量），许可绑定的是「写入时磁盘内容」而非 stat 快照——「旧正文 + 新许可」「同 stat
# tuple 内容替换」「utime 回拨」的放行前提均被逐字节一致性破坏。文件缺失/不可读 →
# 指纹 None → 证据不入账、写前 fail-closed（工作区身份由 bootstrap 承担，gate 文件删除
# 不脱离守门，V142-1）。残余边界（如实声明）：插件层无法证明「返回给会话的正文 ==
# 记账时磁盘内容」——该证明需要核心在 ReadResult 原子携带快照哈希（核心契约需求）。


def _post_tool_call(
    tool_name=None,
    args=None,
    session_id="",
    parent_session_id=None,
    status=None,
    result=None,
    task_id=None,
    **kwargs,
):
    try:
        if tool_name != "read_file":
            return None
        if status != "ok":
            return None
        if not session_id:
            return None
        # V141-2（v1.4.1 确认轮）：容器 backend 的**绝对路径读**会在主机上被误解释——
        # 核心在容器内读「主机工作区路径字符串」时，post hook 会把容器正文的行覆盖绑定
        # 到主机文件的内容指纹上，backend 切回本地后写前复核看到相同哈希即放行（实测
        # 漏拦）。因此容器 backend 的 read 证据一律**不向主机工作区记账**（读操作本身
        # 不拦——治理义务不因读被拦而消失，但没有可信证据就不发许可）。
        container_read = _backend_container_mode(task_id)
        if container_read:
            _log(
                "post_tool_call: container backend read — no host evidence recorded "
                "(review V141-2)"
            )
            return None
        # N1：读证据与写许可共用核心同源锚点。相对路径无锚点 → 不记账（反向路径：
        # 只限制相对写、继续给相对读记账，挡不住 DB cwd=W、task cwd=L 的错区冒领）。
        raw_path = _absolutize(
            (args or {}).get("path"), session_id, parent_session_id, task_id
        )
        if raw_path is None:
            raw = str((args or {}).get("path") or "")
            if raw and not os.path.isabs(os.path.expanduser(raw)):
                _log(
                    "post_tool_call: relative read without authoritative task anchor "
                    "— no evidence recorded (review N1)"
                )
                return None
        target = raw_path
        ws = _find_workspace(target)
        if ws is None:
            return None
        # `result` 在线上是 JSON 字符串（见 _read_evidence 的契约说明）。
        evidence = _evidence_with_fingerprint(_read_evidence(result), target)
        if evidence is None:
            # 无可信证据（空正文 / EOF 越界 / dedup 存根 / 行内截断 / 缺 total / 解析失败 /
            # 指纹取不到，审查 F3/N2）：**不改状态**——既不增加已读证据，也不撤销既有状态。
            _log(
                "post_tool_call: no trustworthy read evidence "
                f"(result type={type(result).__name__}) — coverage unchanged"
            )
            return None
        with _state_lock(ws, session_id):
            state = _load_state(ws, session_id)
            changed = _mark_read(state, ws, target, evidence)
            if state.get("status") == "archived":
                # 审查 F6：恢复后的标签要真实，不能一直挂 archived
                state["status"] = "resumed"
                changed = True
            if changed:
                _save_state(ws, session_id, state)
        return None
    except Exception as e:
        _log(f"post_tool_call error: {e!r}")
        return None


def _touch_session_alive(state):
    """N3：在**真实活动点**（pre_llm / on_session_end）把 archived 翻回 resumed 并开启新 epoch。

    正常恢复（resume 后继续对话）不一定伴随 read_file——v1.3.2 把 resumed 赋值只放在
    post_tool_call 里，纯问答/零回合的恢复会一直挂着 archived，下一次 finalize 就被
    误判成「重复收尾」而覆盖上一条边界（确认轮 N3）。任何活动点都能结束 archived：
    epoch 单调递增，finalize 按 (epoch, total_turns) 区分阶段。
    """
    changed = False
    if state.get("status") == "archived":
        state["status"] = "resumed"
        state["epoch"] = int(state.get("epoch") or 0) + 1
        changed = True
    return changed


def _pre_llm_call(session_id="", parent_session_id=None, **kwargs):
    try:
        if not session_id:
            return None
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        ws = _find_workspace(cwd)
        if ws is None:
            return None
        with _state_lock(ws, session_id):
            state = _load_state(ws, session_id)
            flipped = _touch_session_alive(state)  # N3：任何活动点都结束 archived
            if state.get("reminder_sent") and not flipped:
                return None
            conf = _load_conf(ws)
            unread_gate = [f for f in conf["gate_files"] if not state["read"].get(f)]
            if not unread_gate:
                if flipped:
                    _save_state(ws, session_id, state)  # 翻转必须落盘，否则 finalize 会误判同阶段
                return None
            other = [f for f in ALL_GOV if f not in conf["gate_files"] and not state["read"].get(f)]
            state["reminder_sent"] = True
            _save_state(ws, session_id, state)
        text = f"[workspace-gate] 尚未读取的治理文件: {', '.join(unread_gate)}。请先读取 {GATE_FILE}。"
        if other:
            text += f"（本工作区另需按需读取: {', '.join(other)}）"
        return {"context": text}
    except Exception as e:
        _log(f"pre_llm_call error: {e!r}")
        return None


def _on_session_end(
    session_id="",
    parent_session_id=None,
    completed=None,
    interrupted=None,
    failed=None,
    turn_id=None,
    turn_exit_reason=None,
    **kwargs,
):
    """回合级事件：按回合有界追加（scope="turn"），另存单调回合计数 `total_turns`。

    审查 F6：审计字段补 `turn_id` / `failed` / `reason`（核心 payload 明确提供），
    `total_turns` 作为不受环形裁剪影响的单调计数。
    """
    try:
        if not session_id:
            return None
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        ws = _find_workspace(cwd)
        if ws is None:
            return None
        with _state_lock(ws, session_id):
            state = _load_state(ws, session_id)
            _touch_session_alive(state)  # N3：回合事件本身就是「会话活着」的证据
            state["total_turns"] = int(state.get("total_turns") or 0) + 1
            state["ends"].append(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "scope": "turn",
                    "turn_id": turn_id,
                    "completed": completed,
                    "failed": failed,
                    "interrupted": interrupted,
                    "reason": turn_exit_reason,
                    "read": sorted(f for f, done in state["read"].items() if done),
                    "closure": state.get("closure", False),
                }
            )
            del state["ends"][:-MAX_TURN_RECORDS]
            state["last_end"] = state["ends"][-1]
            _save_state(ws, session_id, state)
        return None
    except Exception as e:
        _log(f"on_session_end error: {e!r}")
        return None


def _on_session_finalize(session_id="", parent_session_id=None, **kwargs):
    """会话级收尾审计（payload：`{"session_id", "platform", "reason"}`，已核对核心源码）。

    会话边界记录写入独立的 `boundaries` 缓冲区；并把 `status` 标记为 `archived`；
    会话恢复后的**任意真实活动点**（pre_llm_call / on_session_end / post_tool_call）
    都会把状态翻回 `resumed` 并递增 `epoch`（审查 N3：不能依赖 read_file 才触发）。

    幂等语义（N3 处置）：finalize 按 `(epoch, total_turns)` 去重——**同一阶段**的重复
    收尾覆盖同一条（幂等）；**不同阶段**（恢复后再收尾，epoch 或回合计数已前进）一律
    **追加**新边界，绝不覆盖上一阶段的记录。极小残余边界：若核心存在「零回合且不触发
    任何活动点钩子的恢复」，两次收尾仍会被视为同一阶段——本实现的诚实声明，见 R98。

    **覆盖范围的诚实声明**：本事件只归属「会话 cwd 所属工作区」；同一会话通过绝对路径
    写入的**其他**工作区不在本次边界记录内（它们的读证据仍各自记账）。
    """
    try:
        if not session_id:
            return None
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        ws = _find_workspace(cwd)
        if ws is None:
            return None
        with _state_lock(ws, session_id):
            state = _load_state(ws, session_id)
            epoch = int(state.get("epoch") or 0)
            total_turns = int(state.get("total_turns") or 0)
            entry = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scope": "session",
                "epoch": epoch,
                "turns_at_close": total_turns,
                "reason": kwargs.get("reason"),
                "platform": kwargs.get("platform"),
                "total_turns": total_turns,
                "read": sorted(f for f, done in state["read"].items() if done),
                "unread": sorted(f for f, done in state["read"].items() if not done),
                "coverage": {
                    f: spans for f, spans in (state.get("coverage") or {}).items() if spans
                },
                "closure": state.get("closure", False),
            }
            boundaries = state["boundaries"]
            last = boundaries[-1] if boundaries else None
            if (
                last is not None
                and last.get("scope") == "session"
                and state.get("status") == "archived"
                and int(last.get("epoch") or 0) == epoch
                and int(last.get("turns_at_close") or 0) == total_turns
            ):
                boundaries[-1] = entry  # 同一阶段的重复 finalize：幂等覆盖
            else:
                boundaries.append(entry)  # 不同阶段：保留历史，追加
            del boundaries[:-MAX_BOUNDARY_RECORDS]
            state["last_finalize"] = entry
            state["status"] = "archived"
            _save_state(ws, session_id, state)
        return None
    except Exception as e:
        _log(f"on_session_finalize error: {e!r}")
        return None


def register(ctx):
    _log(f"workspace-gate active (v1.4.3, hermes_home={_hermes_home()})")
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
