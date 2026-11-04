#!/usr/bin/env python3
"""gate_core.py — WorkspaceTemplate 客户端治理守门·唯一决策核心（v1.1.0）

语义基准：Hermes workspace-gate v1.4.3（C1 六件套）+ 三方讨论收敛案（C12 三段式记账）。
纯 stdlib；无网络；除状态/日志/审计外无其他 IO 副作用。

六件套（C1）：
  1. 工作区身份 = .workspace/bootstrap.json 文件存在（V142-1 教训，不依赖 gate 文件同时存在）
  2. 完整读判定 = 覆盖区间 1..total 合并后无洞（分次可累加；只读首尾不算）
  3. 修订指纹 = 全量内容 sha256（c<len>:<digest>；无大小阈值，V140-2）
  4. 写前复核重哈希：指纹不一致 → 撤销旧记账（留档 was_read=true）→ 按 unread 处理（V140-3/V142-1）
  5. 内部错误 fail-closed：core 异常 → 一律拦截 + 「人工检查」文案
  6. 空正文 / EOF 越界 / 截断 marker 不入账（`_read_evidence` 同款判定）

内容证据记账（失败方向安全：宁可少记账、不可假记账）：
  ① content 锚定：tool_response 原文与磁盘对应区间字节全等比对（不落盘）；
     任一侧含截断 marker（head/tail omission 或 token "Warning: truncated output"）即不记账；
     行尾规范化差异按不可判定处理（不做任何哈希前归一化）。
  ② 截断即沉默：检测到截断 → 不记账、不假记账（deny 文案指引重读/分段）。
  ③ 命令串不是阅读证据；无响应、截断及不可判定均不授证。

状态 schema 5；旧许可清空留档（含旧 adapter 未验证成功/元数据的 schema 3/4）；
flock 并发；mkstemp+os.replace 原子写。interval 仅为 content 的覆盖方式，不是弱证据。
"""
from __future__ import annotations

try:
    import fcntl                      # POSIX
except ImportError:                   # Windows：没有 fcntl，用 msvcrt 做同语义的独占锁
    fcntl = None
    import msvcrt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

SCHEMA = 5
BOOTSTRAP_REL = ".workspace/bootstrap.json"
CUSTOM_REL = ".workspace/gate.custom.json"
STATE_DIRNAME = ".workspace/.hook-state"
GATE_FILE = "AGENT_RULES.md"
DEFAULT_GATE_FILES = [GATE_FILE]
DEFAULT_WRITE_TOOLS = ["write", "edit", "apply_patch"]

LOG_ROTATE_BYTES = 1_000_000

# 截断 marker（实测形态，2026-09-17 evidence-payload-codex.txt / round1 §1.2）
TRUNCATION_MARKERS = (
    "Warning: truncated output",       # agent 侧 token 层（Codex 实测头部）
    "bytes omitted",                   # head/tail omission 中段标记
    "characters omitted",
    "[... truncated",
    "... [truncated",                  # Hermes 同款尾部标记
    "output truncated",
)

GIT_DIR_BOUNDARY = ".git"  # 目录才是边界；文件（worktree/submodule gitdir）不是


# ---------------------------------------------------------------- 基础工具

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_fingerprint(path: str) -> str | None:
    """全量内容 sha256（c<len>:<digest>）。读取失败/文件缺失 → None（fail 方向：不记账）。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return f"c{len(data)}:{sha256_hex(data)}"


def _contains_truncation_marker(text: str) -> bool:
    # Recognize output markers, not prose quoting them (the rules document does).
    return any(
        re.search(r"(?:^\s*Warning: truncated output\b|"
                  r"^\s*.*\b\d+ (?:bytes|characters) omitted\b|"
                  r"(?:\.\.\. \[truncated[^\]]*\]|\[\.\.\. truncated[^\]]*\])\s*$|"
                  r"^\s*output truncated\b)", line, re.IGNORECASE)
        for line in text.splitlines()
    )


def _norm_join(base: str, rel: str) -> str:
    if rel.startswith("~"):
        rel = os.path.expanduser(rel)
    if not os.path.isabs(rel):
        rel = os.path.join(base, rel)
    return os.path.normpath(rel)


# ------------------------------------------------------------ 工作区发现

def _is_dir_git_boundary(p: Path) -> bool:
    return (p / GIT_DIR_BOUNDARY).is_dir()


def _has_bootstrap(d: Path) -> bool:
    try:
        return (d / BOOTSTRAP_REL).is_file()
    except OSError:
        return False


def find_workspace(target: str | None) -> str | None:
    """从目标路径向上找最近的工作区根（bootstrap.json 所在）。

    双判定（Hermes 同款）：先词法路径（normpath，不穿透 symlink），再 resolved 路径；
    词法分支堵「工作区内 symlink → 区外文件」逃逸。`.git` 目录 = 搜索边界（父工作区接管）。
    """
    if not target:
        return None
    raw = os.path.normpath(os.path.expanduser(str(target)))
    try:
        resolved = str(Path(raw).resolve())
    except OSError:
        resolved = None
    for cand in (raw, resolved):
        if not cand:
            continue
        d = Path(cand)
        while True:
            if _has_bootstrap(d):
                return str(d)
            if _is_dir_git_boundary(d) or d.parent == d:
                break
            d = d.parent

    return None


# ------------------------------------------------------------ 配置（fail-safe）

def _default_conf() -> dict:
    return {"enabled": True, "gate_files": list(DEFAULT_GATE_FILES),
            "write_tools": list(DEFAULT_WRITE_TOOLS)}


def load_conf(ws_root: str) -> dict:
    conf = _default_conf()
    p = Path(ws_root) / CUSTOM_REL
    if not p.is_file():
        return conf
    try:
        user = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return conf  # 损坏 → 忽略，fail-safe 默认（调用方记日志）
    if not isinstance(user, dict):
        return conf
    if isinstance(user.get("enabled"), bool):
        conf["enabled"] = user["enabled"]
    gf = user.get("gate_files")
    if isinstance(gf, list) and gf and all(isinstance(x, str) and x.strip() for x in gf):
        conf["gate_files"] = [x.strip() for x in gf]
    wt = user.get("write_tools")
    if isinstance(wt, list) and wt and all(isinstance(x, str) and x.strip() for x in wt):
        conf["write_tools"] = [x.strip() for x in wt]
    return conf


# ------------------------------------------------------------ 豁免令牌（W2/B）

AUTHZ_FILENAME = "authz.json"


def _authz_path(ws_root: str) -> Path:
    return Path(ws_root) / STATE_DIRNAME / AUTHZ_FILENAME


def load_authz(ws_root: str) -> dict:
    """损坏/缺失 → 视为无令牌（fail-safe）。返回 {"schema":1,"tokens":[]}。"""
    p = _authz_path(ws_root)
    if not p.is_file():
        return {"schema": 1, "tokens": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": 1, "tokens": []}
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), list):
        return {"schema": 1, "tokens": []}
    return data


def save_authz(ws_root: str, data: dict) -> None:
    p = _authz_path(ws_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="authz.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def consume_authz(ws_root: str, targets: list[str]) -> dict | None:
    """B3：消费一个未过期、未消耗、目标集合精确相等的豁免令牌。

    命中 → 标 consumed=True 落盘并返回 {"id","targets"}；否则 None。异常吞掉记 log。
    """
    try:
        if not isinstance(targets, list) or not targets:
            return None
        tset = {os.path.normpath(os.path.expanduser(t)) for t in targets if isinstance(t, str)}
        if not tset:
            return None
        data = load_authz(ws_root)
        tokens = data.get("tokens")
        if not isinstance(tokens, list):
            return None
        now = time.time()
        hit = None
        for tok in tokens:
            if not isinstance(tok, dict):
                continue
            if tok.get("consumed"):
                continue
            exp = tok.get("expires_at")
            if not isinstance(exp, (int, float)) or exp <= now:
                continue
            tt = tok.get("targets")
            if not isinstance(tt, list):
                continue
            if {os.path.normpath(os.path.expanduser(t)) for t in tt if isinstance(t, str)} != tset:
                continue
            hit = tok
            break
        if hit is None:
            return None
        hit["consumed"] = True
        save_authz(ws_root, data)
        return {"id": hit.get("id"), "targets": list(hit.get("targets", []))}
    except Exception as exc:
        log(ws_root, f"consume_authz failed: {exc}")
        return None


# ------------------------------------------------------------ 状态（flock + 原子写）


# ------------------------------------------------------------ agent 作用域（2.7.0 / P1）
AGENT_SCOPE_SEP = "."


def scoped_id(session_id: str, agent_id: object = None) -> str:
    """把 (session, agent) 折叠成状态键；无 agent 时保持原样（主线程）。

    状态文件名沿用 `_state_path` 的 `{client}-{sid}.json`，scoped 键形如
    `<session>.<agent>`，因此同一会话的不同 agent 各有独立账本与锁。
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("missing session identity")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return session_id
    agent = agent_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", agent):
        agent = sha256_hex(agent.encode("utf-8"))[:32]
    # 幂等：有些客户端会把 agent 折进 session_id 一起给，重复拼接会让同一子 agent
    # 出现两个账本（v2.8.5 实测：cc-<sid>.<agent>.<agent>.json）。
    if session_id.endswith(f"{AGENT_SCOPE_SEP}{agent}"):
        return session_id
    return f"{session_id}{AGENT_SCOPE_SEP}{agent}"


