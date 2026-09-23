/**
 * The Projects tab: Linear's project list and panel, read off regin's rows.
 *
 * Asserted against a scratch DB that may hold no registered repo at all, so
 * every assertion leans on the one row that always exists — "No project" —
 * and on the refusal paths, which need no data.
 */
import { test, expect } from './auth-fixture.js'

test('the Projects tab lists every scope and ends with "No project"', async ({ page }) => {
  await page.goto('/projects')
  await expect(page.getByTestId('pipeline-tab-projects')).toHaveClass(/is-active/)
  await expect(page.getByTestId('projects-table')).toBeVisible()
  const rows = page.locator('[data-testid^="project-row-"]')
  await expect(rows.last()).toHaveAttribute('data-testid', 'project-row-none')
  // The list IS every project, so the scope selector has nothing to narrow.
  await expect(page.getByTestId('pipeline-project')).toHaveCount(0)
})

test('a row opens its panel, and the panel links into the scoped tabs', async ({ page }) => {
  await page.goto('/projects')
  await page.getByTestId('project-row-none').getByRole('link').click()
  await expect(page).toHaveURL(/\/projects\/none$/)
  await expect(page.getByTestId('project-name')).toHaveText('No project')
  await expect(page.getByTestId('project-progress')).toBeVisible()
  await expect(page.getByTestId('project-tile-features')).toHaveAttribute('href', '/features?project=none')
  await page.getByTestId('project-tile-tasks').click()
  await expect(page).toHaveURL(/\/tasks\?project=none$/)
  await expect(page.getByTestId('pipeline-tab-tasks')).toHaveClass(/is-active/)
})

test('an unknown project says so instead of rendering an empty panel', async ({ page }) => {
  await page.goto('/projects/424242')
  await expect(page.getByTestId('project-error')).toContainText('no project')
  await expect(page.getByTestId('project-name')).toHaveCount(0)
})

test('the Pipeline rail entry stays lit on the projects pages', async ({ page }) => {
  const rail = page.locator('nav.sb-nav .sb-item', { hasText: 'Pipeline' })
  for (const path of ['/projects', '/projects/none']) {
    await page.goto(path)
    await page.waitForSelector('.app-shell')
    await expect(rail).toHaveClass(/is-active/)
  }
})

test('passing through Projects keeps the scope the other tabs were left under', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin.pipeline.project', 'none'))
  await page.goto('/projects')
  await expect(page.getByTestId('projects-table')).toBeVisible()
  await page.getByTestId('pipeline-tab-tasks').click()
  await expect(page).toHaveURL(/\/tasks\?project=none$/)
})

test('a scope that arrived only in the URL survives a trip through Projects', async ({ page }) => {
  await page.goto('/projects/none')
  await expect(page.getByTestId('project-name')).toHaveText('No project')
  // A panel carries its own project on the strip.
  await expect(page.getByTestId('pipeline-tab-tasks')).toHaveAttribute('href', /\?project=none$/)
  await page.getByTestId('project-tile-tasks').click()
  await expect(page).toHaveURL(/\/tasks\?project=none$/)
  await page.getByTestId('pipeline-tab-projects').click()
  await expect(page).toHaveURL(/\/projects$/)
  await page.getByTestId('pipeline-tab-tasks').click()
  await expect(page).toHaveURL(/\/tasks\?project=none$/)
})
