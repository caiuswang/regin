// One rule for every excerpt surface.
//
// Markdown marks carry no meaning once text is clipped: a truncated response
// reads "## Where the limi…", and a search for `rateLimit` misses the
// backticked occurrence it was looking for. Raw markdown is handed ONLY to the
// block renderer (the feed card's MarkdownContent and the presentation
// reader); everything that truncates, previews, or matches runs through here.
export function plainText(s) {
  if (!s) return ''
  return String(s)
    .replace(/```[\s\S]*?```/g, '[code]')
    .replace(/^#{1,4}\s*/gm, '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .replace(/\n{2,}/g, '\n')
    .trim()
}

// Marks-only normalization, for MATCHING rather than for display.
//
// Unlike plainText() this keeps the CONTENT of fenced blocks: an identifier
// inside a code block is the single most likely thing anyone searches a trace
// for, and collapsing the fence to `[code]` made every one of them unfindable.
// It drops only the characters that would otherwise make a search for
// `rateLimit` miss its backticked occurrence.
export function searchText(s) {
  if (!s) return ''
  return String(s)
    .replace(/```/g, '')
    .replace(/`/g, '')
    .replace(/\*\*/g, '')
    .replace(/^#{1,4}\s*/gm, '')
}

// Word count + reading estimate for the reader's vitals line. 210wpm is the
// low end of adult prose reading, which is the right end for technical text.
export function readingMeta(text) {
  const words = plainText(text).split(/\s+/).filter(Boolean).length
  if (!words) return ''
  return `${words} words · ~${Math.max(1, Math.ceil(words / 210))} min`
}
