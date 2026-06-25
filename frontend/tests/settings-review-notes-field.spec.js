import { test, expect } from './auth-fixture.js'

// Confirms the auto_review_notes flag is surfaced in the Settings UI's
// Topic-evolution block (the field the user reported missing).
test.describe('Settings — topic evolution', () => {
  test('exposes the Auto-write LLM review notes flag', async ({ page }) => {
    await page.goto('/settings')
    await page.getByRole('button', { name: /Topic evolution/i }).click()
    await expect(
      page.getByText('Auto-write LLM review notes on proposal runs'),
    ).toBeVisible()
  })
})
