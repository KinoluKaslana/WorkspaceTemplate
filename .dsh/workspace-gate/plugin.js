// workspace-gate-dsh —— DeepSeek Harness（dsh）的「先读再写」守门插件
//
// 契约来源：@deepseek-ai/dsh-tools 的 `tools/pre-execute` / `tools/post-execute`。
// 判定不在这里实现：这里只做 dsh ↔ 工作区 hook 的载荷翻译，真正的读证据与写前判定
// 由 `.workspace/hooks/hook.py`（gate_core）负责，保证各 harness 一套规则。
//
// 失败姿态：插件自身异常 / hook 超时 → **放行并记日志**（advisory，与 R99 一致）；
// 会话身份缺失 → 拒绝（fail-closed，因为无身份就没有读证据可言）。
import { spawn, spawnSync } from 'node:child_process'
import { appendFileSync, existsSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

export const name = 'workspace-gate-dsh'
// 只注入 tools：dsh 没有 `session` 服务（声明它会 pending 而永不激活）
export const inject = ['tools']

const WRITE_TOOLS = new Set(['write', 'edit', 'str_replace_editor'])
const SHELL_TOOLS = new Set(['bash', 'pwsh'])
const HOOK_TIMEOUT_MS = 20000

function cwdOf() {
  return process.env.WORKSPACE_GATE_DSH_CWD || process.cwd()
}

let _pyCache
/**
 * 选 Python 可执行文件：环境变量优先，否则按平台探测。
 * Windows 上没有 `python3`（只有 `python` / `py`），写死 python3 会让 spawn 失败 → fail-open 静默失效。
 */
function resolvePython() {
  if (_pyCache !== undefined) return _pyCache
  const env = process.env.WORKSPACE_GATE_DSH_PYTHON
  if (env) { _pyCache = env; return _pyCache }
  const candidates = process.platform === 'win32'
    ? ['python', 'py', 'python3']
    : ['python3', 'python']
  for (const c of candidates) {
    // 必须真的执行 Python 代码：Windows 的 WindowsApps\python.exe 是个 Store 存根，
    // 退出码 0、无输出、什么都不做（用它守门 = 静默失效）。
    const r = spawnSync(c, ['-c', 'import sys;print(sys.version_info[0])'],
                        { timeout: 4000, encoding: 'utf8' })
    if (r && r.status === 0 && String(r.stdout || '').trim() === '3') {
      _pyCache = c
      return _pyCache
    }
  }
  _pyCache = ''
  return _pyCache
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

function safeGet(obj, key) {
  // Cordis 在未声明 inject 时对服务属性抛错：探测用安全读，避免整条工具调用被带崩
  try { return obj == null ? undefined : obj[key] } catch { return undefined }
}

function sessionId(exec, ctx) {
  // 实测：pre-execute 的 exec.agent.id = "session-<uuid>"；
  // post-execute 的载荷可能不带 agent，回落到 ctx.session（inject 里已声明）。
  const cands = [
    exec?.agent?.id,
    exec?.agent?.sessionId,
    exec?.sessionId,
    safeGet(safeGet(ctx, 'session'), 'id'),
    safeGet(safeGet(ctx, 'session'), 'sessionId'),
    safeGet(safeGet(ctx, 'session'), 'agent')?.id,
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
    // 实测：agent.id 就是会话 id；再当 agent_id 传会得到 "a.a" 双拼键
    agent_id: agentId(exec) === sessionId(exec, ctx) ? '' : agentId(exec),
    cwd: cwdOf(),
    // dsh 的 bash/pwsh 走同一套 shell 静态分析，hook 侧认 "Bash"
    tool_name: SHELL_TOOLS.has(String(exec?.name ?? '')) ? 'Bash' : exec?.name,
    dsh_tool: exec?.name,
    tool_input: exec?.arguments ?? {},
    tool_use_id: exec?.callId,
    ...extra,
  }
}

function runHook(event, data) {
  let body
  try {
    body = JSON.stringify(data)
  } catch (e) {
    logLine({ event, decision: 'allow', reason: 'payload-unserializable', error: String(e) })
    return Promise.resolve({ decision: 'allow', reason: 'payload-unserializable' })
  }
  return new Promise((resolvePromise) => {
    const hook = join(cwdOf(), '.workspace', 'hooks', 'hook.py')
    if (!existsSync(hook)) {
      logLine({ event, decision: 'allow', reason: 'hook-missing', tool: data.tool_name })
      return resolvePromise({ decision: 'allow', reason: 'hook-missing' })
    }
    const py = resolvePython()
    if (!py) {
      logLine({ event, decision: 'allow', reason: 'python-missing', tool: data.tool_name })
      return Promise.resolve({ decision: 'allow', reason: 'python-missing' })
    }
    const child = spawn(py, [hook, event], {
      cwd: cwdOf(),
      env: { ...process.env, GATE_CLIENT: 'dsh' },
    })
    if (event === 'post_read' && process.env.WORKSPACE_GATE_DSH_PROBE === '1') {
      probe({ phase: 'post-body', head: body.slice(0, 500), bytes: body.length })
      try { writeFileSync(join(cwdOf(), '.workspace', '_post_body.json'), body) } catch { /* ignore */ }
    }
    child.stdin.write(body)                      // 必须喂 stdin 并关闭，否则 hook 阻塞
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
      resolvePromise({ decision: 'allow', stderr: err, code })
    })
  })
}

