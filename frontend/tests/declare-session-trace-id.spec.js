/**
 * Declaring a session by pasting its trace id.
 *
 * The sessions endpoint matches `q` against the title and trace ids against a
 * separate `trace_id=` prefix param, so a dialog that only ever sends `q`
 * searches titles with an id and finds nothing.
 */
import { test, expect } from './auth-fixture.js'

const TRACE = '1df74b07-55e8-4a9a-8056-23585574ede3'

function ledger() {
  return {
    id: 257, title: 'the feature', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: {
      stage: 'spec', blurb: 'specified, not started',
      rounds: 1, claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: null, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: {
      declared_sessions: 0, all_sessions: 10,
      declared_usd: 0, all_usd: 1,
    },
  }
}

/** Stub the ledger, and answer /api/sessions the way the server does: `q`
 *  against the title only, `trace_id` as a case-insensitive prefix. */
async function stub(page, seen) {
  await page.route(/\/api\/features\/[^/?]+$/, (route) =>
    route.fulfill({ json: ledger() }))
  await page.route(/\/api\/sessions\?/, (route) => {
    const url = new URL(route.request().url())
    seen.push(url.search)
    const q = (url.searchParams.get('q') || '').toLowerCase()
    const prefix = (url.searchParams.get('trace_id') || '').toLowerCase()
    const row = {
      trace_id: TRACE, title: 'declared a session', started_at: '2026-09-14',
      cost_usd: 1.5,
    }
    const matches = prefix
      ? row.trace_id.startsWith(prefix)
      : (!q || row.title.toLowerCase().includes(q))
    route.fulfill({ json: { sessions: matches ? [row] : [] } })
  })
}

test('a pasted full trace id finds its session', async ({ page }) => {
  const seen = []
  await stub(page, seen)
  await page.goto('/features/257')

  await page.getByRole('button', { name: /Declare a session/ }).click()
  const input = page.getByPlaceholder(/paste a trace id/)
  await input.fill(TRACE)
  await input.press('Enter')

  await expect(page.locator('.picker-row')).toHaveCount(1)
  await expect(page.locator('.picker-row')).toContainText('1df74b07')
  // An id is asked for by id: sending it as `q` searches titles.
  expect(seen.at(-1)).toContain(`trace_id=${TRACE}`)
  expect(seen.at(-1)).not.toContain('q=')
})

test('a title search still goes out as q', async ({ page }) => {
  const seen = []
  await stub(page, seen)
  await page.goto('/features/257')

  await page.getByRole('button', { name: /Declare a session/ }).click()
  const input = page.getByPlaceholder(/paste a trace id/)
  await input.fill('declared')
  await input.press('Enter')

  await expect(page.locator('.picker-row')).toHaveCount(1)
  expect(seen.at(-1)).toContain('q=declared')
  expect(seen.at(-1)).not.toContain('trace_id=')
})
