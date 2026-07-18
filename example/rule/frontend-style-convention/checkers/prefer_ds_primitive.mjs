import { NodeTypes, loadVueContext, walk } from '../lib/ast-utils.mjs'

/**
 * Steer raw HTML controls toward the unified design-system primitives
 * (components/ui/*). Parameterized via rule options so one checker backs the
 * button / select / input / textarea / checkbox-radio rules:
 *
 *   options.tags          - lowercase tag names to flag, e.g. ["button"]
 *   options.input_types   - if set, only flag <input> whose type ∈ this list
 *                           (so we catch checkbox/radio but not text inputs)
 *   options.exclude_input_types - if set, flag every <input> EXCEPT those whose
 *                           type ∈ this list. A missing type attribute counts as
 *                           "text", so untyped text fields are caught while
 *                           file/checkbox/radio/etc. are left to their own rule.
 *   options.replacement   - human text naming the primitive to use instead
 *   options.skip_path_includes - substrings; files whose path contains any are
 *                           skipped (the primitives themselves legitimately use
 *                           raw elements, so components/ui/ is excluded)
 *
 * Severity is `warn`: this drives the incremental migration without blocking,
 * and only fires when a file is edited (PostToolUse), so it nudges each file
 * toward the primitives as it's touched rather than dumping every hit at once.
 */

/**
 * Resolve an <input>'s type for the input_types / exclude_input_types filters.
 * Returns the lowercase type, "text" when the attribute is absent (the HTML
 * default), or null when the type is a non-literal bound expression
 * (`:type="foo"`) — unknowable at lint time, so the caller skips it rather than
 * guessing (a wrong guess is both a false positive here and a false negative in
 * the sibling checkbox/radio rule). A quoted literal binding (`:type="'text'"`)
 * still resolves.
 */
function resolveInputType(node) {
  for (const prop of node.props || []) {
    if (prop.type === NodeTypes.ATTRIBUTE && prop.name === 'type') {
      return (prop.value?.content || 'text').toLowerCase()
    }
    if (prop.type === NodeTypes.DIRECTIVE && prop.name === 'bind' && prop.arg?.content === 'type') {
      const exp = prop.exp?.content || ''
      const literal = exp.match(/^'([^']*)'$|^"([^"]*)"$/)
      if (literal) return (literal[1] ?? literal[2]).toLowerCase()
      return null
    }
  }
  return 'text'
}

export function run({ filePath, options = {} }) {
  const skip = options.skip_path_includes || []
  if (skip.some((s) => filePath.includes(s))) return { matches: 0, details: [] }

  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const tags = (options.tags || []).map((t) => t.toLowerCase())
  const inputTypes = options.input_types ? options.input_types.map((t) => t.toLowerCase()) : null
  const excludeInputTypes = options.exclude_input_types
    ? options.exclude_input_types.map((t) => t.toLowerCase())
    : null
  const replacement = options.replacement || 'the matching design-system primitive'

  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    // Case-SENSITIVE: native HTML elements are lowercase (`button`, `select`),
    // Vue components are PascalCase (`<Button>`, `<Select>`). Lowercasing here
    // would wrongly flag the primitives themselves. Match the raw tag.
    const tag = node.tag || ''
    if (!tags.includes(tag)) return
    if (inputTypes || excludeInputTypes) {
      const t = resolveInputType(node)
      if (t === null) return
      if (inputTypes && !inputTypes.includes(t)) return
      if (excludeInputTypes && excludeInputTypes.includes(t)) return
    }
    const loc = node.loc?.start?.line || '?'
    details.push(`<${tag}> at line ${loc}: use ${replacement} instead`)
  })
  return { matches: details.length, details }
}
