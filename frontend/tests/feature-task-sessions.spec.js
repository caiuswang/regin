/**
 * A task row carries the sessions its runs produced, and a session whose title
 * is only a skill line reads as its task. Stubbed, like feature-ledger.spec.js:
 * every assertion is about rendering one payload, on both cards at once.
 */
import { test, expect } from './auth-fixture.js'

const SESSIONS = [
  { trace_id: 'aaaaaaaa11', short_trace: 'aaaaaaaa', date: '2026-10-01',
    title: '/goal-verified-treenav', display_title: 'Build · the page',
    command: '/goal-verified-treenav', cost_usd: 1.5, provenance: 'run',
    round: null, is_primary: true, task_id: 7, task_title: 'Build · the page' },
  { trace_id: 'bbbbbbbb22', short_trace: 'bbbbbbbb', date: '2026-10-02',
    title: 'Retry after the build failed', display_title: 'Retry after the build failed',
    command: null, cost_usd: null, provenance: 'run',
    round: null, is_primary: true, task_id: 7, task_title: 'Build · the page' },
  { trace_id: 'cccccccc33', short_trace: 'cccccccc', date: '2026-10-03',
    title: 'Hand-declared spike', display_title: 'Hand-declared spike',
    command: null, cost_usd: 2.0, provenance: 'declared',
    round: 1, is_primary: true, task_id: null, task_title: null },
]

function ledger() {
  return {
    id: 135, title: 'Friendlier tasks', origin: 'native', body: 'spec',
    external_ref: null, external_url: null, sync_error: null,
    project_id: null, project_name: null, slice_tasks: 0, verbs: [],
    stage: { stage: 'building', blurb: 'being built', rounds: 1,
             claimed_at: null, open_defects: 0, escapes: 0 },
    stage_live: true,
    cost: { usd: 3.5, sessions: 3, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 60_000, gaps: {} },
    human_prompts: { prompts: 0, sessions: 0, run_sessions: 0, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: SESSIONS, gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0, gaps: {} },
    coverage: { declared_sessions: 3, all_sessions: 10, declared_usd: 3.5, all_usd: 10 },
  }
}

const TASKS = [
  { id: 7, title: 'Build · the page', status: 'doing', kind: 'build', run_count: 2 },
  { id: 8, title: 'Write spec', status: 'done', kind: 'spec', run_count: 0 },
]

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/features\/\d+$/, (route) => route.fulfill({ json: ledger() }))
  await page.route('**/api/tasks?**', (route) =>
    route.fulfill({ json: { items: TASKS, total: TASKS.length } }))
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }))
})

test('a task row summarises its sessions and expands to them', async ({ page }) => {
  await page.goto('/features/135')

  const toggle = page.getByTestId('feature-task-sessions-7')
  // One of the two has no usage: the sum is a floor, said inside the number.
  await expect(toggle).toHaveText(/2 sessions · ≥ \$1\.50/)
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByTestId('feature-task-sessions-list-7')).toHaveCount(0)
  // A task no run has touched carries no session control at all.
  await expect(page.getByTestId('feature-task-sessions-8')).toHaveCount(0)

  await toggle.click()
  const list = page.getByTestId('feature-task-sessions-list-7')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(list.locator('li')).toHaveCount(2)
  await expect(list.locator('li').nth(0)).toContainText('aaaaaaaa')
  await expect(list.locator('li').nth(0)).toContainText('/goal-verified-treenav')
  await expect(list.locator('li').nth(0)).toContainText('$1.50')
  await expect(list.locator('li').nth(1)).toContainText('Retry after the build failed')
  await expect(list.locator('li').nth(1)).toContainText('no usage')
  await expect(list.getByRole('link', { name: 'aaaaaaaa' }))
    .toHaveAttribute('href', '/trace/sessions/aaaaaaaa11')
})

test('a skill-line session reads as its task in the evidence list', async ({ page }) => {
  await page.goto('/features/135')

  const rows = page.locator('#feature-sessions .ev-row')
  await expect(rows).toHaveCount(3)
  const run = rows.nth(0)
  await expect(run.locator('.ev-title')).toHaveText('Build · the page')
  await expect(run.locator('.ev-cmd')).toHaveText('/goal-verified-treenav')
  await expect(run.getByRole('link', { name: 'task #7' })).toHaveAttribute('href', '/tasks/7')
  // A session with its own words keeps them, and wears no command chip.
  await expect(rows.nth(1).locator('.ev-title')).toHaveText('Retry after the build failed')
  await expect(rows.nth(1).locator('.ev-cmd')).toHaveCount(0)
  await expect(rows.nth(2).locator('.ev-title')).toHaveText('Hand-declared spike')
})
