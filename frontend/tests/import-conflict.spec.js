import { test, expect } from './auth-fixture.js'
import fs from 'fs'
import path from 'path'
import os from 'os'
import { execSync } from 'child_process'
import { API_BASE } from './helpers/api-base.js'
import { PATTERNS_DIR } from './helpers/fixtures.js'

/**
 * Build a minimal skill folder for import testing. Returns { root, skillDir }
 * where skillDir is the directory a user would select in the folder picker.
 */
function buildSkillFolder(slug) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'regin-test-bundle-'))
  const skillDir = path.join(root, slug)
  fs.mkdirSync(skillDir, { recursive: true })
  const skillMd = `---\nname: ${slug}\ndescription: Test bundle for E2E\n---\n# Test\n\nBody.\n`
  fs.writeFileSync(path.join(skillDir, 'SKILL.md'), skillMd)
  return { root, skillDir }
}

test.describe('Pattern import conflict', () => {
  // These three share one slug, one server-side pattern row, and one
  // directory under the server's patterns dir — and beforeEach deletes
  // all of it. Run them concurrently and they delete each other's fixture
  // mid-test, so the ordering the file already relies on is stated rather
  // than inherited from the runner's default.
  test.describe.configure({ mode: 'serial' })

  const testSlug = 'e2e-import-conflict'
  let built

  test.beforeAll(async () => {
    built = buildSkillFolder(testSlug)
  })

  test.afterAll(async () => {
    if (built?.root && fs.existsSync(built.root)) {
      fs.rmSync(built.root, { recursive: true, force: true })
    }
  })

  test.beforeEach(async ({ page }) => {
    // Clean up any leftover test pattern before each test.
    // First navigate so localStorage is available (addInitScript has
    // already injected the token, but localStorage needs a real origin).
    await page.goto('/patterns')
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {}

    const list = await page.request.get(`${API_BASE}/api/patterns`, { headers }).then(r => r.json())
    for (const slug of [testSlug, `${testSlug}-renamed`]) {
      const existing = list.docs.find(d => d.slug === slug)
      if (existing) {
        await page.request.post(`${API_BASE}/api/patterns/${slug}/delete`, { headers })
      }
    }
    // Also clean up disk directories in case DB and disk are out of sync.
    for (const slug of [testSlug, `${testSlug}-renamed`]) {
      try {
        execSync(`rm -rf "${PATTERNS_DIR}/${slug}"`)
      } catch (_) {}
    }
  })

  test('import without conflict succeeds', async ({ page }) => {
    await page.goto('/patterns')
    await expect(page.locator('h1')).toHaveText('Patterns')

    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser.setFiles(built.skillDir)

    // Should show success flash and navigate
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await expect(page).toHaveURL(new RegExp(`/patterns/${testSlug}`))
  })

  test('import conflict shows overwrite/rename dialog', async ({ page }) => {
    // First import succeeds
    await page.goto('/patterns')
    const [fileChooser1] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser1.setFiles(built.skillDir)
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await page.goto('/patterns')

    // Second import with same slug triggers conflict dialog
    const [fileChooser2] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser2.setFiles(built.skillDir)

    // Conflict dialog should appear
    await expect(page.locator('.modal-overlay')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('h2', { hasText: 'Pattern already exists' })).toBeVisible()
    await expect(page.locator('button', { hasText: 'Overwrite' })).toBeVisible()
    await expect(page.locator('button', { hasText: 'Rename' })).toBeVisible()
    await expect(page.locator('button', { hasText: 'Cancel' })).toBeVisible()

    // Cancel should close dialog
    await page.locator('button', { hasText: 'Cancel' }).click()
    await expect(page.locator('.modal-overlay')).not.toBeVisible()
    // Still on patterns list
    await expect(page).toHaveURL(/\/patterns$/)
  })

  test('overwrite replaces existing pattern', async ({ page }) => {
    // First import
    await page.goto('/patterns')
    const [fileChooser1] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser1.setFiles(built.skillDir)
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await page.goto('/patterns')

    // Second import → overwrite
    const [fileChooser2] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser2.setFiles(built.skillDir)

    await expect(page.locator('.modal-overlay')).toBeVisible({ timeout: 5000 })
    await page.locator('button', { hasText: 'Overwrite' }).click()

    // Should succeed and navigate
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await expect(page).toHaveURL(new RegExp(`/patterns/${testSlug}`))
  })

  test('rename imports with different slug', async ({ page }) => {
    // First import
    await page.goto('/patterns')
    const [fileChooser1] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser1.setFiles(built.skillDir)
    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await page.goto('/patterns')

    // Second import → rename
    const newSlug = `${testSlug}-renamed`
    const [fileChooser2] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Browse for a skill folder to import' }).click(),
    ])
    await fileChooser2.setFiles(built.skillDir)

    await expect(page.locator('.modal-overlay')).toBeVisible({ timeout: 5000 })
    await page.locator('button', { hasText: 'Rename' }).click()

    // Should show slug input
    await expect(page.locator('input[placeholder*="e.g."]')).toBeVisible()
    await page.locator('input[placeholder*="e.g."]').fill(newSlug)
    await page.locator('button', { hasText: 'Import as new name' }).click()

    // Debug: take screenshot if nothing happens
    try {
      await expect(page.locator('.alert-success, .alert-error')).toBeVisible({ timeout: 5000 })
    } catch (e) {
      await page.screenshot({ path: '/tmp/rename-debug.png' })
      throw e
    }
    const errorFlash = page.locator('.alert-error')
    if (await errorFlash.isVisible().catch(() => false)) {
      const msg = await errorFlash.textContent()
      throw new Error(`Import failed with error: ${msg}`)
    }
    await expect(page).toHaveURL(new RegExp(`/patterns/${newSlug}`))
  })
})
