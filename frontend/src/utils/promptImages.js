// The `[Image #N]` markers a prompt's text carries, and the attached images
// they point at. N is 1-indexed and cumulative across the session, so it can be
// far larger than the number of images on any one prompt.

const MARKER = /\[Image #(\d+)\]/g

/**
 * Split prompt text into ordered `{ type: 'text' | 'image' }` segments.
 *
 * A marker becomes an image segment only when N was actually attached — typing
 * `[Image #5]` without attaching one must leave the literal text alone.
 */
export function imageSegments(text, indices = []) {
  const out = []
  if (!text) return out
  const valid = new Set(indices)
  const re = new RegExp(MARKER.source, 'g')
  let last = 0
  let m
  while ((m = re.exec(text)) !== null) {
    const idx = parseInt(m[1], 10)
    if (m.index > last) out.push({ type: 'text', value: text.slice(last, m.index) })
    out.push(valid.has(idx) ? { type: 'image', idx } : { type: 'text', value: m[0] })
    last = re.lastIndex
  }
  if (last < text.length) out.push({ type: 'text', value: text.slice(last) })
  return out
}

/**
 * Attached images no segment referenced. The hook truncates long prompt text
 * with a `…`, and the cut can take a trailing marker with it; rendering these
 * after the prose is what stops the image being silently dropped.
 */
export function unreferencedImages(segments, indices = []) {
  if (!indices.length) return []
  const referenced = new Set(segments.filter(s => s.type === 'image').map(s => s.idx))
  return indices.filter(idx => !referenced.has(idx))
}
