// The reference grammar: `[label](kind:key)` outside code.
//
// `lib/refs/grammar.py` is the same grammar, statement for statement, and
// `tests/fixtures/refs_grammar_cases.json` holds both to one answer per case.
// A change here that is not made there is how a chip and a backlink come to
// disagree about the same text.
//
// No imports, on purpose: the grammar spec imports this file straight into
// Node, outside Vite.

export const KINDS = ['feature', 'task', 'defect', 'session', 'file', 'memory']

// `file:` has no alias: `f:` is taken by feature.
export const KIND_ALIASES = {
  f: 'feature', t: 'task', d: 'defect', s: 'session', m: 'memory', lesson: 'memory',
}

export const TITLE_MAX = 60
const ELLIPSIS = '…'

const ID_KEY = /^[1-9][0-9]{0,9}$/
const KEY_PATTERNS = {
  feature: ID_KEY,
  task: ID_KEY,
  defect: ID_KEY,
  session: /^[0-9A-Za-z][0-9A-Za-z_-]{7,127}$/,
  memory: /^[0-9a-f]{32}$/,
}
const FILE_SEGMENT = '(?:[A-Za-z0-9\\-._~]|%[0-9A-F]{2})+'
const FILE_KEY = new RegExp(`^${FILE_SEGMENT}(?:/${FILE_SEGMENT})*$`)

const SHORT_PREFIX = { feature: 'F', task: 'T', defect: 'D', session: 'S', memory: 'M' }

