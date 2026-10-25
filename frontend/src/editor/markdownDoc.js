// Markdown source ⇄ ProseMirror document, with block-level source retention.
//
// `parse` cuts the source into marked's top-level tokens: each non-space token
// becomes one top-level node, and the node is remembered with the exact bytes
// it came from (`retained`). ProseMirror documents share structure, so a
// transaction rebuilds only the top-level nodes it touched; every other node
// is still the same object, and `serialize` writes its remembered bytes back.
// Whether a block was edited is therefore decided by node identity, never by
// comparing text.
//
// The lexer is marked's default instance — the one `MarkdownContent` renders
// with — so the editor and the saved view read the same token stream.

import { marked } from 'marked'
import { MarkdownSerializer, defaultMarkdownSerializer } from 'prosemirror-markdown'
import { schema as defaultSchema } from './schema.js'
import { escapeLabel, isValidKey, parseRefs } from '../utils/refs.js'

const IMAGE_HREF = /^image:(\d+)$/
const FRONT_MATTER = /^---[ \t]*\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)[ \t]*(?=\r?\n|$)/
const BLANK = /^\s*$/
const TRAILING_BREAKS = /(?:\r?\n)*$/
const TRAILING_SPACE = /\s*$/
const BR_TAG = /^<br\s*\/?>$/i

class Unrepresentable extends Error {}

// ---------------------------------------------------------------- parse --

// Only these names are decoded; any other `&name;` stays literal text, and the
// serializer leaves it alone so the saved view still renders it as before.
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }
const ENTITY = /&(#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z]+);/g

function decodeEntities(text) {
  if (!text.includes('&')) return text
  return text.replace(ENTITY, (whole, name) => {
    if (name[0] !== '#') return ENTITIES[name] ?? whole
    const code = name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10)
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole
  })
}

// Offsets in `text` for each offset in its LF-normalized form. marked folds
// CRLF and CR to LF before lexing, so its raws are slices of the normalized
// text; this maps them back onto the original bytes.
function offsetMap(text) {
  const map = []
  for (let i = 0; i < text.length; i += 1) {
    map.push(i)
    if (text[i] === '\r' && text[i + 1] === '\n') i += 1
  }
  map.push(text.length)
  return (at) => map[at]
}

function lex(norm) {
  try {
    return marked.lexer(norm)
  } catch {
    return []
  }
}

/** `text` as consecutive `{ raw, token }` pieces whose raws join back to `text`; `token` is null where marked emitted none. */
function tile(text) {
  const norm = text.includes('\r') ? text.replace(/\r\n?/g, '\n') : text
  const toOrig = norm === text ? (at) => at : offsetMap(text)
  const pieces = []
  let at = 0
  const take = (end, token) => {
    pieces.push({ raw: text.slice(toOrig(at), toOrig(end)), token })
    at = end
  }
  for (const token of lex(norm)) {
    if (!token.raw) continue
    const found = norm.startsWith(token.raw, at) ? at : norm.indexOf(token.raw, at)
    if (found < 0) break
    if (found > at) take(found, null)
    take(found + token.raw.length, token)
  }
  if (at < norm.length) take(norm.length, null)
  return pieces
}

class Builder {
  constructor(schema) {
    this.schema = schema
    this.nodes = schema.nodes
  }

  opaque(raw) {
    return this.nodes.opaque.create({ raw })
  }

  paragraph(tokens) {
    return this.nodes.paragraph.create(null, this.inline(tokens ?? []))
  }

  top(token, raw) {
    try {
      const node = this.block(token)
      node.check()
      return node
    } catch {
      return this.opaque(raw)
    }
  }

  block(token) {
    const n = this.nodes
    switch (token.type) {
      case 'paragraph':
      case 'text':
        if (!token.tokens) throw new Unrepresentable(token.type)
        return this.paragraph(token.tokens)
      case 'heading':
        return n.heading.create({ level: token.depth }, this.inline(token.tokens))
      case 'hr':
        return n.horizontalRule.create()
      case 'code':
        return n.codeBlock.create({ language: token.lang || null }, token.text ? this.schema.text(token.text) : null)
      case 'blockquote':
        return n.blockquote.create(null, this.children(token.tokens))
      case 'list':
        return this.list(token)
      case 'table':
        return this.table(token)
      default:
        return this.opaque(token.raw)
    }
  }

