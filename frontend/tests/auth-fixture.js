/**
 * Shared auth fixture for E2E tests.
 *
 * The admin account is seeded into the suite's scratch DB by
 * `scripts/e2e-server.mjs`. Credentials come from `e2e-env.js` rather than a
 * local copy so the seed and the login cannot drift into a silent 401.
 */
import { test as base } from '@playwright/test'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

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
