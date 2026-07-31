import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  // Tests within a file run concurrently too, not just across files: measured
  // 3.2m -> 1.4m over the whole suite, with the same set of failures. A file
  // whose tests genuinely share state says so itself with
  // `test.describe.configure({ mode: 'serial' })` — see import-conflict.spec.js,
  // which shares one pattern slug and one directory on disk.
  fullyParallel: true,
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
  },
  webServer: [
    {
      command: '../.venv/bin/python ../cli/regin.py serve --port 8321',
      port: 8321,
      reuseExistingServer: true,
      timeout: 10000,
    },
    {
      command: 'npx vite --port 5173',
      port: 5173,
      reuseExistingServer: true,
      timeout: 10000,
    },
  ],
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'mobile', testMatch: /responsive\.spec\.js$/, use: { ...devices['iPhone SE'] } },
    { name: 'tablet', testMatch: /responsive\.spec\.js$/, use: { ...devices['iPad (gen 7)'] } },
  ],
})
