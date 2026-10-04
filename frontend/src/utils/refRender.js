// References in a rendered markdown body, drawn as chips (R6).
//
// Whether a link is a reference is the grammar's answer (`parseRefs`), never
// `marked`'s. Each reference is swapped for an empty placeholder link before
// the markdown is parsed, so code is judged by the grammar's own rules and a
// label never reaches `marked` as markup; after the render each placeholder
// becomes the mount point of one chip.

import { KINDS } from './refs.js'

const REF_SCHEME = new RegExp(`^(?:${KINDS.join('|')}):`, 'i')

// The tag is per body instance, so text that merely looks like a placeholder
// (a body documenting this very file) is never mistaken for one.
const slotHref = (tag, at) => `#regin-ref-${tag}-${at}`

/**
 * `text` with every reference in `found` replaced by its placeholder link.
 *
 * The link text is one space, not empty: after a defined label, `[foo][]`
 * would read as a collapsed reference to `foo` and swallow the placeholder,
 * while a blank label can never be a reference.
 */
export function withSlots(text, found, tag) {
  let out = ''
  let at = 0
  found.forEach((ref, i) => {
    out += `${text.slice(at, ref.start)}[ ](${slotHref(tag, i)})`
    at = ref.end
  })
  return out + text.slice(at)
}

// A placeholder `marked` did not make a link — one inside an indented code
// block, which the grammar does not model, or inside raw HTML — sits where the
// body's own text would have shown, so it gets that text back.
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
// plain text, never a dead `<a href="feature:x">`.
function unlinkRefSchemes(container) {
  for (const link of container.querySelectorAll('a[href]')) {
    if (REF_SCHEME.test(link.getAttribute('href'))) {
      link.replaceWith(document.createTextNode(link.textContent))
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
