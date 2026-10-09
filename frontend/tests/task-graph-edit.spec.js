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

async function openGraph(page, feature, nodeId) {
  await page.goto(`/tasks?view=graph&feature_id=${feature}`)
  await expect(page.getByTestId(`graph-node-${nodeId}`)).toBeVisible()
}

test('test_graph_edit_adds_dependency', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openGraph(page, feature, a)

  const node = page.getByTestId(`graph-node-${a}`)
  await node.hover()
  const right = page.getByTestId(`graph-connect-right-${a}`)
  await expect(right).toBeVisible()
  await right.click()

  const picker = page.locator('.task-edge-picker')
  await expect(picker).toBeVisible()
  await expect(picker).toContainText(`Task #${a} depends on`)
  await page.getByTestId('task-edge-picker-filter').fill('B')
  await page.getByTestId(`task-edge-picker-check-${b}`).click()

  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${a}/deps`))
  await page.getByTestId('task-edge-picker-save').click()
  expect((await sent).postDataJSON().depends_on).toContainEqual({ id: b, kind: 'requires' })

  await expect(picker).toHaveCount(0)
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveCount(1)
  expect(await depsOf(page, headers, a)).toEqual([b])
})

test('test_graph_edit_adds_dependent', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openGraph(page, feature, a)

  const node = page.getByTestId(`graph-node-${a}`)
  await node.hover()
  await page.getByTestId(`graph-connect-left-${a}`).click()

  const picker = page.locator('.task-edge-picker')
  await expect(picker).toBeVisible()
  await expect(picker).toContainText(`depends on task #${a}`)
  await page.getByTestId('task-edge-picker-filter').fill('B')
  await page.getByTestId(`task-edge-picker-check-${b}`).click()

  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${b}/deps`))
  await page.getByTestId('task-edge-picker-save').click()
  expect((await sent).postDataJSON().depends_on).toContainEqual({ id: a, kind: 'requires' })
  expect(await depsOf(page, headers, b)).toEqual([a])
})

test('test_graph_edit_removes_edge', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers, { linked: true })
  await openGraph(page, feature, a)

  await page.getByTestId(`graph-edge-hit-${a}-${b}`).dispatchEvent('mouseenter')
  await page.getByTestId(`graph-edge-remove-${a}-${b}`).click()
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveCount(0)
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
  await page.getByTestId(`graph-node-${a}`).hover()
  await expect(page.getByTestId(`graph-connect-left-${a}`)).toHaveCount(0)
  await expect(page.getByTestId(`graph-connect-right-${a}`)).toHaveCount(0)
})
