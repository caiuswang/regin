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
  const left = page.getByTestId(`graph-connect-left-${a}`)
  await expect(left).toBeVisible()
  await left.click()

  const picker = page.locator('.task-edge-picker')
  await expect(picker).toBeVisible()
  await expect(picker).toContainText(`#${a} waits for`)
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
  const right = page.getByTestId(`graph-connect-right-${a}`)
  await right.focus()
  await page.keyboard.press('Enter')

  const picker = page.locator('.task-edge-picker')
  await expect(picker).toBeVisible()
  await expect(picker).toContainText(`Waits for #${a}`)
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

/** Press a node's handle and release over another node, Drawio-style. */
async function dragHandle(page, from, side, to) {
  await page.getByTestId(`graph-node-${from}`).hover()
  const handle = await page.getByTestId(`graph-connect-${side}-${from}`).boundingBox()
  const target = await page.getByTestId(`graph-node-${to}`).boundingBox()
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2)
  await page.mouse.down()
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 8 })
  await expect(page.getByTestId('graph-drag-line')).toHaveCount(1)
  await expect(page.getByTestId(`graph-node-${to}`)).toHaveClass(/is-drop-target/)
  await page.mouse.up()
}

test('test_graph_drag_left_handle_adds_dependency', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openGraph(page, feature, a)

  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${a}/deps`))
  await dragHandle(page, a, 'left', b)
  expect((await sent).postDataJSON().depends_on).toEqual([{ id: b, kind: 'requires' }])
  await expect(page.getByTestId(`graph-edge-${a}-${b}`)).toHaveCount(1)
  await expect(page.locator('.task-edge-picker')).toHaveCount(0)
  await expect(page.getByTestId('graph-drag-line')).toHaveCount(0)
  expect(await depsOf(page, headers, a)).toEqual([b])
})

test('test_graph_drag_right_handle_adds_dependent', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a, b } = await seed(page, headers)
  await openGraph(page, feature, a)

  await page.getByRole('button', { name: 'Zoom out' }).click()
  await page.getByRole('button', { name: 'Zoom out' }).click()
  const sent = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(`/api/tasks/${b}/deps`))
  await dragHandle(page, a, 'right', b)
  expect((await sent).postDataJSON().depends_on).toEqual([{ id: a, kind: 'requires' }])
  await expect(page.getByTestId(`graph-edge-${b}-${a}`)).toHaveCount(1)
  expect(await depsOf(page, headers, b)).toEqual([a])
  expect(await depsOf(page, headers, a)).toEqual([])
})

test('test_graph_drag_to_empty_canvas_is_a_noop', async ({ page }) => {
  const headers = await authed(page)
  const { feature, a } = await seed(page, headers)
  await openGraph(page, feature, a)

  let puts = 0
  page.on('request', r => { if (r.method() === 'PUT') puts += 1 })
  await page.getByTestId(`graph-node-${a}`).hover()
  const handle = await page.getByTestId(`graph-connect-left-${a}`).boundingBox()
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2)
  await page.mouse.down()
  await page.mouse.move(handle.x - 60, handle.y + 200, { steps: 6 })
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('graph-drag-line')).toHaveCount(0)
  await expect(page.locator('.task-edge-picker')).toHaveCount(0)
  expect(puts).toBe(0)
})
