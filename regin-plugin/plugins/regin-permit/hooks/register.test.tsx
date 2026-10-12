import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { parseOutcome } from './register'

const ENV = { HOME: '/home/u', REGIN_HOME: '/opt/regin' }
const PY = ['/opt/regin/.venv/bin/python', '/opt/regin/cli/regin.py']
const INPUT = { command: 'rm -rf build', description: 'clean' }
const ASK = { decision: 'ask' as const, reason: 'Run rm?' }

type Run = { argv: readonly string[]; stdin?: string; timeoutMs?: number }
type Exit = { exitCode: number; stdout?: string }

function done({ exitCode, stdout = '' }: Exit) {
  return { value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
}

// The engine bottom: core's verdict for every check, and each regin process
// answered by its op (`bridge hold` / `bridge release`).
function engine(on: On, verdict: object, ops: Record<string, (r: Run) => Exit | Promise<Exit>>) {
  mock.env(on, ENV)
  const runs: Run[] = []
  const notified: string[] = []
  on('session.id', () => ({ value: 'sess-1' }))
  on('ui.log', () => ({ value: undefined }) as never)
  on('tool.check', () => ({ ...verdict }) as never)
  on('ui.notify', (_$, e) => { notified.push(e.text); return { value: { isSent: false, reason: 'no-channel' } } as never })
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  on('process.run', async (_$, e) => {
    const run = { argv: e.argv, stdin: e.init?.stdin, timeoutMs: e.init?.timeoutMs }
    runs.push(run)
    const op = ops[String(e.argv[3])]
    if (!op) throw new Error(`unexpected process ${e.argv.join(' ')}`)
    return done(await op(run))
  })
  return { runs, notified }
}

const check = (tool = 'Bash', tool_use_id: string | null = 'T1') =>
  ({ tool, input: INPUT, ...(tool_use_id ? { tool_use_id } : {}) }) as never

const BAND = {
  plugin: 'regin-permit', surface: 'terminal', component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: true },
} as never

async function releaseButtons($: { ui: { mount: (t: never) => Promise<{ findAll: (q: object) => Promise<unknown[]> }> } }) {
  return (await $.ui.mount(BAND)).findAll({ key: 'release-T1' })
}

const decided = (behavior: string, reason?: string) =>
  ({ exitCode: 0, stdout: `${JSON.stringify({ op: 'decide', behavior, ...(reason ? { reason } : {}) })}\n` })

test('an outcome line is read; anything else is not an outcome', () => {
  expect(parseOutcome('{"op":"decide","behavior":"allow"}\n')).toEqual({ op: 'decide', behavior: 'allow' })
  expect(parseOutcome('{"op":"decide","behavior":"maybe"}')).toBe(null)
  expect(parseOutcome('')).toBe(null)
})

test('test_permission_ask_is_held_and_allowed_from_the_web', async ($, on) => {
  const { runs, notified } = engine(on, ASK, { hold: () => decided('allow') })

  const verdict = await $.tool.check(check())

  expect(verdict).toEqual({ decision: 'allow' })
  expect(runs).toEqual([{
    argv: [...PY, 'bridge', 'hold', '--session', 'sess-1', '--tool-use-id', 'T1'],
    stdin: JSON.stringify({ tool: 'Bash', input: INPUT, reason: 'Run rm?' }),
    timeoutMs: 600_000,
  }])
  expect(notified).toHaveLength(1)
  expect(await releaseButtons($ as never)).toEqual([])
})

test('test_permission_deny_carries_the_reason_to_the_model', async ($, on) => {
  engine(on, ASK, { hold: () => decided('deny', 'not on main') })

  expect(await $.tool.check(check())).toEqual({ decision: 'deny', reason: 'not on main' })
})

test('test_settled_permissions_are_not_held', async ($, on) => {
  const verdict = { decision: 'allow', reason: undefined as string | undefined }
  const { runs } = engine(on, verdict, {})

  expect(await $.tool.check(check())).toEqual({ decision: 'allow' })
  Object.assign(verdict, { decision: 'deny', reason: 'rule' })
  expect(await $.tool.check(check())).toEqual({ decision: 'deny', reason: 'rule' })
  expect(runs).toEqual([])
})

test('a query without a tool_use_id is not held', async ($, on) => {
  const { runs } = engine(on, ASK, {})

  expect(await $.tool.check(check('Bash', null))).toEqual(ASK)
  expect(runs).toEqual([])
})

test('test_person_required_tools_are_not_held', async ($, on) => {
  const { runs } = engine(on, ASK, {})

  for (const tool of ['AskUserQuestion', 'ExitPlanMode']) {
    expect(await $.tool.check(check(tool))).toEqual(ASK)
  }
  expect(runs).toEqual([])
})

test('test_released_permission_falls_back_to_the_native_dialog', async ($, on) => {
  let release!: () => void
  const released = new Promise<void>(resolve => { release = resolve })
  let holding!: () => void
  const isHolding = new Promise<void>(resolve => { holding = resolve })
  const { runs } = engine(on, ASK, {
    hold: async () => { holding(); await released; return { exitCode: 3 } },
    release: () => { release(); return { exitCode: 0 } },
  })

  const verdict = $.tool.check(check())
  await isHolding
  const band = await $.ui.mount(BAND)
  expect(await band.findAll({ key: 'release-T1' })).toHaveLength(1)
  await band.press({ key: 'release-T1' })

  expect(await verdict).toEqual(ASK)
  expect(runs.map(r => r.argv.slice(2))).toEqual([
    ['bridge', 'hold', '--session', 'sess-1', '--tool-use-id', 'T1'],
    ['bridge', 'release', '--session', 'sess-1', '--tool-use-id', 'T1'],
  ])
  expect(await releaseButtons($ as never)).toEqual([])
})

test('test_unreachable_regin_falls_back_to_the_native_dialog', async ($, on) => {
  engine(on, ASK, { hold: () => ({ exitCode: 2 }) })
  expect(await $.tool.check(check())).toEqual(ASK)
})

test('a hold the platform kills at its ceiling falls back to the native dialog', async ($, on) => {
  engine(on, ASK, { hold: () => { throw new Error('timed out') } })
  expect(await $.tool.check(check())).toEqual(ASK)
  expect(await releaseButtons($ as never)).toEqual([])
})
