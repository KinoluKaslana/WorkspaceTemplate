# 参与贡献

## 快速上手

```bash
git clone https://github.com/KinoluKaslana/WorkspaceTemplate.git
cd WorkspaceTemplate
python3 scripts/mark_rules.py check AGENT_RULES.md     # 条款标记完整性
python3 scripts/verify_rules.py --workspace .          # 注册表 + 文件哈希
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s hooks-tests -p 'test_*.py'
```

全绿才算环境就绪；Windows 上符号链接相关用例会自动跳过（需开发者模式）。git 相关用例在 `shutil.which("git")` 为空时**显式 skip 而不是报错**（`test_template_anchor`、`test_template_sync_rollback` 整类跳过；`test_agent_scope`/`test_bash_dispatch`/`test_cc_transcript`/`test_dsh_post_shell` 的 git 夹具抛 `SkipTest`）。想验证这条声明：`python3 tests/no_git_runner.py`（把 `git` 从 `which` 里摘掉跑全量，期望 `failures=0 errors=0`）。

## 改规则（AGENT_RULES.md）

1. 每条规则必须带 `<!-- id:R<n> -->` 标记，且 ID 全局唯一。
2. 改完跑 `python3 scripts/mark_rules.py registry AGENT_RULES.md` 重新生成 `.workspace/rule-clauses.json`。
3. `python3 scripts/clause_lint.py` 必须 0 error / 0 warning。
4. 条款里的手动步骤要么机械化，要么写清判定标准——不要留「按需判断」。

## 改代码

- 只依赖标准库；不要引入第三方包（`requirements.txt` 只用于开发工具）。
- 路径比较一律 `as_posix()`，不要 `str(relative_to(...))`（Windows 会产出反斜杠）。
- 新增行为必须带测试；修 bug 先写复现测试。
- 提交信息写「为什么」，不写「改了什么」——diff 已经说明改了哪里。

## 改 skill

`skill_audit.py --strict` 必须 0 findings；每个 skill 的 `version:` 要与 `docs/skills.md` 一致。

## 分支与发布

- 日常开发在 `dev`，`alpha` 用于试发，`main` 只接受已通过全部门禁的合并。
- `TEMPLATE_FILES` 里的文件一变，同一次提交必须带 `CHANGELOG.md` 改动；否则 governance job 会以
  `release_anchor` 失败（`.github/scripts/release_anchor.py`）。这条闸只保证「有留痕」，改得对不对仍然靠评审。
- 发布前跑一遍 `gate_status.py --require <harness> --ttl-minutes 30` 的**伪证负例**（把 `.dsh-gate.log` 的 `at` 改成 2 小时前、mtime 置现在，必须判过期——`tests/test_gate_status.py` 已固化）。
- 发布打 tag（`vX.Y.Z`），tag 是模板同步的版本锚点。
- 详细流程见 `UPGRADE.md`（旧工作区自举）与 `docs/README.md`（文档索引）；模板层同步机制见 `.workspace/skills/template-update/SKILL.md`。
