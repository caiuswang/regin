// `![](image:N)` in a task body, resolved for display.
//
// `N` is the attachment's `idx`, which `lib/tasks/images.py` never reuses — so
// a reference names the same bytes for as long as they exist. It is NOT a URL:
// the bytes endpoint is authenticated and an `<img>` tag cannot attach the
// header, which is why `useAttachedImages` pulls every image through the api
// client into an object URL and why the reference has to be rewritten here
// rather than being an href the browser could follow on its own.
//
// Display only. A run still receives every attachment beside its prompt
// (`runs.py`), referenced or not — making a reference decide delivery is a
// contract change this deliberately does not make.

const IMAGE_REF = /!\[([^\]]*)\]\(image:(\d+)\)/g

export const IMAGE_REF_HINT = 'Embed an attachment with ![](image:0) — the index is on each thumbnail.'

/** Every `idx` an `![](image:N)` in `markdown` names. */
export function imageRefsIn(markdown) {
  return new Set([...(markdown || '').matchAll(IMAGE_REF)].map(m => Number(m[2])))
}

/** `text` cut into `{ text }` runs and the `{ idx, alt }` references between them. */
export function splitImageRefs(text) {
  const out = []
  let at = 0
  for (const m of (text || '').matchAll(IMAGE_REF)) {
    if (m.index > at) out.push({ text: text.slice(at, m.index) })
    out.push({ idx: Number(m[2]), alt: m[1] })
    at = m.index + m[0].length
  }
  if (at < (text || '').length) out.push({ text: text.slice(at) })
  return out
}

export function imageRef(idx) {
  return `![](image:${idx})`
}

/**
 * The references for a set of freshly attached `idx`, each on its own line.
 *
 * One block for the whole gesture, never one insert per file: a caller reads
 * the caret off the live textarea and Vue has not flushed the previous write
 * by the next iteration, so a second insert lands at the ORIGINAL caret and a
 * two-image paste comes out reversed.
 */
export function imageRefBlock(idxs) {
  return (idxs || []).map(idx => `\n${imageRef(idx)}\n`).join('')
}

/**
 * `markdown` with every `image:N` reference pointing at a renderable URL.
 *
 * A reference to an image that is no longer attached becomes visible text
 * rather than a broken `<img>`: the author needs to see that it went, and a
 * silently missing picture reads as a rendering bug instead of a deleted file.
 */
export function resolveImageRefs(markdown, items) {
  if (!markdown) return ''
  const urls = {}
  for (const item of items || []) {
    if (item.idx !== null && item.idx !== undefined) urls[item.idx] = item.url
  }
  return markdown.replace(IMAGE_REF, (whole, alt, idx) => (
    urls[idx] ? `![${alt}](${urls[idx]})` : `*(image:${idx} — no longer attached)*`
  ))
}
