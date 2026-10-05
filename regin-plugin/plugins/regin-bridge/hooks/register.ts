import type { EngineInterface, Register } from 'claude-code'

const REGIN = '/Users/taowang/regin'

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

async function listen($: EngineInterface) {
  const session = await $.session.id()
  const child = $.process.spawn({
    argv: [`${REGIN}/.venv/bin/python`, `${REGIN}/cli/regin.py`, 'bridge', 'listen', '--session', session],
    cwd: REGIN,
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
    await $.state.set(TURN, e.turnId)
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (!e.agentId) {
      const { value } = await $.state.get(TURN)
      if (value === e.turnId) await $.state.set(TURN, null)
    }
    return next(e)
  })
}
