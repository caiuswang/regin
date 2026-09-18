import { test, expect } from './auth-fixture.js'

test.describe('Settings page', () => {
  test('loads settings with form inputs', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('h1')).toHaveText('Settings')
    // The Configuration section is the default active pane.
    await expect(page.locator('h2', { hasText: 'Configuration' })).toBeVisible()
    await expect(page.locator('table.tbl').first()).toBeVisible()
    await expect(page.locator('button', { hasText: 'Save settings' })).toBeVisible()
  })

  test('shows hook manager with status', async ({ page }) => {
    await page.goto('/settings')
    // Hook installer state moved behind the "Hook Installers" sidebar section.
    await page.locator('button', { hasText: 'Hook Installers' }).click()
    await expect(page.locator('h2', { hasText: 'Hook Installers' })).toBeVisible()
    await expect(page.locator('.card', { hasText: 'Claude Code' }).first()).toBeVisible()
    await expect(page.locator('.badge-green, .badge-gray').last()).toBeVisible()
  })

  test('grading section hosts the aspects/judge-provider config', async ({ page }) => {
    // GradeAspectsConfig moved here from the retired /grades page.
    await page.goto('/settings?section=grading')
    await expect(page.locator('button', { hasText: 'Grader settings' })).toBeVisible()
  })
})

test.describe('Triggers mutations', () => {
  test('shows reset button', async ({ page }) => {
    // The trigger-log reset moved into Settings → Rule Triggers.
    await page.goto('/settings')
    await page.locator('button', { hasText: 'Rule Triggers' }).click()
    await expect(page.locator('button', { hasText: /Reset/ })).toBeVisible()
  })

  test('filter chips work', async ({ page }) => {
    await page.goto('/rules/triggers')
    const noisyChip = page.locator('.filter-chip', { hasText: 'Noisy' })
    await noisyChip.click()
    await expect(page).toHaveURL(/status=noisy/)
  })
})

test.describe('Confirm dialog', () => {
  test('confirm dialog appears on dangerous action', async ({ page }) => {
    // The trigger-log reset relocated to the raw trace-triggers view.
    await page.goto('/trace/triggers/raw')
    await page.locator('button', { hasText: /Reset/ }).click()
    await expect(page.locator('.fixed.inset-0')).toBeVisible()
    await expect(page.locator('h3', { hasText: 'Reset triggers' })).toBeVisible()
    await page.locator('button', { hasText: 'Cancel' }).click()
    await expect(page.locator('.fixed.inset-0')).not.toBeVisible()
  })
})

test.describe('Pattern delete', () => {
  test('delete button shows and confirm dialog works', async ({ page }) => {
    // Bootstrap our own pattern so the test is portable to fresh installs.
    await page.goto('/patterns')
    const slug = 'e2e-delete-test-' + Date.now()
    const createResult = await page.evaluate(async (slug) => {
      const token = localStorage.getItem('regin_auth_token')
      const res = await fetch('/api/patterns/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ title: 'E2E Delete Test', slug, description: 'Temp pattern' }),
      })
      return res.json()
    }, slug)
    expect(createResult.ok).toBeTruthy()

    await page.goto(`/patterns/${slug}`)
    await expect(page.locator('button', { hasText: 'Delete pattern' })).toBeVisible()

    await page.locator('button', { hasText: 'Delete pattern' }).click()
    await expect(page.locator('.fixed.inset-0')).toBeVisible()
    await expect(page.locator('h3', { hasText: 'Delete pattern' })).toBeVisible()

    await page.locator('.fixed.inset-0 button', { hasText: 'Delete pattern' }).click()

    await expect(page).toHaveURL(/\/patterns$/, { timeout: 5000 })
    await expect(page.locator('.alert-success')).toContainText('Deleted')
  })
})

test.describe('Flash messages', () => {
  test('flash message appears after saving settings', async ({ page }) => {
    await page.goto('/settings')
    await page.locator('button', { hasText: 'Save settings' }).click()
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('.alert-success')).toContainText('Settings saved')
  })
})
