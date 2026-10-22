#!/usr/bin/env python3
"""wtarget 写目标窄识别器 — 单元矩阵（从 W1b 回放矩阵抽取，86 例）。

自包含：不依赖历史语料，只依赖 .workspace/hooks/wtarget.py。
运行：python3 -B hooks-tests/test_wtarget.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".workspace" / "hooks"
sys.path.insert(0, str(HOOKS))
import wtarget  # noqa: E402

GATE_REL = "AGENT_RULES.md"
CWD_MATRIX = str(ROOT)
GATE_MATRIX = [os.path.join(CWD_MATRIX, GATE_REL)]

def _matrix():
    """(command, expected_kind, expect_gate_hit, note)。覆盖判定纪律 3–8 + 对抗负样本。"""
    G = GATE_REL
    return [
        ("echo hi > AGENT_RULES.md", "write", True, "redirect:>"),
        ("printf 'x' >> AGENT_RULES.md", "write", True, "redirect:>>"),
        ("echo hi 2> AGENT_RULES.md", "write", True, "redirect:2>"),
        ("echo hi 2>> AGENT_RULES.md", "write", True, "redirect:2>>"),
        ("echo hi &> AGENT_RULES.md", "write", True, "redirect:&>"),
        ("echo hi >| AGENT_RULES.md", "write", True, "redirect:>|"),
        ("echo hi >AGENT_RULES.md", "write", True, "redirect:> 紧贴写法"),
        ("tee -a AGENT_RULES.md", "write", True, "tee -a"),
        ("tee AGENT_RULES.md other.txt", "write", True, "tee 多操作数"),
        ("sed -i 's/a/b/' AGENT_RULES.md", "write", True, "sed -i"),
        ("sed -i.bak 's/a/b/' AGENT_RULES.md", "write", True, "sed -i.bak"),
        ("sed -E -i 's/a/b/' AGENT_RULES.md", "write", True, "sed -E -i"),
        ("sed -i -e 's/a/b/' AGENT_RULES.md", "write", True, "sed -i -e（脚本不算文件）"),
        ("perl -i -pe 's/a/b/' AGENT_RULES.md", "write", True, "perl -i"),
        ("perl -pi -e 's/a/b/' AGENT_RULES.md", "write", True, "perl -pi"),
        ("truncate -s 0 AGENT_RULES.md", "write", True, "truncate -s N FILE"),
        ("mv /tmp/x AGENT_RULES.md", "write", True, "mv 最后一个操作数"),
        ("cp /tmp/x AGENT_RULES.md", "write", True, "cp 最后一个操作数"),
        ("install -m 644 /tmp/x AGENT_RULES.md", "write", True, "install"),
        ("dd of=AGENT_RULES.md", "write", True, "dd of="),
        ("cat > AGENT_RULES.md <<EOF\nx\nEOF", "write", True, "cat> heredoc 由重定向覆盖"),
        ("sed -n '1,50p' AGENT_RULES.md", "read", False, "读：sed -n"),
        ("cat AGENT_RULES.md", "read", False, "读：cat"),
        ("grep foo AGENT_RULES.md", "read", False, "读：grep"),
        ("egrep foo AGENT_RULES.md", "read", False, "读：egrep"),
        ("ls", "read", False, "读：ls"),
        ("od AGENT_RULES.md", "read", False, "读：od"),
        ("wc -l AGENT_RULES.md", "read", False, "读：wc"),
        ("head -5 AGENT_RULES.md", "read", False, "读：head"),
        ("tail -5 AGENT_RULES.md", "read", False, "读：tail"),
        ("test -f x", "read", False, "读：test"),
        ("true", "read", False, "读：true"),
        ("mkdir -p artifacts", "other", False, "other：mkdir"),
        ("rm AGENT_RULES.md", "other", False, "other：rm 不是写目标"),
        ("git diff -- AGENT_RULES.md", "unresolved", False, "F2-§1：git 不在已知安全集合 → unresolved"),
        ("python3 -c \"open('AGENT_RULES.md','w')\"", "unresolved", False, "unresolved：python3 -c"),
        ("sh -c 'sed -i s/a/b/ AGENT_RULES.md'", "unresolved", False, "unresolved：sh -c"),
        ("make", "unresolved", False, "unresolved：make"),
        ("git apply x.patch", "unresolved", False, "unresolved：git apply"),
        ("git checkout -- AGENT_RULES.md", "unresolved", False, "unresolved：git checkout --"),
        ("echo \"a > b\" > f", "write", False, "对抗：引号内 > 不是重定向，> f 是"),
        ("echo hi 2>&1", "other", False, "对抗：2>&1 fd 复制"),
        ("sed -n '1,5p' f > /tmp/x", "write", False, "对抗：读命令带重定向→写 /tmp/x"),
        ("cd /x && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd 前缀→整条 unresolved（不猜 cwd）"),
        ("echo ok > ~/.bashrc", "write", False, "对抗：~ 不展开（partial）"),
        ("mv a b/", "unresolved", False, "对抗：mv 目标是目录"),
        ("cp a b c/", "unresolved", False, "对抗：cp 多源→目录"),
        ("echo OK > /dev/null", "other", False, "对抗：>/dev/null 不算目标"),
        ("for f in a; do echo $f; done", "unresolved", False, "对抗：$ 变量"),
        ("sed -i \"$f\" AGENT_RULES.md", "unresolved", False, "对抗：目标为变量"),
        # ---- F 席修复回归（缺陷 A heredoc-first / 缺陷 B cd 前缀）----
        ("cat <<'EOF' > AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：heredoc-first >（定界符后重定向）"),
        ("cat <<EOF >> AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：heredoc-first >>"),
        ("cat <<-EOF > AGENT_RULES.md\nhi\nEOF", "write", True, "F-A：<<- 前缀 >"),
        ("cat > AGENT_RULES.md <<'EOF'\nhi\nEOF", "write", True, "F-A：> 在前 heredoc（回归）"),
        ("cat >> AGENT_RULES.md <<EOF\nhi\nEOF", "write", True, "F-A：>> 在前 heredoc（回归）"),
        ("cat <<'EOF' > /tmp/x\necho a > AGENT_RULES.md\nEOF", "write", False, "F-A：heredoc 正文含 > 不当重定向"),
        ("cat <<EOF > /tmp/notgate\nhi\nEOF", "write", False, "F-A：非 gate 目标不误命中"),
        ("cd /tmp && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd /tmp 前缀不命中 gate"),
        ("cd /home/kinolu/Workspaces/TemplateDev/hook-path-probe-261021/gate-ws && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd gate-ws 不因 payload cwd 误判"),
        ("cd - && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd - 前缀"),
        ("cd .. && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：cd .. 前缀"),
        ("pushd /tmp && sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F-B：pushd 前缀"),
        ("sed -i 's/a/b/' /home/kinolu/Workspaces/TemplateDev/hook-path-probe-261021/gate-ws/AGENT_RULES.md", "write", True, "F-B：无 cd 绝对路径 gate 写照旧命中"),
        # ---- F2 席修复回归（N4 子 shell / N5 包装前缀 / N6 apply_patch 命令位 / N7 输入重定向）----
        ("(cd /tmp && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：子 shell (cd 前缀 → 整条 unresolved"),
        ("(cd /home/kinolu/Workspaces/TemplateDev/hook-path-probe-261021/gate-ws && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：子 shell cd gate-ws 不因 payload cwd 误判"),
        ("(pushd /tmp && sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：(pushd 变体"),
        ("(sed -i 's/a/b/' AGENT_RULES.md)", "unresolved", False, "F2-N4：裸括号子 shell"),
        ("echo hi > AGENT_RULES.md)", "unresolved", False, "F2-N4：目标尾括号不拼进路径"),
        ("env X=1 sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：env 包装前缀"),
        ("sudo sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：sudo 包装前缀"),
        ("timeout 5 sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：timeout 包装前缀"),
        ("xargs sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：xargs 包装前缀"),
        ("command sed -i 's/a/b/' AGENT_RULES.md", "unresolved", False, "F2-N5：command 包装前缀"),
        ("cat > AGENT_RULES.md <<EOF\napply_patch\nEOF", "write", True, "F2-N6：heredoc 正文 apply_patch 不当 patch"),
        ("echo apply_patch > /tmp/x", "write", False, "F2-N6：apply_patch 作参数 → 写非 gate"),
        ("grep apply_patch AGENT_RULES.md", "read", False, "F2-N6：grep apply_patch → read"),
        ("apply_patch x.patch", "patch", False, "F2-N6：apply_patch 命令位仍 patch"),
        ("tee AGENT_RULES.md < /tmp/x", "write", True, "F2-N7：输入重定向 < 非写目标"),
        ("sed -n 1,5p < AGENT_RULES.md", "read", False, "F2-N7：sed -n 输入重定向 read 无 targets"),
        ("ls -l", "read", False, "F2-§1：ls 读命令不回归"),
        ("grep -n x AGENT_RULES.md", "read", False, "F2-§1：grep 读命令不回归"),
        # ---- F3 席修复回归（perl 无 -i → unresolved；-i/-pi/-i.bak → write）----
        ("perl -e 'open OUT, \">AGENT_RULES.md\"'", "unresolved", False, "F3：perl -e 可写 → unresolved"),
        ("perl fix_script.pl", "unresolved", False, "F3：perl 脚本文件 → unresolved"),
        ("perl -i.bak -pe 's/a/b/' AGENT_RULES.md", "write", True, "F3：perl -i.bak 仍 write"),
        ("perl -ne 'print' AGENT_RULES.md", "unresolved", False, "F3：perl -n -e 无 -i → unresolved"),
        ("perl -pe 's/a/b/' AGENT_RULES.md", "unresolved", False, "F3：perl -p 无 -i → unresolved"),
    ]

class WTargetMatrix(unittest.TestCase):
    def test_matrix(self):
        failures = []
        cases = _matrix()
        legacy_root = "/home/kinolu/Workspaces/TemplateDev/hook-path-probe-261021/gate-ws"
        for cmd, exp_kind, exp_hit, note in cases:
            # 矩阵里少数用例写死了当年探针工作区的绝对路径；移植到本仓时映射到 ROOT。
            cmd = cmd.replace(legacy_root, CWD_MATRIX)
            r = wtarget.classify(cmd, CWD_MATRIX, GATE_MATRIX)
            got_hit = bool(r.get("gate_targets"))
            if r["kind"] != exp_kind or got_hit != exp_hit:
                failures.append(
                    f"{note}: {cmd!r} expected=({exp_kind},{exp_hit}) actual=({r['kind']},{got_hit})")
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreaterEqual(len(cases), 80)


if __name__ == "__main__":
    unittest.main(verbosity=1)
