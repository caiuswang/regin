import { execFileSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const PROMO_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export const FRONTEND = resolve(PROMO_DIR, '..')
export const ROOT = resolve(FRONTEND, '..')
export const PY = resolve(ROOT, '.venv/bin/python')
export const CLI = resolve(ROOT, 'cli/regin.py')
export const DEFAULT_OUT = join(PROMO_DIR, 'out')
export const DEFAULT_STORYBOARD = join(PROMO_DIR, 'storyboard.zh.json')

// Distinct from the dev stack (8321/5173) and the E2E suite (8322/5174), so a
// build can run beside both and can never re-attach to either one's server.
export const API_PORT = 8323
export const VITE_PORT = 5175
export const API_BASE = `http://127.0.0.1:${API_PORT}`
export const APP_BASE = `http://127.0.0.1:${VITE_PORT}`
export const PROMO_USER = 'promo-admin'

// A 1280x720 layout drawn at 3x: the UI reads at 1.5x its desktop size in
// a 1080p frame, and the film is mastered at 4K from native device pixels.
export const VIEWPORT = { width: 1280, height: 720 }
export const DSF = 3
export const FRAME = { width: 3840, height: 2160 }

export const scratchPaths = (outDir) => {
  const scratch = join(outDir, 'scratch')
  return {
    scratch,
    traceDb: join(scratch, 'db', 'regin.db'),
    memoryDb: join(scratch, 'db', 'regin_memory.db'),
    dataDir: join(scratch, 'data'),
    configDir: join(scratch, 'config'),
  }
}

export function scratchEnv(paths) {
  return {
    REGIN_TRACE_DB_PATH: paths.traceDb,
    REGIN_AGENT_MEMORY__DB_PATH: paths.memoryDb,
    REGIN_DATA_DIR: paths.dataDir,
    REGIN_CONFIG_DIR: paths.configDir,
    REGIN_WEB_PORT: String(API_PORT),
    REGIN_WORKFLOW_WATCH: 'false',
  }
}

export function py(code, args = [], env = process.env) {
  return execFileSync(PY, ['-c', code, ...args], { cwd: ROOT, env, encoding: 'utf8' })
}

// The live stores as regin itself resolves them, read with the caller's own
// environment — a worktree resolves to the main checkout's DB, not its own.
export function liveStores() {
  const clean = { ...process.env }
  for (const k of Object.keys(scratchEnv(scratchPaths('/x')))) delete clean[k]
  const out = py(`
import json
from lib.settings import settings
import lib.orm.engine as e
print(json.dumps({"trace": str(e.DB_PATH),
                  "memory": str(settings.agent_memory.db_path or settings.main_worktree / "db" / "regin_memory.db"),
                  "tmux_socket": settings.agent_bridge.attach_socket}))
`, [], clean)
  return JSON.parse(out)
}

export const COUNT_TABLES = ['sessions', 'agent_tasks', 'agent_task_runs']

export function snapshotCounts(dbPath) {
  return JSON.parse(py(`
import json, sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(json.dumps({t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in sys.argv[2:]}))
`, [dbPath, ...COUNT_TABLES]))
}

export function tmuxSessions(socket) {
  try {
    return execFileSync('tmux', ['-L', socket, 'ls', '-F', '#{session_name}'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
      .split('\n').filter(Boolean).sort()
  } catch {
    return []
  }
}

export function ffprobeDuration(file) {
  return Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=nw=1:nk=1', file], { encoding: 'utf8' }).trim())
}
