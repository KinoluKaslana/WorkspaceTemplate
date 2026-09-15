"""workspace-gate — WorkspaceTemplate 工作区级治理 hook（Hermes 项目级 plugin）。

随工作区分发（`.hermes/plugins/workspace-gate/`），在 WorkspaceTemplate 派生工作区
强制「先读治理文件，再写工作区文件」。启用方式：`HERMES_ENABLE_PROJECT_PLUGINS=true`。

核心判据：守门看「写入目标路径是否在工作区内」，而非「会话 cwd 是否在工作区」。

事件：
- pre_tool_call   守门（write_file/patch 的任一目标在工作区内且未读治理文件 → block）
- post_tool_call  记录（read_file status=="ok" 且 realpath 严格命中治理文件 → 标记已读）
- pre_llm_call    提醒（每个会话至多注入一次未读清单）
- on_session_end  审计（回合级事件：有界追加，不覆盖历史）
- 未注册 on_session_finalize：其 payload 契约未在本机实测确认，会话级收尾审计留待后续
  （见 notes/workspace-gate-hook-260915.md 的未决项）

cwd 获取：plugin 回调 payload 无 cwd，用 session_id 反查 state.db——sessions.cwd 列
为主；model_config.cwd 兜底（ACP/desktop）；再沿 parent_session_id 链向上（delegate/
cron/gateway 子会话）。state.db 位置跟随 $HERMES_HOME（与 hermes_constants.get_hermes_home
的进程作用域一致：env → 平台默认），profile 会话由启动器注入 HERMES_HOME，故同样命中。

守门判据的落点：
- 绝对路径目标：直接按目标路径找工作区（与「会话 cwd」无关）→ 会话在区外、却写区内
  文件同样被拦。
- 相对路径目标：需要会话 cwd 才能解析；解析不出（session 未落库、profile/DB 不匹配）
  时**不放行**——按 fail-closed 拦截并给出可执行的替代方案（改用绝对路径）。懒 agent
  最爱用相对路径，静默放行等于守门失效。
- 提醒（pre_llm_call）以「会话 cwd 所属工作区」为对象，与守门的「目标路径」判据不
  完全一致：cwd 在区外但写区内文件的会话「有拦截、无提醒」——这是有意为之，不是 bug。

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
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

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
            path_hit = False
            try:
                path_hit = (anc / GATE_FILE).is_file()
            except OSError:
                path_hit = False
            if path_hit:
                if (anc / ".workspace" / "bootstrap.json").is_file():
                    try:
                        return anc.resolve()
                    except Exception:
                        return anc
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


def _absolutize(path_arg, session_id, parent_session_id=None):
    """绝对化；相对路径经会话 cwd 解析。解析不出返回 None（由调用方决定 fail-closed）。"""
    if path_arg is None:
        return None
    p = os.path.expanduser(str(path_arg))
    if not p:
        return None
    if not os.path.isabs(p):
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        if not cwd:
            return None
        p = os.path.join(cwd, p)
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


def _resolve_target_paths(args, tool_name, session_id, parent_session_id=None):
    """返回 (已解析绝对路径列表, 无法解析的相对路径列表)。"""
    resolved, unresolved = [], []
    for raw in _raw_targets(args, tool_name):
        abs_path = _absolutize(raw, session_id, parent_session_id)
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


def _mark_read(state, ws, abs_path):
    """严格匹配：realpath 与治理文件 realpath 相等才标记。abs_path 必须已解析。"""
    if not abs_path:
        return False
    try:
        target = Path(abs_path).expanduser().resolve()
    except Exception:
        return False
    changed = False
    for f in ALL_GOV:
        if state["read"].get(f):
            continue
        try:
            if _paths_equal(target, (ws / f).resolve()):
                state["read"][f] = True
                changed = True
        except Exception:
            pass
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
        targets, unresolved = _resolve_target_paths(
            args, tool_name, session_id, parent_session_id
        )
        default_tool = tool_name in WRITE_TOOLS
        if not targets and not (unresolved and default_tool):
            return None
        # 相对路径解析不出会话 cwd：静默放行＝对懒 agent 完全失效 → fail-closed。
        if unresolved and default_tool:
            return _block(
                "WorkspaceTemplate 守门：无法解析会话 cwd，不能确认相对路径写入目标"
                f"（{', '.join(unresolved[:3])}）是否在工作区内。请改用绝对路径，"
                f"或先 read_file 读取 {GATE_FILE} 后改用绝对路径写入。"
            )
        for target in targets:
            ws = _find_workspace(target)
            if ws is None:
                continue
            conf = _load_conf(ws)
            if not conf["enabled"] or tool_name not in conf["write_tools"]:
                continue
            state = _load_state(ws, session_id)
            unread = [f for f in conf["gate_files"] if not state["read"].get(f)]
            if unread:
                return _block(
                    f"WorkspaceTemplate 守门（{ws}）：写工作区文件前请先 read_file 读取 "
                    f"{'、'.join(unread)}（治理规则入口）。目标：{target}"
                )
        return None
    except Exception as e:
        _log(f"pre_tool_call error (fail-closed): {e!r}")
        return _block(
            "WorkspaceTemplate 守门内部错误（fail-closed）：请人工检查 workspace-gate plugin。"
        )


def _post_tool_call(
    tool_name=None,
    args=None,
    session_id="",
    parent_session_id=None,
    status=None,
    **kwargs,
):
    try:
        if tool_name != "read_file":
            return None
        if status != "ok":
            return None
        if not session_id:
            return None
        target = _absolutize((args or {}).get("path"), session_id, parent_session_id)
        ws = _find_workspace(target)
        if ws is None:
            return None
        with _LOCK:
            state = _load_state(ws, session_id)
            if _mark_read(state, ws, target):
                _save_state(ws, session_id, state)
        return None
    except Exception as e:
        _log(f"post_tool_call error: {e!r}")
        return None


def _pre_llm_call(session_id="", parent_session_id=None, **kwargs):
    try:
        if not session_id:
            return None
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        ws = _find_workspace(cwd)
        if ws is None:
            return None
        with _LOCK:
            state = _load_state(ws, session_id)
            if state.get("reminder_sent"):
                return None
            conf = _load_conf(ws)
            unread_gate = [f for f in conf["gate_files"] if not state["read"].get(f)]
            if not unread_gate:
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
    **kwargs,
):
    """回合级事件：按回合有界追加，不覆盖历史。"""
    try:
        if not session_id:
            return None
        cwd = _resolve_session_cwd(session_id, parent_session_id)
        ws = _find_workspace(cwd)
        if ws is None:
            return None
        with _LOCK:
            state = _load_state(ws, session_id)
            state["ends"].append(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "completed": completed,
                    "interrupted": interrupted,
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


def register(ctx):
    _log(f"workspace-gate active (v1.2.1, hermes_home={_hermes_home()})")
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
