import { loadScriptContent, scriptSurfaceArea } from '../lib/complexity-utils.mjs'

// Flags an SFC whose raw "surface area" — top-level reactive-state
// declarations (ref/computed/…) + top-level function definitions — exceeds
// `options.threshold`. This is the god-component signal cyclomatic complexity
// misses: many *simple* refs and functions for many unrelated concerns keep
// per-function CC low while the component is still far too large to navigate.
// Composable calls are excluded by the walker, so extracting state into
// `use*()` composables lowers the score — which is exactly the intended cure.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 45
  const scriptContent = loadScriptContent(filePath)
  const { count, refs, functions } = scriptSurfaceArea(scriptContent)
  if (count <= threshold) return { matches: 0, details: [] }
  return {
    matches: 1,
    details: [
      `surface area=${count} (threshold ${threshold}): `
      + `${refs} reactive decls + ${functions} functions`,
    ],
  }
}
