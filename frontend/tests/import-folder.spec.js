import { test, expect } from './auth-fixture.js'
import fs from 'fs'
import path from 'path'
import os from 'os'
import { execSync } from 'child_process'

/**
 * Build a multi-file skill folder on disk for folder-import testing.
 * Returns the path to the skill folder (the dir the user would pick).
 */
function buildSkillFolder(slug) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'regin-skill-folder-'))
  const skillDir = path.join(root, slug)
  fs.mkdirSync(path.join(skillDir, 'scripts'), { recursive: true })
  fs.mkdirSync(path.join(skillDir, 'references'), { recursive: true })
  fs.writeFileSync(
    path.join(skillDir, 'SKILL.md'),
    `---\nname: ${slug}\ndescription: Folder import E2E\n---\n# Folder import\n\nBody.\n`,
  )
  fs.writeFileSync(path.join(skillDir, 'scripts', 'run.sh'), '#!/bin/sh\necho hi\n')
  fs.writeFileSync(path.join(skillDir, 'references', 'notes.md'), '# notes\n')
  return { root, skillDir }
}

const PATTERNS_DIR = path.join(os.homedir(), '.local/share/regin/patterns')

test.describe('Pattern folder import', () => {
  const testSlug = 'e2e-import-folder'
  let built

  test.beforeAll(() => {
    built = buildSkillFolder(testSlug)
  })

  test.afterAll(() => {
    if (built?.root && fs.existsSync(built.root)) {
      fs.rmSync(built.root, { recursive: true, force: true })
    }
  })

  test.beforeEach(async ({ page }) => {
    await page.goto('/patterns')
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const list = await page.request
      .get('http://localhost:8321/api/patterns', { headers })
      .then((r) => r.json())
    if (list.docs?.find((d) => d.slug === testSlug)) {
      await page.request.post(`http://localhost:8321/api/patterns/${testSlug}/delete`, { headers })
    }
    try { execSync(`rm -rf "${path.join(PATTERNS_DIR, testSlug)}"`) } catch (_) {}
  })

  test('selecting a skill folder imports SKILL.md plus its scripts/references', async ({ page }) => {
    await page.goto('/patterns')
    await expect(page.locator('h1')).toHaveText('Patterns')

    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.locator('button', { hasText: 'Import skill' }).click(),
    ])
    await fileChooser.setFiles(built.skillDir)

    await expect(page.locator('.alert-success')).toBeVisible({ timeout: 5000 })
    await expect(page).toHaveURL(new RegExp(`/patterns/${testSlug}`))

    // The auxiliary files must have landed alongside the rewritten SKILL.md.
    const dir = path.join(PATTERNS_DIR, testSlug)
    expect(fs.existsSync(path.join(dir, 'SKILL.md'))).toBe(true)
    expect(fs.existsSync(path.join(dir, 'scripts', 'run.sh'))).toBe(true)
    expect(fs.existsSync(path.join(dir, 'references', 'notes.md'))).toBe(true)
  })
})