export function apply(ctx) {
  logLine({ event: 'apply', pid: process.pid, cwd: cwdOf() })
  // pre 里能拿到 exec.agent.id；post 的载荷不带 agent，所以按 callId 记住会话
  const sidByCall = new Map()
  let lastSid = ''

  ctx.on('tools/pre-execute', async (exec, next) => {
    const tool = String(exec?.name ?? '')
    const sid = sessionId(exec, ctx)
    if (sid) {
      lastSid = sid
      if (exec?.callId) sidByCall.set(String(exec.callId), sid)
    }
    const event = WRITE_TOOLS.has(tool) ? 'pre_write' : (SHELL_TOOLS.has(tool) ? 'pre_bash' : null)
    if (!event) return next()

    const data = payload(event, exec, ctx)
    if (!data.session_id) data.session_id = sid || lastSid
    probe({ phase: 'pre', event, tool, sid: data.session_id })
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
    const tool = String(exec?.name ?? exec?.tool?.name ?? '')
    probe({ phase: 'post-shape', tool, execKeys: exec && typeof exec === 'object' ? Object.keys(exec).slice(0, 14) : [],
            argKeys: exec?.arguments && typeof exec.arguments === 'object' ? Object.keys(exec.arguments).slice(0, 8) : [],
            resultKeys: result && typeof result === 'object' ? Object.keys(result).slice(0, 8) : [] })
    if (tool === 'read' && process.env.WORKSPACE_GATE_DSH_PROBE === '1') {
      try {
        const dump = JSON.stringify(result)
        probe({ phase: 'read-result', head: dump.slice(0, 1500) })
      } catch (e) { probe({ phase: 'read-result', error: String(e) }) }
    }
    if (tool === 'read' || WRITE_TOOLS.has(tool)) {
      const event = tool === 'read' ? 'post_read' : 'post_write'
      try {
        const sid = sidByCall.get(String(exec?.callId ?? '')) || sessionId(exec, ctx) || lastSid
        const data = payload(event, exec, ctx, { tool_response: result })
        if (!data.session_id) data.session_id = sid
        if (data.session_id) {
          const r = await runHook(event, data)
          logLine({ event, decision: r.decision, tool, sid: data.session_id,
                    stderr: String(r.stderr || '').slice(0, 300), code: r.code ?? null })
          probe({ phase: 'post', event, tool, sid: data.session_id, decision: r.decision, reason: String(r.reason || '').slice(0, 120) })
        } else {
          probe({ phase: 'post-skip', event, tool, reason: 'no-session' })
        }
      } catch (e) {
        logLine({ event, decision: 'allow', reason: 'post-error', error: String(e) })
      }
    }
    return next()
  })
}
