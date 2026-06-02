import { loadVueContext, scriptComplexity } from '../lib/complexity-utils.mjs'

// Flags the SFC when the whole-script aggregate cyclomatic complexity exceeds
// `options.threshold`. Unlike the per-function check, this captures top-level
// `<script setup>` logic that lives outside any function.
export function run({ filePath, options }) {
  const threshold = options.threshold ?? 40
  const { scriptContent } = loadVueContext(filePath)
  const { moduleCC } = scriptComplexity(scriptContent)
  if (moduleCC <= threshold) return { matches: 0, details: [] }
  return {
    matches: 1,
    details: [`script aggregate CC=${moduleCC} (threshold ${threshold})`],
  }
}
