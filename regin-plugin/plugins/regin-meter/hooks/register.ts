import type { EngineInterface, Register, SessionMeasureInput } from 'claude-code'

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

// regin decides whether the reading crosses an alert threshold, and dedupes it
// across sessions; the mod only hands the reading over.
async function report($: EngineInterface, e: SessionMeasureInput) {
  const home = await reginHome($)
  const session = await $.session.id()
  const { exitCode, stderr } = await $.process.run(
    [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'usage', 'measure', '--session', session],
    { cwd: home, stdin: JSON.stringify({ rateLimits: e.rateLimits, changed: e.changed }) },
  )
  if (exitCode !== 0) $.ui.log(`regin-meter: usage measure exited ${exitCode}: ${stderr}`, { to: 'debug' })
}

// A failed report is a debug line, never an error in the person's session:
// the measurement itself is the engine's and passes on regardless.
export const register: Register = on => {
  on('session.measure', async ($, e, next) => {
    if (e.changed.includes('rateLimits')) {
      try {
        await report($, e)
      } catch (err) {
        $.ui.log(`regin-meter: usage measure failed: ${String(err)}`, { to: 'debug' })
      }
    }
    return next(e)
  })
}