  children(tokens) {
    const blocks = tokens
      .filter((t) => t.type !== 'space' && t.type !== 'checkbox')
      .map((t) => this.block(t))
    return blocks.length ? blocks : [this.paragraph([])]
  }

  list(token) {
    const n = this.nodes
    const tight = !token.loose
    const tasks = token.items.filter((item) => item.task).length
    if (tasks) {
      if (tasks < token.items.length || token.ordered) throw new Unrepresentable('mixed task list')
      return n.taskList.create({ tight }, token.items.map((item) => (
        n.taskItem.create({ checked: Boolean(item.checked) }, this.children(item.tokens)))))
    }
    const items = token.items.map((item) => n.listItem.create(null, this.children(item.tokens)))
    if (!token.ordered) return n.bulletList.create({ tight }, items)
    const start = token.start === '' || token.start == null ? 1 : Number(token.start)
    return n.orderedList.create({ start, tight }, items)
  }

  table(token) {
    const n = this.nodes
    const cell = (type, c, i) => type.create({ align: token.align[i] ?? null }, this.paragraph(c.tokens))
    const header = n.tableRow.create(null, token.header.map((c, i) => cell(n.tableHeader, c, i)))
    const rows = token.rows.map((row) => n.tableRow.create(null, row.map((c, i) => cell(n.tableCell, c, i))))
    return n.table.create(null, [header, ...rows])
  }

  inline(tokens, marks = []) {
    const out = []
    for (const token of tokens) out.push(...this.inlineToken(token, marks))
    return out
  }

  text(text, marks) {
    return text ? [this.schema.text(text, marks)] : []
  }

  mark(name, attrs, marks) {
    return this.schema.marks[name].create(attrs).addToSet(marks)
  }

  inlineToken(token, marks) {
    switch (token.type) {
      case 'text':
        return token.tokens ? this.inline(token.tokens, marks) : this.text(decodeEntities(token.text), marks)
      case 'escape':
        return this.text(token.text, marks)
      case 'codespan':
        return this.text(token.text, this.mark('code', null, marks))
      case 'strong':
        return this.inline(token.tokens, this.mark('bold', null, marks))
      case 'em':
        return this.inline(token.tokens, this.mark('italic', null, marks))
      case 'del':
        return this.inline(token.tokens, this.mark('strike', null, marks))
      case 'br':
        return [this.nodes.hardBreak.create(null, null, marks)]
      case 'link':
        return this.link(token, marks)
      case 'image':
        return [this.image(token, marks)]
      case 'checkbox':
        return []
      case 'html':
        // `<br>` is how a GFM table cell holds a line break, and how the
        // serializer writes one back into a cell.
        if (BR_TAG.test(token.raw)) return [this.nodes.hardBreak.create(null, null, marks)]
        throw new Unrepresentable(token.type)
      default:
        // Inline HTML among them: a text node cannot say whether `<b>` was a
        // tag or typed characters, so the whole block stays opaque instead.
        throw new Unrepresentable(token.type)
    }
  }

  link(token, marks) {
    const refs = parseRefs(token.raw)
    if (refs.length === 1 && refs[0].start === 0 && refs[0].end === token.raw.length) {
      const { kind, key, label } = refs[0]
      return [this.nodes.ref.create({ kind, key, label }, null, marks)]
    }
    return this.inline(token.tokens, this.mark('link', { href: token.href, title: token.title ?? null }, marks))
  }

  image(token, marks) {
    const local = IMAGE_HREF.exec(token.href ?? '')
    const attrs = local
      ? { idx: Number(local[1]), alt: token.text ?? '', src: null, title: null }
      : { idx: null, alt: token.text ?? '', src: token.href, title: token.title ?? null }
    return this.nodes.image.create(attrs, null, marks)
  }
}

