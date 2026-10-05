import type { Register } from 'claude-code'

type Question = { question: string }
type Answers = Record<string, string>

// Keys are the full question text and a multi-select answer is one string
// joined by ", ": the shape Claude Code records for a dialog answer
// (lib/agent_sdk/answers.py), so the tool can match it to its own questions.
function answerValue(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (!Array.isArray(value)) return null
  const labels = value.filter((v): v is string => typeof v === 'string' && v.trim() !== '')
  return labels.length ? labels.join(', ') : null
}

export function parseAnswers(text: string, questions: readonly Question[]): Answers | null {
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    return null
  }
  const given = (body as { answers?: unknown } | null)?.answers
  if (typeof given !== 'object' || given === null) return null

  const answers: Answers = {}
  for (const { question } of questions) {
    const value = answerValue((given as Record<string, unknown>)[question])
    if (value !== null) answers[question] = value
  }
  return Object.keys(answers).length ? answers : null
}

export const register: Register = (on, options) => {
  const answerUrl = typeof options.answerUrl === 'string' ? options.answerUrl.trim() : ''
  if (!answerUrl) return

  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    if (e.answers !== undefined) return next(e)

    const res = await $.http.fetch(answerUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tool_use_id: e.tool_use_id, questions: e.questions }),
    })
    const answers = res.ok ? parseAnswers(res.text, e.questions) : null
    return answers ? next({ ...e, answers }) : next(e)
  })
}
