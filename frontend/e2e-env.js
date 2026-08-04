/**
 * The E2E suite's scratch environment.
 *
 * Every regin data store the suite can reach is redirected under one throwaway
 * directory, and the redirect is applied to `process.env` at module load. That
 * placement is load-bearing: Playwright imports the config in the runner AND in
 * every worker process, so the `execFileSync('.venv/bin/python', ...)` calls
 * inside specs inherit the same scratch paths as the server the browser talks
 * to. Setting them in `globalSetup` instead would leave the workers pointed at
 * the developer's real DB.
 *
 * Four stores, not one — a spec reaches all of them:
 *   trace/auth DB  the sessions, spans and `users` table (auth is standalone,
 *                  so `auth-fixture.js` logging in also lands here)
 *   memory DB      the lesson corpus
 *   data dir       patterns; the import specs `rm -rf` directories under it
 *   web port       the badge-push loopback, addressed by port not by path
 */
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// Fixed, not `mkdtemp`-random: the server script provisions it and
// `global-teardown.js` removes it, and a run killed before teardown must leave
// a path the next run can find and wipe rather than a new orphan each time.
export const SCRATCH_DIR = join(tmpdir(), 'regin-e2e')

export const API_PORT = 8322
export const VITE_PORT = 5174
export const API_BASE = `http://localhost:${API_PORT}`

// Distinct from the dev stack's 8321/5173 on purpose. Sharing the ports would
// let `reuseExistingServer` silently re-attach to the developer's server — the
// exact failure this whole directory exists to prevent — and would make a
// running dev stack collide with `strictPort` instead of coexisting.
export const scratchEnv = {
  REGIN_TRACE_DB_PATH: join(SCRATCH_DIR, 'db', 'regin.db'),
  REGIN_AGENT_MEMORY__DB_PATH: join(SCRATCH_DIR, 'db', 'regin_memory.db'),
  REGIN_DATA_DIR: join(SCRATCH_DIR, 'data'),
  // The settings API writes settings.json; unredirected it edits the committed
  // config of this checkout.
  REGIN_CONFIG_DIR: join(SCRATCH_DIR, 'config'),
  REGIN_WEB_PORT: String(API_PORT),
  // Else `regin serve` ingests the developer's local workflow transcripts into
  // the scratch DB, and the suite's baseline data varies per machine.
  REGIN_WORKFLOW_WATCH: 'false',
  REGIN_E2E: '1',
}

export function applyScratchEnv() {
  Object.assign(process.env, scratchEnv)
  process.env.REGIN_E2E_API_BASE = API_BASE
  return scratchEnv
}

export const TEST_USER = {
  username: 'claude-admin',
  password: 'claude-admin-2026',
}
