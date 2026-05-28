import { test, expect } from './auth-fixture.js'

test.describe('Repo Detail', () => {
  test('loads repo detail page', async ({ page }) => {
    await page.goto('/')
    const repoLink = page.locator('table.tbl').first().locator('tbody tr a').first()
    const repoName = await repoLink.textContent()
    await repoLink.click()
    await expect(page.locator('h1')).toHaveText(repoName)
    await expect(page.locator('code')).not.toHaveCount(0)
  })

  test('shows branches table', async ({ page }) => {
    await page.goto('/')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await expect(page.locator('h2', { hasText: 'Branches' })).toBeVisible()
    await expect(page.locator('table.tbl').first().locator('th', { hasText: 'Branch' })).toBeVisible()
  })

  test('shows patterns table', async ({ page }) => {
    await page.goto('/')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await expect(page.locator('h2', { hasText: /Patterns/ })).toBeVisible()
  })
})

test.describe('Triggers', () => {
  test('loads triggers page', async ({ page }) => {
    await page.goto('/trace/triggers')
    await expect(page.locator('h1').first()).toHaveText('Trace')
    await expect(page.locator('text=Rule Trigger Log').first()).toBeVisible()
  })

  test('shows recent events section', async ({ page }) => {
    await page.goto('/trace/triggers')
    await expect(page.locator('.card').first()).toBeVisible()
  })
})

test.describe('Experiments', () => {
  test('loads experiments list page', async ({ page }) => {
    await page.goto('/experiments')
    await expect(page.locator('h1')).toHaveText('Concealment Experiments')
    await expect(page.locator('text=/experiment.*defined/')).toBeVisible()
  })
})
