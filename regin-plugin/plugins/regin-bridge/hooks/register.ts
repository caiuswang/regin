import type { EngineInterface, Register } from 'claude-code'

let listening = false

const TURN = { plugin: 'regin-bridge', key: 'turn' } as const

export type BridgePrompt = { text: string; id: string; session_id: string }
export type BridgeControl = { op: 'abort'; id: string; session_id: string }

// A spawned child's pieces end wherever its writes did, so a line can span
// two pieces: the unterminated tail is carried into the next call.
export function splitLines(carry: string, text: string): [string[], string] {
  const parts = (carry + text).split('\n')
  const tail = parts.pop() ?? ''
  return [parts.filter(line => line.trim() !== ''), tail]
}

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

async function listen($: EngineInterface) {
  const session = await $.session.id()
  const home = await reginHome($)
  const child = $.process.spawn({
    argv: [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'bridge', 'listen', '--session', session],
    cwd: home,
  })
  let carry = ''
  for await (const { stream, text } of child) {
    if (stream !== 'stdout') continue
    const [lines, tail] = splitLines(carry, text)
    carry = tail
    for (const line of lines) {
      const frame = JSON.parse(line) as BridgePrompt | BridgeControl
      if ('op' in frame) await abortRunning($)
      else await $.prompt.submit({ text: frame.text })
    }
  }
}

// regin's "send now": end the running turn and Claude Code runs the prompts
// queued behind it. Submitting one instead would only queue it behind them.
async function abortRunning($: EngineInterface) {
  const { value: turnId } = await $.state.get(TURN)
  if (!turnId) return
  try {
    await $.turn.abort({ turnId })
  } catch (err) {
    // The turn ended between the frame and the call; the queue runs anyway.
    $.ui.log(`regin-bridge: abort skipped: ${String(err)}`, { to: 'debug' })
  }
}

export type PhaseEvent = {
  event: 'turn.start' | 'turn.step' | 'tool.call' | 'turn.complete'
  turn_id?: string
  reason?: string
}

// Only the newest event matters to regin's phase machine, so reports are
// coalesced: one `regin bridge phase` in flight, and whatever arrived meanwhile
// collapses into the latest. That keeps them in order without holding the turn
// on a CLI start per model request.
let pendingPhase: PhaseEvent | null = null
// Kept beside the TURN state so tool.call can tag its report without an
// awaited read that could land after the turn's completion was queued.
let mainTurn: string | null = null
let sendingPhase = false

export function reportPhase($: EngineInterface, ev: PhaseEvent) {
  pendingPhase = ev
  if (!sendingPhase) void drainPhase($)
}

async function drainPhase($: EngineInterface) {
  sendingPhase = true
  try {
    while (pendingPhase) {
      const ev = pendingPhase
      pendingPhase = null
      // One more attempt for a lost report, unless a newer event replaced it:
      // a lost turn.complete would otherwise hold the session at working
      // until the next turn starts.
      if (!(await sendPhase($, ev)) && !pendingPhase) await sendPhase($, ev)
    }
  } finally {
    sendingPhase = false
  }
}

async function sendPhase($: EngineInterface, ev: PhaseEvent): Promise<boolean> {
  try {
    const home = await reginHome($)
    const session = await $.session.id()
    const { exitCode, stderr } = await $.process.run(
      [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'bridge', 'phase', '--session', session],
      { cwd: home, stdin: JSON.stringify(ev) },
    )
    if (exitCode === 0) return true
    $.ui.log(`regin-bridge: phase exited ${exitCode}: ${stderr}`, { to: 'debug' })
  } catch (err) {
    $.ui.log(`regin-bridge: phase failed: ${String(err)}`, { to: 'debug' })
  }
  return false
}

// Without the child the bridge falls back to tmux on its own, so a listener
// that fails is a debug line, never an error in the person's session.
function ended($: EngineInterface, err: unknown) {
  try {
    $.ui.log(`regin-bridge: listener ended: ${String(err)}`, { to: 'debug' })
  } catch {
    // the module is unloading; there is nowhere left to log to
  }
}

// One listener per loaded module: the bridge registers the session for as long
// as this child's stream is open, and a second child would submit every prompt
// twice. A reload ends the old loop (and its child) along with the module.
export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    if (!listening) {
      listening = true
      void listen($)
        .catch(err => ended($, err))
        .finally(() => { listening = false })
    }
    return started
  })
  on('turn.start', async ($, e, next) => {
    mainTurn = e.turnId
    reportPhase($, { event: 'turn.start', turn_id: e.turnId })
    await $.state.set(TURN, e.turnId)
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (!e.agentId) reportPhase($, { event: 'turn.step', turn_id: e.turnId })
    return yield* next(e)
  })
  on('tool.call', async ($, e, next) => {
    if (!e.agentId) reportPhase($, { event: 'tool.call', ...(mainTurn ? { turn_id: mainTurn } : {}) })
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (!e.agentId) {
      if (mainTurn === e.turnId) mainTurn = null
      reportPhase($, { event: 'turn.complete', turn_id: e.turnId, reason: e.reason })
      const { value } = await $.state.get(TURN)
      if (value === e.turnId) await $.state.set(TURN, null)
    }
    return next(e)
  })
}
