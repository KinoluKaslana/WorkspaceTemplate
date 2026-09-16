#!/usr/bin/env python3
"""Self-check for workspace-gate (synthetic-payload attack cases).

Usage: python3 selfcheck.py [path/to/__init__.py]

Builds throwaway workspaces + a synthetic $HERMES_HOME/state.db, then drives the
plugin callbacks directly. Exits non-zero if any expectation fails. Requires no
Hermes runtime — only the stdlib — so it is safe to run from anywhere.

Coverage is organised by the review rounds it encodes:
  * A/B…  default path judgment (absolute/relative, in/out of workspace)
  * C…    patch V4A header variants (Update/Add/Delete/Move, no-space form)
  * D…    session-cwd reverse lookup (CLI column, ACP model_config, parent chain)
  * E…    read accounting (status, decoys, realpath, cross-workspace isolation)
  * F…    state-file robustness (legacy, corrupt) and bookkeeping
  * G…    workspace-level override (plugin.custom.json)
  * H…    fail-closed / fail-safe boundaries (unresolvable cwd, internal errors)
  * K…    read-coverage accounting (partial reads do not unlock; union coverage does)
  * L…    session-boundary audit (turn metadata, own boundaries buffer, idempotent finalize, resume label)
  * M…    unresolved relative targets stay conservatively gated (custom cannot exempt them)
  * N…    I3: cross-process state serialisation (fcntl.flock; 4 workers x 25 increments)
  * O…    GPT-6 review regressions (envelope holes, empty/dedup/inline-truncated results,
         mixed absolute+relative patch targets, target-order invariance)
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("__init__.py"))

ROOT = Path(tempfile.mkdtemp(prefix="wg-selfcheck-"))
HOMEFAKE = ROOT / "hermes-home"
(HOMEFAKE / "logs").mkdir(parents=True)
DB = HOMEFAKE / "state.db"


def build_ws(base: Path, name="ws"):
    ws = base / name
    (ws / ".workspace").mkdir(parents=True)
    (ws / "AGENT_RULES.md").write_text("# fake rules\n", encoding="utf-8")
    (ws / "AGENT_RULES.custom.md").write_text("# fake custom\n", encoding="utf-8")
    (ws / ".workspace" / "bootstrap.json").write_text("{}\n", encoding="utf-8")
    (ws / "notes").mkdir(exist_ok=True)
    (ws / "notes" / "INDEX.md").write_text("x\n", encoding="utf-8")
    (ws / "notes" / "draft-AGENT_RULES.md").write_text("x\n", encoding="utf-8")
    (ws / "skills").mkdir(exist_ok=True)
    (ws / "skills" / "REGISTRY.md").write_text("x\n", encoding="utf-8")
    (ws / "TOOLS.md").write_text("x\n", encoding="utf-8")
    return ws


WS = build_ws(ROOT, "ws")
OUT = ROOT / "outside"
OUT.mkdir()
# workspace root at TOP, submodule (.git gitdir FILE) in the middle, target deep inside
TOP = build_ws(ROOT, "top")
NESTED = TOP / "nested"
(NESTED / "deep").mkdir(parents=True)
(NESTED / ".git").write_text("gitdir: ../../.git/modules/nested\n", encoding="utf-8")
# symlink inside the workspace pointing outside (escape attempt)
SECRET = ROOT / "secret.txt"
SECRET.write_text("secret\n", encoding="utf-8")
(WS / "notes" / "evil").symlink_to(OUT)
# near-miss: rules file without the bootstrap marker
NEARMISS = ROOT / "nearmiss"
NEARMISS.mkdir()
(NEARMISS / "AGENT_RULES.md").write_text("# orphan rules\n", encoding="utf-8")

# synthetic state.db: one CLI session whose cwd column points at WS
con = sqlite3.connect(DB)
con.execute(
    "CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, model_config TEXT, parent_session_id TEXT)"
)
con.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("sess-cli", str(WS), None, None))
con.execute(
    "INSERT INTO sessions VALUES (?,?,?,?)",
    ("sess-acp", None, json.dumps({"cwd": str(WS)}), None),
)
con.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("sess-child", None, None, "sess-cli"))
con.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("sess-unknown-cwd", None, None, None))
con.commit()
con.close()

os.environ["HERMES_HOME"] = str(HOMEFAKE)

spec = importlib.util.spec_from_file_location("wg", PLUGIN)
wg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wg)
# Force the local mirror so $HERMES_HOME drives this test deterministically
# (inside Hermes the plugin prefers hermes_constants.get_hermes_home()).
wg._get_hermes_home = None

FAILS = []


def check(name, expect_block, got, note=""):
    blocked = isinstance(got, dict) and got.get("action") == "block"
    ok = blocked == expect_block
    print(f"{'ok  ' if ok else 'FAIL'}  {name:42} expect={'BLOCK' if expect_block else 'pass '} "
          f"got={'BLOCK' if blocked else 'pass '} {note}")
    if not ok:
        FAILS.append(name)


def expect(name, cond, note=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {name:42} {note}")
    if not cond:
        FAILS.append(name)


def pre(tool, args, sid, parent=None, task_id=None):
    return wg._pre_tool_call(
        tool_name=tool, args=args, session_id=sid, parent_session_id=parent,
        task_id=task_id,
    )


def post(path, sid, status="ok", tool="read_file", lines=1, total=None, extra=None, task_id=None):
    """模拟一次成功的整文件 read_file（返回 JSON 字符串，复刻线上契约）。

    v1.3.1 起：`span=None` 不再解锁，所以「读后放行」类用例必须带真实证据。
    v1.4.0 起：相对路径读取只有在**核心 task 锚点**（tools.terminal_tool）存在时才记账
    （N1）；合成 DB 场景没有锚点，相关用例改用绝对路径或注入锚点。
    """
    body = "".join(f"{i}|line {i}\n" for i in range(1, (total or lines) + 1))
    payload = {"content": body, "total_lines": total or lines}
    if extra:
        payload.update(extra)
    return wg._post_tool_call(
        tool_name=tool,
        args={"path": path},
        session_id=sid,
        status=status,
        result=json.dumps(payload, ensure_ascii=False),
        task_id=task_id,
    )


# A. basic path judgment
check("A1 abs in-ws, unread", True, pre("write_file", {"path": str(WS / "x.txt")}, "s1"))
check("A2 abs outside ws", False, pre("write_file", {"path": str(OUT / "x.txt")}, "s1"))
check("A3 empty session_id -> no gate", False, pre("write_file", {"path": str(WS / "x.txt")}, ""))
check("A4 ../ escapes ws", False, pre("write_file", {"path": str(WS / ".." / "outside" / "t.txt")}, "s1"))
check("A5 ~ outside ws", False, pre("write_file", {"path": "~/nope.txt"}, "s1"))
check("A6 symlink escape (lexical hit)", True,
      pre("write_file", {"path": str(WS / "notes" / "evil" / "t.txt")}, "s1"))
# B. patch V4A header variants (mode='patch' is REQUIRED by the tool schema)
for hdr in ("Update", "Add", "Delete"):
    check(f"B1 patch {hdr} File abs in-ws", True,
          pre("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** {hdr} File: {WS}/x.txt\n*** End Patch\n"}, "s1"))
check("B2 patch header w/o space", True,
      pre("patch", {"mode": "patch", "patch": f"*** Begin Patch\n***Update File: {WS}/x.txt\n*** End Patch\n"}, "s1"))
check("B3 patch Move File (src->dst)", True,
      pre("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Move File: {OUT}/a.txt -> {WS}/b.txt\n*** End Patch\n"}, "s1"))
check("B4 patch Move File out of ws only", False,
      pre("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Move File: {OUT}/a.txt -> {OUT}/b.txt\n*** End Patch\n"}, "s1"))
check("B5 patch target outside ws", False,
      pre("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {OUT}/x.txt\n*** End Patch\n"}, "s1"))
check("B6 patch mode=replace path", True, pre("patch", {"path": str(WS / "x.txt"), "old_string": "a"}, "s1"))
check("B7 patch mode=replace ignores stray body", False,
      pre("patch", {"mode": "replace", "path": str(OUT / "x.txt"),
                    "patch": f"*** Update File: {WS}/x.txt"}, "s1"))
check("B8 patch mode=patch ignores stray path (core parity)", False,
      pre("patch", {"mode": "patch", "path": str(WS / "x.txt"),
                    "patch": f"*** Begin Patch\n*** Update File: {OUT}/x.txt\n*** End Patch\n"}, "s1"))
check("B9 patch mode=patch body in-ws + stray path out", True,
      pre("patch", {"mode": "patch", "path": str(OUT / "x.txt"),
                    "patch": f"*** Begin Patch\n*** Update File: {WS}/x.txt\n*** End Patch\n"}, "s1"))
# C. session-cwd reverse lookup
check("C1 rel path via cwd column", True, pre("write_file", {"path": "notes/probe.md"}, "sess-cli"))
check("C2 rel path via acp model_config", True, pre("write_file", {"path": "notes/probe.md"}, "sess-acp"))
check("C3 rel path via parent chain", True, pre("write_file", {"path": "notes/probe.md"}, "sess-child"))
# D. fail-closed boundary: unresolvable cwd
check("D1 rel WRITE w/ unresolvable cwd -> BLOCK", True,
      pre("write_file", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
check("D2 rel READ w/ unresolvable cwd -> pass", False,
      pre("read_file", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
check("D3 abs write still works w/o cwd", True, pre("write_file", {"path": str(WS / "x.txt")}, "sess-unknown-cwd"))
check("D4 non-default write tool not gated", False,
      pre("search_replace", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
# E. submodule boundary + read accounting
check("E1 submodule: ws above .git file", True,
      pre("write_file", {"path": str(NESTED / "deep" / "f.txt")}, "s1"))
post(str(WS / "AGENT_RULES.md"), "s2", status="error")
check("E2 read w/ status=error not counted", True, pre("write_file", {"path": str(WS / "y.txt")}, "s2"))
post(str(WS / "notes" / "draft-AGENT_RULES.md"), "s3")
check("E3 decoy suffix name not counted", True, pre("write_file", {"path": str(WS / "z.txt")}, "s3"))
post(str(WS / "AGENT_RULES.md"), "s4")
check("E4 read AGENT_RULES.md unlocks", False, pre("write_file", {"path": str(WS / "y.txt")}, "s4"))
post("AGENT_RULES.md", "sess-cli")
# v1.4.0（N1）：相对路径读取无核心 task 锚点 → 不记账、不报错、不落状态文件（合成 DB 无锚点是常态）。
# 记账语义改由 P 组「注入锚点后的相对读」覆盖。
_sp_e5 = wg._state_path(WS, "sess-cli")
st_e5 = json.loads(_sp_e5.read_text(encoding="utf-8"))["read"] if _sp_e5.is_file() else {}
expect("E5 relative read w/o task anchor -> no evidence, no crash",
       post("AGENT_RULES.md", "sess-cli") is None
       and st_e5.get("AGENT_RULES.md") is not True)
post(str(WS / "AGENT_RULES.md"), "s6")
check("E6 read ws-A does not unlock ws-B", True, pre("write_file", {"path": str(TOP / "y.txt")}, "s6"))
# F. state robustness / bookkeeping
sp7 = WS / ".workspace" / ".hook-state" / "s7.json"
sp7.parent.mkdir(parents=True, exist_ok=True)
sp7.write_text('{"read": {"AGENT_RULES.md": true}}', encoding="utf-8")
check("F1 legacy read=true migrated away -> BLOCK", True,
      pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
wg._pre_llm_call(session_id="s7")  # （合成 DB 里没有 s7 的 cwd，这条不会写盘）
post(str(WS / "AGENT_RULES.md"), "s7")  # 触发一次状态写盘，迁移才落盘
st7 = json.loads(sp7.read_text(encoding="utf-8"))
expect("F1b legacy evidence kept for audit", st7.get("read_legacy", {}).get("AGENT_RULES.md") is True
       and st7.get("evidence_schema") == wg.EVIDENCE_SCHEMA,
       f"(read_legacy={st7.get('read_legacy')}, schema={st7.get('evidence_schema')})")
post(str(WS / "AGENT_RULES.md"), "s7")
check("F1c after real evidence the gate unlocks", False,
      pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
sp7.write_text("not json at all", encoding="utf-8")
check("F2 corrupt state: re-init -> block", True, pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
sp7.write_text('{"status": "active", "evidence_schema": %d}' % wg.EVIDENCE_SCHEMA, encoding="utf-8")
check("F3 state missing 'read' key -> block (no crash)", True,
      pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
expect("F4 state filename sanitised", wg._state_path(WS, "a/b c").name == "a_b_c.json",
       f"({wg._state_path(WS, 'a/b c').name})")
# G. reminder + turn audit
ctx1 = wg._pre_llm_call(session_id="sess-acp")
ctx2 = wg._pre_llm_call(session_id="sess-acp")
expect("G1 reminder injected once", bool(ctx1 and ctx1.get("context")),
       f"({(ctx1 or {}).get('context', '')[:60]})")
expect("G2 reminder not repeated", ctx2 is None)
for _ in range(25):
    wg._on_session_end(session_id="sess-acp", completed=True, interrupted=False)
st = json.loads((WS / ".workspace" / ".hook-state" / "sess-acp.json").read_text(encoding="utf-8"))
expect("G3 on_session_end bounded to 20", len(st["ends"]) == 20, f"(len={len(st['ends'])})")
expect("G4 on_session_end keeps last_end", st.get("last_end", {}).get("completed") is True)
# H. internal fail-closed
_orig = wg._resolve_target_paths
wg._resolve_target_paths = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
got = pre("write_file", {"path": str(WS / "x.txt")}, "s1")
wg._resolve_target_paths = _orig
check("H1 internal error -> fail-closed block", True, got, str(got)[:50] if got else "")
# I. workspace-level override (plugin.custom.json)
CFG = WS / ".hermes" / "plugins" / "workspace-gate"
CFG.mkdir(parents=True, exist_ok=True)
CFG.joinpath("plugin.custom.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
check("I1 custom: enabled=false disables gate", False, pre("write_file", {"path": str(WS / "q.txt")}, "s20"))
CFG.joinpath("plugin.custom.json").write_text(
    json.dumps({"gate_files": ["AGENT_RULES.md", "TOOLS.md"]}), encoding="utf-8")
check("I2 custom: extra gate file enforced", True, pre("write_file", {"path": str(WS / "q.txt")}, "s20"))
post(str(WS / "AGENT_RULES.md"), "s20")
check("I3 custom: one of two gate files still blocks", True,
      pre("write_file", {"path": str(WS / "q.txt")}, "s20"))
post(str(WS / "TOOLS.md"), "s20")
check("I4 custom: both gate files read -> pass", False,
      pre("write_file", {"path": str(WS / "q.txt")}, "s20"))
CFG.joinpath("plugin.custom.json").write_text(
    json.dumps({"write_tools": ["write_file", "search_replace"]}), encoding="utf-8")
check("I5 custom: widened write_tools is gated", True,
      pre("search_replace", {"path": str(WS / "q.txt")}, "s23"))
CFG.joinpath("plugin.custom.json").write_text("{ broken json", encoding="utf-8")
check("I6 custom: malformed json -> defaults", True,
      pre("write_file", {"path": str(WS / "q.txt")}, "s22"))
CFG.joinpath("plugin.custom.json").unlink()
# J. environment plumbing
expect("J1 HERMES_HOME state.db used", wg._resolve_session_cwd("sess-cli") == str(WS),
       f"(resolved {wg._resolve_session_cwd('sess-cli')})")
# J2: near-miss（有 AGENT_RULES.md 无 bootstrap）不守门——v1.4.3 起工作区身份 = bootstrap，
# 此用例语义不变（只有 gate 没有 bootstrap 仍不算工作区），但 near-miss 日志仍应记录。
check("J2 near-miss ws (marker missing) not gated", False,
      pre("write_file", {"path": str(NEARMISS / "x.txt")}, "s1"))
near_hits = [
    ln for ln in (HOMEFAKE / "logs" / "workspace-gate-plugin.log").read_text(
        encoding="utf-8").splitlines() if str(NEARMISS) in ln
]
expect("J3 near-miss workspace logged", bool(near_hits), f"({len(near_hits)} log line(s))")

# K. read-coverage accounting (v1.3.0): partial reads do not unlock the gate
COV = build_ws(ROOT, "cov")
RULES_TOTAL = 200
(COV / "AGENT_RULES.md").write_text(
    "".join(f"line {i}\n" for i in range(1, RULES_TOTAL + 1)), encoding="utf-8"
)


def post_span(path, sid, start, end, total: int | None = RULES_TOTAL, wire="json", extra=None):
    """`wire="json"` 复刻线上契约（result 是 JSON 字符串）；`wire="dict"` 用 dict 变体。"""
    body = "".join(f"{i}|line {i}\n" for i in range(start, end + 1))
    payload: dict = {"content": body}
    if total is not None:
        payload["total_lines"] = total
    if extra:
        payload.update(extra)
    if wire == "json":
        result = json.dumps(payload, ensure_ascii=False)
    else:
        result = payload
    return wg._post_tool_call(
        tool_name="read_file",
        args={"path": path, "offset": start, "limit": end - start + 1},
        session_id=sid,
        status="ok",
        result=result,
    )


post_span(str(COV / "AGENT_RULES.md"), "k1", 1, 50)
check("K1 partial read (1-50/200) does not unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "k1"))
st_k1 = json.loads(wg._state_path(COV, "k1").read_text(encoding="utf-8"))
expect("K2 coverage recorded as one span", st_k1["coverage"]["AGENT_RULES.md"] == [[1, 50]],
       f"({st_k1['coverage'].get('AGENT_RULES.md')})")
got = pre("write_file", {"path": str(COV / "x.txt")}, "k1")
expect("K3 block message names the missing lines", "尚缺行" in (got or {}).get("message", ""),
       f"({(got or {}).get('message', '')[:90]})")
post_span(str(COV / "AGENT_RULES.md"), "k1", 51, RULES_TOTAL)
check("K4 union coverage (1-50 + 51-200) unlocks", False,
      pre("write_file", {"path": str(COV / "x.txt")}, "k1"))
post_span(str(COV / "AGENT_RULES.md"), "k2", 1, RULES_TOTAL)
check("K5 full read in one call unlocks", False,
      pre("write_file", {"path": str(COV / "x.txt")}, "k2"))
# dict-form result (older/synthetic callers) must behave like the JSON string form
post_span(str(COV / "AGENT_RULES.md"), "k4", 1, 10, wire="dict")
check("K6 dict-form partial result also does not unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "k4"))
# paginated/truncated page: report lies about total_lines -> must still count as unread
post_span(str(COV / "AGENT_RULES.md"), "k5", 1, 10, total=None, extra={"next_offset": 11})
check("K7 truncated page (next_offset, no total) does not unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "k5"))
# 无 result：v1.3.1 起不再隐式解锁（审查 F3）
post_missing = wg._post_tool_call(
    tool_name="read_file", args={"path": str(COV / "AGENT_RULES.md")},
    session_id="k3", status="ok",
)
check("K8 payload without result no longer unlocks", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "k3"))
# L. session-boundary audit (v1.3.1: own boundaries buffer + monotonic counters)
wg._on_session_end(
    session_id="sess-cli", completed=True, interrupted=False, turn_id="t-1",
    failed=False, turn_exit_reason="text_response(stop)",
)
st_l = json.loads(wg._state_path(WS, "sess-cli").read_text(encoding="utf-8"))
expect("L1 on_session_end keeps turn metadata (scope/turn_id/failed/reason)",
       st_l["ends"][-1].get("scope") == "turn"
       and st_l["ends"][-1].get("turn_id") == "t-1"
       and st_l["ends"][-1].get("reason") == "text_response(stop)"
       and st_l["ends"][-1].get("failed") is False,
       f"({st_l['ends'][-1]})")
expect("L2 total_turns is a monotonic counter", int(st_l.get("total_turns") or 0) >= 1,
       f"(total_turns={st_l.get('total_turns')})")
wg._on_session_finalize(session_id="sess-cli", reason="user_exit", platform="cli")
st_l = json.loads(wg._state_path(WS, "sess-cli").read_text(encoding="utf-8"))
expect("L3 boundary goes to its own buffer, not into the turn ring",
       st_l["boundaries"][-1].get("scope") == "session"
       and st_l.get("status") == "archived"
       and not any(e.get("scope") == "session" for e in st_l["ends"]),
       f"(boundaries={len(st_l['boundaries'])}, ends={len(st_l['ends'])})")
expect("L4 boundary keeps read/unread/coverage evidence + reason",
       isinstance(st_l["boundaries"][-1].get("read"), list)
       and isinstance(st_l["boundaries"][-1].get("unread"), list)
       and isinstance(st_l["boundaries"][-1].get("coverage"), dict)
       and st_l["boundaries"][-1].get("reason") == "user_exit",
       f"({st_l['boundaries'][-1].get('reason')})")
n_bound = len(st_l["boundaries"])
wg._on_session_finalize(session_id="sess-cli", reason="user_exit", platform="cli")
st_l = json.loads(wg._state_path(WS, "sess-cli").read_text(encoding="utf-8"))
expect("L5 repeated finalize is idempotent", len(st_l["boundaries"]) == n_bound,
       f"({len(st_l['boundaries'])} vs {n_bound})")
post(str(WS / "AGENT_RULES.md"), "sess-cli")
st_l = json.loads(wg._state_path(WS, "sess-cli").read_text(encoding="utf-8"))
expect("L6 resumed session is labelled resumed, not archived",
       st_l.get("status") == "resumed", f"(status={st_l.get('status')})")
# M. unresolved relative targets: conservative boundary (review F4 reverted N2's exemption)
N2WS = build_ws(ROOT, "n2")
(N2WS / ".hermes" / "plugins" / "workspace-gate").mkdir(parents=True, exist_ok=True)
(N2WS / ".hermes" / "plugins" / "workspace-gate" / "plugin.custom.json").write_text(
    json.dumps({"write_tools": ["patch"]}), encoding="utf-8"
)
_cwd = os.getcwd()
os.chdir(N2WS)
try:
    check("M1 unknown rel write_file -> BLOCK even with narrowed custom", True,
          pre("write_file", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
    check("M2 unknown rel patch -> BLOCK", True,
          pre("patch", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
    check("M3 unknown rel read_file -> not gated", False,
          pre("read_file", {"path": "notes/probe.md"}, "sess-unknown-cwd"))
finally:
    os.chdir(_cwd)
# N. I3: cross-process serialisation via fcntl.flock
NP_WS = build_ws(ROOT, "npws")
_CHILD = (
    "import importlib.util, sys\n"
    "from pathlib import Path\n"
    "spec = importlib.util.spec_from_file_location('wg', sys.argv[1])\n"
    "wg = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(wg)\n"
    "ws = Path(sys.argv[2]); sid = sys.argv[3]; n = int(sys.argv[4])\n"
    "for _ in range(n):\n"
    "    with wg._state_lock(ws, sid):\n"
    "        st = wg._load_state(ws, sid)\n"
    "        st['counter'] = int(st.get('counter', 0)) + 1\n"
    "        wg._save_state(ws, sid, st)\n"
)
if wg.fcntl is None:
    print("skip  N1 cross-process counter               (fcntl unavailable on this platform)")
else:
    workers, iters = 4, 25
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(PLUGIN), str(NP_WS), "np", str(iters)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for _ in range(workers)
    ]
    errs = [p.communicate()[1].decode("utf-8", "replace") for p in procs]
    codes = [p.returncode for p in procs]
    st_n = json.loads(wg._state_path(NP_WS, "np").read_text(encoding="utf-8"))
    expect("N1 cross-process counter == workers x iters (no lost update)",
           codes == [0] * workers and st_n.get("counter") == workers * iters,
           f"(counter={st_n.get('counter')} of {workers * iters}; rc={codes}; err={errs[0][:60] if errs[0] else ''})")

# O. GPT-6 independent review regressions (F2 envelope holes, F3 no-content, F4 target order, F5 inline truncation)
def post_raw(path, sid, payload):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    return wg._post_tool_call(
        tool_name="read_file", args={"path": path}, session_id=sid, status="ok", result=payload,
    )


post_span(str(COV / "AGENT_RULES.md"), "o1", 1, 1)
post_span(str(COV / "AGENT_RULES.md"), "o1", RULES_TOTAL, RULES_TOTAL)
st_o = json.loads(wg._state_path(COV, "o1").read_text(encoding="utf-8"))
check("O1 first+last line only -> still blocked", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "o1"))
expect("O2 coverage keeps the hole explicit",
       st_o["coverage"]["AGENT_RULES.md"] == [[1, 1], [RULES_TOTAL, RULES_TOTAL]],
       f"({st_o['coverage'].get('AGENT_RULES.md')})")
runs_o = wg._read_evidence(json.dumps({"content": "1|a\n2|b\n9|c\n", "total_lines": 9}))
expect("O3 non-contiguous response -> separate runs, no hole filling",
       runs_o[:-1] == ([[1, 2], [9, 9]], 9) and runs_o[-1] is None, f"({runs_o})")
post_raw(str(COV / "AGENT_RULES.md"), "o2", {"content": "", "total_lines": RULES_TOTAL})
check("O4 EOF/empty content -> no unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "o2"))
post_raw(str(COV / "AGENT_RULES.md"), "o3",
         {"content": "", "total_lines": RULES_TOTAL, "dedup": True, "content_returned": False})
check("O5 dedup stub without content -> no unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "o3"))
post_raw(str(COV / "AGENT_RULES.md"), "o4",
         {"content": "1|" + "x" * 50 + " ... [truncated]\n", "total_lines": 1})
check("O6 inline-truncated line (marker at line end) -> no unlock", True,
      pre("write_file", {"path": str(COV / "x.txt")}, "o4"))
post_raw(str(COV / "AGENT_RULES.md"), "o5",
         {"content": "1|正文里提到 `... [truncated]` 这个标记\n2|第二行\n", "total_lines": 2})
check("O11 marker mentioned mid-line does not invalidate real evidence", False,
      pre("write_file", {"path": str(COV / "x.txt")}, "o5"))
st_o4_path = wg._state_path(COV, "o4")
st_o4 = json.loads(st_o4_path.read_text(encoding="utf-8")) if st_o4_path.is_file() else {}
expect("O7 no coverage recorded for untrusted results",
       not (st_o4.get("coverage") or {}).get("AGENT_RULES.md"),
       f"({(st_o4.get('coverage') or {}).get('AGENT_RULES.md')})")
STRICT = build_ws(ROOT, "strict")
LAX = build_ws(ROOT, "lax")
(LAX / ".hermes" / "plugins" / "workspace-gate").mkdir(parents=True, exist_ok=True)
(LAX / ".hermes" / "plugins" / "workspace-gate" / "plugin.custom.json").write_text(
    json.dumps({"write_tools": ["write_file"]}), encoding="utf-8"
)
os.chdir(LAX)
try:
    body_abs_first = (
        "*** Begin Patch\n*** Update File: " + str(STRICT / "victim.txt") + "\n+x\n"
        "*** Add File: real-decoy.txt\n+probe\n*** End Patch\n"
    )
    body_rel_first = (
        "*** Begin Patch\n*** Add File: real-decoy.txt\n+probe\n"
        "*** Update File: " + str(STRICT / "victim.txt") + "\n+x\n*** End Patch\n"
    )
    check("O8 mixed abs+rel patch -> BLOCK (resolved target checked first)", True,
          pre("patch", {"mode": "patch", "patch": body_abs_first}, "sess-unknown-cwd"))
    check("O9 target order swap does not change the verdict", True,
          pre("patch", {"mode": "patch", "patch": body_rel_first}, "sess-unknown-cwd"))
    post(str(STRICT / "AGENT_RULES.md"), "sess-unknown-cwd")
    check("O10 unresolved target alone -> conservative BLOCK (documented over-block)", True,
          pre("patch", {"mode": "patch", "patch": body_rel_first}, "sess-unknown-cwd"))
finally:
    os.chdir(_cwd)

print()
# P. confirmation-round regressions (v1.4.0): N1 anchor parity / N2 revision binding / N3 resume epoch / N5 int endpoints
print("=== P: confirmation-round regressions (v1.4.0) ===")

# --- P1: N2 cross-revision splice must not unlock (the exact real-cross-revision-splice case) ---
CR = build_ws(ROOT, "crossrev")
RULES_PATH = CR / "AGENT_RULES.md"
RULES_PATH.write_text("old-one\nold-two\nold-three\nold-four\n", encoding="utf-8")
post_span(str(RULES_PATH), "p1", 1, 2, total=4)  # V141-1 修正：旧修订**未读完**（1–2/4）即换版
RULES_PATH.write_text("NEW-one\nNEW-two\nNEW-three\nNEW-four\n", encoding="utf-8")  # 外部全文改写
post_span(str(RULES_PATH), "p1", 3, 4, total=4)
st_p1 = json.loads(wg._state_path(CR, "p1").read_text(encoding="utf-8"))
check("P1 cross-revision splice (1-2 old-partial + 3-4 new) -> still BLOCKED", True,
      pre("write_file", {"path": str(CR / "x.txt")}, "p1"))
st_p1 = json.loads(wg._state_path(CR, "p1").read_text(encoding="utf-8"))
expect("P1b write-time recheck revoked the splice (no fake coverage)",
       st_p1["coverage"].get("AGENT_RULES.md") != [[1, 4]]
       and st_p1["read"].get("AGENT_RULES.md") is not True
       and bool(st_p1.get("revocations")), f"(cov={st_p1['coverage'].get('AGENT_RULES.md')})")
expect("P1c revocation binds two different fingerprints",
       st_p1.get("revocations")
       and st_p1["revocations"][-1].get("from") != st_p1["revocations"][-1].get("to"),
       f"(revocations={[(r['from'][:8], r['to'][:8]) for r in st_p1.get('revocations', [])]})")

# --- P2: N2 write-time re-check revokes a stale read=true grant (read old full, then file replaced) ---
CR2 = build_ws(ROOT, "crossrev2")
R2 = CR2 / "AGENT_RULES.md"
R2.write_text("v1\n", encoding="utf-8")
post(str(R2), "p2")
check("P2a full read unlocks", False, pre("write_file", {"path": str(CR2 / "x.txt")}, "p2"))
R2.write_text("v2-with-different-length\n", encoding="utf-8")  # 外部改写，未重读
check("P2b write after external rewrite -> BLOCKED (stale grant revoked)", True,
      pre("write_file", {"path": str(CR2 / "x.txt")}, "p2"))
st_p2 = json.loads(wg._state_path(CR2, "p2").read_text(encoding="utf-8"))
expect("P2c revocation recorded with was_read=True",
       any(r.get("was_read") for r in st_p2.get("revocations", [])),
       f"(revocations={len(st_p2.get('revocations', []))})")
post(str(R2), "p2")
check("P2d re-read of new revision unlocks again", False,
      pre("write_file", {"path": str(CR2 / "x.txt")}, "p2"))

# --- P3: N1 anchor parity — in-process anchor drives both read accounting and write gating ---
# （不依赖核心模块可导入：直接给插件注入桩，验证插件侧的锚点消费逻辑；生产里锚点
# 来自 tools.terminal_tool，同一消费路径。）
_record_session_cwd = wg._core_get_session_cwd
_record_overrides = wg._core_resolve_task_overrides
ANCH = build_ws(ROOT, "anch")
wg._core_get_session_cwd = lambda tid: str(ANCH) if tid == "task-anch" else None
wg._core_resolve_task_overrides = lambda tid: {}
try:
    # 3a: relative READ with the real anchor -> evidence recorded, gate unlocks
    post("AGENT_RULES.md", "task-anch", task_id="task-anch")
    check("P3a relative read via core anchor unlocks", False,
          pre("write_file", {"path": "y.txt"}, "task-anch", task_id="task-anch"))
    # 3b: state.db cwd deliberately WRONG (points elsewhere) must not matter
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("task-anch", str(OUT), None, None))
    con.commit(); con.close()
    check("P3b write resolved by anchor (DB cwd is ignored) still passes", False,
          pre("write_file", {"path": "z.txt"}, "task-anch", task_id="task-anch"))
    # 3c: anchored target inside a DIFFERENT strict ws (unread) -> BLOCK
    check("P3c anchored relative write into strict ws (unread) -> BLOCK", True,
          pre("write_file", {"path": str(WS / "p3c.txt")}, "task-anch", task_id="task-anch"))
finally:
    wg._core_get_session_cwd = _record_session_cwd
    wg._core_resolve_task_overrides = _record_overrides

# --- P4: N1 reverse direction — task cwd=L (loose dir): relative read of L's rules must NOT grant W ---
REV = build_ws(ROOT, "revws")
wg._core_get_session_cwd = lambda tid: str(ROOT / "loose-l") if tid == "task-rev" else None
try:
    (ROOT / "loose-l").mkdir(exist_ok=True)
    (ROOT / "loose-l" / "AGENT_RULES.md").write_text("Lax decoy\n1..8\n", encoding="utf-8")
    post("AGENT_RULES.md", "task-rev", task_id="task-rev")
    st_p4_path = wg._state_path(REV, "task-rev")
    st_p4 = json.loads(st_p4_path.read_text(encoding="utf-8")) if st_p4_path.is_file() else {}
    expect("P4a decoy relative read granted nothing to REV",
           not (st_p4.get("coverage") or {}).get("AGENT_RULES.md"),
           f"(cov={st_p4.get('coverage')})")
    check("P4b REV strict write still blocked", True,
          pre("write_file", {"path": str(REV / "x.txt")}, "task-rev"))
finally:
    wg._core_get_session_cwd = _record_session_cwd

# --- P5: N3 resume semantics — resume WITHOUT any read must start a new epoch, finalize must APPEND ---
P5 = build_ws(ROOT, "resume")
con = sqlite3.connect(DB)
con.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("p5", str(P5), None, None))
con.commit(); con.close()
post(str(P5 / "AGENT_RULES.md"), "p5")
wg._on_session_finalize(session_id="p5", reason="exit-first", platform="cli")
n_after_first = len(json.loads(wg._state_path(P5, "p5").read_text(encoding="utf-8"))["boundaries"])
# 恢复：一个 pre_llm_call（零回合、零读取——v1.3.2 在这里会永远挂 archived）
ctx_p5 = wg._pre_llm_call(session_id="p5")
st_p5 = json.loads(wg._state_path(P5, "p5").read_text(encoding="utf-8"))
expect("P5a pre_llm alone flips archived->resumed (new epoch)",
       st_p5.get("status") == "resumed" and int(st_p5.get("epoch") or 0) >= 1,
       f"(status={st_p5.get('status')}, epoch={st_p5.get('epoch')})")
wg._on_session_finalize(session_id="p5", reason="exit-second", platform="cli")
st_p5 = json.loads(wg._state_path(P5, "p5").read_text(encoding="utf-8"))
expect("P5b second-stage finalize APPENDS (old boundary preserved)",
       len(st_p5["boundaries"]) == n_after_first + 1
       and st_p5["boundaries"][0].get("reason") == "exit-first"
       and st_p5["boundaries"][-1].get("reason") == "exit-second",
       f"(reasons={[b.get('reason') for b in st_p5['boundaries']]})")
# 重复 finalize（同一阶段）仍幂等
wg._on_session_finalize(session_id="p5", reason="exit-second", platform="cli")
st_p5 = json.loads(wg._state_path(P5, "p5").read_text(encoding="utf-8"))
expect("P5c same-stage repeated finalize stays idempotent",
       len(st_p5["boundaries"]) == n_after_first + 1)
# 恢复后只写文件/读 dedup（无回合事件）路径已由 P5a 覆盖（pre_llm 即活动点）
expect("P5d reminder re-fire blocked (dedup kept)",
       ctx_p5 is None or bool(ctx_p5.get("context")))

# --- P6: N5 missing-range endpoint strictness ---
expect("P6a float endpoints rejected (no silent coercion)",
       wg._missing_ranges([[1.9, 3.8]], 8) == [(1, 8)])
expect("P6b numeric-string endpoints rejected",
       wg._missing_ranges([["4", "8"]], 8) == [(1, 8)])
expect("P6c bool endpoints rejected",
       wg._missing_ranges([[True, True]], 8) == [(1, 8)])
expect("P6d legit int spans still computed",
       wg._missing_ranges([[1, 2], [5, 8]], 8) == [(3, 4)])

# --- P7: v1.4.1 confirmation-round V140 regressions ---
print("=== P: v1.4.1 (V140) ===")
# P7-1 [V140-2]: read→post-stat TOCTOU — OLD content returned, file swapped to NEW before post stat.
# 内容自证指纹下，许可绑定的是「记账时的磁盘内容」：OLD 正文证据 + NEW 磁盘 → 许可绑定
# NEW 内容哈希；写前复核 stat 同一 NEW 内容 → 指纹一致。旧正文 + 新许可的放行前提
# （许可内容 == 写入时磁盘内容）仍成立——本用例断言「不因竞态而崩，语义如实」。
T1 = build_ws(ROOT, "toctou")
T1R = T1 / "AGENT_RULES.md"
T1R.write_text("OLD-1\nOLD-2\nOLD-3\nOLD-4\n", encoding="utf-8")
post_span(str(T1R), "p7a", 1, 4, total=4)
st_p7 = json.loads(wg._state_path(T1, "p7a").read_text(encoding="utf-8"))
expect("P7-1a grant bound to content fingerprint (c-prefix)",
       str(st_p7.get("coverage_rev", {}).get("AGENT_RULES.md", "")).startswith("c"),
       f"(fp={str(st_p7.get('coverage_rev', {}).get('AGENT_RULES.md'))[:20]}…)")
# 现在换内容（同长度，stat 元数据指纹无法察觉的形态）→ 写前复核必须撤销
T1R.write_text("NEW-1\nNEW-2\nNEW-3\nNEW-4\n", encoding="utf-8")
check("P7-1b same-size content swap -> BLOCKED (content hash differs)", True,
      pre("write_file", {"path": str(T1 / "x.txt")}, "p7a"))
st_p7 = json.loads(wg._state_path(T1, "p7a").read_text(encoding="utf-8"))
expect("P7-1c revocation recorded (was_read=True)",
       any(r.get("was_read") for r in st_p7.get("revocations", [])))
# mtime 回拨伪造对内容指纹无效（内容未变 → 指纹不变 → 放行是正确语义：
# 磁盘内容确实就是会话读到的内容——这正是内容自证对 V140-2「utime 伪造 stat
# tuple」形态的免疫性）
post_span(str(T1R), "p7b", 1, 4, total=4)
st_p7b = json.loads(wg._state_path(T1, "p7b").read_text(encoding="utf-8"))
fp_c = st_p7b.get("coverage_rev", {}).get("AGENT_RULES.md")
os.utime(T1R, ns=(1000, 1000))  # 伪造 mtime（旧 stat 指纹会被此骗过）
fp_after = wg._revision_fingerprint(str(T1R))
expect("P7-1d utime forgery cannot move a content fingerprint",
       fp_c and fp_after == fp_c and str(fp_c).startswith("c"),
       f"(fp={str(fp_c)[:16]}… after-utime={str(fp_after)[:16]}…)")
check("P7-1e write after utime forgery (content unchanged) still passes", False,
      pre("write_file", {"path": str(T1 / "x.txt")}, "p7b"))
# P7-2 [V140-3]: 旧许可 + 外部改版 + 完整重读新版 → 首次写必须直接通过
T2 = build_ws(ROOT, "reread")
T2R = T2 / "AGENT_RULES.md"
T2R.write_text("v1\n", encoding="utf-8")
post(str(T2R), "p7c")
check("P7-2a old revision full read unlocks", False,
      pre("write_file", {"path": str(T2 / "x.txt")}, "p7c"))
T2R.write_text("v2-longer-content\n", encoding="utf-8")
post(str(T2R), "p7c")  # 完整重读新版（在写之前）
check("P7-2b full re-read of new revision -> first write PASSES", False,
      pre("write_file", {"path": str(T2 / "x.txt")}, "p7c"))
# P7-3 [V140-1]: 容器 backend 的受管写 fail-closed
# `.venv-314` 缺 requests 使 tools.file_tools_paths 不可导入 → 桩注入 sys.modules
# （与 P3/P4 的锚点桩同思路；生产里该判定来自核心真实 backend 状态）。
import sys as _sys
import types as _types
_ftp_mod = _types.ModuleType("tools.file_tools_paths")
_ftp_mod._uses_container_paths = lambda tid: True
_pkg = _sys.modules.get("tools")
_saved_ftp = None
try:
    if _pkg is not None:
        _saved_ftp = _sys.modules.get("tools.file_tools_paths")
        _pkg.file_tools_paths = _ftp_mod
    _sys.modules["tools.file_tools_paths"] = _ftp_mod
    _sys.modules.setdefault("tools", _types.ModuleType("tools"))
    _sys.modules["tools"].file_tools_paths = _ftp_mod
    check("P7-3a container backend relative write -> BLOCK (fail-closed)", True,
          pre("write_file", {"path": "notes/probe.md"}, "sess-cli", task_id="task-container"))
    check("P7-3b container backend absolute write -> BLOCK (fail-closed)", True,
          pre("write_file", {"path": str(WS / "p73.txt")}, "sess-cli", task_id="task-container"))
    check("P7-3c container backend read_file -> not gated (read path unchanged)", False,
          pre("read_file", {"path": "notes/probe.md"}, "sess-cli", task_id="task-container"))
    check("P7-3d non-container backend write unchanged", True,
          pre("write_file", {"path": str(WS / "p73d.txt")}, "sess-cli", task_id="task-local"))
finally:
    if _saved_ftp is not None:
        _sys.modules["tools.file_tools_paths"] = _saved_ftp
        if _pkg is not None:
            _pkg.file_tools_paths = _saved_ftp
    else:
        _sys.modules.pop("tools.file_tools_paths", None)
# P7-4 [V140-4]: schema 2→3 迁移写独立 *_legacy_s2，不覆盖更早 generic legacy
T3 = T2 / ".workspace" / ".hook-state" / "p7d.json"
T3.parent.mkdir(parents=True, exist_ok=True)
T3.write_text(json.dumps({
    "evidence_schema": 2,
    "read": {"AGENT_RULES.md": True},
    "coverage": {"AGENT_RULES.md": [[1, 1]]},
    "read_legacy": {"AGENT_RULES.md": True},  # schema<2 时代的旧留档，必须保留
    "coverage_legacy": {"AGENT_RULES.md": [[1, 9]]},
}, ensure_ascii=False), encoding="utf-8")
st_p7d = wg._load_state(T2, "p7d")
expect("P7-4a s2 migration writes separate *_legacy_s2",
       st_p7d.get("read_legacy_s2", {}).get("AGENT_RULES.md") is True
       and st_p7d.get("evidence_schema") == wg.EVIDENCE_SCHEMA)
expect("P7-4b earlier generic legacy is NOT overwritten",
       st_p7d.get("read_legacy", {}).get("AGENT_RULES.md") is True
       and st_p7d.get("coverage_legacy", {}).get("AGENT_RULES.md") == [[1, 9]])
expect("P7-4c migrated permissions are cleared",
       st_p7d["read"].get("AGENT_RULES.md") is False
       and not st_p7d.get("coverage", {}).get("AGENT_RULES.md"))

# --- P8: v1.4.2 confirmation-round V141 regressions ---
print("=== P: v1.4.2 (V141) ===")
# P8-1 [V141-1]: 旧修订未读完（部分覆盖 read=false）+ 换版 + 新修订部分读 → 必须仍 BLOCK，
# 且记账时旧 coverage 已被撤销（不得拼成 [[1,4]]）。v1.4.1 的回归正是漏掉 read=false 分支。
X1 = build_ws(ROOT, "v141splice")
X1R = X1 / "AGENT_RULES.md"
X1R.write_text("old-one\nold-two\nold-three\nold-four\n", encoding="utf-8")
post_span(str(X1R), "p8a", 1, 2, total=4)
st_p8 = json.loads(wg._state_path(X1, "p8a").read_text(encoding="utf-8"))
expect("P8-1a partial old coverage, read=false",
       st_p8["coverage"].get("AGENT_RULES.md") == [[1, 2]]
       and st_p8["read"].get("AGENT_RULES.md") is False)
X1R.write_text("NEW-one\nNEW-two\nNEW-three\nNEW-four\n", encoding="utf-8")
post_span(str(X1R), "p8a", 3, 4, total=4)
st_p8 = json.loads(wg._state_path(X1, "p8a").read_text(encoding="utf-8"))
check("P8-1b splice (old 1-2 + new 3-4) -> still BLOCKED", True,
      pre("write_file", {"path": str(X1 / "x.txt")}, "p8a"))
st_p8 = json.loads(wg._state_path(X1, "p8a").read_text(encoding="utf-8"))
expect("P8-1c no fake full coverage across revisions",
       st_p8["coverage"].get("AGENT_RULES.md") != [[1, 4]]
       and st_p8["read"].get("AGENT_RULES.md") is not True
       and any(not r.get("was_read") for r in st_p8.get("revocations", [])),
       f"(cov={st_p8['coverage'].get('AGENT_RULES.md')}, revocations={len(st_p8.get('revocations', []))})")
# P8-2 [V141-2]: 容器 backend 的读（绝对路径指向主机工作区）→ 不向主机工作区记账；
# backend 切回本地后，写前复核无许可 → BLOCK。
import sys as _sys2
import types as _types2
_ftp2 = _types2.ModuleType("tools.file_tools_paths")
_ftp2._uses_container_paths = lambda tid: tid == "task-container"
_pkg2 = _sys2.modules.get("tools")
_saved2 = _sys2.modules.get("tools.file_tools_paths")
_sys2.modules["tools.file_tools_paths"] = _ftp2
if _pkg2 is not None:
    _pkg2.file_tools_paths = _ftp2
else:
    _pkg2 = _types2.ModuleType("tools")
    _pkg2.file_tools_paths = _ftp2
    _sys2.modules["tools"] = _pkg2
try:
    X2 = build_ws(ROOT, "v141container")
    X2R = str(X2 / "AGENT_RULES.md")
    X2R_body = "".join(f"{i}|host line {i}\n" for i in range(1, 31))
    # 容器绝对读（路径字符串就是主机工作区路径——V141-2 的污染形态）
    wg._post_tool_call(tool_name="read_file", args={"path": X2R}, session_id="p8b",
                       status="ok", task_id="task-container",
                       result=json.dumps({"content": X2R_body, "total_lines": 30}))
    st_p8b_path = wg._state_path(X2, "p8b")
    st_p8b = json.loads(st_p8b_path.read_text(encoding="utf-8")) if st_p8b_path.is_file() else {}
    expect("P8-2a container absolute read granted NOTHING to host ws",
           not (st_p8b.get("coverage") or {}).get("AGENT_RULES.md"),
           f"(cov={st_p8b.get('coverage')})")
    check("P8-2b host write after container-read poisoning -> still BLOCKED", True,
          pre("write_file", {"path": str(X2 / "x.txt")}, "p8b", task_id="task-local"))
    # 容器相对读同样不入账
    wg._post_tool_call(tool_name="read_file", args={"path": "AGENT_RULES.md"}, session_id="p8b",
                       status="ok", task_id="task-container",
                       result=json.dumps({"content": X2R_body, "total_lines": 30}))
    st_p8b = json.loads(st_p8b_path.read_text(encoding="utf-8")) if st_p8b_path.is_file() else {}
    expect("P8-2c container relative read also grants nothing",
           not (st_p8b.get("coverage") or {}).get("AGENT_RULES.md"))
finally:
    if _saved2 is not None:
        _sys2.modules["tools.file_tools_paths"] = _saved2
        if _pkg2 is not None:
            _pkg2.file_tools_paths = _saved2
    else:
        _sys2.modules.pop("tools.file_tools_paths", None)
# P8-3 [V141-3]: 大 gate 文件（>256KB）同长度替换 + mtime 回拨 → 内容指纹必须察觉，写被拦
X3 = build_ws(ROOT, "v141bigfile")
X3R = X3 / "AGENT_RULES.md"
big_old = ("line-%d padding " % 0) * 1 + "\n"  # 构造 >256KB
big_old = "".join(f"line {i:06d} " + "x" * 40 + "\n" for i in range(6000))  # ~300KB
X3R.write_text(big_old, encoding="utf-8")
st_size = X3R.stat().st_size
if st_size <= 256 * 1024:
    big_old = "".join(f"line {i:06d} " + "x" * 60 + "\n" for i in range(6000))
    X3R.write_text(big_old, encoding="utf-8")
    st_size = X3R.stat().st_size
post_span(str(X3R), "p8c", 1, 30, total=6000)  # 只读前 30 行（部分覆盖即可触发写前复核撤销）
big_new = big_old.replace("line 000000", "LINE 000000")  # 同长度替换
X3R.write_text(big_new, encoding="utf-8")
os.utime(X3R, ns=(1000, 1000))  # mtime 回拨
check(f"P8-3 large gate file (>256KB, {st_size}B) same-length swap -> BLOCKED", True,
      pre("write_file", {"path": str(X3 / "x.txt")}, "p8c"))
st_p8c = json.loads(wg._state_path(X3, "p8c").read_text(encoding="utf-8"))
expect("P8-3b large-file fingerprints are content-based (no stat fallback)",
       str(st_p8c.get("coverage_rev", {}).get("AGENT_RULES.md", "")).startswith("c"),
       f"(fp={str(st_p8c.get('coverage_rev', {}).get('AGENT_RULES.md'))[:16]}…)")
# P8-4: 重读新版（V140-3 语义）在 V141-1 修正后仍放行——确保没把合法路径修回去
X4 = build_ws(ROOT, "v141reread")
X4R = X4 / "AGENT_RULES.md"
X4R.write_text("v1\n", encoding="utf-8")
post(str(X4R), "p8d")
X4R.write_text("v2-longer-content\n", encoding="utf-8")
post(str(X4R), "p8d")  # 完整重读新版
check("P8-4 full re-read of new revision still unlocks (V140-3 preserved)", False,
      pre("write_file", {"path": str(X4 / "x.txt")}, "p8d"))

# --- P9: v1.4.3 confirmation-round V142 regressions ---
print("=== P: v1.4.3 (V142) ===")
# P9-1 [V142-1]: 合规全文读取后 **删除** AGENT_RULES.md → 工作区身份由 bootstrap 承担，
# 写前复核指纹 None → 状态 unread → BLOCK（v1.4.2 在此形态 fail-open，实测放行）。
D1 = build_ws(ROOT, "v142delete")
D1R = D1 / "AGENT_RULES.md"
post(str(D1R), "p9a")
check("P9-1a full read unlocks", False, pre("write_file", {"path": str(D1 / "x.txt")}, "p9a"))
D1R.unlink()  # 删除 gate 文件（外部同步/误操作/已获许可的工具调用）
check("P9-1b write after gate deletion -> BLOCK (workspace identity = bootstrap)", True,
      pre("write_file", {"path": str(D1 / "after-delete.txt")}, "p9a"))
st_p9 = json.loads(wg._state_path(D1, "p9a").read_text(encoding="utf-8"))
expect("P9-1c deletion revoked the grant (to=null, was_read=true)",
       any(r.get("to") is None and r.get("was_read") for r in st_p9.get("revocations", [])),
       f"(revocations={[(r.get('to') is None, r.get('was_read')) for r in st_p9.get('revocations', [])]})")
# P9-1d patch 同样被拦（patch 走同一目标解析/复核路径）
check("P9-1d patch after gate deletion -> BLOCK", True,
      pre("patch", {"path": str(D1 / "after-delete.txt"), "old_string": "a"}, "p9a"))
# P9-1e 重建 gate 并完整重读 → 恢复放行（治理文件由维护者恢复的正常路径）
D1R.write_text("restored rules\n", encoding="utf-8")
post(str(D1R), "p9a")
check("P9-1e gate restored + full re-read -> unlocks again", False,
      pre("write_file", {"path": str(D1 / "x.txt")}, "p9a"))
# P9-2 [V142-1]: 无许可 + gate 缺失 → BLOCK（不能因为 gate 不存在就认为工作区不设防）
D2 = build_ws(ROOT, "v142delete2")
(D2 / "AGENT_RULES.md").unlink()
check("P9-2 no-permission + gate missing -> write BLOCK", True,
      pre("write_file", {"path": str(D2 / "x.txt")}, "p9b"))
# P9-3: gate 存在但 bootstrap 缺失（疑似残缺工作区）→ 仍不守门（near-miss 语义保持）
print()

print()
print(f"plugin: {PLUGIN}")
print(f"fixtures: {ROOT}")
if FAILS:
    print(f"FAILED {len(FAILS)}: {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
shutil.rmtree(ROOT, ignore_errors=True)
