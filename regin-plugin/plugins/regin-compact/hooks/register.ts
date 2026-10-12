import type { EngineInterface, Register } from 'claude-code'

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

// What regin says this session must not forget (its feature, task and finish
// command); '' when it holds nothing, or when regin cannot be reached.
async function sessionContext($: EngineInterface): Promise<string> {
  try {
    const home = await reginHome($)
    const session = await $.session.id()
    const { exitCode, stdout, stderr } = await $.process.run(
      [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'session', 'context', '--session', session],
      { cwd: home },
    )
    if (exitCode === 0) return stdout.trim()
    $.ui.log(`regin-compact: session context exited ${exitCode}: ${stderr}`, { to: 'debug' })
  } catch (err) {
    $.ui.log(`regin-compact: session context failed: ${String(err)}`, { to: 'debug' })
  }
  return ''
}

export function joinInstructions(instructions: string | undefined, context: string): string {
  return instructions ? `${instructions}\n\n${context}` : context
}

// A subagent's or fork's transcript is its own: the feature and the finish
// command belong to the main conversation, so only its compaction keeps them.
export const register: Register = on => {
  on('session.compact', async ($, e, next) => {
    if (e.agentId) return next(e)
    const context = await sessionContext($)
    if (!context) return next(e)
    return next({ ...e, instructions: joinInstructions(e.instructions, context) })
  })
}
