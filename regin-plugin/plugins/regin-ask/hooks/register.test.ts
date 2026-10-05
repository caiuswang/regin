import { expect, test } from 'claude-code/testing'

import { parseAnswers } from './register'

const URL = 'http://127.0.0.1:8321/api/ask'
const QUESTIONS = [{
  question: 'Which library should we use?',
  header: 'Library',
  options: [{ label: 'dayjs', description: 'small' }, { label: 'luxon', description: 'zones' }],
  multiSelect: false,
}]
const ANSWERED = JSON.stringify({ answers: { 'Which library should we use?': 'luxon' } })

function respond(text: string, status = 200) {
  return () => ({ value: { status, ok: status < 300, headers: {}, text } })
}

test('answers are keyed by full question text; a multi-select list is comma-joined', async () => {
  const qs = [{ question: 'Q one?' }, { question: 'Q two?' }]
  expect(parseAnswers(JSON.stringify({ answers: { 'Q one?': 'A', 'Q two?': ['B', 'C'] } }), qs))
    .toEqual({ 'Q one?': 'A', 'Q two?': 'B, C' })
  expect(parseAnswers(JSON.stringify({ answers: { 'Library': 'A' } }), qs)).toBe(null)
  expect(parseAnswers('not json', qs)).toBe(null)
})

test('ask rewrite reaches the tool with answers', { options: { answerUrl: URL } }, async ($, on) => {
  const posted: unknown[] = []
  const reached: Record<string, unknown>[] = []
  on('http.fetch', (_$, e) => { posted.push(e); return respond(ANSWERED)() })
  on('tool.call', (_$, e) => { reached.push({ ...e }); return { result: { answers: {} } } as never })

  await $.tool.call({ tool: 'AskUserQuestion', questions: QUESTIONS } as never)

  expect(reached.length).toBe(1)
  expect(reached[0]?.answers).toEqual({ 'Which library should we use?': 'luxon' })
  expect(reached[0]?.questions).toEqual(QUESTIONS)
  expect(posted.length).toBe(1)
})

test('other tools pass untouched', { options: { answerUrl: URL } }, async ($, on) => {
  const posted: unknown[] = []
  const reached: Record<string, unknown>[] = []
  on('http.fetch', (_$, e) => { posted.push(e); return respond(ANSWERED)() })
  on('tool.call', (_$, e) => { reached.push({ ...e }); return { result: { stdout: '', stderr: '', interrupted: false } } as never })

  await $.tool.call({ tool: 'Bash', command: 'ls', description: 'List files' } as never)

  expect(reached.length).toBe(1)
  expect(reached[0]?.command).toBe('ls')
  expect('answers' in (reached[0] ?? {})).toBe(false)
  expect(posted.length).toBe(0)
})

for (const [name, options, reply] of [
  ['no answer source configured', {}, respond(ANSWERED)],
  ['the source errors', { answerUrl: URL }, respond('boom', 503)],
  ['the source answers no asked question', { answerUrl: URL }, respond(JSON.stringify({ answers: { Library: 'luxon' } }))],
] as const) {
  test(`the dialog keeps the question when ${name}`, { options }, async ($, on) => {
    const reached: Record<string, unknown>[] = []
    on('http.fetch', reply)
    on('tool.call', (_$, e) => { reached.push({ ...e }); return { result: { answers: {} } } as never })

    await $.tool.call({ tool: 'AskUserQuestion', questions: QUESTIONS } as never)

    expect(reached.length).toBe(1)
    expect('answers' in (reached[0] ?? {})).toBe(false)
  })
}
