import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const ENV = { HOME: '/home/u', REGIN_HOME: '/opt/regin' }
const ARGV = ['/opt/regin/.venv/bin/python', '/opt/regin/cli/regin.py', 'trace', 'spawn', '--session', 'sess-1']

function scripted(on: On, answer: { model: string; agentId?: string } | { deny: string }, exitCode = 0) {
  mock.env(on, ENV)
  const runs: { argv: readonly string[]; stdin?: string }[] = []
  on('session.id', () => ({ value: 'sess-1' }))
  on('process.run', (_$, e) => {
    runs.push({ argv: e.argv, stdin: e.init?.stdin })
    return { value: { exitCode, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('agent.spawn', () => answer as never)
  return runs
}

const spawn = (extra: Record<string, unknown> = {}) => ({
  tool_use_id: 'toolu_1', prompt: 'go', description: 'look', subagentType: 'Explore',
  provider: { plugin: 'engine', tier: 'core' }, parentModel: 'claude-opus-5-5',
  background: false, fork: false, ...extra,
}) as never

test('test_spawn_event_records_agent_lineage', async ($, on) => {
  const runs = scripted(on, { model: 'claude-opus-5-5', agentId: 'child-1' })

  const result = await $.agent.spawn(spawn({ parentAgentId: 'parent-1' }))

  expect(runs).toEqual([{
    argv: ARGV,
    stdin: JSON.stringify({ agentId: 'child-1', parentAgentId: 'parent-1', tool_use_id: 'toolu_1', subagentType: 'Explore' }),
  }])
  expect(result).toEqual({ model: 'claude-opus-5-5', agentId: 'child-1' })
})

test('a spawn from the main loop reports a null parent', async ($, on) => {
  const runs = scripted(on, { model: 'claude-opus-5-5', agentId: 'child-2' })

  await $.agent.spawn(spawn())

  expect(JSON.parse(runs[0].stdin ?? '{}').parentAgentId).toBeNull()
})

test('a refused spawn reports nothing', async ($, on) => {
  const runs = scripted(on, { deny: 'no' })

  const result = await $.agent.spawn(spawn())

  expect(runs).toEqual([])
  expect(result).toEqual({ deny: 'no' })
})

test('a failing regin still passes the spawn result on', async ($, on) => {
  const runs = scripted(on, { model: 'claude-opus-5-5', agentId: 'child-3' }, 1)

  const result = await $.agent.spawn(spawn())

  expect(runs).toHaveLength(1)
  expect(result).toEqual({ model: 'claude-opus-5-5', agentId: 'child-3' })
})