function addBlock(state, node, raw) {
  state.segments.push({ kind: 'block', raw, node })
  state.retained.set(node, raw)
  state.blocks.push(node)
}

function addPiece(state, build, { raw, token }) {
  if (token?.type === 'space' || (!token && BLANK.test(raw))) state.segments.push({ kind: 'gap', raw })
  else addBlock(state, token ? build.top(token, raw) : build.opaque(raw), raw)
}

/**
 * `src` as `{ doc, segments, retained }`.
 *
 * `segments` tiles the source — `segments.map(s => s.raw).join('') === src` —
 * as one `block` per top-level node and a `gap` for the blank lines between.
 * `retained` maps each top-level node to its raw. Never throws: a block with
 * no node of its own becomes `opaque`.
 */
export function parse(src, { schema = defaultSchema } = {}) {
  const text = src ?? ''
  const build = new Builder(schema)
  const state = { segments: [], retained: new WeakMap(), blocks: [] }
  const front = FRONT_MATTER.exec(text)
  if (front) addBlock(state, build.opaque(front[0]), front[0])
  for (const piece of tile(text.slice(front ? front[0].length : 0))) addPiece(state, build, piece)
  // A document needs a block; an empty or all-blank source gets an empty
  // paragraph that stands for no bytes, so its gaps still come back.
  if (!state.blocks.length) addBlock(state, build.paragraph([]), '')
  const doc = schema.topNodeType.create(null, state.blocks)
  return { doc, segments: state.segments, retained: state.retained }
}

// ------------------------------------------------------------ serialize --

// A literal `<` or `&` typed in the editor must not come back as a tag or an
// entity. Only an `&` that `decodeEntities` would read is escaped: any other
// `&name;` came through parse undecoded and goes back as it was.
function guardHtml(text) {
  return text
    .replace(ENTITY, (whole) => (decodeEntities(whole) === whole ? whole : `&amp;${whole.slice(1)}`))
    .replace(/<(?=[A-Za-z/!?])/g, '&lt;')
}

const WIDE = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/u

function displayWidth(text) {
  let width = 0
  for (const ch of text) width += WIDE.test(ch) || ch.codePointAt(0) >= 0x20000 ? 2 : 1
  return width
}

function pad(text, width, align) {
  const room = width - displayWidth(text)
  if (align === 'right') return ' '.repeat(room) + text
  if (align === 'center') return ' '.repeat(Math.floor(room / 2)) + text + ' '.repeat(Math.ceil(room / 2))
  return text + ' '.repeat(room)
}

function delimiterCell(width, align) {
  if (align === 'center') return `:${'-'.repeat(width - 2)}:`
  if (align === 'right') return `${'-'.repeat(width - 1)}:`
  if (align === 'left') return `:${'-'.repeat(width - 1)}`
  return '-'.repeat(width)
}

function cellText(cell) {
  return markdown(cell)
    .replace(/\\\n|\n+/g, '<br>')
    .replace(/\|/g, '\\|')
}

function tableLines(node) {
  const rows = []
  node.forEach((row) => {
    const cells = []
    row.forEach((cell) => cells.push({ text: cellText(cell), align: cell.attrs.align }))
    rows.push(cells)
  })
  const columns = rows[0].length
  const aligns = rows[0].map((c) => c.align)
  const widths = aligns.map((_, i) => Math.max(3, ...rows.map((r) => displayWidth(r[i]?.text ?? ''))))
  const line = (cells) => `| ${widths.map((w, i) => pad(cells[i]?.text ?? '', w, aligns[i])).join(' | ')} |`
  const rule = `| ${widths.map((w, i) => delimiterCell(w, aligns[i])).join(' | ')} |`
  return columns ? [line(rows[0]), rule, ...rows.slice(1).map(line)] : []
}

