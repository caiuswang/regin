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
