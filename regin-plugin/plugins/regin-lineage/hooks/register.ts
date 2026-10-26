import type { AgentSpawnInput, EngineInterface, Register } from 'claude-code'

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

async function report($: EngineInterface, e: AgentSpawnInput, agentId: string) {
  const home = await reginHome($)
  const session = await $.session.id()
  const record = {
    agentId,
    parentAgentId: e.parentAgentId ?? null,
    tool_use_id: e.tool_use_id || null,
    subagentType: e.subagentType,
  }
  const { exitCode, stderr } = await $.process.run(
    [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'trace', 'spawn', '--session', session],
    { cwd: home, stdin: JSON.stringify(record) },
  )
  if (exitCode !== 0) $.ui.log(`regin-lineage: trace spawn exited ${exitCode}: ${stderr}`, { to: 'debug' })
}

// The agent id exists only once core has started the agent, so the report
// follows next(e). Without a record regin falls back to inferring the parent,
// so a failed report is a debug line and the spawn's result passes on as is.
export const register: Register = on => {
  on('agent.spawn', async ($, e, next) => {
    const started = await next(e)
    if (started.agentId) {
      try {
        await report($, e, started.agentId)
      } catch (err) {
        $.ui.log(`regin-lineage: trace spawn failed: ${String(err)}`, { to: 'debug' })
      }
    }
    return started
  })
}