function codeFence(text) {
  const longest = Math.max(2, ...(text.match(/`{3,}/g) ?? []).map((run) => run.length))
  return '`'.repeat(longest + 1)
}

const BULLETS = ['-', '*', '+']
const DELIMITERS = ['.', ')']

function listKind(node) {
  const name = node?.type.name
  if (name === 'bulletList' || name === 'taskList') return 'bullet'
  return name === 'orderedList' ? 'ordered' : null
}

function rawMarker(raw) {
  const match = /^\s*(?:([-*+])|\d+([.)]))/.exec(raw)
  return match ? (match[1] ?? match[2]) : null
}

// Two lists of one kind with only blank lines between them parse as ONE list
// unless their markers differ, so a list is written with the first marker its
// same-kind neighbours do not use. A neighbour outside the serialized run is
// described by `state.options.context`.
function markerFor(state, parent, index) {
  const kind = listKind(parent.child(index))
  const context = state.options.context ?? {}
  const outer = parent.type === parent.type.schema.topNodeType
  const avoid = []
  if (index > 0 && listKind(parent.child(index - 1)) === kind) avoid.push(markerFor(state, parent, index - 1))
  if (index === 0 && outer && context.before?.kind === kind) avoid.push(context.before.marker)
  if (index === parent.childCount - 1 && outer && context.after?.kind === kind) avoid.push(context.after.marker)
  const choices = kind === 'ordered' ? DELIMITERS : BULLETS
  return choices.find((c) => !avoid.includes(c)) ?? choices[0]
}

function renderBullets(state, node, parent, index) {
  const bullet = `${markerFor(state, parent, index)} `
  state.renderList(node, '  ', () => bullet)
}

const defaults = defaultMarkdownSerializer.nodes

const serializer = new MarkdownSerializer({
  blockquote: defaults.blockquote,
  heading: defaults.heading,
  paragraph: defaults.paragraph,
  hardBreak: defaults.hard_break,
  listItem: defaults.list_item,
  codeBlock(state, node) {
    const text = node.textContent
    const fence = codeFence(text)
    state.write(`${fence}${node.attrs.language || ''}\n`)
    if (text) {
      state.text(text, false)
      state.write('\n')
    }
    state.write(fence)
    state.closeBlock(node)
  },
  horizontalRule(state, node) {
    state.write('---')
    state.closeBlock(node)
  },
  bulletList: renderBullets,
  taskList: renderBullets,
  taskItem(state, node) {
    state.write(node.attrs.checked ? '[x] ' : '[ ] ')
    state.renderContent(node)
  },
  orderedList(state, node, parent, index) {
    const start = node.attrs.start ?? 1
    const delimiter = markerFor(state, parent, index)
    const width = String(start + node.childCount - 1).length
    state.renderList(node, ' '.repeat(width + 2), (i) => {
      const number = String(start + i)
      return `${' '.repeat(width - number.length)}${number}${delimiter} `
    })
  },
  table(state, node) {
    state.text(tableLines(node).join('\n'), false)
    state.closeBlock(node)
  },
  opaque(state, node) {
    state.text(node.attrs.raw.replace(TRAILING_BREAKS, ''), false)
    state.closeBlock(node)
  },
  text(state, node) {
    state.text(state.inAutolink ? node.text : guardHtml(node.text), !state.inAutolink)
  },
  image(state, node) {
    const { alt, idx, src, title } = node.attrs
    const target = idx != null ? `image:${idx}` : `${(src ?? '').replace(/[()]/g, '\\$&')}${title ? ` "${title.replace(/"/g, '\\"')}"` : ''}`
    state.write(`![${state.esc(alt || '')}](${target})`)
  },
  ref(state, node) {
    const { kind, key, label } = node.attrs
    if (isValidKey(kind, key)) state.write(`[${escapeLabel(label ?? '')}](${kind}:${key})`)
    else state.text(label ?? '')
  },
}, {
  // `*`, not `_`: an underscore cannot open emphasis between two CJK
  // characters, so `这是_强调_` would come back as literal underscores.
  italic: { open: '*', close: '*', mixable: true, expelEnclosingWhitespace: true },
  bold: { open: '**', close: '**', mixable: true, expelEnclosingWhitespace: true },
  strike: { open: '~~', close: '~~', mixable: true, expelEnclosingWhitespace: true },
  link: defaultMarkdownSerializer.marks.link,
  code: defaultMarkdownSerializer.marks.code,
})

