// References in a rendered markdown body, drawn as chips (R6).
//
// Whether a link is a reference is the grammar's answer (`parseRefs`), never
// `marked`'s. Each reference is swapped for an empty placeholder link before
// the markdown is parsed, so code is judged by the grammar's own rules and a
// label never reaches `marked` as markup; after the render each placeholder
// becomes the mount point of one chip.
//
// Where a chip can go is `marked`'s answer, though: only where it reads the
// reference's own source as a link of its own. Anywhere else — raw HTML, an
// image's alt text, a link's destination or text, a bare URL it autolinked —
// the reference keeps its source, and renders as `marked` renders that.

import { marked } from 'marked'
import { KINDS } from './refs.js'

const REF_SCHEME = new RegExp(`^(?:${KINDS.join('|')}):`, 'i')

// The tag is per body instance, so text that merely looks like a placeholder
// (a body documenting this very file) is never mistaken for one.
const slotHref = (tag, at) => `#regin-ref-${tag}-${at}`

/**
 * `text` with the references in `found` replaced by their placeholder links —
 * every one, or only those whose index is in `kept`.
 *
 * The link text is one space, not empty: after a defined label, `[foo][]`
 * would read as a collapsed reference to `foo` and swallow the placeholder,
 * while a blank label can never be a reference.
 */
export function withSlots(text, found, tag, kept = null) {
  let out = ''
  let at = 0
  found.forEach((ref, i) => {
    if (kept && !kept.has(i)) return
    out += `${text.slice(at, ref.start)}[ ](${slotHref(tag, i)})`
    at = ref.end
  })
  return out + text.slice(at)
}

// Every token under `tokens`, told whether a link or an image encloses it —
// the containers `marked.walkTokens` descends, walked here because that one
// concatenates its result array once per token, which is quadratic in the
// length of a body.
function eachToken(tokens, visit, enclosed = false) {
  for (const token of tokens) {
    visit(token, enclosed)
    const inner = enclosed || token.type === 'link' || token.type === 'image'
    if (token.type === 'table') {
      for (const cell of token.header) eachToken(cell.tokens, visit, inner)
      for (const row of token.rows) for (const cell of row) eachToken(cell.tokens, visit, inner)
    } else if (token.type === 'list') {
      eachToken(token.items, visit, inner)
    } else if (token.tokens) {
      eachToken(token.tokens, visit, inner)
    }
  }
}

// The placeholders `marked` reads as links of their own: not inside an image
// or another link, whose text it flattens or nests.
function linkedSlots(markdown, tag) {
  const slot = new RegExp(`^#regin-ref-${tag}-(\\d+)$`)
  const linked = new Set()
  eachToken(marked.lexer(markdown), (token, enclosed) => {
    const match = token.type === 'link' && !enclosed ? slot.exec(token.href ?? '') : null
    if (match) linked.add(Number(match[1]))
  })
  return linked
}

// `text` with only each reference's href swapped for its slot's. The labels
// stay, so `marked` reads this exactly as it reads the body itself — where a
// placeholder's one-space label would end an unquoted attribute or a link
// destination early, and be read as a link the body never had.
function withSlotHrefs(text, found, tag) {
  let out = ''
  let at = 0
  found.forEach((ref, i) => {
    const source = text.slice(ref.start, ref.end)
    const label = source.slice(0, source.length - ref.ref.length - 1)
    out += `${text.slice(at, ref.start)}${label}${slotHref(tag, i)})`
    at = ref.end
  })
  return out + text.slice(at)
}

/**
 * The markdown `marked` renders for `text`: a placeholder for each reference a
 * chip can stand in for, and every other reference as its source.
 *
 * Read once more with the placeholders in, and again after any round that put
 * one back, until each placeholder left is a link of its own. The kept set
 * only shrinks, so the rounds end.
 */
export function slotted(text, found, tag) {
  if (!found.length) return text
  let kept = linkedSlots(withSlotHrefs(text, found, tag), tag)
  for (;;) {
    const markdown = withSlots(text, found, tag, kept)
    const linked = linkedSlots(markdown, tag)
    if (linked.size === kept.size) return markdown
    kept = linked
  }
}

// A placeholder `marked` did not make a link after all, where `slotted` read
// one, sits where the body's own text would have shown, so it gets that text
// back.
function restoreSource(container, found, text, tag) {
  const literal = new RegExp(`\\[ \\]\\(#regin-ref-${tag}-(\\d+)\\)`, 'g')
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const hits = []
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeValue.includes(`#regin-ref-${tag}-`)) hits.push(node)
  }
  for (const node of hits) {
    node.nodeValue = node.nodeValue.replace(literal, (whole, i) => {
      const ref = found[Number(i)]
      return ref ? text.slice(ref.start, ref.end) : whole
    })
  }
}

// C6: a link in a reference's scheme that the grammar refused is its label as
// plain text, never a dead `<a href="feature:x">`; an image in one is its alt
// text, never a broken image.
function unlinkRefSchemes(container) {
  for (const link of container.querySelectorAll('a[href]')) {
    if (REF_SCHEME.test(link.getAttribute('href'))) {
      link.replaceWith(document.createTextNode(link.textContent))
    }
  }
  for (const image of container.querySelectorAll('img[src]')) {
    if (REF_SCHEME.test(image.getAttribute('src'))) {
      image.replaceWith(document.createTextNode(image.getAttribute('alt') ?? ''))
    }
  }
}

/**
 * Turn the rendered placeholders in `container` into empty chip hosts.
 * Returns `[{ host, at }]`, `at` indexing `found`.
 */
export function mountPoints(container, found, text, tag) {
  const slot = new RegExp(`^#regin-ref-${tag}-(\\d+)$`)
  const points = []
  for (const link of container.querySelectorAll('a[href^="#regin-ref-"]')) {
    const match = slot.exec(link.getAttribute('href'))
    const at = match ? Number(match[1]) : -1
    if (!found[at]) continue
    const host = document.createElement('span')
    host.className = 'ref-chip-host'
    link.replaceWith(host)
    points.push({ host, at })
  }
  unlinkRefSchemes(container)
  restoreSource(container, found, text, tag)
  return points
}

/**
 * C2: a plain left click on a chip moves the app in place; a modified or
 * middle click is the browser's (a new tab, a download).
 */
export function followChip(event, push) {
  if (event.defaultPrevented || event.button !== 0) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const chip = event.target?.closest?.('a.ref-chip[href]')
  if (!chip) return
  event.preventDefault()
  push(chip.getAttribute('href'))
}
