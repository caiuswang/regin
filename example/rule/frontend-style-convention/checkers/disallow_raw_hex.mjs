import {
  NodeTypes,
  getProp,
  hasRawHex,
  loadVueContext,
  staticClassList,
  walk,
} from '../lib/ast-utils.mjs'

export function run({ filePath }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return

    const styleAttr = getProp(node, ['style'])
    if (styleAttr && hasRawHex(styleAttr)) {
      const loc = node.loc?.start?.line || '?'
      details.push(`element at line ${loc} uses raw hex color in style attribute`)
      return
    }

    const classes = staticClassList(node)
    const rawHexClass = classes.find(hasRawHex)
    if (rawHexClass) {
      const loc = node.loc?.start?.line || '?'
      details.push(`element at line ${loc} uses raw hex color token in class "${rawHexClass}"`)
    }
  })
  return { matches: details.length, details }
}