// The key class is the union of every kind's key alphabet; which kind accepts
// which key is decided after the match, so a near-miss is skipped rather than
// swallowing the text after it.
const LINK = /\[((?:\\[\s\S]|[^\\[\]])*)\]\(([a-z]+):([A-Za-z0-9\-._~%/]+)\)/g
const ASCII_PUNCT = new Set('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')
const LABEL_ESCAPES = /([\\[\]<>&`])/g
const NEWLINES = /\r\n|\r|\n/g
const EDGE_BLANKS = /^[ \t]+|[ \t]+$/g
const TRAILING_BLANKS = /[ \t]+$/

// `[^\r\n]`, never `.`: JS's `.` also refuses U+2028/U+2029, and the two
// grammars must not disagree about where a fence opens.
const OPEN_FENCE = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/
const CLOSE_FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/
const BLANK = /^[ \t]*$/
const QUOTE_MARKER = /^ {0,3}>[ \t]?/

const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key)

/** A kind or one of its aliases, as the kind it names; null for neither. */
export function canonicalKind(name) {
  if (KINDS.includes(name)) return name
  return has(KIND_ALIASES, name ?? '') ? KIND_ALIASES[name] : null
}

function encodeSegment(segment) {
  return encodeURIComponent(segment).replace(
    /[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)
}

/**
 * A repo-relative path as a file key: each segment percent-encoded with only
 * RFC 3986's unreserved characters left bare. null for a path that is empty,
 * absolute, or holds an empty, `.` or `..` segment.
 */
export function encodeFileKey(path) {
  const segments = (path ?? '').split('/')
  if (segments.some((seg) => seg === '' || seg === '.' || seg === '..' || seg.includes('\0'))) {
    return null
  }
  try {
    return segments.map(encodeSegment).join('/')
  } catch {
    return null
  }
}

/**
 * The path a file key names, or null when the key is not canonical.
 *
 * Canonical is what makes the check whole: `%2E%2E` decodes to `..` and `%2F`
 * hides a separator inside a segment, and neither survives re-encoding to
 * itself.
 */
export function decodeFileKey(key) {
  if (!FILE_KEY.test(key ?? '')) return null
  const segments = []
  for (const seg of key.split('/')) {
    try {
      segments.push(decodeURIComponent(seg))
    } catch {
      return null
    }
  }
  const path = segments.join('/')
  return encodeFileKey(path) === key ? path : null
}

export function isValidKey(kind, key) {
  if (kind === 'file') return decodeFileKey(key) !== null
  const pattern = has(KEY_PATTERNS, kind) ? KEY_PATTERNS[kind] : null
  return Boolean(pattern && pattern.test(key ?? ''))
}

/** `feature:128` → `{ kind: 'feature', key: '128' }`; null when it is not a reference. */
export function parseRefTarget(ref) {
  const text = ref ?? ''
  const at = text.indexOf(':')
  if (at < 0) return null
  const kind = text.slice(0, at)
  const key = text.slice(at + 1)
  if (!KINDS.includes(kind) || !isValidKey(kind, key)) return null
  return { kind, key }
}

function flatten(text) {
  return (text ?? '').replace(NEWLINES, ' ').replace(EDGE_BLANKS, '')
}

// Cut by code point, as Python's `str` slices, so the two grammars write the
// same label for a title holding astral characters.
function cut(title) {
  const chars = Array.from(flatten(title))
  if (chars.length <= TITLE_MAX) return chars.join('')
  return chars.slice(0, TITLE_MAX).join('').replace(TRAILING_BLANKS, '') + ELLIPSIS
}

/** Backslash-escape what would make a label live markup in `marked`. */
export function escapeLabel(text) {
  return text.replace(LABEL_ESCAPES, '\\$1')
}

/**
 * What accepting a menu row writes between `[` and `]`, escaped.
 *
 * A path is never cut: a file reference's label is the only place its
 * readable form survives, and half a path names nothing.
 */
export function formatLabel(kind, key, title = '') {
  if (kind === 'file') return escapeLabel(flatten(decodeFileKey(key) ?? key))
  const short = kind === 'session' || kind === 'memory' ? key.slice(0, 8) : key
  const head = `${SHORT_PREFIX[kind]}${short}`
  const text = cut(title)
  return escapeLabel(text ? `${head} ${text}` : head)
}

export function formatRef(kind, key, title = '') {
  if (!KINDS.includes(kind) || !isValidKey(kind, key)) {
    throw new Error(`not a reference: ${kind}:${key}`)
  }
  return `[${formatLabel(kind, key, title)}](${kind}:${key})`
}

/** A stored label as `marked` renders it: `\` before ASCII punctuation drops, any other backslash stays. */
export function unescapeLabel(label) {
  let out = ''
  let i = 0
  while (i < label.length) {
    if (label[i] === '\\' && i + 1 < label.length && ASCII_PUNCT.has(label[i + 1])) {
      out += label[i + 1]
      i += 2
    } else {
      out += label[i]
      i += 1
    }
  }
  return out
}

// `{ start, end, line }` per line, `end` stopping before the line ending —
// CRLF, CR or LF, as CommonMark reads them.
function lines(text) {
  const out = []
  let start = 0
  for (const ending of text.matchAll(NEWLINES)) {
    out.push({ start, end: ending.index, line: text.slice(start, ending.index) })
    start = ending.index + ending[0].length
  }
  out.push({ start, end: text.length, line: text.slice(start) })
  return out
}

function closes(line, fence) {
  const match = CLOSE_FENCE.exec(line)
  return Boolean(match && match[1][0] === fence.char && match[1].length >= fence.length)
}

function opens(line) {
  const match = OPEN_FENCE.exec(line)
  if (!match) return null
  const run = match[1]
  if (run[0] === '`' && match[2].includes('`')) return null
  return { char: run[0], length: run.length }
}

// Whether the character at `at` follows an odd run of backslashes.
function escaped(text, at, floor) {
  let count = 0
  let j = at - 1
  while (j >= floor && text[j] === '\\') {
    count += 1
    j -= 1
  }
  return count % 2 === 1
}

function runEnd(text, i, stop) {
  while (i < stop && text[i] === '`') i += 1
  return i
}

// The end of the next backtick run of exactly `length`, or null. Backslashes
// are literal inside a code span, so no run here is escaped.
function closer(text, i, stop, length) {
  while (i < stop) {
    if (text[i] !== '`') {
      i += 1
      continue
    }
    const run = i
    i = runEnd(text, i, stop)
    if (i - run === length) return i
  }
  return null
}

// Inline code spans in one paragraph: a backtick run closes at the next run of
// exactly its length. A run that never closes is literal text.
function spanRanges(text, start, stop) {
  const ranges = []
  let i = start
  while (i < stop) {
    if (text[i] !== '`') {
      i += 1
      continue
    }
    let opener = i
    i = runEnd(text, i, stop)
    if (escaped(text, opener, start)) opener += 1
    if (opener === i) continue
    const close = closer(text, i, stop, i - opener)
    if (close !== null) {
      ranges.push([opener, close])
      i = close
    }
  }
  return ranges
}

// `line` with up to `limit` block-quote markers (`>`) taken off the front, and
// how many were.
function unquote(line, limit) {
  let depth = 0
  let rest = line
  while (depth < limit) {
    const marker = QUOTE_MARKER.exec(rest)
    if (!marker) break
    rest = rest.slice(marker[0].length)
    depth += 1
  }
  return { depth, rest }
}

// One pass over the lines: fenced blocks become ranges whole, and the
// paragraphs between them are scanned for inline spans.
class Blocks {
  constructor(text) {
    this.text = text
    this.ranges = []
    this.fence = null
    this.fenceDepth = 0
    this.fenceStart = 0
    this.fenceEnd = 0
    this.para = null
    this.paraEnd = 0
  }

  flush() {
    if (this.para !== null) {
      this.ranges.push(...spanRanges(this.text, this.para, this.paraEnd))
      this.para = null
    }
  }

  closeFence(end) {
    this.ranges.push([this.fenceStart, end])
    this.fence = null
  }

  // Whether `line` belongs to the open fence. A line quoted less deeply than
  // the opener ends the quote, and the fence with it.
  insideFence(end, line) {
    const { depth, rest } = unquote(line, this.fenceDepth)
    if (depth < this.fenceDepth) {
      this.closeFence(this.fenceEnd)
      return false
    }
    if (closes(rest, this.fence)) this.closeFence(end)
    else this.fenceEnd = end
    return true
  }

  feed(start, end, line) {
    if (this.fence !== null && this.insideFence(end, line)) return
    const { depth, rest } = unquote(line, line.length)
    const opened = opens(rest)
    if (opened !== null || BLANK.test(rest)) {
      this.flush()
      if (opened !== null) {
        this.fence = opened
        this.fenceDepth = depth
        this.fenceStart = start
        this.fenceEnd = end
      }
      return
    }
    if (this.para === null) this.para = start
    this.paraEnd = end
  }

  finish() {
    if (this.fence !== null) this.closeFence(this.text.length)
    else this.flush()
    return this.ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  }
}

/**
 * Half-open `[start, end]` ranges of fenced blocks and inline code spans.
 *
 * A fence closes only on its own character at least as long, one inside a
 * block quote ends with the quote, and one left open runs to the end of the
 * text, as in CommonMark. An inline span never crosses a blank line or a
 * fence. Indented code blocks and list-item indentation are deliberately not
 * modelled.
 */
export function codeRanges(text) {
  const blocks = new Blocks(text)
  for (const { start, end, line } of lines(text)) blocks.feed(start, end, line)
  return blocks.finish()
}

function overlaps(ranges, start, end) {
  return ranges.some(([lo, hi]) => lo < end && start < hi)
}

function isReference(text, match, code) {
  const start = match.index
  const end = start + match[0].length
  if (start > 0 && text[start - 1] === '!') return false
  if (escaped(text, start, 0) || overlaps(code, start, end)) return false
  const kind = match[2]
  return KINDS.includes(kind) && isValidKey(kind, match[3])
}

/**
 * Every reference in `text`, in order, duplicates kept, as
 * `{ ref, kind, key, label, start, end }` — `label` unescaped, offsets in
 * UTF-16 units of `text`.
 *
 * Not a reference: an image (`!` before the `[`), an escaped `\[`, a kind
 * outside KINDS, a key its kind does not accept, a link with a title part, an
 * autolink, and anything touching fenced or inline code.
 */
export function parseRefs(text) {
  if (!text) return []
  const code = codeRanges(text)
  const found = []
  for (const match of text.matchAll(LINK)) {
    if (!isReference(text, match, code)) continue
    found.push({
      ref: `${match[2]}:${match[3]}`,
      kind: match[2],
      key: match[3],
      label: unescapeLabel(match[1]),
      start: match.index,
      end: match.index + match[0].length,
    })
  }
  return found
}

/** The reference whose closing `)` sits directly before `caret`, or null (E3's atomic Backspace). */
export function refEndingAt(text, caret) {
  return parseRefs(text).find((r) => r.end === caret) ?? null
}
