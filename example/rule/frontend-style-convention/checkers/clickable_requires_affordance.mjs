import {
  NodeTypes,
  isClickableContainer,
  loadVueContext,
  staticClassList,
  walk,
} from '../lib/ast-utils.mjs'

export function run({ filePath, options = {} }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const requiredPointerClass = options.required_pointer_class || 'cursor-pointer'
  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    if (!isClickableContainer(node)) return
    const classes = staticClassList(node)
    const missingPointer = !classes.includes(requiredPointerClass)
    const missingAffordance = !classes.some((cls) =>
      cls.startsWith('hover:') ||
      cls.startsWith('focus:') ||
      cls.startsWith('focus-visible:')
    )
    if (!missingPointer && !missingAffordance) return
    const loc = node.loc?.start?.line || '?'
    const missing = []
    if (missingPointer) missing.push(requiredPointerClass)
    if (missingAffordance) missing.push('hover/focus-visible affordance')
    details.push(`clickable container at line ${loc} is missing ${missing.join(' and ')}`)
  })
  return { matches: details.length, details }
}
