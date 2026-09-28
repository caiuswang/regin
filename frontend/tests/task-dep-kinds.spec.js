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

async function openGraph(page, nodeId) {
  await page.goto('/tasks?view=graph')
  await expect(page.getByTestId(`graph-node-${nodeId}`)).toBeVisible()
}

test.describe('/tasks — a dependency edge carries a kind', () => {
  test('the lane graph draws both kinds, and an unmet one of either blocks',
    async ({ page }) => {
      const headers = await authed(page)
      const strict = await capture(page, headers, `${mark()} strict dep`)
      const loose = await capture(page, headers, `${mark()} loose dep`)
      const dependent = await capture(page, headers, `${mark()} dependent`)
      await setDeps(page, headers, dependent,
        [strict, { id: loose, kind: 'after' }])

      await openGraph(page, dependent)

      const requires = page.getByTestId(`graph-edge-${dependent}-${strict}`)
      const after = page.getByTestId(`graph-edge-${dependent}-${loose}`)
      await expect(requires).toHaveAttribute('data-kind', 'requires')
      await expect(after).toHaveAttribute('data-kind', 'after')
      // Neither dependency has settled, so both still hold the task back.
      await expect(requires).toHaveAttribute('data-blocking', '')
      await expect(after).toHaveAttribute('data-blocking', '')
    })

  test('the deps editor saves an ordering-only edge', async ({ page }) => {
    const headers = await authed(page)
    const dep = await capture(page, headers, `${mark()} unrelated work`)
    const dependent = await capture(page, headers, `${mark()} queued behind`)

    await page.goto(`/tasks/${dependent}`)
    await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
    await page.getByTestId('task-verb-deps').click()
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
