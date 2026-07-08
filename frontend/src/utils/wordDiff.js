// Word-level diff over two markdown bodies, on top of jsdiff's Myers
// implementation (diffWordsWithSpace preserves whitespace, so segments
// concatenate back to the original text). Two consumers:
//   - the flowing Inline view: one flat segment list for the whole body;
//   - the Unified view: per-line word segments, so a changed line
//     highlights only the words that moved, not the whole row.
// A segment is { type: 'context' | 'add' | 'remove', text }.
import { diffWordsWithSpace } from 'diff'

function toSegments(parts) {
  return parts.map((p) => ({
    type: p.added ? 'add' : p.removed ? 'remove' : 'context',
    text: p.value,
  }))
}

export function wordSegments(before, after) {
  return toSegments(diffWordsWithSpace(String(before ?? ''), String(after ?? '')))
}

// Non-whitespace token counts, rendered as Common / New / Removed.
export function wordStats(segments) {
  const tokens = (text) => (text.match(/\S+/g) || []).length
  let common = 0
  let added = 0
  let removed = 0
  for (const seg of segments) {
    if (seg.type === 'add') added += tokens(seg.text)
    else if (seg.type === 'remove') removed += tokens(seg.text)
    else common += tokens(seg.text)
  }
  return { common, added, removed }
}

// Word-diff one before-line against one after-line, keeping only the
// segments that belong on `side` ('remove' → before line, 'add' → after
// line). Context words appear on both sides; the moved words highlight.
function lineWordSegments(beforeText, afterText, side) {
  const parts = diffWordsWithSpace(String(beforeText ?? ''), String(afterText ?? ''))
  const visible = side === 'remove' ? (p) => !p.added : (p) => !p.removed
  return toSegments(parts.filter(visible))
}

// Turn the flat lineDiff rows into render rows carrying `.segments`.
// A maximal run of remove-rows immediately followed by add-rows is a
// block replacement: pair them index-wise and word-diff each pair so the
// changed words highlight. Everything else (context, unpaired add/remove)
// falls back to a single whole-line segment.
export function annotateRowSegments(rows) {
  const out = rows.map((row) => ({ segments: [{ type: row.type, text: row.text }] }))
  let i = 0
  while (i < rows.length) {
    if (rows[i].type !== 'remove') {
      i += 1
      continue
    }
    const removeStart = i
    while (i < rows.length && rows[i].type === 'remove') i += 1
    const addStart = i
    while (i < rows.length && rows[i].type === 'add') i += 1
    const pairs = Math.min(addStart - removeStart, i - addStart)
    for (let k = 0; k < pairs; k += 1) {
      const before = rows[removeStart + k].text
      const after = rows[addStart + k].text
      out[removeStart + k].segments = lineWordSegments(before, after, 'remove')
      out[addStart + k].segments = lineWordSegments(before, after, 'add')
    }
  }
  return out
}
