/**
 * R5's last item: every link a Pipeline row or figure offers lands on the
 * scope it names.
 *
 * Browser-level on purpose. The source can say `to: { hash: '#…' }` and be
 * inert — `router-link` preventDefaults the native jump, this router declares
 * no `scrollBehavior`, and the page scrolls inside `.content-scroll` — so the
 * only check that can fail on a dead link is one that clicks it and looks at
 * where the page ended up.
 *
 * Stubbed, like its two neighbours: what is under test is where a link goes,
 * not what a database happens to hold.
 */
import { test, expect } from './auth-fixture.js'

function indexRow(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native', external_ref: null,
    stage: 'shipped', blurb: 'completed and never walked back', rounds: 1,
    sessions: 3, tasks: 2, cost_usd: 41.9,
    gaps: { no_usage_captured: 0 },
    defects: { total: 4, open: 1, escapes: 0 },
    last_activity: '2026-09-01 10:00:00',
    ...over,
  }
}

// Nothing to count, so nothing to link to.
const untouched = indexRow({
  id: 206, title: 'Defect records', stage: 'spec',
  sessions: 0, tasks: 0, cost_usd: null,
  defects: { total: 0, open: 0, escapes: 0 },
})

async function stubIndex(page, items) {
  await page.route(/\/api\/features(\?.*)?$/, (route) => route.fulfill({
    json: {
      items,
      pagination: { strategy: 'offset', total: items.length, page: 0,
                    size: 50, has_next: false, has_prev: false },
      total: items.length,
      coverage: { declared_sessions: 1, all_sessions: 2,
                  declared_usd: 1, all_usd: 2 },
      unattributed_defects: 0,
    },
  }))
}

function ledger(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: { stage: 'shipped', blurb: 'completed and never walked back',
             rounds: 1, claimed_at: '2026-09-13 05:40:00',
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: 41.9, sessions: 3, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 5_400_000, gaps: {} },
    tokens: {},
    sessions: {
      // Enough rows that the list starts below the fold, so "did the page
      // move" is a question with an answer.
      rows: Array.from({ length: 12 }, (_, n) => ({
        trace_id: `trace${n}0123456789`, short_trace: `trace${n}`,
        date: '2026-09-01', title: `session ${n}`,
        cost_usd: 1.5, provenance: 'declared', round: 1, is_primary: true,
      })),
      gaps: { traceless_runs: 0 },
    },
    defects: { open: 1, total: 4, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2,
                declared_usd: 1, all_usd: 2 },
    ...over,
  }
}

async function stubLedger(page, body) {
  await page.route(/\/api\/features\/[^/?]+$/, (route) =>
    route.fulfill({ json: body }))
  await page.route(/\/api\/tasks\?[^/]*feature_id=/, (route) => route.fulfill({
    json: { items: [], pagination: { has_next: false } },
  }))
  await page.route(/\/api\/defects\?[^/]*feature_id=/, (route) => route.fulfill({
    json: { items: [], scope: 'feature',
            pagination: { total: 0, page: 0, size: 50 } },
  }))
}

test('the defects tile lands on this feature, not on every defect',
  async ({ page }) => {
    await stubLedger(page, ledger())
    await page.goto('/features/242')

    await page.getByTestId('figure-defects').locator('.fig-link').click()
    await expect(page).toHaveURL(/\/defects\?feature_id=242/)
    // The scope has to be STATED, or a narrowed list wears an unnarrowed
    // heading — which is what an unscoped landing looked like.
    await expect(page.getByTestId('defects-scoped-to')).toBeVisible()
  })

test('the sessions figure moves the page to the sessions', async ({ page }) => {
  await stubLedger(page, ledger())
  await page.goto('/features/242')

  const evidence = page.locator('#feature-sessions')
  await expect(evidence).not.toBeInViewport()

  await page.getByTestId('figure-sessions').locator('.fig-link').click()
  await expect(evidence).toBeInViewport()
  // And the position is linkable, not merely reached.
  await expect(page).toHaveURL(/#feature-sessions$/)
})

// A short list at the foot of the page cannot reach the top of the pane, so
// `scrollIntoView` asks the next ancestor for the rest — and `main.content`,
// clipped but still a scroll container, used to give it, shoving the whole
// shell up off a blank slab it could not scroll back from.
test('revealing the sessions scrolls the pane and nothing around it',
  async ({ page }) => {
    const short = ledger()
    short.sessions.rows = short.sessions.rows.slice(0, 2)
    await stubLedger(page, short)
    await page.goto('/features/242#feature-sessions')

    const pane = page.locator('.content-scroll')
    await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
    const moved = await page.evaluate(() => ({
      shell: document.querySelector('main.content').scrollTop,
      window: window.scrollY,
    }))
    expect(moved).toEqual({ shell: 0, window: 0 })
    await expect(page.locator('#feature-sessions')).toBeInViewport({ ratio: 1 })
  })

test('a feature reached by its sessions count opens ON its sessions',
  async ({ page }) => {
    await stubLedger(page, ledger())
    await stubIndex(page, [indexRow()])
    await page.goto('/features');

    await page.getByTestId('feature-sessions-link-242').click()
    await expect(page).toHaveURL(/\/features\/242#feature-sessions$/)
    await expect(page.locator('#feature-sessions')).toBeInViewport()
  })

test('the task count opens the tasks it counted', async ({ page }) => {
  await stubIndex(page, [indexRow()])
  const asked = []
  await page.route(/\/api\/tasks(\/counts)?\?/, (route) => {
    asked.push(new URL(route.request().url()).searchParams)
    return route.fulfill({ json: {
      items: [], pagination: { has_next: false },
      total: 2, by_status: {}, by_priority: {}, by_project: {},
    } })
  })

  await page.goto('/features')
  await page.getByTestId('feature-tasks-link-242').click()

  await expect(page).toHaveURL(/\/tasks\?feature_id=242/)
  await expect(page.getByTestId('tasks-scoped-to')).toBeVisible()
  expect(asked.some(q => q.get('feature_id') === '242')).toBe(true)
  // Arriving by a link wins over a filter left on from yesterday: the count
  // clicked was the whole of the feature's tasks.
  expect(asked.every(q => !q.get('status'))).toBe(true)

  // And back out again, to the whole backlog.
  await page.getByTestId('tasks-scoped-to').getByText('every task').click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByTestId('tasks-scoped-to')).toHaveCount(0)
})

test('a count of nothing is not a link', async ({ page }) => {
  await stubIndex(page, [untouched])
  await page.goto('/features')

  await expect(page.getByTestId('feature-sessions-link-206')).toHaveCount(0)
  await expect(page.getByTestId('feature-tasks-link-206')).toHaveCount(0)
  await expect(page.getByTestId('feature-row')).toContainText('—')
})
