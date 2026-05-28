import {
  NodeTypes,
  loadVueContext,
  walk,
} from '../lib/ast-utils.mjs'

export function run({ filePath }) {
  const { template, ast } = loadVueContext(filePath)
  if (!template.trim() || !ast) return { matches: 0, details: [] }

  const headings = []
  const details = []
  walk(ast, (node) => {
    if (node.type !== NodeTypes.ELEMENT) return
    const tag = (node.tag || '').toLowerCase()
    if (!/^h[1-6]$/.test(tag)) return
    headings.push({
      level: Number(tag.slice(1)),
      line: node.loc?.start?.line || '?',
    })
  })

  let previous = null
  for (const heading of headings) {
    if (previous && heading.level > previous.level + 1) {
      details.push(`heading at line ${heading.line} skips from h${previous.level} to h${heading.level}`)
    }
    previous = heading
  }
  return { matches: details.length, details }
}
