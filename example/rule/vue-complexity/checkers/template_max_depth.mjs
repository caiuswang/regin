import { loadVueContext, templateMetrics } from '../lib/complexity-utils.mjs'

// Flags templates whose element nesting depth exceeds `options.threshold`.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 6
  const { ast } = loadVueContext(filePath)
  const { depth } = templateMetrics(ast)
  if (depth <= threshold) return { matches: 0, details: [] }
  return { matches: 1, details: [`template nesting depth ${depth} (threshold ${threshold})`] }
}
