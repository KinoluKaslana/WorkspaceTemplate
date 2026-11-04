# Security Policy

## 报告漏洞

请**不要**开公开 issue。用 GitHub 的私密报告通道：
<https://github.com/KinoluKaslana/WorkspaceTemplate/security/advisories/new>

或在仓库 Settings → Security → Advisories 里新建草稿。

请附：影响版本（`git describe --tags`）、复现步骤、预期与实际行为、影响面（是否能绕过守门写文件）。

## 承诺

- 3 个工作日内确认收到，7 个工作日内给初步结论。
- 修复流程：补测试 → 修复 → 在 `dev` 验证 → 合并 `main` 并打补丁版本标签。

## 范围说明（重要）

本模板的守门是 **advisory 边界**，不是安全沙箱：

- hook 未送达、客户端不实现该事件、同 uid 进程直接改文件 —— 均可绕过，见 `docs/architecture.md` §4。
- 因此「绕过守门写文件」本身不算安全漏洞，属于**已知设计边界**；我们关心的是：在正常送达路径上出现错误放行（fail-open 于应当 deny 的语义），或审计记录与实际决策不一致。
- `WORKSPACE_GATE_DSH_FAIL_CLOSED=1` 可把「hook 故障即放行」改为「故障即拒绝」，需要更强保证的场景请显式开启。
