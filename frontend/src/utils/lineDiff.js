// Line-level diff via longest-common-subsequence — no npm diff dep (the
// bundle only ships `marked`). Returns a flat list of rows the UI renders
// as a unified diff:
//   { type: 'context' | 'add' | 'remove', text, beforeLine, afterLine }
// beforeLine/afterLine are 1-based gutter numbers (null on the side a row
// doesn't exist). Trailing newlines are normalized so an identical body
// never reports a phantom trailing change.

function splitLines(text) {
  const normalized = String(text ?? '').replace(/\r\n/g, '\n').replace(/\n+$/, '')
  if (normalized === '') return []
  return normalized.split('\n')
}

// Classic LCS length table over two line arrays. O(n*m) — fine for wiki
// pages (hundreds of lines), and the panel diffs one page at a time.
function lcsTable(a, b) {
  const n = a.length
  const m = b.length
  const table = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  return table
}

export function lineDiff(before, after) {
  const a = splitLines(before)
  const b = splitLines(after)
  const table = lcsTable(a, b)
  const rows = []
  let i = 0
  let j = 0
  let beforeLine = 1
  let afterLine = 1
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      rows.push({ type: 'context', text: a[i], beforeLine: beforeLine++, afterLine: afterLine++ })
      i++; j++
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      rows.push({ type: 'remove', text: a[i], beforeLine: beforeLine++, afterLine: null })
      i++
    } else {
      rows.push({ type: 'add', text: b[j], beforeLine: null, afterLine: afterLine++ })
      j++
    }
  }
  while (i < a.length) {
    rows.push({ type: 'remove', text: a[i++], beforeLine: beforeLine++, afterLine: null })
  }
  while (j < b.length) {
    rows.push({ type: 'add', text: b[j++], beforeLine: null, afterLine: afterLine++ })
  }
  return rows
}

// Summary counts; identical bodies → { added: 0, removed: 0 }.
export function diffStats(rows) {
  let added = 0
  let removed = 0
  for (const row of rows) {
    if (row.type === 'add') added++
    else if (row.type === 'remove') removed++
  }
  return { added, removed }
}
