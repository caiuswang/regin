/**
 * Feature #155 R5 — list order and landing on a new row: Features default to
 * newest first and remember the sort the reader picks; a row created from
 * the Tasks or Features list is scrolled to and marked `data-just-created`
 * until the reader next moves the list, and a row the filters hide is
 * offered through the flash's Show.
 *
 * Live against the suite's server: every claim is about rows it wrote.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const token = () => `r5${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

async function api(request, method, path, data) {
  const { token: jwt } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${jwt}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

const newFeature = (request, title) => api(request, 'post', '/api/features', { title })

test('test_features_default_sort_newest', async ({ page, request }) => {
  const tag = token()
  const older = await newFeature(request, `${tag} older`)
  const newer = await newFeature(request, `${tag} newer`)
  expect(newer.id).toBeGreaterThan(older.id)

  await page.goto(`/features?q=${tag}`)
  await expect(page.getByTestId('features-sort')).toContainText('Newest')
  const rows = page.locator('[data-testid^="feature-row-"]')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toHaveAttribute('data-testid', `feature-row-${newer.id}`)
})

test('the Lifecycle sort is offered and asks the server for stage', async ({ page }) => {
  await page.goto('/features')
  await page.getByTestId('features-sort').click()
  const asked = page.waitForRequest(r => /\/api\/features\?/.test(r.url())
    && new URL(r.url()).searchParams.get('sort') === 'stage:asc')
  await page.getByRole('option', { name: 'Lifecycle' }).click()
  await asked
  await expect(page).toHaveURL(/sort=stage(%3A|:)asc/)
})

test('test_features_sort_is_remembered', async ({ page }) => {
  await page.goto('/features')
  await page.getByTestId('features-sort').click()
  await page.getByRole('option', { name: 'Rounds ↓' }).click()
  await expect(page).toHaveURL(/sort=rounds(%3A|:)desc/)

  await page.goto('/tasks')
  await page.goto('/features')
  await expect(page).not.toHaveURL(/sort=/)
  await expect(page.getByTestId('features-sort')).toContainText('Rounds')

  // A sort the URL states wins over the remembered one, and does not replace it.
  await page.goto('/features?sort=cost:desc')
  await expect(page.getByTestId('features-sort')).toContainText('Cost')
  await page.goto('/features')
  await expect(page.getByTestId('features-sort')).toContainText('Rounds')
})

async function composeTask(page, title) {
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await page.getByTestId('task-title').fill(title)
  await page.getByTestId('task-submit').click()
}

test('test_created_task_row_is_highlighted', async ({ page }) => {
  const tag = token()
  await page.goto(`/tasks?group=none&q=${tag}`)
  await expect(page.getByTestId('list-search')).toHaveValue(tag)

  await composeTask(page, `${tag} landed`)
  const marked = page.locator('[data-just-created]')
  await expect(marked).toHaveCount(1)
  await expect(marked).toContainText(`${tag} landed`)
  await expect(marked).toBeInViewport()

  // Moving the list — here its sort direction — is what clears the mark.
  await page.getByTestId('list-sort-dir').click()
  await expect(page.getByText(`${tag} landed`)).toBeVisible()
  await expect(marked).toHaveCount(0)
})

test('the default next-step reading marks the created row too', async ({ page }) => {
  const tag = token()
  await page.goto(`/tasks?q=${tag}`)
  await expect(page.getByTestId('list-search')).toHaveValue(tag)

  await composeTask(page, `${tag} next-step`)
  const marked = page.locator('[data-just-created]')
  await expect(marked).toHaveCount(1)
  await expect(marked).toContainText(`${tag} next-step`)
  await expect(marked).toBeInViewport()
})

test('a task the filters hide is offered through Show', async ({ page }) => {
  const tag = token()
  // The default grouping, so Show must leave it for the flat newest-first list.
  await page.goto(`/tasks?q=${tag}-nomatch`)
  await expect(page.getByTestId('list-search')).toHaveValue(`${tag}-nomatch`)

  await composeTask(page, `${tag} hidden`)
  await expect(page.getByTestId('flash')).toContainText('hidden by current filters')
  await expect(page.locator('[data-just-created]')).toHaveCount(0)

  await page.getByTestId('flash-link').click()
  await expect(page).not.toHaveURL(/q=/)
  await expect(page).toHaveURL(/group=none/)
  const marked = page.locator('[data-just-created]')
  await expect(marked).toContainText(`${tag} hidden`)
  await expect(marked).toBeInViewport()

  // Show is a way to one row, not a grouping the reader chose to keep.
  expect(await page.evaluate(() => localStorage.getItem('regin.tasks.group'))).toBeNull()
  await page.goto('/tasks')
  await expect(page).not.toHaveURL(/group=none/)
})

test('a feature declared from the list lands in the list, marked', async ({ page }) => {
  const tag = token()
  await page.goto(`/features?q=${tag}`)
  await page.getByTestId('declare-feature').click()
  await page.getByTestId('declare-title').fill(`${tag} declared`)
  await page.getByTestId('declare-submit').click()

  await expect(page).toHaveURL(/\/features\?/)
  const marked = page.locator('[data-just-created]')
  await expect(marked).toContainText(`${tag} declared`)
  await expect(marked).toBeInViewport()

  await page.getByTestId('feature-stage-building').click()
  await expect(page.locator('[data-just-created]')).toHaveCount(0)
})
