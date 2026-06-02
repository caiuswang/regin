import { loadVueContext, templateMetrics } from '../lib/complexity-utils.mjs'

// Flags templates with too many branching constructs (v-if/v-else-if/v-for) —
// the closest template-side analog of cyclomatic complexity.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 12
  const { ast } = loadVueContext(filePath)
  const { conditionalLoopCount } = templateMetrics(ast)
  if (conditionalLoopCount <= threshold) return { matches: 0, details: [] }
  return {
    matches: 1,
    details: [`${conditionalLoopCount} v-if/v-else-if/v-for branches (threshold ${threshold})`],
  }
}
