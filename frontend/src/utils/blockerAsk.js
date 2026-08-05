// The shape of a parked ask as the blocker surfaces read it, and the payload
// `bridge-answer` takes back. Shared so the pop-out's Send and the answer
// surface that stages the picks can never disagree about which option index
// belongs to which question — a disagreement there answers the wrong tab.

// Every question on the card. A card assembled before the server carried the
// whole ask — or one recovered from a message body — has only the flat
// `question`/`options` fields, which are question 1 by definition.
export function askQuestions(row) {
  const carried = row?.questions
  if (Array.isArray(carried) && carried.length) return carried
  return [{
    question: row?.question || '',
    header: row?.header || '',
    multiSelect: !!row?.multi_select,
    options: row?.options || [],
  }]
}

// The TUI appends its "Type something." entry directly after the listed
// options, so a typed answer is delivered as a pick of that index.
export function freeIndex(question) {
  return (question?.options || []).length
}

export function isAnswered(answer) {
  if (!answer) return false
  if (answer.kind === 'option') return true
  return answer.kind === 'free' && !!(answer.text || '').trim()
}

// What one question delivers. `confirm_text` is the question's own text: the
// backend refuses the walk if that tab is not the focused one, so a desynced
// pane can never have an answer typed into the wrong question.
export function answerPayload(question, answer, { confirm = false } = {}) {
  const confirm_text = confirm ? (question?.question || undefined) : undefined
  if (answer?.kind === 'free') {
    return { option_index: freeIndex(question), text: (answer.text || '').trim(), confirm_text }
  }
  return { option_index: answer?.index, label: answer?.label, confirm_text }
}

// The text a staged answer will actually deliver — read off the payload so a
// pre-send summary structurally cannot drift from the POST.
export function answerSummary(question, answer) {
  if (!isAnswered(answer)) return ''
  const payload = answerPayload(question, answer)
  return payload.text || payload.label || ''
}
