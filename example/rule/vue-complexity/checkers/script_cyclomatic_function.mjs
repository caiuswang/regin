import { loadScriptContent, scriptComplexity } from '../lib/complexity-utils.mjs'

// Flags each function whose cyclomatic complexity exceeds `options.threshold`.
// Works on both `.vue` SFC scripts and plain `.js`/`.ts` modules (composables,
// utils) — the metric is the same JS analysis. Per-function CC is wrapper-safe:
// a composable's `useX()` wrapper is mostly declarations (low CC), so only
// genuinely branchy inner functions trip it.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 15
  const scriptContent = loadScriptContent(filePath)
  const { functions } = scriptComplexity(scriptContent)
  const offending = functions.filter((f) => f.cc > threshold)
  const details = offending
    .sort((a, b) => b.cc - a.cc)
    .slice(0, 3)
    .map((f) => `${f.name} (CC=${f.cc}, line ${f.line})`)
  return { matches: offending.length, details }
}
