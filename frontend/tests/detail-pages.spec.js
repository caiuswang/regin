import { test, expect } from './auth-fixture.js'

test.describe('Repo Detail', () => {
  test('loads repo detail page', async ({ page }) => {
    await page.goto('/repos')
    const repoLink = page.locator('table.tbl').first().locator('tbody tr a').first()
    const repoName = await repoLink.textContent()
    await repoLink.click()
    await expect(page.locator('h1')).toHaveText(repoName)
    await expect(page.locator('code')).not.toHaveCount(0)
  })

  test('shows branches table', async ({ page }) => {
    await page.goto('/repos')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await expect(page.locator('h2', { hasText: 'Branches' })).toBeVisible()
    await expect(page.locator('table.tbl').first().locator('th', { hasText: 'Branch' })).toBeVisible()
  })

  test('shows patterns table', async ({ page }) => {
    await page.goto('/repos')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await expect(page.locator('h2', { hasText: /Patterns/ })).toBeVisible()
  })
})

test.describe('Triggers', () => {
  test('loads triggers page', async ({ page }) => {
    await page.goto('/trace/triggers')
    await expect(page.locator('h1').first()).toHaveText('Trace')
    await expect(page.locator('.kpi-tile__label', { hasText: 'rules configured' })).toBeVisible()
  })

  test('shows recent events section', async ({ page }) => {
    await page.goto('/trace/triggers')
    await expect(page.locator('.trigger-toolbar__search')).toBeVisible()
  })
})

test.describe('Experiments', () => {
  test('loads experiments list page', async ({ page }) => {
    // /experiments is gated behind the experimental_conceal feature flag (off by
    // default → the router guard redirects to the dashboard). useFeatures reads
    // the flag from the /api/settings feed, so enable it there to render the page.
    await page.route('**/api/settings', async route => {
      await route.fulfill({ json: [{ key: 'experimental_conceal', value: true }] })
    })
    await page.goto('/experiments')
    await expect(page.locator('h1')).toHaveText('Concealment Experiments')
    await expect(page.locator('text=/experiment.*defined/')).toBeVisible()
  })
})
