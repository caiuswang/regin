import { loadVueContext, templateMetrics } from '../lib/complexity-utils.mjs'

// Flags templates with more than `options.threshold` element nodes — a proxy
// for an over-large component that should be split.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 80
  const { ast } = loadVueContext(filePath)
  const { nodeCount, componentCount } = templateMetrics(ast)
  if (nodeCount <= threshold) return { matches: 0, details: [] }
  return {
    matches: 1,
    details: [`${nodeCount} template elements, ${componentCount} components (threshold ${threshold})`],
  }
}
