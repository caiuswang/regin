import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

function mark() {
  const info = test.info()
  return `graphedit${info.workerIndex}x${info.retry}x${Date.now()}`
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

/** A feature holding tasks A and B, so the graph can be scoped to just them. */
async function seed(page, headers, { linked = false } = {}) {
  const m = mark()
  const feature = (await post(page, headers, '/api/features', { title: `${m} feature` })).id
  const a = (await post(page, headers, '/api/tasks', { title: `${m} A`, feature_id: feature })).task.id
  const b = (await post(page, headers, '/api/tasks', { title: `${m} B`, feature_id: feature })).task.id
  if (linked) {
    const resp = await page.request.put(`${API_BASE}/api/tasks/${a}/deps`,
      { headers, data: { depends_on: [b] } })
    expect(resp.status()).toBe(200)
  }
  return { feature, a, b }
}

async function depsOf(page, headers, id) {
  const graph = await (await page.request.get(
    `${API_BASE}/api/tasks/graph?root=${id}&depth=1`, { headers })).json()
  return graph.edges.filter(e => e.task_id === id).map(e => e.depends_on_id)
}

async function openEditMode(page, feature, nodeId) {
  await page.goto(`/tasks?view=graph&feature_id=${feature}`)
  await expect(page.getByTestId(`graph-node-${nodeId}`)).toBeVisible()
  await page.getByTestId('graph-edit-toggle').click()
  await expect(page.getByTestId('graph-edit-hint'))
    .toContainText('Pick a source task, then pick the task it should wait for.')
}

test('test_graph_edit_adds_dependency', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openEditMode(page, feature, a)

  await page.getByTestId(`graph-pick-${a}`).click()
  await expect(page.getByTestId(`graph-node-${a}`)).toHaveClass(/is-source/)
  await expect(page.getByTestId('graph-edit-kind').getByRole('radio', { name: 'needs it' }))
    .toHaveAttribute('aria-checked', 'true')
  await page.getByTestId(`graph-pick-${b}`).click()

  const pending = page.getByTestId(`graph-pending-edge-${a}-${b}`)
  await expect(pending).toHaveAttribute('data-kind', 'requires')
  await expect(pending).toHaveClass(/is-pending/)
  await expect(page.getByTestId('graph-edit-count')).toHaveText('1 task changed')

  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${a}/deps`))
  await page.getByTestId('graph-edit-save').click()
  expect((await sent).postDataJSON().depends_on).toContainEqual({ id: b, kind: 'requires' })
  // A same-row edge is a flat path with a zero-height box, which Playwright
  // calls hidden — presence is the check.
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveCount(1)
  await expect(page.getByTestId('graph-edit-savebar')).toHaveCount(0)
  expect(await depsOf(page, headers, a)).toEqual([b])
})

test('test_graph_edit_removes_edge', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers, { linked: true })
  await openEditMode(page, feature, a)

  await page.getByTestId(`graph-edge-hit-${a}-${b}`).dispatchEvent('click')
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveAttribute('data-removed', '')
  await page.getByTestId('graph-edit-save').click()
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveCount(0)
  expect(await depsOf(page, headers, a)).toEqual([])
})

test('test_graph_edit_escape_and_discard', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openEditMode(page, feature, a)

  await page.getByTestId(`graph-pick-${a}`).click()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId(`graph-node-${a}`)).not.toHaveClass(/is-source/)

  await page.getByTestId(`graph-pick-${a}`).click()
  await page.getByTestId(`graph-pick-${b}`).click()
  // Leaving edit mode with pending edits would drop them silently.
  await expect(page.getByTestId('graph-edit-toggle')).toBeDisabled()
  await page.getByTestId('graph-edit-discard').click()
  await expect(page.getByTestId(`graph-pending-edge-${a}-${b}`)).toHaveCount(0)
  await expect(page.getByTestId('graph-edit-hint')).toHaveCount(0)
  await expect(page.getByTestId(`graph-pick-${a}`)).toHaveCount(0)
  expect(await depsOf(page, headers, a)).toEqual([])
})

test('test_graph_edit_viewer_hidden', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a } = await seed(page, headers)
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (!raw) return
    const user = JSON.parse(raw)
    user.role = 'viewer'
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
  })
  await page.goto(`/tasks?view=graph&feature_id=${feature}`)
  await expect(page.getByTestId(`graph-node-${a}`)).toBeVisible()
  await expect(page.getByTestId('graph-edit-toggle')).toHaveCount(0)
})
