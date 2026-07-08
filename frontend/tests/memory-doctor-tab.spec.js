import { test, expect } from './auth-fixture'

// Verifies the Doctor page was folded into the Memory view as a sub-tab and the
// redundant header controls were removed.
test('Memory doctor is a sub-tab; header Doctor + Run-reflect buttons are gone', async ({ page }) => {
  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push(String(e)))

  await page.goto('/memory')
  await expect(page.getByRole('heading', { name: 'Memory', level: 1 })).toBeVisible()

  // The pinned header (its action row) must no longer carry a Doctor link or a
  // Run-reflect button — scope to the header so the in-tab reflect control
  // (rendered inside the Doctor panel) can't produce a false pass/fail.
  const header = page.locator('div.sticky').first()
  await expect(header.getByRole('button', { name: 'Run reflect' })).toHaveCount(0)
  await expect(header.getByRole('button', { name: 'Doctor' })).toHaveCount(0)
  await expect(header.getByRole('link', { name: 'Doctor' })).toHaveCount(0)
  await expect(header.getByRole('button', { name: 'Include test data' })).toBeVisible()

  // A Doctor tab now sits alongside the other Memory tabs (Reka renders it as
  // role=tab, not a button — so the removed header Doctor <button> can't alias it).
  const doctorTab = page.getByRole('tab', { name: 'Doctor' })
  await expect(doctorTab).toHaveCount(1)
  await doctorTab.click()

  // Selecting it deep-links (tab=doctor) and renders the health panel.
  await expect(page).toHaveURL(/[?&]tab=doctor/)
  await expect(page.getByRole('heading', { name: 'Memory doctor' })).toBeVisible()
  await expect(page.getByText('Reflect pipeline')).toBeVisible()
  await expect(page.getByText('Tier', { exact: true })).toBeVisible()
  await expect(page.getByText('dream')).toBeVisible()

  expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
})

test('direct /memory?tab=doctor opens the Doctor tab; old /memory/doctor route is gone', async ({ page }) => {
  await page.goto('/memory?tab=doctor')
  await expect(page.getByRole('heading', { name: 'Memory doctor' })).toBeVisible()

  // The standalone route was removed; the SPA falls back (no dedicated doctor
  // page heading, and no crash).
  await page.goto('/memory/doctor')
  await expect(page.getByRole('heading', { name: 'Memory doctor' })).toHaveCount(0)
})
