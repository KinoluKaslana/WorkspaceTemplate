// workspace-gate-dsh —— DeepSeek Harness（dsh）的「先读再写」守门插件
//
// 契约来源：@deepseek-ai/dsh-tools 的 `tools/pre-execute` / `tools/post-execute`。
// 判定不在这里实现：这里只做 dsh ↔ 工作区 hook 的载荷翻译，真正的读证据与写前判定
// 由 `.workspace/hooks/hook.py`（gate_core）负责，保证各 harness 一套规则。
//
// 失败姿态：插件自身异常 / hook 超时 → **放行并记日志**（advisory，与 R99 一致）；
// 会话身份缺失 → 拒绝（fail-closed，因为无身份就没有读证据可言）。
import { spawn } from 'node:child_process'
import { appendFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

export const name = 'workspace-gate-dsh'
export const inject = ['tools']

const WRITE_TOOLS = new Set(['write', 'edit', 'str_replace_editor'])
const SHELL_TOOLS = new Set(['bash', 'pwsh'])
const HOOK_TIMEOUT_MS = 20000

function cwdOf() {
  return process.env.WORKSPACE_GATE_DSH_CWD || process.cwd()
}

function logLine(entry) {
  try {
    appendFileSync(join(cwdOf(), '.workspace', '.dsh-gate.log'),
      JSON.stringify({ at: new Date().toISOString(), ...entry }) + '\n')
  } catch { /* 日志失败不影响决策 */ }
}

function probe(entry) {
  if (process.env.WORKSPACE_GATE_DSH_PROBE !== '1') return
  try {
    appendFileSync(join(cwdOf(), '.workspace', '.dsh-probe.jsonl'),
      JSON.stringify(entry) + '\n')
  } catch { /* 取证失败不影响决策 */ }
}

function sessionId(exec, ctx) {
  const cands = [
    exec?.agent?.session?.id, exec?.agent?.sessionId, exec?.agent?.id,
    exec?.sessionId, exec?.session?.id,
    ctx?.session?.id, ctx?.session?.sessionId,
    process.env.DSH_SESSION_ID,
  ]
  return cands.find((v) => typeof v === 'string' && v.trim()) || ''
}

function agentId(exec) {
  const v = exec?.agent?.id ?? exec?.agentId
  return typeof v === 'string' && v.trim() ? v : ''
}

function payload(event, exec, ctx, extra = {}) {
  return {
    client: 'dsh',
    hook_event_name: event === 'pre_write' || event === 'pre_bash' ? 'PreToolUse' : 'PostToolUse',
    session_id: sessionId(exec, ctx),
    agent_id: agentId(exec),
    cwd: cwdOf(),
    tool_name: exec?.name,
    tool_input: exec?.arguments ?? {},
    tool_use_id: exec?.callId,
    ...extra,
  }
}

function runHook(event, data) {
  return new Promise((resolvePromise) => {
    const hook = join(cwdOf(), '.workspace', 'hooks', 'hook.py')
    if (!existsSync(hook)) {
      logLine({ event, decision: 'allow', reason: 'hook-missing', tool: data.tool_name })
      return resolvePromise({ decision: 'allow', reason: 'hook-missing' })
    }
    const child = spawn(process.env.WORKSPACE_GATE_DSH_PYTHON || 'python3', [hook, event], {
      cwd: cwdOf(),
      env: { ...process.env, GATE_CLIENT: 'dsh' },
    })
    child.stdin.write(JSON.stringify(data))      // 必须喂 stdin 并关闭，否则 hook 阻塞
    child.stdin.end()
    let out = ''
    let err = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      logLine({ event, decision: 'allow', reason: 'hook-timeout', tool: data.tool_name })
      resolvePromise({ decision: 'allow', reason: 'hook-timeout' })
    }, HOOK_TIMEOUT_MS)
    child.stdout.on('data', (b) => { out += b })
    child.stderr.on('data', (b) => { err += b })
    child.on('error', () => {
      clearTimeout(timer)
      logLine({ event, decision: 'allow', reason: 'hook-spawn-error', tool: data.tool_name })
      resolvePromise({ decision: 'allow', reason: 'hook-spawn-error' })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      let parsed = null
      try { parsed = out.trim() ? JSON.parse(out.trim().split('\n').pop()) : null } catch { parsed = null }
      const hso = parsed?.hookSpecificOutput
      if (hso?.permissionDecision === 'deny') {
        return resolvePromise({ decision: 'deny', reason: String(hso.permissionDecisionReason || 'denied') })
      }
      if (err.trim()) logLine({ event, decision: 'allow', stderr: err.slice(0, 300), code })
      resolvePromise({ decision: 'allow' })
    })
  })
}

export function apply(ctx) {
  logLine({ event: 'apply', pid: process.pid, cwd: cwdOf() })
  ctx.on('tools/pre-execute', async (exec, next) => {
    const tool = String(exec?.name ?? '')
    const event = WRITE_TOOLS.has(tool) ? 'pre_write' : (SHELL_TOOLS.has(tool) ? 'pre_bash' : null)
    if (!event) return next()

    const data = payload(event, exec, ctx)
    probe({ phase: 'pre', ...data })
    if (!data.session_id) {
      logLine({ event, decision: 'deny', reason: 'no-session', tool })
      return { kind: 'deny', reason: `[workspace-gate:no-session] 无法确定 dsh 会话身份，无法核对读证据；写入被拒绝（fail-closed）。` }
    }

    const res = await runHook(event, data)
    if (res.decision === 'deny') {
      logLine({ event, decision: 'deny', tool, call: exec?.callId })
      return { kind: 'deny', reason: res.reason }
    }
    return next()
  })

  ctx.on('tools/post-execute', async (exec, result, next) => {
    const tool = String(exec?.name ?? '')
    if (tool === 'read' || WRITE_TOOLS.has(tool)) {
      const event = tool === 'read' ? 'post_read' : 'post_write'
      const data = payload(event, exec, ctx, { tool_response: result })
      probe({ phase: 'post', ...data })
      if (data.session_id) await runHook(event, data)
    }
    return next()
  })
}
