/**
 * F180 S5 — the feature page previews its task graph above the spec.
 *
 * Every read is stubbed, including `/api/tasks/graph`: the preview and the
 * Tasks tab's Graph view read that one endpoint, so one stub serves both and
 * a node or edge that differs between them is a component difference.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const FID = 1805
const LONG_SPEC = Array.from({ length: 400 }, (_, i) => `line ${i}`).join('\n\n')

function ledger(overrides = {}) {
  return {
    id: FID, title: 'Graph preview', origin: 'native', body: '# Spec\n\nShort.',
    external_ref: null, external_url: null, sync_error: null,
    project_id: null, project_name: null,
    stage: { stage: 'building', blurb: 'in progress', rounds: 1,
             claimed_at: null, open_defects: 1, escapes: 0 },
    synced_at: null, stage_live: true, verbs: [], ship_check: null,
    next_step: null, slice_tasks: 0, images: [],
    cost: { usd: 1, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: {},
    sessions: { rows: [], gaps: {} },
    defects: { open: 1, total: 1, escapes: 0, gaps: {} },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    ...overrides,
  }
}

function node(id, status, extra = {}) {
  return {
    id, feature_id: FID, title: `step ${id}`, status, kind: 'build',
    blocked: false, blocked_by: [], runnable: status === 'armed',
    action: status === 'armed' ? 'run' : null, waits_on: null,
    fixes_defects: [], fixes_open_defects: [], ...extra,
  }
}

const GRAPH = {
  nodes: [
    node(11, 'done'),
    node(12, 'armed'),
    node(13, 'armed', { kind: 'fix', fixes_defects: [77], fixes_open_defects: [77] }),
  ],
  edges: [
    { task_id: 12, depends_on_id: 11, kind: 'requires', blocking: false },
    { task_id: 13, depends_on_id: 12, kind: 'requires', blocking: true },
  ],
  lanes: [{ feature_id: FID, title: 'Graph preview', done: 1, total: 3 }],
  chains: 1, lanes_hidden: 0, open_only: false, truncated: false,
  total_nodes: 3, node_cap: 150,
}

const EMPTY_GRAPH = {
  nodes: [], edges: [], lanes: [], chains: 0, lanes_hidden: 0,
  open_only: false, truncated: false, total_nodes: 0, node_cap: 150,
}

// The app shell's own stream handshakes are POSTs too; they are not the card's.
const SHELL = /\/api\/(auth\/stream-ticket|notifications\/stream)/

async function stub(page, { graph = GRAPH, writes = [], body = ledger() } = {}) {
  page.on('request', (req) => {
    const url = req.url()
    if (url.includes('/api/') && !SHELL.test(url) && req.method() !== 'GET') writes.push(`${req.method()} ${url}`)
  })
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: body }))
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({
    json: { sliceable: false, refusal: null, tail_missing: false, rows: [] },
  }))
  await page.route(new RegExp(`/api/features/${FID}/branches$`), route => route.fulfill({
    json: { integration: { name: '', source: null, exists: false, merged_into_default: null }, runs: [] },
  }))
  await page.route('**/api/tasks/graph?**', route => route.fulfill({ json: graph.current || graph }))
  await page.route('**/api/tasks?**', route => route.fulfill({
    json: { items: graph.nodes, next_cursor: null },
  }))
  await page.route('**/api/defects?feature_id=*', route => route.fulfill({
    json: { items: [], scope: 'feature', pagination: { total: 0 } },
  }))
  return writes
}

async function asViewer(page) {
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (!raw) return
    const user = JSON.parse(raw)
    user.role = 'viewer'
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
  })
}

const preview = page => page.locator('#feature-graph')

/** Node ids and edge keys the graph inside `scope` drew. */
function drawn(scope) {
  return scope.evaluate((el) => ({
    nodes: [...el.querySelectorAll('[data-node-id]')].map(n => Number(n.dataset.nodeId)).sort(),
    edges: [...el.querySelectorAll('[data-testid^="graph-edge-"]')]
      .map(e => e.dataset.testid)
      .filter(t => /^graph-edge-\d+-\d+$/.test(t))
      .sort(),
  }))
}

