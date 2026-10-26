import { test, expect } from './auth-fixture.js'

test.describe('F182 — home is the Pipeline', () => {
  test('test_root_lands_on_overview', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/overview(\?.*)?$/)
    await expect(page.getByTestId('overview')).toBeVisible()
    await expect(page.locator('h1', { hasText: /^Dashboard$/ })).toHaveCount(0)
  })

  test('test_root_redirect_keeps_query', async ({ page }) => {
    await page.goto('/?project=3')
    await expect(page).toHaveURL(/\/overview\?project=3$/)
  })

  test('test_brand_link_opens_pipeline', async ({ page }) => {
    await page.goto('/patterns')
    await page.getByRole('link', { name: 'regin — Pipeline' }).click()
    await expect(page).toHaveURL(/\/overview(\?.*)?$/)
  })

  test('test_non_admin_trace_redirects_to_overview', async ({ page }) => {
    await page.addInitScript(() => {
      const user = JSON.parse(localStorage.getItem('regin_auth_user') || '{}')
      localStorage.setItem('regin_auth_user', JSON.stringify({ ...user, role: 'viewer' }))
    })
    await page.goto('/trace/sessions')
    await expect(page).toHaveURL(/\/overview(\?.*)?$/)
  })

  test('test_not_found_links_back_to_pipeline', async ({ page }) => {
    await page.goto('/no-such-page')
    const back = page.getByRole('link', { name: '← Back to the Pipeline' })
    await expect(back).toBeVisible()
    await expect(back).toHaveAttribute('href', '/')
  })
})
