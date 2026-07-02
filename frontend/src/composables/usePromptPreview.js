// Client-side mirror of lib/prompts/engine.py for the skeleton editor's live
// preview + unknown-variable validation. ONLY {{double-brace}} tokens are
// placeholders; single braces / literal JSON pass through untouched.

const PLACEHOLDER_RE = /\{\{\s*([A-Za-z0-9_.:-]+)\s*\}\}/g

function sampleValue(variable) {
  if (variable && variable.example) return variable.example
  const name = (variable && variable.name) || ''
  return `‹${name}›`
}

// Render a preview: fill declared {{var}} with its example (or a ‹name›
// placeholder), and show {{include:slug}} as a labelled marker. Unknown vars
// are left visible so the author sees they won't be filled.
export function renderPreview(body, variables) {
  const byName = new Map((variables || []).map(v => [v.name, v]))
  return String(body || '').replace(PLACEHOLDER_RE, (match, token) => {
    if (token.startsWith('include:')) return `‹include: ${token.slice(8)}›`
    if (byName.has(token)) return sampleValue(byName.get(token))
    return match
  })
}

// The {{var}} tokens (excluding include:) referenced by the body but NOT in the
// declared variable palette — these would go unfilled at render time.
export function unknownVariables(body, variables) {
  const declared = new Set((variables || []).map(v => v.name))
  const seen = []
  let m
  PLACEHOLDER_RE.lastIndex = 0
  while ((m = PLACEHOLDER_RE.exec(String(body || ''))) !== null) {
    const token = m[1]
    if (token.startsWith('include:')) continue
    if (!declared.has(token) && !seen.includes(token)) seen.push(token)
  }
  return seen
}
