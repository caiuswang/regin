import { test, expect } from './auth-fixture.js'

test.describe('Settings page', () => {
  test('loads settings with form inputs', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('h1')).toHaveText('Settings')
    await expect(page.locator('table.tbl').first()).toBeVisible()
    await expect(page.locator('button', { hasText: 'Save settings' })).toBeVisible()
    await expect(page.locator('button', { hasText: 'Rescan repositories' })).toBeVisible()
  })

  test('shows hook manager with status', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('h2', { hasText: 'Post-Edit Hooks' })).toBeVisible()
    await expect(page.locator('.card', { hasText: 'Claude Code' }).first()).toBeVisible()
    await expect(page.locator('.badge-green, .badge-gray').last()).toBeVisible()
  })
})

test.describe('Triggers mutations', () => {
  test('shows reset button', async ({ page }) => {
    await page.goto('/rules/triggers')
    await expect(page.locator('button', { hasText: /Reset/ })).toBeVisible()
  })

  test('filter chips work', async ({ page }) => {
    await page.goto('/rules/triggers')
    const triggeredChip = page.locator('.filter-chip', { hasText: 'Triggered only' })
    await triggeredChip.click()
    await expect(page).toHaveURL(/triggered=1/)
  })
})

test.describe('Experiment detail mutations', () => {
  test('loads experiment detail with action buttons', async ({ page }) => {
    const resp = await page.request.get('/api/experiments')
    const expData = await resp.json()
    if (expData.grouped.length === 0) {
      test.skip()
      return
    }
    const firstExp = expData.grouped[0][1][0]
    await page.goto(`/experiments/${firstExp.id}`)
    await expect(page.locator('h1')).toHaveText(firstExp.name)

    const activateBtn = page.locator('button', { hasText: 'Activate' })
    const deactivateBtn = page.locator('button', { hasText: 'Deactivate' })
    const hasActivate = await activateBtn.isVisible({ timeout: 1000 }).catch(() => false)
    const hasDeactivate = await deactivateBtn.isVisible({ timeout: 1000 }).catch(() => false)
    expect(hasActivate || hasDeactivate).toBeTruthy()
    await expect(page.locator('button', { hasText: 'Delete' })).toBeVisible()
  })

  test('edit form opens', async ({ page }) => {
    const resp = await page.request.get('/api/experiments')
    const expData = await resp.json()
    if (expData.grouped.length === 0) {
      test.skip()
      return
    }
    const firstExp = expData.grouped[0][1][0]
    await page.goto(`/experiments/${firstExp.id}`)
    await page.locator('summary', { hasText: 'Edit experiment' }).click()
    await expect(page.locator('button', { hasText: 'Save changes' })).toBeVisible()
  })
})

test.describe('Confirm dialog', () => {
  test('confirm dialog appears on dangerous action', async ({ page }) => {
    await page.goto('/rules/triggers')
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
  test('flash message appears after settings rescan', async ({ page }) => {
    await page.goto('/settings')
    await page.locator('button', { hasText: 'Rescan repositories' }).click()
    await page.locator('.fixed.inset-0 button', { hasText: 'Rescan repositories' }).click()
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('.alert-success')).toContainText('Scanned')
  })
})
