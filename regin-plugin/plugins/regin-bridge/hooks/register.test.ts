import { expect, mock, test } from 'claude-code/testing'

import { splitLines } from './register'

const ENV = { HOME: '/home/u', REGIN_HOME: '/opt/regin' }

test('a line split across pieces is carried, not lost', async () => {
  expect(splitLines('', '{"text":"a"}\n{"te')).toEqual([['{"text":"a"}'], '{"te'])
  expect(splitLines('{"te', 'xt":"b"}\n')).toEqual([['{"text":"b"}'], ''])
})

test('listener line becomes a submitted prompt', async ($, on) => {
  mock.env(on, ENV)
  const spawned: (readonly string[])[] = []
  let submitted!: (text: string) => void
  const prompt = new Promise<string>(resolve => { submitted = resolve })
  let exit!: () => void
  const exited = new Promise<void>(resolve => { exit = resolve })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('process.spawn', async function* (_$, e) {
    spawned.push(e.argv)
    yield { stream: 'stdout' as const, text: '{"text":"hel' }
    yield { stream: 'stdout' as const, text: 'lo","id":"1","session_id":"sess-1"}\n' }
    await exited
    return { code: 0, signal: null }
  })
  on('prompt.submit', (_$, e) => {
    submitted(e.text)
    return { text: e.text }
  })

  const start = { cwd: '/tmp', surface: 'terminal', isInteractive: true } as never
  await $.session.start(start)
  await $.session.start(start)

  expect(await prompt).toBe('hello')
  expect(spawned).toHaveLength(1)
  expect(spawned[0].slice(0, 2)).toEqual(['/opt/regin/.venv/bin/python', '/opt/regin/cli/regin.py'])
  expect(spawned[0].slice(2)).toEqual(['bridge', 'listen', '--session', 'sess-1'])
  exit()
})

// A listen child whose stdout the test writes one line at a time.
function scriptedListener(on: Parameters<Parameters<typeof test>[1]>[1]) {
  const lines: string[] = []
  let wake: () => void = () => {}
  const submitted: string[] = []
  const aborted: string[] = []
  const seen: (() => void)[] = []
  const notify = () => { for (const f of seen.splice(0)) f() }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('process.spawn', async function* () {
    for (;;) {
      while (lines.length) yield { stream: 'stdout' as const, text: `${lines.shift()}\n` }
      await new Promise<void>(resolve => { wake = resolve })
    }
  })
  on('prompt.submit', (_$, e) => { submitted.push(e.text); notify(); return { text: e.text } })
  on('turn.abort', (_$, e) => { aborted.push(e.turnId); notify(); return { value: undefined } })
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  return {
    submitted,
    aborted,
    write: (frame: object) => { lines.push(JSON.stringify(frame)); wake() },
    acted: () => new Promise<void>(resolve => { seen.push(resolve) }),
  }
}

const START = { cwd: '/tmp', surface: 'terminal', isInteractive: true } as never
const ABORT = { op: 'abort', id: 'c1', session_id: 'sess-1' }
const PROMPT = { text: 'after', id: 'p1', session_id: 'sess-1' }
const DONE = { answer: 'done', durationMs: 1, isAborted: false, reason: 'answer' } as const

test('send now aborts the running turn', async ($, on) => {
  mock.env(on, ENV)
  const child = scriptedListener(on)
  await $.session.start(START)
  await $.turn.start({ text: 'long job', turnId: 'T' })

  let acted = child.acted()
  child.write(ABORT)
  await acted
  expect(child.aborted).toEqual(['T'])

  acted = child.acted()
  child.write(PROMPT)
  await acted
  expect(child.submitted).toEqual(['after'])
})

test('abort frame without a running turn is ignored', async ($, on) => {
  mock.env(on, ENV)
  const child = scriptedListener(on)
  await $.session.start(START)
  await $.turn.start({ text: 'short job', turnId: 'T' })
  await $.turn.complete({ ...DONE, turnId: 'T' })

  child.write(ABORT)
  const acted = child.acted()
  child.write(PROMPT)
  await acted
  expect(child.aborted).toEqual([])
  expect(child.submitted).toEqual(['after'])
})

test("a subagent's turn ending leaves the running turn abortable", async ($, on) => {
  mock.env(on, ENV)
  const child = scriptedListener(on)
  await $.session.start(START)
  await $.turn.start({ text: 'long job', turnId: 'T' })
  await $.turn.complete({ ...DONE, turnId: 'T', agentId: 'sub-1' })

  const acted = child.acted()
  child.write(ABORT)
  await acted
  expect(child.aborted).toEqual(['T'])
})

