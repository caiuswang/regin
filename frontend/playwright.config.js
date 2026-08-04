import { defineConfig, devices } from '@playwright/test'

import { API_BASE, API_PORT, VITE_PORT, applyScratchEnv } from './e2e-env.js'

// At module scope, so the runner AND every worker process (each of which
// re-imports this config) inherit the scratch paths — specs that shell out to
// `.venv/bin/python` write through the same redirect as the server.
applyScratchEnv()

export default defineConfig({
  testDir: './tests',
  globalTeardown: './global-teardown.js',
  // Tests within a file run concurrently too, not just across files: measured
  // 3.2m -> 1.4m over the whole suite, with the same set of failures. A file
  // whose tests genuinely share state says so itself with
  // `test.describe.configure({ mode: 'serial' })` — see import-conflict.spec.js,
  // which shares one pattern slug and one directory on disk.
  fullyParallel: true,
  timeout: 30000,
  use: {
    baseURL: `http://localhost:${VITE_PORT}`,
    headless: true,
  },
  // `reuseExistingServer: false`, and both ports off the dev stack's: the
  // suite must own its server, because that server owns the database. Reusing
  // whatever was listening is how ~70 specs came to write into the developer's
  // real `db/regin.db`.
  webServer: [
    {
      command: 'node scripts/e2e-server.mjs',
      port: API_PORT,
      reuseExistingServer: false,
      // Provisions a fresh DB (`regin init` + a seeded admin) before serving.
      timeout: 60000,
    },
    {
      command: `npx vite --port ${VITE_PORT}`,
      port: VITE_PORT,
      reuseExistingServer: false,
      timeout: 30000,
      env: { REGIN_API_TARGET: API_BASE },
    },
  ],
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'mobile', testMatch: /responsive\.spec\.js$/, use: { ...devices['iPhone SE'] } },
    { name: 'tablet', testMatch: /responsive\.spec\.js$/, use: { ...devices['iPad (gen 7)'] } },
  ],
})
