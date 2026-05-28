import {
  NodeTypes,
  isInteractiveElement,
  loadVueContext,
  staticClassList,
  walk,
} from '../lib/ast-utils.mjs'

export function run({ filePath, options = {} }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const requiredPrefixes = options.required_prefixes || ['focus-visible:']
  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    if (!isInteractiveElement(node)) return
    const classes = staticClassList(node)
    const hasRequired = classes.some((cls) =>
      requiredPrefixes.some((prefix) => cls.startsWith(prefix))
    )
    if (hasRequired) return
    const loc = node.loc?.start?.line || '?'
    details.push(`interactive element at line ${loc} lacks explicit focus-visible styling`)
  })
  return { matches: details.length, details }
}
