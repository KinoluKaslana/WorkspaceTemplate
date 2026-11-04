// dsh 插件的合成驱动：真实 plugin.js + 真实 .workspace/hooks/hook.py，只替换运行时。
// 用法：node tests/dsh_harness.mjs <workspace> [--json]
// 退出码：0 全过；1 有失败；2 环境不满足（缺文件/缺 python3）。
import { readFileSync, appendFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'

const ws = process.argv[2]
const asJson = process.argv.includes('--json')
if (!ws) { console.error('usage: node dsh_harness.mjs <workspace>'); process.exit(2) }

const pluginPath = join(ws, '.dsh/workspace-gate/plugin.js')
const hookPath = join(ws, '.workspace/hooks/hook.py')
if (!existsSync(pluginPath) || !existsSync(hookPath)) {
  console.error(`missing plugin/hook under ${ws}`); process.exit(2)
}
if (spawnSync('python3', ['-c', 'import sys'], { stdio: 'ignore' }).status !== 0) {
  console.error('python3 not available'); process.exit(2)
}

process.env.WORKSPACE_GATE_DSH_CWD = ws
// 审计节流在测试里必须为 0，否则同一进程里第二次审计会被 5s 窗口挡掉（测的就不是逻辑了）
process.env.WORKSPACE_GATE_DSH_AUDIT_MS = '0'
// Windows 上 import() 不接受裸盘符路径（ERR_UNSUPPORTED_ESM_URL_SCHEME），统一转 file:// URL
const plugin = await import(pathToFileURL(pluginPath).href)
const handlers = {}
plugin.apply({ on: (e, fn) => { handlers[e] = fn } })
const pre = handlers['tools/pre-execute']
const post = handlers['tools/post-execute']
const nextAllow = async () => ({ kind: 'allow' })
const mk = (name, args) => ({
  name, arguments: args, callId: 'call-' + Math.random().toString(36).slice(2),
  agent: { id: 'session-harness' },
})

const results = []
const check = (label, ok, extra = '') => {
  results.push({ label, ok: !!ok, extra })
  if (!asJson) console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  ' + extra : ''}`)
}

let r = await pre(mk('write', { file_path: join(ws, 'AGENT_RULES.md'), content: 'x' }), nextAllow)
check('write-before-read denied', r.kind === 'deny')
check('deny carries [workspace-gate:no-read]', String(r.reason || '').includes('[workspace-gate:no-read]'), String(r.reason||'').slice(0,120))

// str_replace_editor 在该构建不存在（tools.definitions() 枚举无此名）；
// 这里只验证「若客户端真的发出该工具名，插件按写工具拦」这条映射。
r = await pre(mk('str_replace_editor', { path: join(ws, 'AGENT_RULES.md'), old_str: 'x', new_str: 'y' }), nextAllow)
check('str_replace_editor (path alias) gated before read', r.kind === 'deny', r.kind === 'deny' ? '' : String(r.kind))

r = await pre(mk('glob', { pattern: '*.md' }), nextAllow)
check('non-write tool passes', r.kind === 'allow')

const raw = readFileSync(join(ws, 'AGENT_RULES.md'), 'utf8')
const lines = raw.split('\n')
const physical = raw.endsWith('\n') ? lines.length - 1 : lines.length
const numbered = lines.slice(0, physical).map((l, i) => `${i + 1}: ${l}`).join('\n')
const dshReadResult = { isError: false, content: [{ type: 'text',
  text: `<path>${join(ws, 'AGENT_RULES.md')}</path>\n<type>file</type>\n<content>\n${numbered}\n\n(End of file - total ${physical} lines)\n</content>` }] }
await post(mk('read', { file_path: join(ws, 'AGENT_RULES.md'), offset: 1, limit: physical }), dshReadResult, nextAllow)
r = await pre(mk('write', { file_path: join(ws, 'AGENT_RULES.md'), content: 'x' }), nextAllow)
check('write-after-full-read allowed', r.kind === 'allow', r.kind==='deny'?String(r.reason).slice(0,160):'')

appendFileSync(join(ws, 'AGENT_RULES.md'), '\n<!-- revision bump for test -->\n')
r = await pre(mk('write', { file_path: join(ws, 'AGENT_RULES.md'), content: 'x' }), nextAllow)
check('write-after-revision-change denied', r.kind === 'deny')

r = await pre(mk('write', { path: '/tmp/dsh-harness-outside.md' }), nextAllow)
check('write outside workspace passes', r.kind === 'allow', r.kind==='deny'?String(r.reason).slice(0,160):'')

r = await pre(mk('edit', { file_path: join(ws, 'AGENT_RULES.md') }), nextAllow)
check('edit tool also gated (file_path alias)', r.kind === 'deny')

// 缺会话身份 → 插件必须 fail-closed
const noSession = { name: 'write', arguments: { path: 'AGENT_RULES.md' }, callId: 'call-nosess', agent: {} }
r = await pre(noSession, nextAllow)
check('missing session fails closed', r.kind === 'deny')

// shell 调用的盲区事后可见：post-execute 之后必须留一条 post_shell 审计，且不改变工具结果
const logPath = join(ws, '.workspace', '.dsh-gate.log')
const beforeLog = existsSync(logPath) ? readFileSync(logPath, 'utf8') : ''
r = await post(mk('bash', { command: 'awk \'BEGIN{print "x" > "blind.txt"}\'' }),
               { isError: false, content: [] }, nextAllow)
check('shell post-execute does not block', !(r && r.kind === 'deny'))
const afterLog = existsSync(logPath) ? readFileSync(logPath, 'utf8') : ''
check('post_shell audit recorded', afterLog.length > beforeLog.length && afterLog.includes('"event":"post_shell"'))

// 未知工具（不在只读集合里）也要审计：未来新增的写工具正是绕开写前识别的入口
const beforeUnknown = existsSync(logPath) ? readFileSync(logPath, 'utf8') : ''
r = await post(mk('some_future_writer', { path: join(ws, 'x.txt') }), { isError: false }, nextAllow)
check('unknown tool post does not block', !(r && r.kind === 'deny'))
const afterUnknown = existsSync(logPath) ? readFileSync(logPath, 'utf8') : ''
check('post_tool audit recorded for unknown tool', afterUnknown.length > beforeUnknown.length && afterUnknown.includes('"event":"post_tool"'))

const failed = results.filter((x) => !x.ok)
if (asJson) console.log(JSON.stringify({ results, passed: results.length - failed.length, total: results.length }))
else console.log(`\n${results.length - failed.length}/${results.length} passed`)
process.exit(failed.length ? 1 : 0)
