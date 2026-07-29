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
  console.error('frontend-ux-runner: expected JSON on stdin')
  process.exit(2)
}

let payload
try {
  payload = JSON.parse(raw)
} catch (err) {
  console.error(`frontend-ux-runner: invalid JSON: ${err}`)
  process.exit(2)
}

const checkerName = payload?.rule?.checker
if (!checkerName) {
  process.stdout.write(JSON.stringify({ matches: 0, details: [] }))
  process.exit(0)
}

if (typeof payload.file_path !== 'string' || !payload.file_path) {
  console.error('frontend-ux-runner: payload is missing file_path')
  process.exit(2)
}
const targetPath = resolveTarget(payload.file_path, payload.repo_root)
if (targetPath === null) {
  console.error('frontend-ux-runner: relative file_path needs an absolute repo_root')
  process.exit(2)
}
if (!statOrNull(targetPath)?.isFile()) {
  console.error(`frontend-ux-runner: no such file: ${targetPath}`)
  process.exit(2)
}

const mod = await loadChecker(checkerName)
if (!mod?.run) {
  process.stdout.write(JSON.stringify({ matches: 0, details: [] }))
  process.exit(0)
}

const result = await mod.run({
  filePath: targetPath,
  repoRoot: payload.repo_root,
  rule: payload.rule,
  options: payload.rule?.options || {},
})

process.stdout.write(JSON.stringify(result || { matches: 0, details: [] }))
