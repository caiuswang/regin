import { expect, test } from 'claude-code/testing'

import { splitLines } from './register'

test('a line split across pieces is carried, not lost', async () => {
  expect(splitLines('', '{"text":"a"}\n{"te')).toEqual([['{"text":"a"}'], '{"te'])
  expect(splitLines('{"te', 'xt":"b"}\n')).toEqual([['{"text":"b"}'], ''])
})

test('listener line becomes a submitted prompt', async ($, on) => {
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
  const child = scriptedListener(on)
  await $.session.start(START)
  await $.turn.start({ text: 'long job', turnId: 'T' })
  await $.turn.complete({ ...DONE, turnId: 'T', agentId: 'sub-1' })

  const acted = child.acted()
  child.write(ABORT)
  await acted
  expect(child.aborted).toEqual(['T'])
})
