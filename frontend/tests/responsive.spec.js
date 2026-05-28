import { test, expect } from './auth-fixture.js'

test.describe('Responsive layout', () => {
  test('mobile shows hamburger button and hides inline nav links', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'mobile-viewport-only assertion')
    await page.goto('/')

    const hamburger = page.locator('nav button[aria-label="Open navigation"]')
    await expect(hamburger).toBeVisible()

    await expect(page.locator('nav .nav-link', { hasText: 'Patterns' })).toBeHidden()
  })

  test('mobile hamburger opens drawer with nav links', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, 'mobile-viewport-only assertion')
    await page.goto('/')

    await page.locator('nav button[aria-label="Open navigation"]').click()

    const drawer = page.locator('.p-drawer')
    await expect(drawer).toBeVisible()
    for (const label of ['Patterns', 'Skills', 'Rules', 'Trace', 'Settings']) {
      await expect(drawer.locator('.nav-link', { hasText: label })).toBeVisible()
    }
  })

  test('tablet shows inline nav links, no hamburger', async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width < 768, 'tablet+-viewport-only assertion')
    await page.goto('/')

    await expect(page.locator('nav button[aria-label="Open navigation"]')).toBeHidden()
    await expect(page.locator('nav .nav-link', { hasText: 'Patterns' })).toBeVisible()
  })
})
