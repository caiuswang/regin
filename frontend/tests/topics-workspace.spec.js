import { test, expect } from './auth-fixture.js'

test.describe('Topics Workspace', () => {
  test('renders wiki and proposals workspaces', async ({ page }) => {
    await page.goto('/repos')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await page.locator('a.btn', { hasText: 'Topics' }).click()

    await expect(page.locator('h1')).toHaveText('Topics Workspace')
    await expect(page.getByRole('button', { name: /Approved/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Proposals/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Approved Topic Graph', exact: true })).toBeVisible()
    // The flat table is replaced by a persistent bucket tree + filter (WikiWorkspace).
    await expect(page.getByPlaceholder('Filter pages…')).toBeVisible()

    await page.getByRole('button', { name: /Proposals/ }).click()
    await expect(page).toHaveURL(/tab=proposals/)
    await expect(page.getByRole('heading', { name: 'Proposal Operations', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Proposal Runs', exact: true })).toBeVisible()
  })
})