test('test_feature_page_previews_task_graph_above_spec', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await stub(page, { body: ledger({ body: LONG_SPEC }) })
  await page.goto(`/features/${FID}`)

  await expect(preview(page).locator('[data-node-id]')).toHaveCount(3)
  const tops = await page.evaluate(() => ({
    graph: document.getElementById('feature-graph').getBoundingClientRect().top,
    spec: document.getElementById('feature-spec').getBoundingClientRect().top,
  }))
  expect(tops.graph).toBeLessThan(900)
  expect(tops.graph).toBeLessThan(tops.spec)
  await expect(page.getByTestId('feature-section-nav')).toContainText('Graph')

  const onPage = await drawn(preview(page))
  expect(onPage.nodes).toEqual([11, 12, 13])
  expect(onPage.edges).toHaveLength(2)

  await page.goto(`/tasks?feature_id=${FID}&view=graph&open=0`)
  const full = page.getByTestId('task-lane-graph')
  await expect(full.locator('[data-node-id]')).toHaveCount(3)
  expect(await drawn(full)).toEqual(onPage)
})

test('test_graph_node_marks_fix_task', async ({ page }) => {
  await stub(page)
  await page.goto(`/features/${FID}`)
  const badge = preview(page).getByTestId('graph-node-fixes-13')
  await expect(badge).toHaveText('fixes #77')
  await expect(badge).toHaveAttribute('data-open', '')
  await expect(preview(page).getByTestId('graph-node-fixes-12')).toHaveCount(0)

  await page.goto(`/tasks?feature_id=${FID}&view=graph&open=0`)
  await expect(page.getByTestId('task-lane-graph').getByTestId('graph-node-fixes-13'))
    .toHaveText('fixes #77')
})

test('test_graph_preview_is_read_only', async ({ page }) => {
  const writes = await stub(page)
  await page.goto(`/features/${FID}`)
  const card = preview(page)
  await expect(card.locator('[data-node-id]')).toHaveCount(3)

  await expect(card.locator('[data-testid^="task-primary-action"], .tln-action')).toHaveCount(0)
  await expect(card.locator('[data-testid^="graph-connect-"]')).toHaveCount(0)
  await expect(card.locator('[data-testid^="graph-edge-hit-"], [data-testid^="graph-edge-remove-"]'))
    .toHaveCount(0)
  await expect(card.getByTestId('graph-hint')).toHaveCount(0)

  const box = await card.getByTestId('graph-node-12').boundingBox()
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width + 120, box.y + box.height / 2, { steps: 5 })
  await page.mouse.up()
  await card.getByTestId('graph-node-12').click({ button: 'right' })
  await expect(card.getByTestId('graph-drag-line')).toHaveCount(0)
  expect(writes).toEqual([])
})

test('test_graph_preview_node_opens_task', async ({ page }) => {
  await stub(page)
  await page.goto(`/features/${FID}`)
  await preview(page).getByTestId('graph-node-12').click()
  await expect(page).toHaveURL(/\/tasks\/12$/)
})

test('test_graph_preview_open_full_graph_link', async ({ page }) => {
  await stub(page)
  await page.goto(`/features/${FID}`)
  await page.getByTestId('feature-graph-open').click()
  await expect(page).toHaveURL(new RegExp(`/tasks\\?feature_id=${FID}&view=graph&open=0$`))
})

test('test_graph_preview_hidden_without_tasks', async ({ page }) => {
  await stub(page, { graph: EMPTY_GRAPH })
  await page.goto(`/features/${FID}`)
  await expect(page.locator('#feature-spec')).toBeVisible()
  await expect(page.getByTestId('feature-section-nav')).toContainText('Spec')
  await expect(page.locator('#feature-graph')).toHaveCount(0)
  await expect(page.getByTestId('feature-graph-preview')).toBeHidden()
  await expect(page.getByTestId('feature-section-nav')).not.toContainText('Graph')
})

test('test_viewer_can_read_task_graph', async ({ page }) => {
  await asViewer(page)
  await stub(page)
  await page.goto(`/features/${FID}`)
  await expect(preview(page)).toBeVisible()
  await expect(preview(page).locator('[data-node-id]')).toHaveCount(3)
})

test('test_graph_preview_follows_task_writes', async ({ page }) => {
  const graph = { current: EMPTY_GRAPH }
  await stub(page, { graph })
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('feature-section-nav')).toContainText('Spec')
  await expect(page.locator('#feature-graph')).toHaveCount(0)

  // Any task write on the server reaches the page as a pipeline event; the
  // page re-reads its task set, and the preview is part of that set.
  graph.current = GRAPH
  const login = await page.request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await login.json()
  const made = await page.request.post(`${API_BASE}/api/tasks`, {
    data: { title: 'a write elsewhere' }, headers: { Authorization: `Bearer ${token}` },
  })
  expect(made.ok()).toBeTruthy()

  await expect(preview(page).locator('[data-node-id]')).toHaveCount(3)
  await expect(page.getByTestId('feature-section-nav')).toContainText('Graph')
})
