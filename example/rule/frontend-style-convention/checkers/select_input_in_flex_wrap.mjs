import {
  NodeTypes,
  loadVueContext,
  staticClassList,
} from '../lib/ast-utils.mjs'

const FLEX_WRAP_HINT = /\bflex-wrap\b/
// Filter rows in this codebase are flex-wrap containers by convention,
// even when their class names don't say so directly.
const FILTER_ROW_HINT = /\bfilter-row\b/
const WIDTH_OVERRIDE_HINT = /\b(w-(auto|fit)|width-auto|filter-select|search-scope|inline-|dense-input)/

function ancestorsTriggerFlexWrap(ancestors) {
  return ancestors.some((node) => {
    const classes = staticClassList(node).join(' ')
    return FLEX_WRAP_HINT.test(classes) || FILTER_ROW_HINT.test(classes)
  })
}

function hasWidthOverride(classes) {
  return classes.some((cls) => WIDTH_OVERRIDE_HINT.test(cls))
}

function walkWithAncestors(node, ancestors, visit) {
  visit(node, ancestors)
  const nextAncestors = node.type === NodeTypes.ELEMENT
    ? [...ancestors, node]
    : ancestors
  for (const child of node.children || []) walkWithAncestors(child, nextAncestors, visit)
  if (node.branches) {
    for (const branch of node.branches) walkWithAncestors(branch, nextAncestors, visit)
  }
}

export function run({ filePath }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const details = []
  walkWithAncestors(ast, [], (node, ancestors) => {
    if (node.type !== NodeTypes.ELEMENT) return
    const tag = (node.tag || '').toLowerCase()
    if (tag !== 'select' && tag !== 'input') return

    const classes = staticClassList(node)
    if (!classes.includes('input')) return
    if (hasWidthOverride(classes)) return
    if (!ancestorsTriggerFlexWrap(ancestors)) return

    const loc = node.loc?.start?.line || '?'
    details.push(
      `<${tag} class="input ..."> at line ${loc} lives inside a flex-wrap row; ` +
      `the shared .input class sets width:100% and will push the control onto its own line. ` +
      `Add a scoped class with width:auto + max-width.`
    )
  })

  return { matches: details.length, details }
}