def split_scoped_id(key: object) -> tuple[str, str | None]:
    if not isinstance(key, str):
        return "", None
    base, sep, agent = key.partition(AGENT_SCOPE_SEP)
    return (base, agent) if sep and agent else (key, None)


def file_sha(path: object) -> str | None:
    try:
        return sha256_hex(Path(str(path)).read_bytes())
    except OSError:
        return None


def record_intent(state: dict, targets: list[str]) -> list[dict]:
    """写前意图：target + 写前内容 sha（不存在则 None）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    recs = []
    events = state.get("events")
    if not isinstance(events, list):
        events = []
    for target in targets:
        rec = {"type": "gate-intent", "target": os.path.normpath(str(target)),
               "base_sha": file_sha(target), "ts": ts}
        events.append(rec)
        recs.append(rec)
    state["events"] = events[-200:]
    return recs


def _git_ok(ws_root: str) -> bool:
    """工作区是不是 git 仓库。git 在模板里是可选初始化项（§0.3），必须能区分
    「不是仓库」和「是仓库但干净」——`git status` 两者都返回非空/空，靠 rc 判。"""
    try:
        r = subprocess.run(["git", "-C", ws_root, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and (r.stdout or "").strip() == "true"
    except Exception:
        return False


def _recent_files(ws_root: str, since_ns: int, cap: int = 2000) -> list[str]:
    """非 git 工作区的差集：扫出 mtime ≥ 水位的文件（相对路径，'/' 分隔）。

    为什么不存全量基线：基线要落进会话状态，条数一多就得截断，截断后**干净的树**也会
    每次审计都报一堆假阳性（实测 700 文件 / 截断 500 → 每次 20 条假告警）。水位线是
    O(1) 状态，且「本次会话后有没有人动过这个文件」正是要回答的问题。
    """
    skip = {".git", ".workspace", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache"}
    out: list[str] = []
    try:
        for root, dirs, files in os.walk(ws_root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".venv-")]
            for name in files:
                p = os.path.join(root, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime_ns >= since_ns:
                    out.append(os.path.relpath(p, ws_root).replace(os.sep, "/"))
                    if len(out) >= cap:
                        return out
    except Exception:
        return out
    return out


HASH_MAX_FILE = 1 << 20          # 单文件超过 1 MiB 不算哈希（预算留给小文件）
HASH_BUDGET_BYTES = 32 << 20     # 每次哈希扫描最多读 32 MiB
HASH_CAP = 2000                  # 最多记多少个文件的指纹


def _hash_baseline_path(ws_root: str, client: str, session_id: str):
    return Path(ws_root) / STATE_DIRNAME / ("hashes-%s-%s.json" % (client, session_id))


def _scan_hashes(ws_root: str, budget: int = HASH_BUDGET_BYTES,
                 max_file: int = HASH_MAX_FILE, cap: int = HASH_CAP):
    """对预算内的小文件算 sha256，返回 ({rel: sha}, complete)。

    为什么需要这一层：非 git 工作区的水位线只看 mtime，而 `cp -p` / `tar -x` /
    `rsync -t` 这类写会**保留原 mtime**——只靠水位线会漏。哈希兜底按预算覆盖小文件，
    超预算的文件记 `complete=False`（宁可标不完整，也不假装覆盖了）。
    """
    out: dict[str, str] = {}
    used = 0
    skip = {".git", ".workspace", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache"}
    try:
        for root, dirs, files in os.walk(ws_root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".venv-")]
            for name in files:
                p = os.path.join(root, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size > max_file:
                    continue
                if used + st.st_size > budget or len(out) >= cap:
                    return out, False
                sha = file_sha(p)
                if sha:
                    out[os.path.relpath(p, ws_root).replace(os.sep, "/")] = sha
                    used += st.st_size
    except Exception:
        return out, False
    return out, True


def worktree_snapshot(ws_root: str) -> dict[str, str | None]:
    """git 工作区快照：git status 列出的路径 → 内容 sha。

    非 git 工作区返回空（差集改由 `_recent_files` 的水位线完成，见 `worktree_audit`）——
    不要在这里回退成"全量文件清单"，那份清单必须落盘，落盘就得截断，截断就制造假阳性。
    """
    snap: dict[str, str | None] = {}
    try:
        st = subprocess.run(["git", "-C", ws_root, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=5)
        if st.returncode != 0:
            return snap
        for line in (st.stdout or "").splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip()
            if " -> " in rel:
                rel = rel.split(" -> ")[-1].strip()
            if not rel:
                continue
            abs_path = os.path.normpath(os.path.join(ws_root, rel))
            snap[rel] = file_sha(abs_path) if os.path.isfile(abs_path) else None
    except Exception:
        return snap
    return snap


def sibling_commits(ws_root: str, client: str, session_id: str) -> set[str]:
    """同一会话（含其所有子 agent 作用域）已 commit 的目标集合。

    worktree 差集是**按会话**算的，而 commit 落在各 agent 的作用域账本里；
    不合并就会把「子 agent 合法写」误报成 unobserved-write。
    """
    base, _ = split_scoped_id(session_id)
    out: set[str] = set()
    try:
        d = Path(ws_root) / STATE_DIRNAME
        prefix = f"{client}-{base}"
        for path in d.glob(f"{client}-{base}*.json"):
            stem = path.name[len(client) + 1:-5]
            if stem != base and not stem.startswith(base + AGENT_SCOPE_SEP):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for e in (data.get("events") or []):
                if isinstance(e, dict) and e.get("type") == "gate-commit":
                    t = e.get("target")
                    if isinstance(t, str):
                        out.add(t)
    except Exception:
        return out
    return out


def _append_commit_ledger(ws_root: str, client: str, session_id: str, rec: dict) -> None:
    """工作区级 commit 账本（跨会话/跨 agent）：worktree 差集按它判定「已观测」。"""
    try:
        d = Path(ws_root) / STATE_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        p = d / "commits.jsonl"
        if p.exists() and p.stat().st_size > LOG_ROTATE_BYTES:
            # 压缩而不是丢弃：按 target 只保留最新一条（体积按 target 数有界）。
            latest: dict[str, dict] = {}
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    t = entry.get("target")
                    if isinstance(t, str):
                        latest[t] = entry
            except Exception:
                latest = {}
            with open(p, "w", encoding="utf-8") as fh:
                for entry in latest.values():
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"client": client, "session_id": session_id, **rec},
                                ensure_ascii=False) + "\n")
    except Exception as exc:
        log(ws_root, f"commit ledger append failed: {exc}")


def committed_targets(ws_root: str, current_sha: dict[str, str | None]) -> set[str]:
    """从工作区级账本读出「当前磁盘 sha 与账本记录一致」的目标集合。"""
    out: set[str] = set()
    try:
        p = Path(ws_root) / STATE_DIRNAME / "commits.jsonl"
        if not p.is_file():
            return out
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        # 压缩后体积按 target 数有界；这里不硬截行数，避免「轮转后判定不到」。
        if len(lines) > 50000:
            lines = lines[-50000:]
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("target")
            sha = rec.get("new_sha")
            if isinstance(t, str) and isinstance(sha, str) and current_sha.get(t) == sha:
                out.add(t)
    except Exception:
        return out
    return out

def _state_path(ws_root: str, client: str, session_id: str) -> Path:
    if client not in ("cc", "codex", "dsh") or not isinstance(session_id, str) or not session_id:
        raise ValueError("missing or invalid client/session identity")
    sid = session_id if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", session_id) else (
        "~" + sha256_hex(session_id.encode("utf-8")))
    return Path(ws_root) / STATE_DIRNAME / f"{client}-{sid}.json"


class _StateLock:
    """独占锁，持锁窗口仅覆盖读-改-写（防并发丢更新）。

    POSIX 用 `fcntl.flock`；Windows 没有 fcntl，用 `msvcrt.locking` 锁同一字节区间。
    两种实现都只保护「读-改-写」窗口，不改变状态语义。
    """

    def __init__(self, ws_root: str, client: str, session_id: str):
        sp = _state_path(ws_root, client, session_id)
        sp.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = Path(str(sp) + ".lock")
        self._fh = None  # IO[bytes] | None

    def __enter__(self):
        self._fh = open(self._lock_path, "a+b")
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        else:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *exc):
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            else:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._fh.close()
            self._fh = None


def load_state(ws_root: str, client: str, session_id: str) -> dict:
    sp = _state_path(ws_root, client, session_id)
    state: dict | None = None
    if sp.is_file():
        try:
            loaded = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except Exception:
            state = None  # 损坏 → 重建（调用方记日志）
    if state is None:
        state = {"session_id": session_id, "workspace_root": ws_root}
    if (state.get("schema") != SCHEMA or state.get("session_id") != session_id
            or state.get("client") != client or state.get("workspace_root") != ws_root):
        state["legacy"] = {"schema": state.get("schema"),
                           "reads": state.pop("reads", {}),
                           "previous": state.get("legacy")}
    state["schema"] = SCHEMA
    state["client"] = client
    state["session_id"] = session_id
    state["workspace_root"] = ws_root
    rem = state.get("reminder")
    if not isinstance(rem, dict):
        rem = {}
    rem.setdefault("channels", {"session_start": False, "user_prompt": 0})
    state["reminder"] = rem
    env = state.get("env")
    if not isinstance(env, dict):
        env = {}
    env.setdefault("permission_mode", None)
    state["env"] = env
    reads = state.get("reads")
    if not isinstance(reads, dict):
        reads = {}
    state["reads"] = reads
    if not isinstance(state.get("revocations"), list):
        state["revocations"] = []
    if not isinstance(state.get("ends"), list):
        state["ends"] = []
    if not isinstance(state.get("events"), list):
        state["events"] = []
    if not isinstance(state.get("anchors"), dict):
        state["anchors"] = {}
    state.setdefault("legacy", None)
    return state


def _mark_activity(state: dict) -> None:
    """Resume on observed activity, never merely on load or elapsed time."""
    lifecycle = state.setdefault("lifecycle", {"epoch": 0, "status": "active"})
    if lifecycle.get("status") == "ended":
        lifecycle["epoch"] += 1
        lifecycle["status"] = "resumed"


def note_activity(ws_root: str, client: str, session_id: str) -> None:
    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        _mark_activity(state)
        save_state(ws_root, client, session_id, state)


def save_state(ws_root: str, client: str, session_id: str, state: dict) -> None:
    sp = _state_path(ws_root, client, session_id)
    sp.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(sp.parent), prefix=sp.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, sp)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------ 覆盖区间（C1-4）

def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    ivs = sorted([a, b] for a, b in intervals if b >= a)
    out: list[list[int]] = []
    for a, b in ivs:
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def coverage_complete(coverage: list[list[int]], total_lines: int) -> bool:
    """完整读 = 合并后恰好覆盖 1..total（无洞；空正文/EOF 越界天然不入账）。"""
    if total_lines <= 0 or not coverage:
        return False
    merged = merge_intervals(coverage)
    return merged == [[1, total_lines]]


def _fmt_ivs(ivs: list[list[int]]) -> str:
    """区间串：[[1,5],[8,8]] → "1..5、8"；空 → "无"。"""
    if not ivs:
        return "无"
    parts = [str(a) if a == b else f"{a}..{b}" for a, b in ivs]
    return "、".join(parts)


def _missing_intervals(coverage: list[list[int]], total: int) -> list[list[int]]:
    """1..total 内未被覆盖的区间（C9 缺失区间）。"""
    merged = merge_intervals(coverage)
    missing: list[list[int]] = []
    cursor = 1
    for a, b in merged:
        if a > cursor:
            missing.append([cursor, a - 1])
        cursor = max(cursor, b + 1)
    if cursor <= total:
        missing.append([cursor, total])
    return missing


def _physical_lines(raw: bytes) -> int:
    """wc -l 口径：正文 LF 行数（末尾空 EOF 行不计）。"""
    if not raw:
        return 0
    return len(raw.split(b"\n")) - (1 if raw.endswith(b"\n") else 0)


def _c9_deny_reason(ws_root: str, gate_rel: str, coverage: list[list[int]],
                    total: int, target: str | None = None,
                    physical: int | None = None) -> str:
    """C9 文案：已覆盖 / 缺失 / 共 N 行 / 下一步 四要素。不含 nonce、不含其他会话信息。

    `total` 保持 core 的 split(b"\\n") 行数契约（缺失区间按此口径）；
    仅当调用方给出 `physical` 且与 `total` 不同（文末有空行）时，追加 wc -l 对照，
    避免模型把文末空行当成「还没读到的那一行」而反复重读。
    """
    cov = _fmt_ivs(merge_intervals(coverage))
    miss = _fmt_ivs(_missing_intervals(coverage, total))
    total_note = f"共 {total} 行"
    if physical is not None and physical != total:
        total_note += f"（wc -l = {physical}，差 {total - physical} 行为文末空行）"
    tail = "下一步：read_file 补齐缺失区间后重试。"
    if target is not None:
        tail += f"目标：{target}"
    return (f"WorkspaceTemplate 守门（{ws_root}）：写工作区文件前须先完整读取治理文件。\n"
            f"文件 {gate_rel}：已覆盖 {cov}；缺失 {miss}；{total_note}。\n"
            f"{tail}")


# ------------------------------------------------------------ 记账（C12 三段式）

def _read_evidence_from_tool_response(tool_response, disk_bytes: bytes) -> dict | None:
    """内容锚定（①）：tool_response 原文与磁盘字节流各自 sha256 后全等比对。

    任一侧含截断 marker → 不给证据（②沉默）。行尾规范化差异按不可判定处理。
    返回 {"anchor": "content"} 或 None。
    """
    if not isinstance(tool_response, str) or not tool_response or not disk_bytes:
        return None
    resp_text = tool_response
    if _contains_truncation_marker(resp_text):
        return None
    resp_bytes = resp_text.encode("utf-8")
    if _contains_truncation_marker(disk_bytes.decode("utf-8", "replace")):
        return None
    if sha256_hex(resp_bytes) != sha256_hex(disk_bytes):
        return None
    return {"anchor": "content"}


_READ_CMD_RE = re.compile(
    r"^\s*(cat|nl|bat)\s+(?:-[A-Za-z]+\s+)*[\"']?([^\"';|&]+)[\"']?\s*$")
_TRUNC_PIPELINE_RE = re.compile(r"\|\s*(head|tail|sed|cut|grep|awk|wc)\b|\bnl\b.*-n\s")


def read_command_full_file(command: str, gate_abs: str) -> bool:
    """仅供诊断的命令识别器；不得用于授证。"""
    if not command or "|" in command:
        # 管道即可能截断/改写（wc -c 之类），一律不给 heuristic
        return False
    m = _READ_CMD_RE.match(command)
    if not m:
        return False
    target = m.group(2).strip().strip('"').strip("'")
    target = os.path.normpath(os.path.expanduser(target))
    return target == os.path.normpath(gate_abs)


def record_read(state: dict, ws_root: str, gate_rel: str, client: str,
                read_info: dict) -> dict:
    """PostToolUse 读记账。read_info: {path, offset?, limit?, tool_response, read_ok}。

    失败方向安全：任何不可判定 → 不入账（不假记账）。
    """
    _mark_activity(state)
    reads = state["reads"]
    path = read_info.get("path") or ""
    gate_abs = _norm_join(ws_root, gate_rel)
    if os.path.normpath(os.path.expanduser(path)) != gate_abs:
        return {"recorded": False, "reason": "not-gate-file"}

    gate_path = Path(gate_abs)
    try:
        disk_bytes = gate_path.read_bytes()
    except OSError:
        _revocation(state, gate_rel, "gate-missing")
        return {"recorded": False, "reason": "gate-missing"}
    fp = f"c{len(disk_bytes)}:{sha256_hex(disk_bytes)}"
    entry = reads.get(gate_rel) or {}
    if entry and entry.get("fingerprint") != fp:
        _revocation(state, gate_rel, "revision-changed")
        entry = {}
    if read_info.get("read_ok") is not True:
        return {"recorded": False, "reason": "read-failed"}
    if not disk_bytes:
        return {"recorded": False, "reason": "empty-body"}
    # LF 行号；保留原始字节和 CRLF，末尾 LF 的空行沿用 CC 行数契约。
    # 仅空 EOF 行的读取不构成正文证据。
    text_lines = disk_bytes.split(b"\n")
    total_lines = len(text_lines)
    # Check metadata against the SAME snapshot used for content and fingerprint.
    if "total_lines" in read_info and (type(read_info["total_lines"]) is not int
                                      or read_info["total_lines"] != total_lines):
        return {"recorded": False, "reason": "total-lines-mismatch"}
    start = read_info.get("offset", 1)
    limit = read_info.get("limit", total_lines)
    if start is None:
        start = 1
    if limit is None:
        limit = total_lines
    if (type(start) is not int or type(limit) is not int
            or start < 1 or start > total_lines or limit < 1):
        return {"recorded": False, "reason": "invalid-interval"}
    if read_info.get("strict_interval") is True:
        # sed addresses real LF-delimited lines, not the core's EOF sentinel.
        # Reject overshoot rather than silently crediting a clamped request.
        physical_lines = total_lines - (1 if text_lines[-1] == b"" else 0)
        if start + limit - 1 > physical_lines:
            return {"recorded": False, "reason": "request-past-eof"}
    end = min(start + limit - 1, total_lines)
    expected = b"\n".join(text_lines[start - 1:end])
    if end < total_lines:
        expected += b"\n"
    if _contains_truncation_marker(disk_bytes.decode("utf-8", "replace")):
        return {"recorded": False, "reason": "truncation-marker"}
    coverage = [list(iv) for iv in entry.get("coverage", []) if isinstance(iv, list)]

    # ① 内容锚定
    ev = _read_evidence_from_tool_response(read_info.get("tool_response"), expected)
    if ev is not None:
        # 返回的最后一个 LF 已证明 EOF 空行，不要求空正文读单独授证。
        if end == total_lines - 1 and text_lines[-1] == b"":
            end = total_lines
        coverage = merge_intervals(coverage + [[start, end]])
        entry.update({
            "anchor": "content",
            "fingerprint": fp,
            "fingerprint_source": "tool_response" if start == 1 and end == total_lines else "disk",
            "coverage": coverage,
            "complete": coverage_complete(coverage, total_lines),
        })
        reads[gate_rel] = entry
        return {"recorded": True, "anchor": "content", "complete": entry["complete"]}

    return {"recorded": False, "reason": "no-admissible-evidence"}


# ------------------------------------------------------------ 写前判定

def _revocation(state: dict, gate_rel: str, reason: str) -> None:
    entry = state["reads"].pop(gate_rel, None)
    if entry:
        state["revocations"].append({
            "file": gate_rel, "was_read": bool(entry.get("complete")),
            "reason": reason, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })


def evaluate_pre_write(ws_root: str, client: str, session_id: str, targets: list[str],
                       conf: dict | None = None) -> dict:
    """Route each absolute target to its own workspace; never use process cwd.

    ws_root is only a configuration hint. Adapters must resolve relative paths
    using their authoritative tool cwd before calling this function.
    """
    try:
        if not isinstance(targets, list) or not targets or any(
                not isinstance(t, str) or not os.path.isabs(t) for t in targets):
            return {"action": "deny", "reason": "无法确定写入目标；请使用绝对路径。", "ws": ws_root}
        # No shared unknown session, even for disabled workspaces.
        _state_path(ws_root, client, session_id)
        grouped = {}
        for target in targets:
            owner = find_workspace(target)
            if owner:
                grouped.setdefault(owner, []).append(target)
        for owner, paths in grouped.items():
            local_conf = conf if owner == os.path.normpath(ws_root) else None
            result = _evaluate_workspace_write(owner, client, session_id, paths, local_conf)
            if result["action"] == "deny":
                return result
        return {"action": "allow", "reason": "ok", "ws": ws_root}
    except Exception:
        return {"action": "deny", "reason": "Workspace gate 内部错误；请人工检查。", "ws": ws_root}


def _evaluate_workspace_write(ws_root: str, client: str, session_id: str,
                              targets: list[str], conf: dict | None = None) -> dict:
    """返回 {"action": "allow"|"deny", "reason": str, "ws": ws_root|None}。

    core 异常由调用方 fail-closed；本函数只处理「可判定」路径。
    """
    conf = conf or load_conf(ws_root)
    if not conf["enabled"]:
        return {"action": "allow", "reason": "gate-disabled", "ws": ws_root}

    gate_abs_map = {g: _norm_join(ws_root, g) for g in conf["gate_files"]}

    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        # 所有受管目标都必须满足治理文件许可，不只治理文件自身。
        # 修订复核：重哈希，不一致 → 撤销（V140-3/V142-1）。逐 gate 只做一次。
        unread: list[str] = []
        per_gate: dict[str, tuple[bytes, dict | None]] = {}
        for gate_rel, gate_abs in gate_abs_map.items():
            _mark_activity(state)
            try:
                current = Path(gate_abs).read_bytes()
                fp = f"c{len(current)}:{sha256_hex(current)}"
            except OSError:
                current, fp = b"", None
            entry = state["reads"].get(gate_rel)
            if entry and (fp is None or entry.get("fingerprint") != fp):
                _revocation(state, gate_rel,
                            "revision-changed" if fp else "gate-missing")
                entry = None
            if (not current or entry is None or entry.get("complete") is not True
                    or entry.get("anchor") != "content"
                    or not coverage_complete(entry.get("coverage", []),
                                             len(current.split(b"\n")))):
                unread.append(gate_rel)
            per_gate[gate_rel] = (current, entry)
        # 3.0.0：子作用域的写**不**由自身读证据授权，必须走委托（策略 R98 扩展）
        if split_scoped_id(session_id)[1] is not None and conf["gate_files"]:
            unread = list(conf["gate_files"])
        if unread:
            # B4 豁免令牌：仅当静态可判目标集合命中任一 gate 文件时尝试消费；
            # 命中 → allow + authorized-gatefile-write 审计；未命中 → 维持既有 C9 deny。
            gate_abs_set = {os.path.normpath(g) for g in gate_abs_map.values()}
            target_set = {os.path.normpath(t) for t in targets}
            if gate_abs_set & target_set:
                authz = consume_authz(ws_root, targets)
                if authz:
                    state["events"].append({
                        "type": "authorized-gatefile-write",
                        "targets": list(targets),
                        "nonce": authz["id"],
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    state["events"] = state["events"][-50:]
                    record_intent(state, targets)
                    save_state(ws_root, client, session_id, state)
                    return {"action": "allow", "reason": "authorized-gatefile-write", "ws": ws_root}
            # R98 扩展（3.0.0）：子作用域的写可由「父证据 + 有效委托」授权。
            # 目标必须精确命中委托集合；委托需未过期、gate 指纹一致、父证据有效。
            try:
                import delegations as _dg  # 同目录模块，由 hook.py 注入 sys.path
                if split_scoped_id(session_id)[1] is not None:  # 仅子作用域走委托
                    for _t in targets:
                        _d = _dg.find_valid(ws_root, session_id, _t, client)
                        if _d:
                            state["events"].append({
                                "type": "delegation-allow",
                                "delegation_id": _d["id"],
                                "parent_scope": _d["parent_scope"],
                                "target": _t,
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            state["events"] = state["events"][-50:]
                            record_intent(state, targets)
                            save_state(ws_root, client, session_id, state)
                            return {"action": "allow", "reason": "delegation", "ws": ws_root}
            except Exception as _exc:
                log(ws_root, f"delegation check failed: {type(_exc).__name__}: {_exc}")
            gate_rel = unread[0]
            current, entry = per_gate[gate_rel]
            save_state(ws_root, client, session_id, state)
            total = len(current.split(b"\n"))
            cov = entry.get("coverage", []) if isinstance(entry, dict) else []
            return {
                "action": "deny",
                "reason": _c9_deny_reason(ws_root, gate_rel, cov, total, targets[0],
                                          physical=_physical_lines(current)),
                "ws": ws_root,
            }
        # 全部治理文件当前修订均已读，持久化撤销留档；同时记写前意图（target+base_sha）。
        record_intent(state, targets)
        save_state(ws_root, client, session_id, state)
    return {"action": "allow", "reason": "ok", "ws": ws_root}


def evaluate_read_required(ws_root: str, client: str, session_id: str) -> dict:
    """B′ 只读判定：任一 gate 文件当前修订未完整读 → deny（C9 文案）；否则 allow。

    只读状态：不写 reads、不撤销、不落任何写前状态。内部异常 fail-closed（deny +
    人工检查），并吞掉日志，不向 adapter 抛异常。
    """
    try:
        if not ws_root or not os.path.isabs(ws_root):
            return {"action": "deny", "reason": "Workspace gate 内部错误；请人工检查。", "ws": ws_root}
        conf = load_conf(ws_root)
        if not conf["enabled"]:
            return {"action": "allow"}
        gate_abs_map = {g: _norm_join(ws_root, g) for g in conf["gate_files"]}
        with _StateLock(ws_root, client, session_id):
            state = load_state(ws_root, client, session_id)
            for gate_rel, gate_abs in gate_abs_map.items():
                try:
                    current = Path(gate_abs).read_bytes()
                    fp = f"c{len(current)}:{sha256_hex(current)}"
                except OSError:
                    current, fp = b"", None
                entry = state["reads"].get(gate_rel)
                # 修订复核（只读）：指纹不一致按 unread 判，不撤销既有记账。
                if entry and (fp is None or not isinstance(entry, dict)
                              or entry.get("fingerprint") != fp):
                    entry = None
                total = len(current.split(b"\n"))
                complete = (bool(current) and isinstance(entry, dict)
                            and entry.get("complete") is True
                            and entry.get("anchor") == "content"
                            and coverage_complete(entry.get("coverage", []), total))
                if not complete:
                    cov = entry.get("coverage", []) if isinstance(entry, dict) else []
                    return {"action": "deny",
                            "reason": _c9_deny_reason(ws_root, gate_rel, cov, total,
                                                      physical=_physical_lines(current)),
                            "ws": ws_root}
        return {"action": "allow"}
    except Exception as exc:
        log(ws_root, f"evaluate_read_required failed: {exc}")
        return {"action": "deny", "reason": "Workspace gate 内部错误；请人工检查。", "ws": ws_root}


# ------------------------------------------------------------ Lifecycle reconciliation


def remember_read_workspace(owner: str, target: str, client: str, session_id: str) -> None:
    """Index only workspaces that received verified content for this session.

    Stored in the cwd workspace, not a global registry. Never scan disk for
    sessions or infer target paths from Stop text/transcripts/shell commands.
    """
    if owner == target:
        return
    with _StateLock(owner, client, session_id):
        state = load_state(owner, client, session_id)
        known = state.setdefault("read_workspaces", [])
        if not isinstance(known, list) or not all(isinstance(x, str) for x in known):
            raise ValueError("invalid read workspace index")
        if target not in known:
            known.append(target)
            save_state(owner, client, session_id, state)


def reconcile_session_reads(owner: str, client: str, session_id: str, trigger: str) -> None:
    """Revoke stale evidence at a lifecycle boundary; NOT a PostToolUse event.

    Compare only existing reads of this exact client/session, including partial
    coverage. Missing/unreadable files revoke. Unchanged bytes grant nothing.
    No assertion about which tool caused a change; no shell write attribution.
    A session outside a managed cwd has no index: normal per-write checks remain.
    """
    if trigger not in ("PreToolUse", "Stop", "SessionEnd"):
        raise ValueError("invalid reconciliation trigger")
    if not _state_path(owner, client, session_id).is_file():
        return  # Ordinary reads must not create permission/write state.
    with _StateLock(owner, client, session_id):
        index = load_state(owner, client, session_id).get("read_workspaces", [])
        if not isinstance(index, list) or not all(
                isinstance(ws, str) and os.path.isabs(ws) for ws in index):
            raise ValueError("invalid read workspace index")
        roots = sorted(set([owner, *index]))
    # Never hold two workspace locks at once.
    for ws in roots:
        if not _state_path(ws, client, session_id).is_file():
            continue
        with _StateLock(ws, client, session_id):
            state = load_state(ws, client, session_id)
            checked, revoked = [], []
            for gate_rel, entry in list(state["reads"].items()):
                fp = file_fingerprint(_norm_join(ws, gate_rel))
                checked.append(gate_rel)
                if fp is None or not isinstance(entry, dict) or entry.get("fingerprint") != fp:
                    if isinstance(entry, dict) and entry:
                        _revocation(state, gate_rel, "lifecycle-reconciliation")
                        state["revocations"][-1].update(
                            trigger=trigger, change="missing-or-unreadable" if fp is None else "revision-changed",
                            previous_fingerprint=entry.get("fingerprint"), current_fingerprint=fp)
                    else:
                        state["reads"].pop(gate_rel, None)
                    revoked.append(gate_rel)
            record = {"trigger": trigger, "checked": checked, "revoked": revoked,
                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            records = state.setdefault("reconciliations", [])
            records.append(record)
            state["reconciliations"] = records[-20:]
            save_state(ws, client, session_id, state)
            if revoked:
                log(ws, f"lifecycle_reconciliation client={client} session={session_id} "
                        f"trigger={trigger} revoked={revoked}")


# ------------------------------------------------------------ 提醒 / 收尾

def evaluate_session_start(ws_root: str, client: str, session_id: str,
                           permission_mode: str | None = None) -> dict:
    conf = load_conf(ws_root)
    if not conf["enabled"]:
        return {"action": "none"}
    gate_abs_map = {g: _norm_join(ws_root, g) for g in conf["gate_files"]}
    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        if permission_mode:
            state["env"]["permission_mode"] = permission_mode
        _mark_activity(state)
        if not isinstance(state.get("worktree_baseline"), dict):
            snap = worktree_snapshot(ws_root) if _git_ok(ws_root) else {}
            state["worktree_baseline"] = dict(list(snap.items())[:500])
        # 非 git 工作区的差集靠这条水位线（mtime ≥ 水位）。git 工作区不依赖它。
        state.setdefault("audit_watermark_ns", time.time_ns())
        if not _git_ok(ws_root):
            # 哈希兜底基线：`cp -p` / `tar -x` 这类保 mtime 的写只靠水位线会漏。
            hp = _hash_baseline_path(ws_root, client, session_id)
            if not hp.exists():
                try:
                    hashes, _complete = _scan_hashes(ws_root)
                    hp.parent.mkdir(parents=True, exist_ok=True)
                    hp.write_text(json.dumps(hashes), encoding="utf-8")
                except Exception as exc:
                    log(ws_root, f"hash baseline failed: {exc}")
        ch = state["reminder"]["channels"]
        unread = [g for g in conf["gate_files"]
                  if not (state["reads"].get(g) or {}).get("complete")]
        if not unread or ch.get("session_start"):
            save_state(ws_root, client, session_id, state)
            return {"action": "none"}
        ch["session_start"] = True
        save_state(ws_root, client, session_id, state)
    return {"action": "context",
            "text": (f"[workspace-gate] 尚未读取的治理文件: {', '.join(unread)}。"
                     f"写工作区文件前请先完整 read_file 读取"
                     f" {', '.join(gate_abs_map[g] for g in unread)}。")}


def evaluate_prompt_submit(ws_root: str, client: str, session_id: str) -> dict:
    conf = load_conf(ws_root)
    if not conf["enabled"]:
        return {"action": "none"}
    gate_abs_map = {g: _norm_join(ws_root, g) for g in conf["gate_files"]}
    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        ch = state["reminder"]["channels"]
        _mark_activity(state)
        ch["user_prompt"] = int(ch.get("user_prompt", 0)) + 1
        unread = [g for g in conf["gate_files"]
                  if not (state["reads"].get(g) or {}).get("complete")]
        save_state(ws_root, client, session_id, state)
    if not unread:
        return {"action": "none"}
    return {"action": "context",
            "text": (f"[workspace-gate] 提醒：尚未读取的治理文件 {', '.join(unread)}；"
                     f"写工作区文件前需先完整 read_file 读取"
                     f" {', '.join(gate_abs_map[g] for g in unread)}。")}


def evaluate_session_end(ws_root: str, client: str, session_id: str,
                         completed: bool | None = None, interrupted: bool | None = None,
                         reason: str | None = None) -> dict:
    """One boundary per epoch. Native shutdown reason is not task outcome.

    Absent/non-boolean completed/interrupted fields remain null (unknown).
    """
    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        lifecycle = state.setdefault("lifecycle", {"epoch": 0, "status": "active"})
        if lifecycle.get("status") == "ended":
            return {"action": "none"}
        completed = completed if type(completed) is bool else None
        interrupted = interrupted if type(interrupted) is bool else None
        outcome = ("completed" if completed is True and interrupted is False else
                   "interrupted" if interrupted is True and completed is False else "unknown")
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "epoch": lifecycle["epoch"], "reason": reason if isinstance(reason, str) else None,
                 "completed": completed, "interrupted": interrupted, "outcome": outcome}
        state["ends"].append(entry)
        state["ends"] = state["ends"][-20:]
        lifecycle["status"] = "ended"
        save_state(ws_root, client, session_id, state)
        _append_audit(ws_root, client, state)
    return {"action": "none"}


def evaluate_post_write(ws_root: str, client: str, session_id: str,
                        targets: list[str]) -> dict:
    """写后：撤销被写 gate 的读证据，并落 commit / unobserved-write。

    - `gate-commit`：该 target 有写前 `gate-intent`，且写后 sha 与磁盘一致；
    - `unobserved-write`：磁盘确有内容但找不到对应意图（或 target 不存在），
      表示「这次变化没有经过本 gate 的观测」，**不得读成 allow**。
    """
    conf = load_conf(ws_root)
    gate_abs_map = {g: _norm_join(ws_root, g) for g in conf["gate_files"]}
    touched, commits, unobserved = [], [], []
    with _StateLock(ws_root, client, session_id):
        state = load_state(ws_root, client, session_id)
        events = state.get("events") if isinstance(state.get("events"), list) else []
        for target in targets:
            norm = os.path.normpath(os.path.expanduser(str(target)))
            _mark_activity(state)
            new_sha = file_sha(norm)
            # 找最近一条未被 commit 的同目标意图
            intent = None
            for ev in reversed(events):
                if not isinstance(ev, dict) or ev.get("type") != "gate-intent":
                    continue
                if ev.get("target") != norm or ev.get("committed"):
                    continue
                intent = ev
                break
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            if intent is not None and new_sha is not None:
                intent["committed"] = True
                rec = {"type": "gate-commit", "target": norm, "new_sha": new_sha,
                       "base_sha": intent.get("base_sha"), "intent_ts": intent.get("ts"),
                       "ts": ts}
                commits.append(rec)
                _append_commit_ledger(ws_root, client, session_id, rec)
            else:
                rec = {"type": "unobserved-write", "target": norm, "new_sha": new_sha,
                       "reason": "no-intent" if intent is None else "target-missing",
                       "detected_by": "post-write", "ts": ts}
                unobserved.append(rec)
            events.append(rec)
            for gate_rel, gate_abs in gate_abs_map.items():
                if norm == gate_abs and gate_rel in state["reads"]:
                    _revocation(state, gate_rel, "post-write-invalidation")
                    touched.append(gate_rel)
        state["events"] = events[-200:]
        save_state(ws_root, client, session_id, state)
    return {"action": "none", "revoked": touched,
            "committed": commits, "unobserved": unobserved}


# ------------------------------------------------------------ 指纹锚（W2/A）

def _anchor_snapshot(gate_abs: str) -> dict | None:
    try:
        p = Path(gate_abs)
        st = p.stat()
        data = p.read_bytes()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": sha256_hex(data), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def anchor_baseline(ws_root: str, client: str, session_id: str,
                    refresh: bool = False) -> dict:
    """W2 A1：对 conf["gate_files"] 记录指纹锚基线。仅在缺失或显式 refresh 时写。"""
    try:
        conf = load_conf(ws_root)
        recorded: list[str] = []
        with _StateLock(ws_root, client, session_id):
            state = load_state(ws_root, client, session_id)
            anchors = state.setdefault("anchors", {})
            if not isinstance(anchors, dict):
                anchors = {}
                state["anchors"] = anchors
            for gate_rel in conf["gate_files"]:
                if not refresh and gate_rel in anchors:
                    continue
                snap = _anchor_snapshot(_norm_join(ws_root, gate_rel))
                if snap is None:
                    continue
                anchors[gate_rel] = snap
                recorded.append(gate_rel)
            save_state(ws_root, client, session_id, state)
        return {"recorded": recorded}
    except Exception as exc:
        log(ws_root, f"anchor_baseline failed: {exc}")
        return {"recorded": []}


def anchor_check(ws_root: str, client: str, session_id: str, source: str) -> dict:
    """W2 A2：指纹锚复核（report-only，绝不返回 deny）。

    字节变化（size 或 sha256）→ 落 unsanctioned-gatefile-change 并撤销读许可
    （deny 仍由既有写前复核产生）；字节相同但 mtime_ns 变 → 落 suspect，不撤销。
    """
    changed: list[str] = []
    suspect: list[str] = []
    try:
        conf = load_conf(ws_root)
        if not conf["enabled"]:
            return {"changed": [], "suspect": []}
        with _StateLock(ws_root, client, session_id):
            state = load_state(ws_root, client, session_id)
            anchors = state.setdefault("anchors", {})
            if not isinstance(anchors, dict):
                anchors = {}
                state["anchors"] = anchors
            for gate_rel in conf["gate_files"]:
                gate_abs = _norm_join(ws_root, gate_rel)
                prev = anchors.get(gate_rel)
                snap = _anchor_snapshot(gate_abs)
                if snap is None:
                    continue
                if not isinstance(prev, dict):
                    anchors[gate_rel] = snap
                    continue
                byte_changed = (prev.get("size") != snap["size"]
                                or prev.get("sha256") != snap["sha256"])
                mtime_changed = prev.get("mtime_ns") != snap["mtime_ns"]
                if not byte_changed and not mtime_changed:
                    continue
                anchors[gate_rel] = snap
                events = state.get("events")
                if not isinstance(events, list):
                    events = []
                    state["events"] = events
                if byte_changed:
                    _revocation(state, gate_rel, "revision-changed")
                    changed.append(gate_rel)
                    events.append({
                        "type": "unsanctioned-gatefile-change",
                        "file": gate_rel,
                        "previous": prev,
                        "current": snap,
                        "source": source,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                else:
                    suspect.append(gate_rel)
                    events.append({
                        "type": "unsanctioned-gatefile-change",
                        "file": gate_rel,
                        "previous": prev,
                        "current": snap,
                        "source": source,
                        "suspect": True,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                state["events"] = events[-50:]
            save_state(ws_root, client, session_id, state)
    except Exception as exc:
        log(ws_root, f"anchor_check failed: {exc}")
    return {"changed": changed, "suspect": suspect}


# ------------------------------------------------------------ 日志 / 审计

def log(ws_root: str, msg: str) -> None:
    try:
        d = Path(ws_root) / STATE_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        lp = d / "gate.log"
        if lp.exists() and lp.stat().st_size > LOG_ROTATE_BYTES:
            lp.replace(lp.with_suffix(".log.1"))
        with open(lp, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _append_audit(ws_root: str, client: str, state: dict) -> None:
    try:
        d = Path(ws_root) / STATE_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        events = state.get("events")
        if not isinstance(events, list):
            events = []
        with open(d / f"audit-{client}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "session_id": state.get("session_id"),
                "reads": {k: bool(v.get("complete")) for k, v in state.get("reads", {}).items()},
                "revocations": len(state.get("revocations", [])),
                "permission_mode": (state.get("env") or {}).get("permission_mode"),
                "boundary": state["ends"][-1] if state.get("ends") else None,
                "events": events[-50:],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


AUDIT_JSONL_MAX_BYTES = 256 * 1024


def _append_audit_jsonl(ws_root: str, client: str, rec: dict) -> None:
    """把审计事件追加到工作区级 `audit-<client>.jsonl`（有界轮转）。

    为什么：`state["events"]` 只保留最后 50 条（有界窗口，防止状态文件无限增长）。
    一次长会话里审计记录会被后面的普通事件顶掉——实测 60 条事件后，早先的
    `unobserved-write` 直接消失。审计是"事后可见"的唯一出口，丢了就等于没做。
    """
    try:
        path = Path(ws_root) / STATE_DIRNAME / ("audit-%s.jsonl" % client)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if path.stat().st_size > AUDIT_JSONL_MAX_BYTES:
            os.replace(str(path), str(path) + ".1")
    except Exception as exc:
        log(ws_root, f"audit jsonl failed: {exc}")


def append_audit_event(ws_root: str, client: str, session_id: str, record: dict) -> None:
    """向审计槽位追加一条事件（有界 50）。report-only：异常吞掉记 log，绝不影响判定。

    同一份记录同时落到 `audit-<client>.jsonl`：状态窗口会轮转，jsonl 不会。
    """
    try:
        if not isinstance(record, dict) or not record.get("type"):
            return
        rec = dict(record)
        rec.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        rec.setdefault("session_id", session_id)
        with _StateLock(ws_root, client, session_id):
            state = load_state(ws_root, client, session_id)
            events = state.get("events")
            if not isinstance(events, list):
                events = []
            events.append(dict(record, ts=rec["ts"]))
            state["events"] = events[-50:]
            save_state(ws_root, client, session_id, state)
        _append_audit_jsonl(ws_root, client, rec)
    except Exception as exc:
        log(ws_root, f"append_audit_event failed: {exc}")


def worktree_audit(ws_root: str, client: str, session_id: str, source: str) -> None:
    """W3 客户端无关差集审计（最小版）。git status --porcelain + git diff --stat，
    把结果作为 worktree-change 记录写入 audit（report-only，不产生 deny）。

    source 取值：
      - `stop` / `session_end`：客户端自己的收尾事件（CC `Stop`、Codex `SessionEnd`）；
      - `post_shell`：客户端**没有**收尾事件时的替代口径（dsh），在每次 shell 工具调用后
        做一次最小差集——shell 写是静态分类器的盲区，写前拦不住，至少让它事后可见。
    """
    if source not in ("stop", "session_end", "post_shell", "post_tool"):
        return
    is_git = _git_ok(ws_root)
    status = diff = None
    files: list[str] = []
    if is_git:
        try:
            status = subprocess.run(["git", "-C", ws_root, "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=5)
            diff = subprocess.run(["git", "-C", ws_root, "diff", "--stat"],
                                  capture_output=True, text=True, timeout=5)
        except Exception as exc:
            log(ws_root, f"worktree_audit git failed: {exc}")
            status, diff = None, None
        if status is not None and status.returncode == 0:
            for line in (status.stdout or "").splitlines():
                if len(line) < 4:
                    continue
                path = line[3:].strip()
                if " -> " in path:  # rename: "R  old -> new"
                    path = path.split(" -> ")[-1].strip()
                if path:
                    files.append(path)
    # 未观测写：worktree 里有内容变化、但本会话没有对应 gate-commit 的目标。
    # report-only，最多 20 条；不代表 deny，只是"没有经过 gate 观测"。
    try:
        state = load_state(ws_root, client, session_id)
        if is_git:
            _b = state.get("worktree_baseline")
            baseline: dict = _b if isinstance(_b, dict) else {}
            current = worktree_snapshot(ws_root)
            changed = {rel for rel, sha in current.items() if baseline.get(rel) != sha}
            abs_sha: dict[str, str | None] = {os.path.normpath(os.path.join(ws_root, rel)): sha
                                              for rel, sha in current.items()}
        else:
            watermark = int(state.get("audit_watermark_ns") or 0)
            changed = set(_recent_files(ws_root, watermark))
            # 哈希兜底：覆盖"保留原 mtime"的写（cp -p / tar -x / rsync -t）。
            try:
                hp = _hash_baseline_path(ws_root, client, session_id)
                prev = json.loads(hp.read_text(encoding="utf-8")) if hp.is_file() else {}
                cur, complete = _scan_hashes(ws_root)
                if isinstance(prev, dict):
                    for rel, sha in cur.items():
                        old = prev.get(rel)
                        if old is not None and old != sha:
                            changed.add(rel)
                hp.parent.mkdir(parents=True, exist_ok=True)
                hp.write_text(json.dumps(cur), encoding="utf-8")
                if not complete:
                    append_audit_event(ws_root, client, session_id,
                                       {"type": "snapshot-incomplete",
                                        "reason": "hash-budget", "source": source})
            except Exception as exc:
                log(ws_root, f"hash audit failed: {exc}")
            abs_sha = {os.path.normpath(os.path.join(ws_root, rel)): None for rel in changed}
        append_audit_event(ws_root, client, session_id, {
            "type": "worktree-change",
            "files": (files or sorted(changed))[:200],
            "source": source,
            "diff_stat": (diff.stdout if diff is not None else "").strip(),
            "git": is_git,
        })
        committed = {e.get("target") for e in (state.get("events") or [])
                     if isinstance(e, dict) and e.get("type") == "gate-commit"}
        # 只认同 sha 的 commit（commits.jsonl 记录），不按路径无条件跳过：否则
        # 「被 commit 过、随后又被未观测改动」会漏报（DS 复核 ②/④）。
        committed |= committed_targets(ws_root, abs_sha)
        gate_abs = { (_norm_join(ws_root, g)) for g in load_conf(ws_root)["gate_files"] }
        pending = []
        # 以 changed 为准而不是 git 列表：非 git 工作区没有 porcelain 输出，但差集仍然成立。
        for rel in sorted(changed)[:200]:
            abs_path = os.path.normpath(os.path.join(ws_root, rel))
            if not os.path.isfile(abs_path):
                continue
            if abs_path in committed:
                continue
            if abs_path in gate_abs:
                continue
            pending.append({"type": "unobserved-write", "target": abs_path,
                            "new_sha": file_sha(abs_path),
                            "reason": "changed-without-commit",
                            "detected_by": "worktree-diff",
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        for rec in pending[:20]:
            append_audit_event(ws_root, client, session_id, rec)
        if not is_git:
            # 水位推进：下一次只看这之后被动过的文件（O(1) 状态，不需要存全量基线）。
            state = load_state(ws_root, client, session_id)
            state["audit_watermark_ns"] = time.time_ns()
            save_state(ws_root, client, session_id, state)
    except Exception as exc:
        log(ws_root, f"worktree unobserved-audit failed: {exc}")
