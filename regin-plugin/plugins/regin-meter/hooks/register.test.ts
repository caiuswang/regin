import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const ENV = { HOME: '/home/u', REGIN_HOME: '/opt/regin' }
const WINDOW = { kind: 'five_hour', percentUsed: 82, resetsAt: '2026-10-12T20:00:00Z' }

function scripted(on: On, exitCode = 0) {
  mock.env(on, ENV)
  const runs: { argv: readonly string[]; stdin?: string }[] = []
  const reached: unknown[] = []
  on('session.id', () => ({ value: 'sess-1' }))
  on('process.run', (_$, e) => {
    runs.push({ argv: e.argv, stdin: e.init?.stdin })
    return { value: { exitCode, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.measure', (_$, e) => { reached.push(e.changed); return { changed: e.changed } })
  return { runs, reached }
}

const measure = (changed: string[]) =>
  ({ context: { window: 200000 }, rateLimits: [WINDOW], changed }) as never

test('a rate-limit change is handed to regin usage measure', async ($, on) => {
  const { runs, reached } = scripted(on)

  const result = await $.session.measure(measure(['rateLimits', 'cost']))

  expect(runs).toEqual([{
    argv: ['/opt/regin/.venv/bin/python', '/opt/regin/cli/regin.py', 'usage', 'measure', '--session', 'sess-1'],
    stdin: JSON.stringify({ rateLimits: [WINDOW], changed: ['rateLimits', 'cost'] }),
  }])
  expect(reached).toEqual([['rateLimits', 'cost']])
  expect(result).toEqual({ changed: ['rateLimits', 'cost'] })
})

test('test_meter_skips_measurements_without_rate_limit_changes', async ($, on) => {
  const { runs, reached } = scripted(on)

  await $.session.measure(measure(['context']))

  expect(runs).toEqual([])
  expect(reached).toEqual([['context']])
})

test('a failing regin still passes the measurement on', async ($, on) => {
  const { runs, reached } = scripted(on, 1)

  await $.session.measure(measure(['rateLimits']))

  expect(runs).toHaveLength(1)
  expect(reached).toEqual([['rateLimits']])
})
