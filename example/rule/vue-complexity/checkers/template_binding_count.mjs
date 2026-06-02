import { loadVueContext, templateMetrics } from '../lib/complexity-utils.mjs'

// Flags templates with too many reactive bindings — `{{ }}` interpolations
// plus v-bind/v-model attributes — a measure of template data coupling.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 60
  const { ast } = loadVueContext(filePath)
  const { bindingCount } = templateMetrics(ast)
  if (bindingCount <= threshold) return { matches: 0, details: [] }
  return { matches: 1, details: [`${bindingCount} template bindings (threshold ${threshold})`] }
}
