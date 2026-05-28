import { test, expect } from './auth-fixture.js'

test.describe('Topics Workspace', () => {
  test('renders wiki and proposals workspaces', async ({ page }) => {
    await page.goto('/')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await page.locator('a.btn', { hasText: 'Topics' }).click()

    await expect(page.locator('h1')).toHaveText('Topics Workspace')
    await expect(page.getByRole('button', { name: /Approved/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Proposals/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Approved Topics', exact: true })).toBeVisible()

    await page.getByRole('button', { name: /Proposals/ }).click()
    await expect(page).toHaveURL(/tab=proposals/)
    await expect(page.getByRole('heading', { name: 'Proposal Runs', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Draft Topics', exact: true })).toBeVisible()
  })
})