// A `regin bridge phase` stand-in: records each report's stdin and lets the
// test hold the in-flight one open.
function phaseReports(on: Parameters<Parameters<typeof test>[1]>[1]) {
  const sent: { event: string; turn_id?: string; reason?: string }[] = []
  const waiters: (() => void)[] = []
  let gate: Promise<void> = Promise.resolve()
  let open: () => void = () => {}
  on('process.run', async (_$, e) => {
    expect(e.argv.slice(2)).toEqual(['bridge', 'phase', '--session', 'sess-1'])
    sent.push(JSON.parse(e.init?.stdin ?? '{}'))
    for (const w of waiters.splice(0)) w()
    await gate
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return {
    sent,
    next: () => new Promise<void>(resolve => { waiters.push(resolve) }),
    hold: () => { gate = new Promise<void>(resolve => { open = resolve }) },
    release: () => open(),
  }
}

async function drainStep($: Parameters<Parameters<typeof test>[1]>[0], input: object) {
  for await (const _ of $.turn.step(input as never)) { /* drain */ }
}

test('test_run_phase_follows_turn_events: each main-loop event is reported in order', async ($, on) => {
  mock.env(on, ENV)
  scriptedListener(on)
  const phase = phaseReports(on)
  on('turn.step', async function* (_$, e) {
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn', usage: null } as never
  })
  on('tool.call', () => ({ result: { text: 'ok' } }) as never)
  await $.session.start(START)

  let reported = phase.next()
  await $.turn.start({ text: 'job', turnId: 'T' })
  await reported
  reported = phase.next()
  await drainStep($, { turnId: 'T', index: 0, model: 'claude-opus-5-5', messageCount: 1 })
  await reported
  reported = phase.next()
  await $.tool.call({ tool: 'Bash', input: { command: 'ls' }, tool_use_id: 'tu1' } as never)
  await reported
  reported = phase.next()
  await $.turn.complete({ ...DONE, turnId: 'T' })
  await reported

  expect(phase.sent).toEqual([
    { event: 'turn.start', turn_id: 'T' },
    { event: 'turn.step', turn_id: 'T' },
    { event: 'tool.call', turn_id: 'T' },
    { event: 'turn.complete', turn_id: 'T', reason: 'answer' },
  ])
})

test("a subagent's events are not reported", async ($, on) => {
  mock.env(on, ENV)
  scriptedListener(on)
  const phase = phaseReports(on)
  on('turn.step', async function* (_$, e) {
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn', usage: null } as never
  })
  await $.session.start(START)

  await drainStep($, { turnId: 'S', index: 0, model: 'claude-opus-5-5', messageCount: 1, agentId: 'sub-1' })
  await $.turn.complete({ ...DONE, turnId: 'S', agentId: 'sub-1' })
  const reported = phase.next()
  await $.turn.start({ text: 'job', turnId: 'T' })
  await reported
  expect(phase.sent).toEqual([{ event: 'turn.start', turn_id: 'T' }])
})

test('reports made while one is in flight collapse into the newest', async ($, on) => {
  mock.env(on, ENV)
  scriptedListener(on)
  const phase = phaseReports(on)
  await $.session.start(START)

  phase.hold()
  let reported = phase.next()
  await $.turn.start({ text: 'a', turnId: 'T1' })
  await reported
  await $.turn.complete({ ...DONE, turnId: 'T1' })
  await $.turn.start({ text: 'b', turnId: 'T2' })
  reported = phase.next()
  phase.release()
  await reported
  expect(phase.sent).toEqual([
    { event: 'turn.start', turn_id: 'T1' },
    { event: 'turn.start', turn_id: 'T2' },
  ])
})

test('a lost report is sent once more', async ($, on) => {
  mock.env(on, ENV)
  scriptedListener(on)
  const sent: string[] = []
  let done!: () => void
  const twice = new Promise<void>(resolve => { done = resolve })
  on('process.run', (_$, e) => {
    sent.push(JSON.parse(e.init?.stdin ?? '{}').event)
    if (sent.length === 2) done()
    return { value: { exitCode: sent.length === 1 ? 1 : 0, stdout: '', stderr: 'down', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  await $.session.start(START)
  await $.turn.complete({ ...DONE, turnId: 'T' })
  await twice
  expect(sent).toEqual(['turn.complete', 'turn.complete'])
})
