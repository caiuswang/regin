import { loadVueContext, scriptComplexity } from '../lib/complexity-utils.mjs'

// Flags each function in the SFC script whose cyclomatic complexity exceeds
// `options.threshold`.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 15
  const { scriptContent } = loadVueContext(filePath)
  const { functions } = scriptComplexity(scriptContent)
  const offending = functions.filter((f) => f.cc > threshold)
  const details = offending
    .sort((a, b) => b.cc - a.cc)
    .slice(0, 3)
    .map((f) => `${f.name} (CC=${f.cc}, line ${f.line})`)
  return { matches: offending.length, details }
}
