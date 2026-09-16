/**
 * The feature ledger page (CAI-247).
 *
 * The ledger payload is stubbed rather than seeded: every assertion here is
 * about how a figure and its qualifier survive being rendered, and binding
 * that to live data would make a rendering regression look like an empty
 * database.
 */
import { test, expect } from './auth-fixture.js'

function ledger(overrides = {}) {
  return {
    key: 'CAI-242',
    stage: {
      feature_key: 'CAI-242', stage: 'shipped',
      blurb: 'completed and never walked back',
      rounds: 1, open_defects: 0, escapes: 0,
    },
    stage_synced_at: '2026-09-13 05:40:00',
    cost: { usd: 41.9, sessions: 3, recalled: 1, shared: 0, gaps: {} },
    work_time: { active_work_ms: 5_400_000, gaps: {} },
    tokens: { input: 1200, output: 3400 },
    sessions: {
      rows: [
        {
          trace_id: 'abcdef0123456789', short_trace: 'abcdef01',
          date: '2026-09-01', title: 'built the ledger',
          cost_usd: 41.9, provenance: 'declared', round: 1, is_primary: true,
        },
      ],
      gaps: { traceless_runs: 0 },
    },
    defects: { open: 0, total: 0, escapes: 0, gaps: {} },
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    ...overrides,
  }
}

async function stub(page, body) {
  await page.route(/\/api\/features\/[^/?]+$/, (route) =>
    route.fulfill({ json: body }))
}

test('the cost figure carries its bound inside the glyph', async ({ page }) => {
  await stub(page, ledger())
  await page.goto('/features/CAI-242')

  const tile = page.getByTestId('figure-cost')
  await expect(tile).toContainText('≤ $41.90')
  await expect(tile).toContainText('upper bound')

  // The bound must survive a crop of the numeral alone — it is part of the
  // number at the number's own size, not an 11px caption beside it.
  const glyphSize = await tile.locator('.fig-glyph').evaluate(
    (el) => getComputedStyle(el).fontSize)
  const valueSize = await tile.locator('.fig-value').evaluate(
    (el) => getComputedStyle(el).fontSize)
  expect(glyphSize).toBe(valueSize)
})

test('an empty ledger renders none declared, never $0.00', async ({ page }) => {
  await stub(page, ledger({
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
  }))
  await page.goto('/features/CAI-242')

  const tile = page.getByTestId('figure-cost')
  await expect(tile.locator('.fig-value')).toHaveText('none declared')
  await expect(tile.locator('.fig-value')).not.toContainText('$0.00')
  // Both empty states NAME the wrong reading rather than avoiding it: a
  // reader who would have assumed $0.00 has to be told they are wrong.
  await expect(tile.locator('.fig-sub')).toContainText('not $0.00')
  await expect(page.getByTestId('none-declared')).toContainText('$0.00')
})

test('an absent figure shrinks rather than shouting', async ({ page }) => {
  await stub(page, ledger({
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
  }))
  await page.goto('/features/CAI-242')

  const size = await page.getByTestId('figure-cost').locator('.fig-value')
    .evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
  expect(size).toBeLessThan(20)
})

test('a gap renders inside the tile it qualifies', async ({ page }) => {
  await stub(page, ledger({
    cost: {
      usd: 41.9, sessions: 3, recalled: 0, shared: 0,
      gaps: { no_usage_captured: 2 },
    },
  }))
  await page.goto('/features/CAI-242')

  await expect(page.getByTestId('figure-cost'))
    .toContainText('2 no-usage-captured')
  await expect(page.getByTestId('figure-defects'))
    .not.toContainText('no-usage-captured')
})

test('the escalation rail fires only where a gap exceeds its figure',
  async ({ page }) => {
    await stub(page, ledger({
      cost: {
        usd: 41.9, sessions: 3, recalled: 0, shared: 0,
        gaps: { no_usage_captured: 2 },
      },
      work_time: {
        active_work_ms: 5_400_000, gaps: { zero_duration: 9 },
      },
    }))
    await page.goto('/features/CAI-242')

    // 2 gaps against 3 sessions: counted, not alarmed.
    await expect(page.getByTestId('figure-cost')).not.toHaveClass(/is-escalated/)
    // 9 gaps against 1 session: the absence is the larger fact.
    await expect(page.getByTestId('figure-work')).toHaveClass(/is-escalated/)
  })

test('gaps outnumbering the rows become ghost rows', async ({ page }) => {
  await stub(page, ledger({
    sessions: {
      rows: ledger().sessions.rows,
      gaps: { traceless_runs: 4 },
    },
  }))
  await page.goto('/features/CAI-242')

  const ghosts = page.locator('.ev-ghost')
  await expect(ghosts).toHaveCount(1)
  await expect(ghosts.first()).toContainText('×4')
  await expect(ghosts.first()).toContainText('traceless-runs')
})

test('stage null renders no chip at all', async ({ page }) => {
  await stub(page, ledger({ stage: null, stage_synced_at: null }))
  await page.goto('/features/CAI-242')

  await expect(page.getByTestId('stage-not-fetched'))
    .toHaveText(/stage not fetched/)
  await expect(page.locator('.stage-line-main .badge')).toHaveCount(0)
  // No stage means no "as of": only a successful fetch writes the timestamp.
  await expect(page.locator('.stage-freshness')).toHaveCount(0)
})

test('a failed refresh is an inline note that says the stage is unchanged',
  async ({ page }) => {
    await stub(page, ledger())
    await page.route(/\/stage\/refresh$/, (route) =>
      route.fulfill({ status: 502, json: { error: 'linear is unreachable' } }))
    await page.goto('/features/CAI-242')

    await page.getByRole('button', { name: /Refresh the stage/ }).click()

    const note = page.locator('.stage-fail')
    await expect(note).toBeVisible()
    await expect(note).toContainText('unchanged')
    await expect(note).toContainText('local')
    // Still there, still dated — nothing was persisted, so nothing moved.
    await expect(page.locator('.stage-freshness')).toContainText('as of')
  })

test('the session row short id links into the trace', async ({ page }) => {
  await stub(page, ledger())
  await page.goto('/features/CAI-242')

  const id = page.locator('.ev-id').first()
  await expect(id).toHaveText('abcdef01')
  await expect(id).toHaveAttribute('href', '/trace/sessions/abcdef0123456789')
})

test('a task-lane row offers no un-declare and names its task', async ({ page }) => {
  await stub(page, ledger({
    sessions: {
      rows: [{
        trace_id: 'abcdef0123456789', short_trace: 'abcdef01',
        date: '2026-09-01', title: 'ran from a task',
        cost_usd: 4.2, provenance: 'task-link', round: null, is_primary: true,
        task_id: 44,
      }],
      gaps: { traceless_runs: 0 },
    },
  }))
  await page.goto('/features/CAI-242')

  await expect(page.locator('.ev-tag-link')).toHaveText('task #44')
  await expect(page.locator('.ev-tag-link')).toHaveAttribute('href', '/tasks/44')
  await expect(page.locator('.ev-undeclare')).toHaveCount(0)
})
