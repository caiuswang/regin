import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, ToolCheckInput, ToolCheckResult } from 'claude-code'

import type { HeldCall } from '../types'

const held = atom({ plugin: 'regin-permit', key: 'held' } as const, [])

// The engine ignores a plugin's allow for these: the dialog shows regardless
// (F158 P0 Q2), so holding one would only delay that dialog.
const PERSON_REQUIRED = new Set(['AskUserQuestion', 'ExitPlanMode'])

// `$.process.run`'s own ceiling. Past it the run rejects and the call goes
// back to the native dialog; regin sets no timeout of its own.
const HOLD_MS = 600_000

export const EXIT_DECIDED = 0
export const EXIT_RELEASED = 3

export type HoldOutcome = { op: 'decide'; behavior: 'allow' | 'deny'; reason?: string }

export function parseOutcome(stdout: string): HoldOutcome | null {
  const line = stdout.trim().split('\n').pop() ?? ''
  try {
    const o = JSON.parse(line)
    if (o?.op === 'decide' && (o.behavior === 'allow' || o.behavior === 'deny')) return o
  } catch {
    // not an outcome line
  }
  return null
}

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

async function bridgeArgv($: EngineInterface, op: 'hold' | 'release', toolUseId: string) {
  const home = await reginHome($)
  const session = await $.session.id()
  const argv = [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'bridge', op,
    '--session', session, '--tool-use-id', toolUseId]
  return { argv, home }
}

async function hold($: EngineInterface, e: ToolCheckInput, toolUseId: string, asked: ToolCheckResult) {
  const { argv, home } = await bridgeArgv($, 'hold', toolUseId)
  // `input` exactly as tool.check got it: regin's card keys on its gate_key,
  // which a later classic PermissionRequest must reproduce from tool_input.
  const stdin = JSON.stringify({ tool: e.tool, input: e.input, reason: asked.reason })
  return $.process.run(argv, { cwd: home, stdin, timeoutMs: HOLD_MS })
}

export function settle(asked: ToolCheckResult, exitCode: number, stdout: string): ToolCheckResult {
  if (exitCode !== EXIT_DECIDED) return asked
  const outcome = parseOutcome(stdout)
  if (outcome === null) return asked
  return { decision: outcome.behavior, reason: outcome.reason }
}

async function notify($: EngineInterface, tool: string) {
  try {
    await $.ui.notify(`${tool} is waiting for permission — answer on regin or in the terminal`)
  } catch (err) {
    $.ui.log(`regin-permit: notify failed: ${String(err)}`, { to: 'debug' })
  }
}

// Every way the hold can end — decided, released, regin unreachable, the run
// rejected at its ceiling — leaves the call answered and the band cleared.
async function holdForRegin($: EngineInterface, e: ToolCheckInput, toolUseId: string, asked: ToolCheckResult) {
  const call: HeldCall = { toolUseId, tool: e.tool }
  await update($, held, (rows: HeldCall[]) => [...rows, call])
  try {
    await notify($, e.tool)
    const { exitCode, stdout, stderr } = await hold($, e, toolUseId, asked)
    if (exitCode !== EXIT_DECIDED && exitCode !== EXIT_RELEASED) {
      $.ui.log(`regin-permit: hold exited ${exitCode}: ${stderr}`, { to: 'debug' })
    }
    return settle(asked, exitCode, stdout)
  } catch (err) {
    $.ui.log(`regin-permit: hold failed: ${String(err)}`, { to: 'debug' })
    return asked
  } finally {
    await update($, held, (rows: HeldCall[]) => rows.filter(r => r.toolUseId !== toolUseId))
  }
}

// The hold's own process sees the release and exits 3; this only asks regin
// to end it. A failed release leaves the hold running, so it is logged.
async function release($: EngineInterface, toolUseId: string) {
  const { argv, home } = await bridgeArgv($, 'release', toolUseId)
  const { exitCode, stderr } = await $.process.run(argv, { cwd: home })
  if (exitCode !== 0) $.ui.log(`regin-permit: release exited ${exitCode}: ${stderr}`, { to: 'debug' })
}

// `setting` and `decider` arrive on engines newer than the types this mod is
// checked against, so they are read off the verdict rather than typed.
const VERDICT_KEYS = ['decision', 'reason', 'rule', 'hook', 'setting', 'decider', 'ceiling'] as const

// Above post_event's worst case (3 attempts × 500ms + backoff) and the CLI's
// start-up, well under the 30s default a hung regin would otherwise cost.
const REPORT_MS = 5_000

// regin stamps this onto the call's tool span as `attributes.permission`. Only
// the trace reads it, so a failed report is a debug line, never the verdict.
// Awaited: nothing documents un-awaited `$` work surviving the hook's return.
async function reportDecision($: EngineInterface, e: ToolCheckInput, toolUseId: string, verdict: ToolCheckResult) {
  const fields = verdict as unknown as Record<string, unknown>
  const record: Record<string, unknown> = { tool_use_id: toolUseId, agentId: e.agentId ?? null }
  for (const key of VERDICT_KEYS) if (fields[key] !== undefined) record[key] = fields[key]
  try {
    const home = await reginHome($)
    const session = await $.session.id()
    const { exitCode, stderr } = await $.process.run(
      [`${home}/.venv/bin/python`, `${home}/cli/regin.py`, 'trace', 'decision', '--session', session],
      { cwd: home, stdin: JSON.stringify(record), timeoutMs: REPORT_MS },
    )
    if (exitCode !== 0) $.ui.log(`regin-permit: trace decision exited ${exitCode}: ${stderr}`, { to: 'debug' })
  } catch (err) {
    $.ui.log(`regin-permit: trace decision failed: ${String(err)}`, { to: 'debug' })
  }
}

export const register: Register = on => {
  on('tool.check', async ($, e, next) => {
    const asked = await next(e)
    if (e.tool_use_id) await reportDecision($, e, e.tool_use_id, asked)
    if (asked.decision !== 'ask' || !e.tool_use_id || PERSON_REQUIRED.has(e.tool)) return asked
    return holdForRegin($, e, e.tool_use_id, asked)
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const rows = await read($, held)
    if (rows.length === 0) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {rows.map(r => (
          <Box key={`held-${r.toolUseId}`}>
            <Text color="yellow">{r.tool} is waiting for permission on regin </Text>
            <Button key={`release-${r.toolUseId}`} onPress={() => release($, r.toolUseId)}>
              Answer here
            </Button>
          </Box>
        ))}
      </Box>
    )
  })
}
