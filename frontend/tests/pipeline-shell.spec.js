/**
 * Tasks, features and defects behind one sidebar entry (feature #70).
 *
 * The three were three rail items, which is how the loop they form — queued,
 * claimed done, came back — read as three unrelated tables. The switcher is
 * now a tab strip above the three, mirroring /trace.
 *
 * Asserted against the rendered rail and the real navigations: the failure
 * these cover is an entry that no longer highlights, or a URL that moved.
 */
import { test, expect } from './auth-fixture.js'

const TABS = [
  { testid: 'pipeline-tab-tasks', path: '/tasks' },
  { testid: 'pipeline-tab-features', path: '/features' },
  { testid: 'pipeline-tab-defects', path: '/defects' },
]

test('one rail entry, not three', async ({ page }) => {
  await page.goto('/tasks')
  await page.waitForSelector('.app-shell')
  const labels = await page.$$eval('nav.sb-nav .sb-item', (items) =>
    items.map((i) => (i.getAttribute('aria-label') || i.innerText || '').trim()))
  expect(labels).toContain('Pipeline')
  for (const gone of ['Tasks', 'Features', 'Defects']) {
    expect(labels).not.toContain(gone)
  }
})

test('the tab strip switches between the three, and the URLs do not move',
  async ({ page }) => {
    await page.goto('/tasks')
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    for (const tab of TABS) {
      await page.getByTestId(tab.testid).click()
      await expect(page).toHaveURL(new RegExp(`${tab.path}$`))
      await expect(page.getByTestId(tab.testid)).toHaveClass(/is-active/)
    }
  })

test('a deep link to any of the three lands on its own tab', async ({ page }) => {
  for (const tab of TABS) {
    await page.goto(tab.path)
    await expect(page.getByTestId(tab.testid)).toHaveClass(/is-active/)
    await expect(page.locator('.page-title')).toHaveText('Pipeline')
  }
})

test('the Pipeline entry stays lit on every one of the three, and on a detail page',
  async ({ page }) => {
    const rail = page.locator('nav.sb-nav .sb-item', { hasText: 'Pipeline' })
    for (const path of ['/tasks', '/features', '/defects', '/tasks/1', '/features/1']) {
      await page.goto(path)
      await page.waitForSelector('.app-shell')
      await expect(rail).toHaveClass(/is-active/)
    }
  })

test("each tab's own controls ride up into the shell header", async ({ page }) => {
  await page.goto('/tasks')
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toBeVisible()
  await page.getByTestId('pipeline-tab-features').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toBeVisible()
  // One tab's action must not outlive it.
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toHaveCount(0)
  await page.getByTestId('pipeline-tab-defects').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toHaveCount(0)
})

test('the shell renders no console error on a cold load of any tab',
  async ({ page }) => {
    const errors = []
    page.on('console', (m) => {
      if (m.type() === 'error' || /Vue warn/.test(m.text())) errors.push(m.text())
    })
    for (const tab of TABS) {
      await page.goto(tab.path)
      await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    }
    expect(errors.join('\n---\n')).toBe('')
  })
