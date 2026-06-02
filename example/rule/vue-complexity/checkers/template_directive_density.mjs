import { loadVueContext, templateMetrics } from '../lib/complexity-utils.mjs'

// Flags templates whose total directive count (v-if/v-else-if/v-else/v-for/
// v-bind/v-on/v-model/v-slot/v-show) exceeds `options.threshold`.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 40
  const { ast } = loadVueContext(filePath)
  const { directiveTotal, directives } = templateMetrics(ast)
  if (directiveTotal <= threshold) return { matches: 0, details: [] }
  const top = Object.entries(directives)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([name, n]) => `v-${name}×${n}`)
    .join(', ')
  return {
    matches: 1,
    details: [`${directiveTotal} directives (threshold ${threshold}): ${top}`],
  }
}
