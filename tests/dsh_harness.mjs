// dsh 插件的合成驱动：真实 plugin.js + 真实 .workspace/hooks/hook.py，只替换运行时。
// 用法：node tests/dsh_harness.mjs <workspace> [--json]
// 退出码：0 全过；1 有失败；2 环境不满足（缺文件/缺 python3）。
import { readFileSync, appendFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
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
const plugin = await import(pluginPath)
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

const failed = results.filter((x) => !x.ok)
if (asJson) console.log(JSON.stringify({ results, passed: results.length - failed.length, total: results.length }))
else console.log(`\n${results.length - failed.length}/${results.length} passed`)
process.exit(failed.length ? 1 : 0)
