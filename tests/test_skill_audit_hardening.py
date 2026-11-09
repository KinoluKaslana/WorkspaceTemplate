#!/usr/bin/env python3
"""skill_audit / template_sync 加固回归（列席评审发现的加固项）。

覆盖：退役路径、状态不默认、CRLF 审批哈希、评审产物越界、计数缺失不静默、
重复节、旧标题兼容、非 ASCII 目录名、空目录拒绝清表、TOOLS 本地块、--tools-snippet 余量提示。
运行：python3 -B tests/test_skill_audit_hardening.py
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skill_audit import FULL, audit, build  # noqa: E402

LOCAL_FULL = FULL.replace('version: 1.0.0', 'version: 1.0.0\nstatus: {status}')


def add_local(ws: Path, name: str, status: str = "approved", area: str = "", raw: bytes | None = None) -> Path:
    d = ws / ".workspace" / "skills-local" / (f"{area}/" if area else "") / name
    d.mkdir(parents=True)
    body = raw if raw is not None else LOCAL_FULL.format(name=name, status=status).encode("utf-8")
    (d / "SKILL.md").write_bytes(body)
    return d


def approve(d: Path, body: bytes, **over) -> None:
    ev = {"approved_by": "user", "approved_at": "2026-11-10T00:00:00",
          "approved_directive": "照做", "approved_skill_sha256": hashlib.sha256(body).hexdigest()}
    ev.update(over)
    (d / "APPROVAL.json").write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")


def registry_text(ws: Path) -> str:
    return (ws / "skills" / "REGISTRY.md").read_text(encoding="utf-8")


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hard-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_t1_retired_path(self):
        """退役项登记路径必须是 _retired/。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        add_local(ws, "old-one", status="deprecated", area="_retired")
        rc, out = audit(ws, "--sync-registry")
        self.assertEqual(rc, 0, out)
        self.assertIn(".workspace/skills-local/_retired/old-one/SKILL.md", registry_text(ws))

    def test_t2_status_not_defaulted(self):
        """活跃区 status 缺失不再默认成 draft。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        body = FULL.format(name="d1").encode("utf-8")
        d = add_local(ws, "d1", raw=body)
        approve(d, body)
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("status 缺失或非法", out)

    def test_t2b_deprecated_in_active(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        d = add_local(ws, "d2", status="deprecated")
        approve(d, (d / "SKILL.md").read_bytes())
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("只能是 approved", out)

    def test_t3_crlf_hash(self):
        """审批哈希按字节：CRLF 文件不应因换行归一化而假失配。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        body = LOCAL_FULL.format(name="crlf", status="approved").replace("\n", "\r\n").encode("utf-8")
        d = add_local(ws, "crlf", raw=body)
        approve(d, body)
        audit(ws, "--sync-registry")
        rc, out = audit(ws)
        self.assertNotIn("approved_skill_sha256", out)

    def test_t4_artifact_escape(self):
        """评审产物必须是工作区内相对路径。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        body = LOCAL_FULL.format(name="esc", status="approved").encode("utf-8")
        d = add_local(ws, "esc", raw=body)
        approve(d, body, approved_directive=None, review_artifact="/etc/hosts")
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("approved_directive", out)

    def test_t5_missing_count(self):
        """计数行缺失必须报，而不是静默跳过。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8").replace("已登记（1 入口：demo）", "见下表"), encoding="utf-8")
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("缺『已登记", out)

    def test_t6_duplicate_section(self):
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8") +
                       "\n## 本地层入口\n\n| 路由 ID | 状态 | 触发摘要 | 入口 |\n|---|---|---|---|\n"
                       "\n## 本地层入口\n\n| 路由 ID | 状态 | 触发摘要 | 入口 |\n|---|---|---|---|\n", encoding="utf-8")
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("多个", out)

    def test_t7_legacy_heading(self):
        """旧标题（带后缀）仍能解析，不报假「缺」。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8") + "\n## 本地层入口（workspace-skills-local）\n\n"
                       "| 路由 ID | 状态 | 触发摘要 | 入口 |\n|---|---|---|---|\n"
                       "| old-one | deprecated | x | `.workspace/skills-local/_retired/old-one/SKILL.md` |\n",
                       encoding="utf-8")
        add_local(ws, "old-one", status="deprecated", area="_retired")
        rc, out = audit(ws)
        self.assertNotIn("缺 ['old-one']", out)

    def test_t8_unicode_name(self):
        """非 ASCII 目录名不该让 --sync-registry 永久失败。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        add_local(ws, "示例技能", status="draft", area="_drafts")
        rc, out = audit(ws, "--sync-registry")
        self.assertEqual(rc, 0, out)

    def test_t9_refuse_wipe(self):
        """目录没了但登记表非空 → 拒绝清空（退出 4，文件不动）。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        reg = ws / "skills" / "REGISTRY.md"
        reg.write_text(reg.read_text(encoding="utf-8") + "\n## 本地层入口\n\n> 生成\n\n"
                       "| 路由 ID | 状态 | 触发摘要 | 入口 |\n|---|---|---|---|\n"
                       "| precious | approved | x | `.workspace/skills-local/precious/SKILL.md` |\n", encoding="utf-8")
        before = registry_text(ws)
        rc, _ = audit(ws, "--sync-registry")
        self.assertEqual(rc, 4)
        self.assertEqual(before, registry_text(ws))

    def test_t10_tools_block(self):
        """TOOLS 本地块：块外的同名行不算登记。"""
        ws = build(self.root, {"demo": FULL.format(name="demo")})
        body = LOCAL_FULL.format(name="blk", status="approved").encode("utf-8")
        d = add_local(ws, "blk", raw=body)
        approve(d, body)
        tools = ws / "TOOLS.md"
        tools.write_text(tools.read_text(encoding="utf-8") + "| **blk** | 块外 |\n", encoding="utf-8")
        rc, out = audit(ws)
        self.assertEqual(rc, 1, out)
        self.assertIn("本地层块", out)
        audit(ws, "--sync-registry")
        self.assertIn("local-skills:begin", tools.read_text(encoding="utf-8"))

    def test_t11_tools_snippet_reports_others(self):
        """--tools-snippet 必须提示其余 findings。"""
        ws = build(self.root, {"demo": FULL.format(name="demo").replace("## 维护", "## 别的")})
        rc, out = audit(ws, "--tools-snippet")
        self.assertEqual(rc, 0, out)
        self.assertIn("另有", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
