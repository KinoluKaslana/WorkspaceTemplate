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
"""
import importlib.util
import json
import os
import shutil
import sqlite3
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


def pre(tool, args, sid, parent=None):
    return wg._pre_tool_call(
        tool_name=tool, args=args, session_id=sid, parent_session_id=parent
    )


def post(path, sid, status="ok", tool="read_file"):
    return wg._post_tool_call(
        tool_name=tool, args={"path": path}, session_id=sid, status=status
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
check("E5 relative read recorded (realpath)", False,
      pre("write_file", {"path": str(WS / "y.txt")}, "sess-cli"))
post(str(WS / "AGENT_RULES.md"), "s6")
check("E6 read ws-A does not unlock ws-B", True, pre("write_file", {"path": str(TOP / "y.txt")}, "s6"))
# F. state robustness / bookkeeping
sp7 = WS / ".workspace" / ".hook-state" / "s7.json"
sp7.parent.mkdir(parents=True, exist_ok=True)
sp7.write_text('{"read": {"AGENT_RULES.md": true}}', encoding="utf-8")
check("F1 legacy state: honors gate key", False, pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
sp7.write_text("not json at all", encoding="utf-8")
check("F2 corrupt state: re-init -> block", True, pre("write_file", {"path": str(WS / "y.txt")}, "s7"))
sp7.write_text('{"status": "active"}', encoding="utf-8")
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
check("J2 near-miss ws (marker missing) not gated", False,
      pre("write_file", {"path": str(NEARMISS / "x.txt")}, "s1"))
near_hits = [
    ln for ln in (HOMEFAKE / "logs" / "workspace-gate-plugin.log").read_text(
        encoding="utf-8").splitlines() if str(NEARMISS) in ln
]
expect("J3 near-miss workspace logged", bool(near_hits), f"({len(near_hits)} log line(s))")

print()
print(f"plugin: {PLUGIN}")
print(f"fixtures: {ROOT}")
if FAILS:
    print(f"FAILED {len(FAILS)}: {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
shutil.rmtree(ROOT, ignore_errors=True)
