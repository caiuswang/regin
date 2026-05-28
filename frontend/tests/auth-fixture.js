/**
 * Shared auth fixture for E2E tests.
 *
 * Uses the pre-created claude-admin account (created via CLI before tests).
 * Tests run as admin so all mutations are permitted.
 */
import { test as base } from '@playwright/test'

const API_BASE = 'http://localhost:8321'
const TEST_USER = { username: 'claude-admin', password: 'claude-admin-2026' }

export const test = base.extend({
  page: async ({ page }, use) => {
    // Login with the pre-created admin account
    const loginRes = await page.request.post(`${API_BASE}/api/auth/login`, {
      data: TEST_USER,
    })
    const { token, user } = await loginRes.json()

    // Inject token into localStorage before any page navigation
    await page.addInitScript(({ token, user }) => {
      localStorage.setItem('regin_auth_token', token)
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }, { token, user })

    await use(page)
  },
})

export { expect } from '@playwright/test'
