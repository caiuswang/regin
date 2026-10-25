// A long body's headings, as the outline lists them: h1–h4 in document
// order, numbered rather than slugged because a spec repeats its headings
// ("Rules" under every section). The read view stamps the same `md-h-<n>`
// ids on its rendered headings (`MarkdownContent`).
export const OUTLINE_MIN = 4

export const headingId = (n) => `md-h-${n}`

// Top-level blocks only, so a pass costs the number of blocks, not of nodes.
export function outlineFromDoc(doc) {
  const out = []
  doc.forEach((node, pos) => {
    if (node.type.name !== 'heading' || node.attrs.level > 4) return
    out.push({ id: headingId(out.length), level: node.attrs.level, text: node.textContent, pos })
  })
  return out
}

export function outlineFromDom(container) {
  if (!container) return []
  return [...container.querySelectorAll('h1, h2, h3, h4')].map((el, n) => ({
    id: el.id || headingId(n), level: Number(el.tagName[1]), text: el.textContent.trim(),
  }))
}
