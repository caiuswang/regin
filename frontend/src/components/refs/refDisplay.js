// How a reference names itself on screen. The `@` menu's rows and the chips a
// body renders share it, so a feature reads `F128` behind the same glyph in
// both places.

export const REF_GLYPHS = {
  feature: 'flag', task: 'list-checks', defect: 'alert-triangle',
  session: 'terminal', file: 'file', memory: 'archive',
}

const PREFIX = { feature: 'F', task: 'T', defect: 'D', session: 'S', memory: 'M' }

/** `F128` · `T310` · `S3fd8137e` · `M8866e26a`; a file is its whole path. */
export function shortKey(item) {
  if (item.kind === 'file') return item.title || item.key
  const key = item.kind === 'session' || item.kind === 'memory' ? item.key.slice(0, 8) : item.key
  return `${PREFIX[item.kind]}${key}`
}
