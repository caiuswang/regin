import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

function mark() {
  const info = test.info()
  return `depedit${info.workerIndex}x${info.retry}x${Date.now()}`
}

async function authed(page) {
  const resp = await page.request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await resp.json()
  return { Authorization: `Bearer ${token}` }
}

async function post(page, headers, path, data) {
  const resp = await page.request.post(`${API_BASE}${path}`, { headers, data })
  expect(resp.ok(), `${path}: ${resp.status()}`).toBeTruthy()
  return resp.json()
}

async function capture(page, headers, title, featureId = null) {
  const data = { title, body: 'deps editor spec' }
  if (featureId != null) data.feature_id = featureId
  return (await post(page, headers, '/api/tasks', data)).task.id
}

async function openEditor(page, taskId) {
  await page.goto(`/tasks/${taskId}`)
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-deps').click()
  await expect(page.getByTestId('task-deps-preview')).toBeVisible()
}

test('test_deps_editor_groups_current_feature_first', async ({ page }) => {
  const headers = await authed(page)
  const m = mark()
  const own = (await post(page, headers, '/api/features', { title: `${m} own` })).id
  const other = (await post(page, headers, '/api/features', { title: `${m} other` })).id
  const task = await capture(page, headers, `${m} subject`, own)
  const sibA = await capture(page, headers, `${m} sibling a`, own)
  const sibB = await capture(page, headers, `${m} sibling b`, own)
  const away = await capture(page, headers, `${m} elsewhere`, other)
  const loose = await capture(page, headers, `${m} loose`)

  await openEditor(page, task)
  await page.getByTestId('task-deps-filter').fill(m)
  await page.setViewportSize({ width: 1280, height: 900 })
  const box = await page.locator('.task-deps-dialog').boundingBox()
  expect(Math.round(box.width)).toBe(864)

  const groups = page.locator('[data-testid^="task-deps-group-"]')
  await expect(groups).toHaveCount(3)
  await expect(groups.nth(0)).toHaveAttribute('data-testid', 'task-deps-group-own')
  await expect(groups.nth(0)).toContainText(`This feature (#${own}`)
  await expect(groups.nth(1)).toHaveAttribute('data-testid', 'task-deps-group-other')
  await expect(groups.nth(2)).toHaveAttribute('data-testid', 'task-deps-group-loose')
  await expect(page.getByTestId('task-deps-group-other')).toContainText(`#${away}`)
  await expect(page.getByTestId('task-deps-group-loose')).toContainText(`#${loose}`)

  const ownRows = page.getByTestId('task-deps-group-own').locator('[data-testid^="task-deps-row-"]')
  await expect(ownRows.first()).toHaveAttribute('data-testid', `task-deps-row-${sibA}`)
  // Picking the later sibling pins it above the earlier one.
  await page.getByTestId(`task-dep-${sibB}`).click()
  await expect(ownRows.first()).toHaveAttribute('data-testid', `task-deps-row-${sibB}`)

  // A filter that matches nothing in a group hides that group's header.
  await page.getByTestId('task-deps-filter').fill(`${m} loose`)
  await expect(page.getByTestId('task-deps-group-other')).toHaveCount(0)
  await expect(page.getByTestId('task-deps-group-loose')).toBeVisible()
})

test('test_deps_editor_preview_toggles_selection', async ({ page }) => {
  const headers = await authed(page)
  const m = mark()
  const feat = (await post(page, headers, '/api/features', { title: `${m} feat` })).id
  const task = await capture(page, headers, `${m} subject`, feat)
  const dep = await capture(page, headers, `${m} candidate`, feat)
  const dependent = await capture(page, headers, `${m} downstream`, feat)
  const put = await page.request.put(`${API_BASE}/api/tasks/${dependent}/deps`,
    { headers, data: { depends_on: [task] } })
  expect(put.status()).toBe(200)

  await openEditor(page, task)
  const preview = page.getByTestId('task-deps-preview')
  const node = preview.getByTestId(`deps-preview-node-${dep}`)
  await expect(node).toHaveAttribute('data-role', 'ghost')
  await expect(preview.getByTestId(`deps-preview-node-${dependent}`)).toHaveAttribute('data-role', 'dependent')

  // Click an unselected node: it becomes a picked dependency in the list.
  await node.click()
  await expect(page.getByTestId(`task-dep-${dep}`).locator('input')).toBeChecked()
  const edge = preview.getByTestId(`deps-preview-edge-${task}-${dep}`)
  await expect(edge).toHaveAttribute('data-kind', 'requires')
  await expect(edge).toContainText('requires')

  // Switching the kind in the list relabels the preview edge.
  await page.getByTestId(`task-dep-kind-${dep}`).getByRole('radio', { name: 'just after' }).click()
  await expect(edge).toHaveAttribute('data-kind', 'after')
  await expect(edge).toContainText('after')

  // A direct dependent cannot be picked.
  await preview.getByTestId(`deps-preview-node-${dependent}`).click()
  await expect(page.getByTestId('deps-preview-hint')).toHaveText('A task cannot depend on its dependent.')
  await expect(page.getByTestId(`task-dep-${dependent}`).locator('input')).not.toBeChecked()

  // Clicking the picked edge removes the dependency.
  await edge.locator('line').first().dispatchEvent('click')
  await expect(page.getByTestId(`task-dep-${dep}`).locator('input')).not.toBeChecked()
  await expect(edge).toHaveCount(0)
})

test('test_deps_editor_saves_kind', async ({ page }) => {
  const headers = await authed(page)
  const m = mark()
  const task = await capture(page, headers, `${m} subject`)
  const dep = await capture(page, headers, `${m} ordering only`)

  await openEditor(page, task)
  await page.getByTestId('task-deps-filter').fill(m)
  await page.getByTestId(`task-dep-${dep}`).click()
  await page.getByTestId(`task-dep-kind-${dep}`).getByRole('radio', { name: 'just after' }).click()

  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${task}/deps`))
  await page.getByTestId('task-deps-save').click()
  expect((await sent).postDataJSON()).toEqual({ depends_on: [{ id: dep, kind: 'after' }] })
  await expect(page.getByTestId('task-deps-save')).toBeHidden()
})
