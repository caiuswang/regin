/**
 * A live tracker's header, and a relative link (CAI-269).
 *
 * The payload is stubbed rather than seeded: every assertion here is about
 * what the header does with `stage_live`, and binding that to a live database
 * would make a rendering regression look like a missing task.
 */
import { test, expect } from './auth-fixture.js'

function ledger(overrides = {}) {
  return {
    key: 'regin:42',
    stage: {
      feature_key: 'regin:42', stage: 'building',
      blurb: 'being built for the first time',
      rounds: 1, open_defects: 0, escapes: 0,
    },
    stage_synced_at: '2026-09-13 05:40:00',
    stage_live: true,
    cost: { usd: 3.2, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 60_000, gaps: {} },
    tokens: { input: 10, output: 20 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0, gaps: {} },
    coverage: {
      declared_sessions: 1, all_sessions: 10,
      declared_usd: 3.2, all_usd: 100,
    },
    ...overrides,
  }
}

async function stub(page, body) {
  await page.route(/\/api\/features\/[^/?]+$/, (route) =>
    route.fulfill({ json: body }))
}

test('a live feature header renders live and no refresh control',
  async ({ page }) => {
    await stub(page, ledger())
    await page.goto('/features/regin%3A42')

    await expect(page.getByTestId('stage-live')).toContainText('live')
    // The question `as of T` answers does not arise for a tracker whose
    // issues live in this database, so the slot is taken, not shared.
    await expect(page.getByText(/as of /)).toHaveCount(0)
    await expect(
      page.getByRole('button', { name: /Refresh the stage/ })).toHaveCount(0)
  })

test('a linear feature header is unchanged', async ({ page }) => {
  await stub(page, ledger({
    key: 'CAI-242', stage_live: false,
    stage: {
      feature_key: 'CAI-242', stage: 'shipped',
      blurb: 'completed and never walked back',
      rounds: 1, open_defects: 0, escapes: 0,
    },
  }))
  await page.goto('/features/CAI-242')

  await expect(page.getByTestId('stage-live')).toHaveCount(0)
  await expect(page.getByText(/as of /)).toBeVisible()
  await expect(
    page.getByRole('button', { name: /Refresh the stage/ })).toBeVisible()
})

test('a relative link url renders as a router-link with no external glyph',
  async ({ page }) => {
    // Keyed on the URL's SHAPE, not on the provider: the renderer stays
    // vendor-free, and any future local tracker inherits the behaviour.
    await page.route(/\/api\/tasks\/7$/, (route) => route.fulfill({
      json: { task: {
        id: 7, title: 'a run', body: null, status: 'idea', source: 'web',
        tags: [], links: [], params: {}, verify_cmd: null, autorun: false,
        project_id: null, priority: 'none', priority_rank: 4,
        parent_id: null, assignee: null, assignee_name: null, rounds: 1,
        created_at: '2026-09-01', updated_at: '2026-09-01',
        external_id: null, created_by: null,
      } },
    }))
    await page.route(/\/api\/tasks\/7\/runs$/, (route) =>
      route.fulfill({ json: { runs: [] } }))
    await page.route(/\/api\/tasks\/7\/comments$/, (route) =>
      route.fulfill({ json: { threads: [] } }))
    await page.route(/\/api\/tasks\?parent_id=7/, (route) =>
      route.fulfill({ json: { tasks: [] } }))
    await page.route(/\/api\/tasks\/7\/links$/, (route) => route.fulfill({
      json: { links: [{
        id: 1, task_id: 7, provider: 'regin', external_key: 'regin:9',
        url: '/tasks/9', issue: { title: 'the feature', state: 'idea',
                                  state_category: 'backlog', labels: [],
                                  extra: {} },
        stage: { stage: 'spec', blurb: 'specified, not started', rounds: 1,
                 open_defects: 0, escapes: 0 },
        synced_at: '2026-09-13 05:40:00', stage_live: true,
        sync_error: null, created_at: '2026-09-01',
      }] },
    }))
    await page.goto('/tasks/7')

    const key = page.getByRole('link', { name: 'regin:9' })
    await expect(key).toBeVisible()
    await expect(key).toHaveAttribute('href', '/tasks/9')
    await expect(key).not.toHaveAttribute('target', '_blank')
    await expect(page.getByTestId('task-link-live-1')).toContainText('live')
    await expect(page.getByTestId('task-link-refresh-1')).toHaveCount(0)
  })
