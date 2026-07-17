/**
 * Regression: changing a facet filter must NOT unmount the filter form.
 *
 * Before the fix, SessionsView wrapped the ENTIRE page (sticky header +
 * filter pills + table) in `v-if="loading" / v-else`. Every facet change
 * calls useCursor.load() which flips `loading`, so the whole page —
 * including the pill you just touched and the ones under it — unmounted and
 * remounted: the "blink". The fix keeps the header + filters always mounted
 * and scopes the loading placeholder to the results region (initial load
 * only). This test gates the reload response so the in-flight `loading`
 * window is observable, then asserts the form stays attached and the prior
 * rows stay visible during that window.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

function row(traceId, overrides = {}) {
  return {
    trace_id: traceId,
    title: `BLINK_FIXTURE_${traceId.slice(0, 8)}`,
    status: 'ended', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000',
    last_seen: '2026-07-10T12:00:00.000000',
    span_count: 3, file_edits: 1, tool_calls: 2, skill_reads: 0,
    rule_checks: 0, plans: 0, prompts: 1,
    agent_type: 'claude', agent_kind: 'claude', origin: 'session',
    category: 'user', is_workflow: false, is_run: false,
    model: null, cwd: null, repos: [], primary_repo: null, is_multi_repo: false,
    context_pct: null, context_pct_all: null, active_work_ms: null,
    active_pct: null, idle_ms: null,
    tags: [{ slug: 'user', source: 'auto', builtin: true }],
    ...overrides,
  }
}

function envelope(rows) {
  return {
    items: rows, sessions: rows,
    pagination: { next_cursor: null, size: 50, has_next: false },
    tag_counts: { user: rows.length },
    builtin_tags: [{ slug: 'user', label: 'User' }],
    server_now: '2026-07-10T13:00:00.000000',
    server_now_utc: '2026-07-10T09:00:00.000Z',
  }
}

test('changing a facet filter does not unmount the filter form (no blink)', async ({ page }) => {
  const idA = randomUUID()
  const idB = randomUUID()

  // Gate the reload (the `active=` request) so its in-flight window is
  // observable. The initial (no-`active`) request resolves immediately.
  let releaseReload
  const reloadGate = new Promise((r) => { releaseReload = r })

  await page.route('**/api/sessions?*', async (route, request) => {
    const url = new URL(request.url())
    const active = url.searchParams.get('active')
    if (active === 'active') {
      await reloadGate                                   // hold the reload
      return route.fulfill({ json: envelope([row(idA)]) })
    }
    if (active === 'inactive') {
      return route.fulfill({ json: envelope([]) })       // 0-result branch
    }
    return route.fulfill({ json: envelope([row(idA), row(idB)]) })
  })

  await page.goto('/trace/sessions')

  // Initial load rendered: both rows + the filter form are present.
  await expect(page.getByText(`BLINK_FIXTURE_${idA.slice(0, 8)}`).first()).toBeVisible()
  const filterForm = page.locator('form.session-filters')
  await expect(filterForm).toBeVisible()

  // Open the Status facet and pick "Active only" → triggers a reload. Arm
  // the request wait BEFORE the click so we don't miss the in-flight event.
  const reloadReq = page.waitForRequest((r) => r.url().includes('active=active'))
  await page.getByLabel('Filter by active status').click()
  await page.getByRole('option', { name: 'Active only', exact: true }).click()
  await reloadReq                                        // held open by the gate

  // THE ASSERTION: during the in-flight reload the filter form is STILL
  // mounted (old bug: replaced by a full-page "Loading sessions…"), and the
  // previously loaded rows are still visible (useCursor keeps them until the
  // fetch resolves). The full-page loader must NOT have taken over.
  await expect(filterForm).toBeVisible()
  await expect(page.getByLabel('Filter by active status')).toBeVisible()
  await expect(page.getByText(`BLINK_FIXTURE_${idA.slice(0, 8)}`).first()).toBeVisible()

  // Release the reload; the list settles to the single active row.
  releaseReload()
  await expect(page.getByText(`BLINK_FIXTURE_${idB.slice(0, 8)}`).first()).toBeHidden()
  await expect(filterForm).toBeVisible()
})

test('a facet filter yielding zero rows shows the empty message, not a stuck loader', async ({ page }) => {
  const idA = randomUUID()
  await page.route('**/api/sessions?*', async (route, request) => {
    const active = new URL(request.url()).searchParams.get('active')
    return route.fulfill({ json: envelope(active === 'inactive' ? [] : [row(idA)]) })
  })

  await page.goto('/trace/sessions')
  await expect(page.getByText(`BLINK_FIXTURE_${idA.slice(0, 8)}`).first()).toBeVisible()

  await page.getByLabel('Filter by active status').click()
  await page.getByRole('option', { name: 'Inactive only' }).click()

  await expect(page.getByText('No session traces yet.')).toBeVisible()
  await expect(page.getByText('Loading sessions…')).toBeHidden()
  await expect(page.locator('form.session-filters')).toBeVisible()
})

const OUT = '/private/tmp/claude-501/-Users-taowang-regin/02537478-02be-46d7-b7cd-459f9c0a2b20/scratchpad'

for (const [label, w, h] of [['desktop', 1280, 800], ['mobile', 390, 844]]) {
  test(`renders without horizontal overflow at ${label} (${w}px)`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h })
    const idA = randomUUID(); const idB = randomUUID()
    await page.route('**/api/sessions?*', (route) => route.fulfill({ json: envelope([row(idA), row(idB)]) }))

    await page.goto('/trace/sessions')
    // Wait for the list to settle (rows present, not empty/loading) without
    // asserting a specific row node — the desktop <table> and mobile <ul>
    // both render the title but only one is visible per breakpoint.
    await expect(page.locator('form.session-filters')).toBeVisible()
    await expect(page.getByText('No session traces yet.')).toBeHidden()
    await expect(page.getByText('Loading sessions…')).toBeHidden()

    const geom = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    await page.screenshot({ path: `${OUT}/sessions_${label}.png`, fullPage: false })
    expect(geom.scroll, `no horizontal overflow (${label})`).toBeLessThanOrEqual(geom.client)
  })
}
