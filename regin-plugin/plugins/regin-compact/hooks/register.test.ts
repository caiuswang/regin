import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const ENV = { HOME: '/home/u', REGIN_HOME: '/opt/regin' }
const FINISH = "/opt/regin/.venv/bin/python /opt/regin/cli/regin.py task finish 506 --run 497 --reason '<what you did, and why>'"
const CONTEXT = [
  'Keep verbatim in the summary:',
  '- This session works on feature F158: Claude 新功能Mod调研（二期）',
  '- It is task 506 (限流告警与压缩上下文), run 497. Report it finished with:',
  `  ${FINISH}`,
].join('\n')

const MESSAGES = [{ role: 'user', text: 'hello', toolUses: [], toolResults: [] }]

// regin's `session context` printing `stdout`, and the compaction core sees.
function scripted(on: On, stdout: string, exitCode = 0) {
  mock.env(on, ENV)
  const runs: (readonly string[])[] = []
  const reached: { instructions?: string }[] = []
  on('session.id', () => ({ value: 'sess-1' }))
  on('process.run', (_$, e) => {
    runs.push(e.argv)
    return { value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.compact', (_$, e) => { reached.push({ instructions: e.instructions }); return { messages: e.messages } })
  return { runs, reached }
}

test('test_compaction_keeps_the_feature_task_and_finish_command', async ($, on) => {
  const { runs, reached } = scripted(on, `${CONTEXT}\n`)

  await $.session.compact({ trigger: 'manual', instructions: 'focus on tests', messages: MESSAGES } as never)

  expect(runs).toEqual([[
    '/opt/regin/.venv/bin/python', '/opt/regin/cli/regin.py', 'session', 'context', '--session', 'sess-1',
  ]])
  const instructions = reached[0]?.instructions
  expect(instructions?.startsWith('focus on tests')).toBe(true)
  expect(instructions).toContain('F158')
  expect(instructions).toContain('task 506')
  expect(instructions).toContain(FINISH)
})

test('an auto compaction with no instructions gets the context alone', async ($, on) => {
  const { reached } = scripted(on, `${CONTEXT}\n`)

  await $.session.compact({ trigger: 'auto', messages: MESSAGES } as never)

  expect(reached).toEqual([{ instructions: CONTEXT }])
})

test('test_compaction_without_context_is_untouched', async ($, on) => {
  const { runs, reached } = scripted(on, '')

  await $.session.compact({ trigger: 'manual', instructions: 'focus on tests', messages: MESSAGES } as never)

  expect(runs).toHaveLength(1)
  expect(reached).toEqual([{ instructions: 'focus on tests' }])
})

test('an unreachable regin leaves the compaction untouched', async ($, on) => {
  const { reached } = scripted(on, 'Traceback …', 1)

  await $.session.compact({ trigger: 'manual', instructions: 'focus on tests', messages: MESSAGES } as never)

  expect(reached).toEqual([{ instructions: 'focus on tests' }])
})

test('test_subagent_compaction_is_untouched', async ($, on) => {
  const { runs, reached } = scripted(on, `${CONTEXT}\n`)

  await $.session.compact({ trigger: 'auto', agentId: 'agent-1', instructions: 'keep it short', messages: MESSAGES } as never)

  expect(runs).toEqual([])
  expect(reached).toEqual([{ instructions: 'keep it short' }])
})
