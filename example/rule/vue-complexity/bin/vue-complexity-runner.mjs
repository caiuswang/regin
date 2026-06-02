// Node runner for the vue-complexity bundle. Same JSON-over-stdin contract
// as frontend-ux-runner.mjs:
//   stdin:  {"repo_root": str, "file_path": str, "rule": {"checker": str, "options": {...}}}
//   stdout: {"matches": int, "details": [str, ...]}
// A checker that throws or a file that won't parse degrades to matches:0 —
// a complexity probe must never break the edit hook.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const rulesRoot = path.resolve(__dirname, '..')

async function readStdin() {
  let data = ''
  for await (const chunk of process.stdin) data += chunk
  return data.trim()
}

async function loadChecker(checkerName) {
  const checkerPath = path.join(rulesRoot, 'checkers', `${checkerName}.mjs`)
  if (!fs.existsSync(checkerPath)) return null
  return import(pathToFileURL(checkerPath).href)
}

const raw = await readStdin()
if (!raw) {
  console.error('vue-complexity-runner: expected JSON on stdin')
  process.exit(2)
}

let payload
try {
  payload = JSON.parse(raw)
} catch (err) {
  console.error(`vue-complexity-runner: invalid JSON: ${err}`)
  process.exit(2)
}

const checkerName = payload?.rule?.checker
if (!checkerName) {
  process.stdout.write(JSON.stringify({ matches: 0, details: [] }))
  process.exit(0)
}

const mod = await loadChecker(checkerName)
if (!mod?.run) {
  process.stdout.write(JSON.stringify({ matches: 0, details: [] }))
  process.exit(0)
}

let result
try {
  result = await mod.run({
    filePath: payload.file_path,
    repoRoot: payload.repo_root,
    rule: payload.rule,
    options: payload.rule?.options || {},
  })
} catch (err) {
  console.error(`vue-complexity-runner: checker ${checkerName} threw: ${err}`)
  result = { matches: 0, details: [] }
}

process.stdout.write(JSON.stringify(result || { matches: 0, details: [] }))
