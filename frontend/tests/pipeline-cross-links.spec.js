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

    // There is no defects list outside the feature (feature #110): the tile
    // lands on this page's own defect list, and moves the page to it.
    await page.getByTestId('figure-defects').locator('.fig-link').click()
    await expect(page).toHaveURL(/\/features\/242#feature-defects$/)
    await expect(page.locator('#feature-defects')).toBeInViewport()
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
