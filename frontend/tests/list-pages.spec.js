import { test, expect } from './auth-fixture.js'

test.describe('Navigation', () => {
  test('navbar renders logo and search', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.sidebar a.sb-brand-name', { hasText: 'regin' })).toBeVisible()
    await expect(page.locator('.sidebar button.sb-search[aria-label="Open quick search"]')).toBeVisible()
  })
})

test.describe('Dashboard', () => {
  test('loads dashboard with stat cards', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('h1')).toHaveText('Dashboard')
    await expect(page.locator('.stat-card')).not.toHaveCount(0)
  })

  test('shows repositories section', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.stat-card', { hasText: 'Repos registered' })).toBeVisible()
  })
})

test.describe('Patterns', () => {
  test('loads pattern list page', async ({ page }) => {
    await page.goto('/patterns')
    await expect(page.locator('h1')).toContainText('Patterns')
  })
})

test.describe('Rules', () => {
  test('loads rules page', async ({ page }) => {
    await page.goto('/rules')
    await expect(page.locator('h1')).toHaveText('Rules')
  })
})

test.describe('Skills', () => {
  test('loads skills page', async ({ page }) => {
    await page.goto('/skills')
    await expect(page.locator('h1')).toHaveText('Skills')
  })
})

test.describe('Tags', () => {
  test('legacy /tags redirects to patterns', async ({ page }) => {
    await page.goto('/tags')
    await expect(page).toHaveURL(/\/patterns$/)
    await expect(page.locator('h1')).toContainText('Patterns')
  })
})
