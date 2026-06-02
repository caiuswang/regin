// Calibration harness: run every metric over a glob of .vue files and print
// the per-metric distribution (p50/p75/p90/p95/max) so thresholds can be set
// from real data rather than guesses.
//   node references/calibrate.mjs <dir>
import fs from 'node:fs'
import path from 'node:path'
import { loadVueContext, scriptComplexity, templateMetrics } from '../lib/complexity-utils.mjs'

const root = process.argv[2]
if (!root) {
  console.error('usage: node references/calibrate.mjs <dir-with-vue-files>')
  process.exit(1)
}

function findVue(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) findVue(full, out)
    else if (entry.name.endsWith('.vue')) out.push(full)
  }
  return out
}

function pct(sorted, p) {
  if (!sorted.length) return 0
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)
  return sorted[Math.max(0, idx)]
}

const files = findVue(root)
const rows = []
for (const f of files) {
  try {
    const { scriptContent, ast } = loadVueContext(f)
    const s = scriptComplexity(scriptContent)
    const t = templateMetrics(ast)
    const maxFnCC = s.functions.reduce((m, fn) => Math.max(m, fn.cc), 0)
    const maxFnLen = s.functions.reduce((m, fn) => Math.max(m, fn.lines), 0)
    rows.push({
      file: path.relative(root, f),
      scriptFnCC: maxFnCC,
      scriptModuleCC: s.moduleCC,
      scriptFnLen: maxFnLen,
      tplDepth: t.depth,
      tplDirectives: t.directiveTotal,
      tplCondLoop: t.conditionalLoopCount,
      tplNodes: t.nodeCount,
      tplBindings: t.bindingCount,
      tplComponents: t.componentCount,
    })
  } catch (err) {
    console.error(`SKIP ${f}: ${err.message}`)
  }
}

const metrics = ['scriptFnCC', 'scriptModuleCC', 'scriptFnLen', 'tplDepth',
  'tplDirectives', 'tplCondLoop', 'tplNodes', 'tplBindings', 'tplComponents']

console.log(`\nFiles analyzed: ${rows.length}\n`)
console.log('metric'.padEnd(16), 'p50'.padStart(6), 'p75'.padStart(6),
  'p90'.padStart(6), 'p95'.padStart(6), 'max'.padStart(6))
const dist = {}
for (const m of metrics) {
  const sorted = rows.map((r) => r[m]).sort((a, b) => a - b)
  dist[m] = { p50: pct(sorted, 50), p75: pct(sorted, 75), p90: pct(sorted, 90), p95: pct(sorted, 95), max: sorted[sorted.length - 1] }
  console.log(m.padEnd(16),
    String(dist[m].p50).padStart(6), String(dist[m].p75).padStart(6),
    String(dist[m].p90).padStart(6), String(dist[m].p95).padStart(6),
    String(dist[m].max).padStart(6))
}

// Top offenders per metric, for sanity-checking thresholds.
console.log('\nTop 5 by each metric:')
for (const m of metrics) {
  const top = [...rows].sort((a, b) => b[m] - a[m]).slice(0, 5)
    .map((r) => `${r.file.replace(/^.*\//, '')}=${r[m]}`).join(', ')
  console.log(`  ${m.padEnd(15)} ${top}`)
}

fs.writeFileSync(
  path.join(path.dirname(new URL(import.meta.url).pathname), 'calibration.json'),
  JSON.stringify({ files: rows.length, distribution: dist, rows }, null, 2),
)
console.log('\nWrote references/calibration.json')
