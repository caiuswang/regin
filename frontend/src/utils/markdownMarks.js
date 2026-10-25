// The edit a markdown toolbar button makes, as one replacement of the source:
// `{ start, end, text }` to write over, and `select` — the range to leave
// selected afterwards. One replacement, so the browser records one undo step.

const WRAPS = { bold: '**', italic: '_' }
const LINE_MARKS = {
  heading: (line) => deeperHeading(line),
  bullet_list: (line) => `- ${line}`,
  numbered_list: (line, i) => `${i + 1}. ${line}`,
  checkbox: (line) => `- [ ] ${line}`,
}
const MAX_HEADING = 4
const FENCE = '```'

function deeperHeading(line) {
  const hashes = /^#+(?= |$)/.exec(line)?.[0].length ?? 0
  if (!hashes) return `## ${line}`
  return hashes >= MAX_HEADING ? line : `#${line}`
}

// The whole lines a selection touches. A selection ending just past a newline
// ends on the line before: the caret sits at the next line's start, but no
// character of that line was selected.
function lineSpan(value, start, end) {
  const last = end > start && value[end - 1] === '\n' ? end - 1 : end
  const from = start === 0 ? 0 : value.lastIndexOf('\n', start - 1) + 1
  const nl = value.indexOf('\n', last)
  return { from, to: nl === -1 ? value.length : nl }
}

function wrapEdit(marker, value, start, end) {
  const inner = start + marker.length
  return {
    start, end,
    text: marker + value.slice(start, end) + marker,
    select: [inner, inner + (end - start)],
  }
}

function lineEdit(mark, value, start, end) {
  const { from, to } = lineSpan(value, start, end)
  const before = value.slice(from, to)
  const text = before.split('\n').map(mark).join('\n')
  if (start === end) {
    const caret = Math.max(from, start + text.length - before.length)
    return { start: from, end: to, text, select: [caret, caret] }
  }
  return { start: from, end: to, text, select: [from, from + text.length] }
}

// The fences stand on lines of their own, so a selection that starts or ends
// mid-line is broken off from the text beside it.
function fenceEdit(value, start, end) {
  let body = value.slice(start, end)
  let after = end < value.length && value[end] !== '\n' ? '\n' : ''
  if (body.endsWith('\n')) {
    body = body.slice(0, -1)
    after = '\n'
  }
  const before = start > 0 && value[start - 1] !== '\n' ? '\n' : ''
  const inner = start + before.length + FENCE.length + 1
  return {
    start, end,
    text: `${before}${FENCE}\n${body}\n${FENCE}${after}`,
    select: [inner, inner + body.length],
  }
}

export function markEdit(kind, value, start, end) {
  if (WRAPS[kind]) return wrapEdit(WRAPS[kind], value, start, end)
  if (LINE_MARKS[kind]) return lineEdit(LINE_MARKS[kind], value, start, end)
  if (kind === 'code_block') return fenceEdit(value, start, end)
  throw new Error(`unknown mark: ${kind}`)
}

// With no selection the label is left for the writer, so the caret waits
// between the brackets; with one, the link is done and the caret follows it.
export function linkEdit(value, start, end, url) {
  const label = value.slice(start, end)
  const text = `[${label}](${url})`
  const caret = label ? start + text.length : start + 1
  return { start, end, text, select: [caret, caret] }
}
