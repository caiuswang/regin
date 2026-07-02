import { test, expect } from './auth-fixture.js'
import fs from 'fs'
import path from 'path'
import os from 'os'
import { execSync } from 'child_process'

/**
 * Build a scan root with one `<slug>/SKILL.md` skill folder per slug — the
 * parent dir the Batch-import modal scans.
 */
function buildScanRoot(slugs) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'regin-selective-'))
  for (const slug of slugs) {
    const d = path.join(root, slug)
    fs.mkdirSync(d, { recursive: true })
    fs.writeFileSync(
      path.join(d, 'SKILL.md'),
      `---\nname: ${slug}\ndescription: Selective import E2E\n---\n# Body\n\nText.\n`,
    )
  }
  return root
}

const PATTERNS_DIR = path.join(os.homedir(), '.local/share/regin/patterns')

test.describe('Selective folder import', () => {
  const keepSlug = 'e2e-sel-keep'
  const skipSlug = 'e2e-sel-skip'
  let root

  test.beforeAll(() => { root = buildScanRoot([keepSlug, skipSlug]) })

  // Never leave E2E-invented rows behind (test-data isolation).
  test.afterAll(() => {
    if (root && fs.existsSync(root)) fs.rmSync(root, { recursive: true, force: true })
  })

  async function cleanup(page) {
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const list = await page.request
      .get('http://localhost:8321/api/patterns', { headers })
      .then((r) => r.json())
    for (const slug of [keepSlug, skipSlug]) {
      if (list.docs?.find((d) => d.slug === slug)) {
        await page.request.post(`http://localhost:8321/api/patterns/${slug}/delete`, { headers })
      }
      try { execSync(`rm -rf "${path.join(PATTERNS_DIR, slug)}"`) } catch (_) {}
    }
  }

  test.beforeEach(async ({ page }) => { await page.goto('/patterns'); await cleanup(page) })
  test.afterEach(async ({ page }) => { await cleanup(page) })

  test('unchecking a candidate imports only the checked skills', async ({ page }) => {
    const errors = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

    await page.goto('/patterns')
    await page.getByRole('button', { name: 'Batch import' }).click()

    await page.getByLabel('Folder path to scan').fill(root)
    await page.getByRole('button', { name: 'Scan' }).click()

    // Both candidates appear; every importable one defaults to checked (opt-out).
    const keepRow = page.locator('tr', { hasText: keepSlug })
    const skipRow = page.locator('tr', { hasText: skipSlug })
    await expect(keepRow.locator('input[type=checkbox]')).toBeChecked()
    await expect(skipRow.locator('input[type=checkbox]')).toBeChecked()

    // Deselect one → the import button count reflects the selection.
    await skipRow.locator('input[type=checkbox]').uncheck()
    const importBtn = page.getByRole('button', { name: /Import 1 skill\b/ })
    await expect(importBtn).toBeEnabled()

    await importBtn.click()

    // Success flash + result table both persist after the parent list reload.
    await expect(page.getByText(/Imported 1 skill/i)).toBeVisible({ timeout: 5000 })
    await expect(page.locator('th', { hasText: 'Note' })).toBeVisible()

    // On disk: only the checked skill imported; the unchecked one was untouched.
    expect(fs.existsSync(path.join(PATTERNS_DIR, keepSlug, 'SKILL.md'))).toBe(true)
    expect(fs.existsSync(path.join(PATTERNS_DIR, skipSlug))).toBe(false)
    expect(errors).toEqual([])
  })

  test('deselecting all disables the import button', async ({ page }) => {
    await page.goto('/patterns')
    await page.getByRole('button', { name: 'Batch import' }).click()
    await page.getByLabel('Folder path to scan').fill(root)
    await page.getByRole('button', { name: 'Scan' }).click()

    // Master select-all checkbox lives in the candidate table header.
    const headerBox = page.locator('thead input[type=checkbox]')
    await expect(headerBox).toBeChecked()
    await headerBox.uncheck()

    await expect(page.locator('tbody input[type=checkbox]:checked')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Import 0 skills/ })).toBeDisabled()
  })
})
