import { defineConfig } from '@playwright/test'

// Specs run against the production build (`npm run build` first), served by
// `vite preview`. Parallel verifies each pick their own port via
// WEBSITE_E2E_PORT so two runs never share — or reuse — one server.
const PORT = Number(process.env.WEBSITE_E2E_PORT || 4317)
// A run that built into its own directory serves that one, so a concurrent
// `npm run build` rewriting dist/ cannot pull files out from under it.
const OUT_DIR = process.env.WEBSITE_E2E_DIST ? ` --outDir ${JSON.stringify(process.env.WEBSITE_E2E_DIST)}` : ''

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  timeout: 30000,
  use: {
    baseURL: `http://localhost:${PORT}`,
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npx vite preview --port ${PORT} --strictPort${OUT_DIR}`,
    port: PORT,
    reuseExistingServer: false,
    timeout: 30000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
