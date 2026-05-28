import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
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
