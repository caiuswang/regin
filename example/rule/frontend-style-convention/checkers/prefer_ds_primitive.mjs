import { NodeTypes, getProp, loadVueContext, walk } from '../lib/ast-utils.mjs'

/**
 * Steer raw HTML controls toward the unified design-system primitives
 * (components/ui/*). Parameterized via rule options so one checker backs the
 * button / select / checkbox-radio rules:
 *
 *   options.tags          - lowercase tag names to flag, e.g. ["button"]
 *   options.input_types   - if set, only flag <input> whose type ∈ this list
 *                           (so we catch checkbox/radio but not text inputs)
 *   options.replacement   - human text naming the primitive to use instead
 *   options.skip_path_includes - substrings; files whose path contains any are
 *                           skipped (the primitives themselves legitimately use
 *                           raw elements, so components/ui/ is excluded)
 *
 * Severity is `warn`: this drives the incremental migration without blocking,
 * and only fires when a file is edited (PostToolUse), so it nudges each file
 * toward the primitives as it's touched rather than dumping 356 errors at once.
 */
export function run({ filePath, options = {} }) {
  const skip = options.skip_path_includes || []
  if (skip.some((s) => filePath.includes(s))) return { matches: 0, details: [] }

  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const tags = (options.tags || []).map((t) => t.toLowerCase())
  const inputTypes = options.input_types ? options.input_types.map((t) => t.toLowerCase()) : null
  const replacement = options.replacement || 'the matching design-system primitive'

  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    // Case-SENSITIVE: native HTML elements are lowercase (`button`, `select`),
    // Vue components are PascalCase (`<Button>`, `<Select>`). Lowercasing here
    // would wrongly flag the primitives themselves. Match the raw tag.
    const tag = node.tag || ''
    if (!tags.includes(tag)) return
    if (inputTypes) {
      const t = String(getProp(node, ['type']) || '').toLowerCase()
      if (!inputTypes.includes(t)) return
    }
    const loc = node.loc?.start?.line || '?'
    details.push(`<${tag}> at line ${loc}: use ${replacement} instead`)
  })
  return { matches: details.length, details }
}
