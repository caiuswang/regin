import {
  NodeTypes,
  getProp,
  isIconOnlyButton,
  loadVueContext,
  walk,
} from '../lib/ast-utils.mjs'

export function run({ filePath }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    if ((node.tag || '').toLowerCase() !== 'button') return
    const ariaLabel = getProp(node, ['aria-label'])
    const ariaLabelledby = getProp(node, ['aria-labelledby'])
    const title = getProp(node, ['title'])
    if (ariaLabel || ariaLabelledby || title) return
    if (!isIconOnlyButton(node)) return
    const loc = node.loc?.start?.line || '?'
    details.push(`button at line ${loc} is icon-only and lacks accessible label`)
  })
  return { matches: details.length, details }
}
