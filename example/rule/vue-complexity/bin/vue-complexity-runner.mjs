// Node runner for the vue-complexity bundle. Same JSON-over-stdin contract
// as frontend-ux-runner.mjs:
//   stdin:  {"repo_root": str, "file_path": str, "rule": {"checker": str, "options": {...}}}
//   stdout: {"matches": int, "details": [str, ...]}
// `file_path` may be absolute or relative to `repo_root`.
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

// A relative `file_path` is relative to the caller's `repo_root`, never to
// this process's cwd — BundleEngine spawns the runner with cwd set to the
// bundle dir, so resolving against cwd would read the wrong tree (or nothing).
// An absolute `file_path` needs no root, so a junk `repo_root` alongside one
// is ignored rather than rejected. Returns null when the payload can't name a
// single unambiguous file.
function resolveTarget(filePath, repoRoot) {
  if (path.isAbsolute(filePath)) return filePath
  if (repoRoot === undefined || repoRoot === null) return path.resolve(filePath)
  if (typeof repoRoot !== 'string' || !path.isAbsolute(repoRoot)) return null
  return path.resolve(repoRoot, filePath)
}

// A directory satisfies existsSync but not the checkers, which read it as a file.
function statOrNull(target) {
  try {
    return fs.statSync(target)
  } catch {
    return null
  }
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

if (typeof payload.file_path !== 'string' || !payload.file_path) {
  console.error('vue-complexity-runner: payload is missing file_path')
  process.exit(2)
}
const targetPath = resolveTarget(payload.file_path, payload.repo_root)
if (targetPath === null) {
  console.error('vue-complexity-runner: relative file_path needs an absolute repo_root')
  process.exit(2)
}
if (!statOrNull(targetPath)?.isFile()) {
  console.error(`vue-complexity-runner: no such file: ${targetPath}`)
  process.exit(2)
}

const mod = await loadChecker(checkerName)
if (!mod?.run) {
  process.stdout.write(JSON.stringify({ matches: 0, details: [] }))
  process.exit(0)
}

let result
try {
  result = await mod.run({
    filePath: targetPath,
    repoRoot: payload.repo_root,
    rule: payload.rule,
    options: payload.rule?.options || {},
  })
} catch (err) {
  console.error(`vue-complexity-runner: checker ${checkerName} threw: ${err}`)
  result = { matches: 0, details: [] }
}

process.stdout.write(JSON.stringify(result || { matches: 0, details: [] }))
