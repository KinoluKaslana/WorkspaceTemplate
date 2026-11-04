"""dsh 适配器的两条「失败路径」回归（SOL 复核时点出的待验证项）。

A. 解释器缺失/不可用时，插件必须在有限时间内返回 allow，而不是让工具调用永久挂起。
   依据：`plugin.js` 的 `resolvePython()` 全候选失败 → `python-missing` → 必须 settle。
B. hook 收到非 UTF-8 字节时，stdin 用 strict 解码 → 抛错 → `pre_write` 走 deny（不是被 replace 成合法 JSON 后放行）。
"""
import json
import os

os.environ.setdefault("PYTHONUTF8", "1")   # 子进程 stdio 统一 UTF-8（Windows 默认不是）
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"


class PluginPythonMissing(unittest.TestCase):
    """A：用 node 直接驱动真实 plugin.js，把候选解释器全部弄失效。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace" / "hooks").parent.mkdir(parents=True)
        shutil.copytree(HOOKS, self.ws / ".workspace" / "hooks")
        shutil.copytree(ROOT / ".dsh" / "workspace-gate", self.ws / ".dsh" / "workspace-gate")
        shutil.copy2(ROOT / "AGENT_RULES.md", self.ws / "AGENT_RULES.md")
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "mcp"},
                        "template": {"source_repo": "https://example.invalid/t"}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_python_settles_as_allow(self):
        script = r"""
import { pathToFileURL } from 'node:url'
const ws = process.argv[1]
const plugin = await import(pathToFileURL(ws + '/.dsh/workspace-gate/plugin.js').href)
const handlers = {}
plugin.apply({ on: (e, fn) => { handlers[e] = fn } })
const pre = handlers['tools/pre-execute']
const started = Date.now()
const r = await Promise.race([
  pre({ name: 'write', arguments: { file_path: ws + '/AGENT_RULES.md', content: 'x' },
        callId: 'call-nopy', agent: { id: 'session-nopy' } }, async () => ({ kind: 'allow' })),
  new Promise((resolve) => setTimeout(() => resolve({ kind: 'TIMEOUT' }), 5000)),
])
console.log(JSON.stringify({ ms: Date.now() - started, kind: r.kind, reason: String(r.reason || '') }))
"""
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用")
        env = {**os.environ,
               "WORKSPACE_GATE_DSH_CWD": str(self.ws),
               # 清空 PATH 且不设 WORKSPACE_GATE_DSH_PYTHON：三个候选全部失败 → python-missing
               "PATH": "/nonexistent"}
        env.pop("WORKSPACE_GATE_DSH_PYTHON", None)
        p = subprocess.run([node, "--input-type=module", "-e", script, str(self.ws)],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertNotEqual(out["kind"], "TIMEOUT", "插件在解释器缺失时挂起了")
        self.assertEqual(out["kind"], "allow")
        self.assertLess(out["ms"], 3000, "解释器缺失时应立即返回")
        log = (self.ws / ".workspace" / ".dsh-gate.log").read_text(encoding="utf-8")
        self.assertIn("python-missing", log)


class FailClosedKnob(unittest.TestCase):
    """C：WORKSPACE_GATE_DSH_FAIL_CLOSED=1 时，解释器缺失必须 deny（把 advisory 默认变成显式强保证）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace" / "hooks").parent.mkdir(parents=True)
        shutil.copytree(HOOKS, self.ws / ".workspace" / "hooks")
        shutil.copytree(ROOT / ".dsh" / "workspace-gate", self.ws / ".dsh" / "workspace-gate")
        shutil.copy2(ROOT / "AGENT_RULES.md", self.ws / "AGENT_RULES.md")
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "mcp"},
                        "template": {"source_repo": "https://example.invalid/t"}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fail_closed_denies_when_python_missing(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用")
        script = r"""
import { pathToFileURL } from 'node:url'
const ws = process.argv[1]
const plugin = await import(pathToFileURL(ws + '/.dsh/workspace-gate/plugin.js').href)
const handlers = {}
plugin.apply({ on: (e, fn) => { handlers[e] = fn } })
const pre = handlers['tools/pre-execute']
const r = await pre({ name: 'write', arguments: { file_path: ws + '/AGENT_RULES.md', content: 'x' },
                      callId: 'call-fc', agent: { id: 'session-fc' } }, async () => ({ kind: 'allow' }))
console.log(JSON.stringify({ kind: r.kind, reason: String(r.reason || '') }))
"""
        env = {**os.environ, "WORKSPACE_GATE_DSH_CWD": str(self.ws), "PATH": "/nonexistent",
               "WORKSPACE_GATE_DSH_FAIL_CLOSED": "1"}
        env.pop("WORKSPACE_GATE_DSH_PYTHON", None)
        p = subprocess.run([node, "--input-type=module", "-e", script, str(self.ws)],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(out["kind"], "deny", out)
        self.assertIn("internal-error", out["reason"])


class StdinStrictDecode(unittest.TestCase):
    """B：非 UTF-8 字节 → strict 解码失败 → pre_write 必须 deny。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / ".workspace").mkdir(parents=True)
        shutil.copytree(HOOKS, self.ws / ".workspace" / "hooks")
        shutil.copy2(ROOT / "AGENT_RULES.md", self.ws / "AGENT_RULES.md")
        (self.ws / ".workspace" / "bootstrap.json").write_text(
            json.dumps({"workspace_status": "active", "python": {"mode": "mcp"},
                        "template": {"source_repo": "https://example.invalid/t"}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_invalid_utf8_bytes_denied(self):
        payload = json.dumps({
            "client": "dsh", "hook_event_name": "PreToolUse", "session_id": "bad-bytes",
            "cwd": str(self.ws), "tool_name": "write",
            "tool_input": {"file_path": str(self.ws / "x.txt"), "content": "hi"},
            "tool_use_id": "call-bad",
        }).encode("utf-8")
        # 把 content 里的 hi 换成非法 UTF-8 序列 0xFF 0xFE
        broken = payload.replace(b'"hi"', b'"\xff\xfe"')
        p = subprocess.run([sys.executable, str(self.ws / ".workspace/hooks/hook.py"), "pre_write"],
                           input=broken, capture_output=True,
                           env={**os.environ, "GATE_CLIENT": "dsh"})
        out = p.stdout.decode("utf-8", "replace")
        self.assertIn('"permissionDecision": "deny"', out, out[-300:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
