import { loadVueContext, scriptComplexity } from '../lib/complexity-utils.mjs'

// Flags each function in the SFC script longer than `options.threshold` lines.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 80
  const { scriptContent } = loadVueContext(filePath)
  const { functions } = scriptComplexity(scriptContent)
  const offending = functions.filter((f) => f.lines > threshold)
  const details = offending
    .sort((a, b) => b.lines - a.lines)
    .slice(0, 3)
    .map((f) => `${f.name} (${f.lines} lines, line ${f.line})`)
  return { matches: offending.length, details }
}