const OPTIONS = { tightLists: true, hardBreakNodeName: 'hardBreak' }

function markdown(parent, context = null) {
  return serializer.serialize(parent, { ...OPTIONS, context })
}

function nextBlock(segments, from) {
  for (let i = from + 1; i < segments.length; i += 1) if (segments[i].kind === 'block') return i
  return -1
}

function between(segments, from, to) {
  return segments.slice(from + 1, to).map((s) => s.raw).join('')
}

function blankLine(out, eol) {
  if (!out) return ''
  const breaks = (TRAILING_BREAKS.exec(out)[0].match(/\n/g) ?? []).length
  return eol.repeat(Math.max(0, 2 - breaks))
}

// Top-level output units in document order: a retained node is its raw (with
// `at`, its block's index in `segments`), an opaque node its `raw` attr, and
// a run of edited nodes one `run` that goes through the serializer together,
// so the adjacency rules between them are the serializer's own.
function collect(doc, segments, retained) {
  const position = new Map()
  segments.forEach((s, i) => { if (s.kind === 'block') position.set(s.node, i) })
  const out = []
  doc.forEach((child) => {
    if (retained.has(child)) out.push({ node: child, text: retained.get(child), at: position.get(child) ?? null })
    else if (child.type.name === 'opaque') out.push({ node: child, text: child.attrs.raw, at: null })
    else if (out.at(-1)?.run) out.at(-1).run.push(child)
    else out.push({ run: [child], at: null })
  })
  return out
}

// An opaque block can still be a list on the next parse (a list holding inline
// HTML), so its kind is read off its raw.
function rawKind(raw) {
  if (/^\s*[-*+](?:\s|$)/.test(raw)) return 'bullet'
  return /^\s*\d+[.)](?:\s|$)/.test(raw) ? 'ordered' : null
}

function edge(unit) {
  if (!unit?.node) return null
  const kind = unit.node.type.name === 'opaque' ? rawKind(unit.text) : listKind(unit.node)
  return { kind, marker: rawMarker(unit.text) }
}

function render(list, doc, eol) {
  list.forEach((unit, k) => {
    if (!unit.run) return
    const text = markdown(doc.type.create(null, unit.run), { before: edge(list[k - 1]), after: edge(list[k + 1]) })
    unit.text = eol === '\n' ? text : text.replace(/\n/g, eol)
  })
  return list
}

function tail(unit, segments, source, out) {
  if (unit.at !== null && nextBlock(segments, unit.at) < 0) return between(segments, unit.at, segments.length)
  const want = TRAILING_SPACE.exec(source)[0]
  const have = TRAILING_SPACE.exec(out)[0]
  return want.startsWith(have) ? want.slice(have.length) : ''
}

/**
 * `doc` as markdown source: every top-level node `retained` still maps is its
 * original raw, and the blank lines between two nodes that were adjacent at
 * parse time are the original gap. Edited and new nodes go through the
 * serializer, set off from their neighbours by one blank line, in the
 * source's line ending; the source's leading and trailing whitespace stays.
 */
export function serialize(doc, segments, retained) {
  const source = segments.map((s) => s.raw).join('')
  const eol = source.includes('\r\n') ? '\r\n' : '\n'
  const list = render(collect(doc, segments, retained), doc, eol)
  const lead = between(segments, -1, nextBlock(segments, -1))
  let out = lead
  list.forEach((unit, k) => {
    const prev = list[k - 1]
    if (prev && prev.at !== null && unit.at !== null && unit.at === nextBlock(segments, prev.at)) out += between(segments, prev.at, unit.at)
    else if (prev) out += blankLine(out, eol)
    out += unit.text
  })
  // The leading gap is already written; only what follows it can be a tail.
  return list.length ? out + tail(list.at(-1), segments, source.slice(lead.length), out) : out
}
