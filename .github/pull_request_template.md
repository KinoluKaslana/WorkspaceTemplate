## 这个 PR 改了什么

## 为什么（问题背景）

## 验证方式（贴真实输出）

```
python3 scripts/mark_rules.py check AGENT_RULES.md
python3 scripts/verify_rules.py --workspace .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s hooks-tests -p 'test_*.py'
```

## 检查项

- [ ] 新增/修改行为有对应测试
- [ ] 路径比较用 `as_posix()`，未引入平台分隔符假设
- [ ] 无新增第三方运行时依赖
- [ ] 改了规则则已重建 `.workspace/rule-clauses.json`
- [ ] `skill_audit.py --strict` 仍为 0 findings（若改了 skill）
