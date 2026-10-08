/**
 * Feature #144 R2 — Feature ledger Tasks card shows dependency hints and
 * read-only jumps to the Tasks page. The ledger and task list are stubbed so
 * the assertions stay about rendering, not about the shape of live data.
 */
import { test, expect } from './auth-fixture.js'

const FID = 244
const AID = 801
const BID = 802
const CID = 803

function ledger(overrides = {}) {
  return {
    id: FID, title: 'Feature with chained tasks', origin: 'native', project_id: null,
    external_ref: null, external_url: null, sync_error: null,
    body: '## Spec\n\nDo the thing.',
    stage: { stage: 'building', blurb: 'in build', rounds: 1, claimed_at: '2026-10-01T00:00:00Z',
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    verbs: [],
    ship_check: { allowed: false, open_tasks: [], open_defects: [], warnings: [] },
    next_step: { kind: 'none', label: 'Keep building', target: `/features/${FID}#feature-tasks` },
    slice_tasks: 0,
    ...overrides,
  }
}

const TASKS = [
  {
    id: AID, title: 'First task', status: 'armed', priority: 'none', body: '',
    kind: 'task', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    tags: [], links: [], project_id: null, feature_id: FID, run_count: 0,
    fixes_defects: [], blocked_by: [], waits_on: null,
  },
  {
    id: BID, title: 'Second task', status: 'idea', priority: 'none', body: '',
    kind: 'task', created_at: '2026-10-01T01:00:00Z', updated_at: '2026-10-01T01:00:00Z',
    tags: [], links: [], project_id: null, feature_id: FID, run_count: 0,
    fixes_defects: [], blocked_by: [AID], waits_on: { id: AID, status: 'armed' },
  },
  {
    id: CID, title: 'Third task', status: 'idea', priority: 'none', body: '',
    kind: 'task', created_at: '2026-10-01T02:00:00Z', updated_at: '2026-10-01T02:00:00Z',
    tags: [], links: [], project_id: null, feature_id: FID, run_count: 0,
    fixes_defects: [], blocked_by: [AID, BID], waits_on: { id: AID, status: 'armed' },
  },
]

function listBody(items) {
  return {
    items,
    pagination: { total: items.length, page: 0, size: 200, has_next: false, has_prev: false },
    population: items.length,
    applied: { sort: 'created:asc' },
    extras: { counts: { by_status: {}, by_priority: {}, by_project: {} } },
  }
}

async function stubFeatureTasks(page, { tasks = TASKS } = {}) {
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: ledger() }))
  await page.route(/\/api\/tasks\?feature_id=244.*$/, route => route.fulfill({ json: listBody(tasks) }))
  await page.route(/\/api\/tasks\/graph\?feature_id=244.*$/, route => route.fulfill({ json: {
    nodes: tasks.map(t => ({
      id: t.id, feature_id: FID, title: t.title, status: t.status,
      blocked: t.blocked_by.length > 0, blocked_by: t.blocked_by,
      runnable: false, action: null, waits_on: t.waits_on?.id ?? null,
    })),
    edges: tasks.flatMap(t => t.blocked_by.map(dep => ({
      task_id: t.id, depends_on_id: dep, kind: 'requires',
    }))),
    root: null, truncated: false, total_nodes: tasks.length, node_cap: 0,
  } }))
}

test('Feature task rows show what they wait on', async ({ page }) => {
  await stubFeatureTasks(page)
  await page.goto(`/features/${FID}`)

  const list = page.getByTestId('feature-tasks-list')
  await expect(list).toBeVisible()

  const rowA = list.locator(`[data-testid="feature-task-waits-${AID}"]`)
  await expect(rowA).toHaveCount(0)

  const rowB = list.locator(`[data-testid="feature-task-waits-${BID}"]`)
  await expect(rowB).toHaveText(`waits on #${AID}`)
  await expect(rowB).toHaveAttribute('href', `/tasks/${AID}`)

  const rowC = list.locator(`[data-testid="feature-task-waits-${CID}"]`)
  await expect(rowC).toHaveText(`waits on #${AID}+1`)
  await expect(rowC).toHaveAttribute('href', `/tasks/${AID}`)
})

test('Feature tasks jump to the Tasks page scoped to the feature', async ({ page }) => {
  await stubFeatureTasks(page)
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('feature-tasks-list')).toBeVisible()

  await page.getByTestId('feature-tasks-open-graph').click()
  await expect(page).toHaveURL(`/tasks?feature_id=${FID}&view=graph&open=0`)

  await page.goBack()
  await page.getByTestId('feature-tasks-open-list').click()
  await expect(page).toHaveURL(`/tasks?feature_id=${FID}&view=list`)
})
