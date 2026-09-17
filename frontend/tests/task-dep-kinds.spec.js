import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

// Serial: both tests read the shared graph, and the second edits an edge the
// first would otherwise be drawing.
test.describe.configure({ mode: 'serial' })

function mark() {
  const info = test.info()
  return `depkind${info.workerIndex}x${info.retry}`
}

// The fixture puts the token in localStorage, which `page.request` does not
// read — these calls carry the Bearer header themselves.
async function authed(page) {
  const resp = await page.request.post(`${API_BASE}/api/auth/login`,
    { data: TEST_USER })
  const { token } = await resp.json()
  return { Authorization: `Bearer ${token}` }
}

async function capture(page, headers, title) {
  const resp = await page.request.post(`${API_BASE}/api/tasks`,
    { headers, data: { title, body: mark() } })
  expect(resp.status()).toBe(201)
  return (await resp.json()).task.id
}

async function setDeps(page, headers, taskId, dependsOn) {
  const resp = await page.request.put(`${API_BASE}/api/tasks/${taskId}/deps`,
    { headers, data: { depends_on: dependsOn } })
  expect(resp.status()).toBe(200)
  return resp.json()
}

async function openGraph(page, rootId) {
  await page.goto('/tasks')
  await page.getByTestId('tasks-view-tabs').getByText('Graph').click()
  await expect(page.getByTestId(`task-graph-node-${rootId}`)).toBeVisible()
}

test.describe('/tasks — a dependency edge carries a kind', () => {
  test('after edges draw dashed and requires edges draw solid',
    async ({ page }) => {
      const headers = await authed(page)
      const strict = await capture(page, headers, `${mark()} strict dep`)
      const loose = await capture(page, headers, `${mark()} loose dep`)
      const dependent = await capture(page, headers, `${mark()} dependent`)
      await setDeps(page, headers, dependent,
        [strict, { id: loose, kind: 'after' }])

      await openGraph(page, dependent)

      const solid = page.getByTestId(`task-graph-edge-${dependent}-${strict}`)
      const dashed = page.getByTestId(`task-graph-edge-${dependent}-${loose}`)
      await expect(solid).toHaveAttribute('data-kind', 'requires')
      await expect(dashed).toHaveAttribute('data-kind', 'after')
      // The class is what carries the dash; asserting it rather than the
      // computed stroke keeps the test off the token's exact value.
      await expect(solid).not.toHaveClass(/tg-edge-after/)
      await expect(dashed).toHaveClass(/tg-edge-after/)
    })

  test('the deps editor saves an ordering-only edge', async ({ page }) => {
    const headers = await authed(page)
    const dep = await capture(page, headers, `${mark()} unrelated work`)
    const dependent = await capture(page, headers, `${mark()} queued behind`)

    await page.goto(`/tasks/${dependent}`)
    await page.getByTestId('task-detail-deps').click()
    await page.getByTestId(`task-dep-${dep}`).click()

    const kind = page.getByTestId(`task-dep-kind-${dep}`)
    await expect(kind).toBeVisible()
    await kind.click()
    await page.getByRole('option', { name: 'just after' }).click()
    await page.getByTestId('task-deps-save').click()

    await expect(page.getByTestId('task-deps-save')).toBeHidden()
    const graph = await (await page.request.get(
      `${API_BASE}/api/tasks/graph?root=${dependent}&depth=1`,
      { headers })).json()
    expect(graph.edges).toContainEqual(
      { task_id: dependent, depends_on_id: dep, kind: 'after' })
  })
})
