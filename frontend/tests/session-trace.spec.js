/**
 * Session trace view smoke test.
 *
 * Opens the first available session from /trace/sessions, switches to the
 * Timeline view, and asserts the span tree + details panel render. Confirms
 * the read-only GET /api/sessions/:id projection still produces a usable tree
 * for the UI.
 *
 * The read-only contract itself (GET never mutates, POST /materialize does) is
 * covered by tests/test_trace_api.py — that's where we exercise the DB
 * side-effect assertion, since Playwright can't see sqlite state.
 */
import { test, expect } from './auth-fixture.js'

// Open the first session in the list and switch to Timeline view (the default
// is Conversation, which renders chat cards rather than the TreeTable). The
// view-mode tabs only appear once the header has rendered, so waiting for the
// Timeline button doubles as the "trace view loaded" signal.
async function openFirstSessionTimeline(page) {
  await page.goto('/trace/sessions')
  const firstLink = page.locator('a[href^="/trace/sessions/"]').first()
  await expect(firstLink).toBeVisible({ timeout: 10_000 })
  await firstLink.click()

  const timelineTab = page.getByRole('button', { name: 'Timeline', exact: true })
  await expect(timelineTab).toBeVisible({ timeout: 10_000 })
  await timelineTab.click()
}

test.describe('Session Trace View', () => {
  test('renders span tree + details for a real session', async ({ page }) => {
    await openFirstSessionTimeline(page)

    // At least one span row should render in the TreeTable.
    const spanRows = page.locator('[role="row"][aria-level]')
    await expect(spanRows.first()).toBeVisible({ timeout: 10_000 })
    expect(await spanRows.count()).toBeGreaterThanOrEqual(1)

    // Span details panel populates for the auto-selected span.
    await expect(page.getByRole('heading', { name: 'Span details' }))
      .toBeVisible({ timeout: 10_000 })
  })

  test('turns sidebar links turns to spans', async ({ page }) => {
    await openFirstSessionTimeline(page)

    // The panel starts closed with a "load" button — the cost is per-turn
    // aggregation on the backend, so we defer until the user asks.
    const loadBtn = page.getByRole('button', { name: /^load$/i })
    await expect(loadBtn).toBeVisible({ timeout: 10_000 })
    await loadBtn.click()

    // The count text ("N turns") replaces the button once the fetch settles.
    // The regex covers both the populated path and the "0 turns" edge case for
    // old sessions.
    const countLabel = page.getByText(/\d+ turns/i)
    await expect(countLabel).toBeVisible({ timeout: 10_000 })

    const countText = await countLabel.textContent()
    const match = (countText || '').match(/(\d+) turns/)
    const turnCount = match ? parseInt(match[1], 10) : 0
    test.skip(turnCount === 0,
      'session has no turn_usage rows — exercised in unit tests instead')

    // At least one row should now render with a HH:MM:SS local time.
    // `turn_usage.timestamp` is UTC in the DB; the view reformats to local —
    // regression guard against the format slipping back to ISO.
    const firstRow = page.locator('ul > li').filter({ hasText: /\d{2}:\d{2}:\d{2}/ }).first()
    await expect(firstRow).toBeVisible({ timeout: 10_000 })

    // Clicking the first (earliest) turn row must select it — the deterministic
    // proof that the row → selectTurn wiring fired.
    await firstRow.click()
    await expect(firstRow).toHaveClass(/bg-indigo-50/, { timeout: 5_000 })

    // …and it must dim the overview-strip bars that fall outside that turn's
    // interval. Each bar is a prompt-level root; the first turn's interval is
    // `(-∞, firstTurn.ts]`, so EVERY later prompt bar gets dimmed. Gate on the
    // real precondition — ≥2 strip bars — not turnCount: a session with one
    // prompt but many tool-use turns has turnCount>1 yet nothing to dim.
    const bars = page.locator('[data-testid="overview-strip-bar"]')
    if ((await bars.count()) >= 2) {
      const dimmed = page.locator('[data-testid="overview-strip-bar"].opacity-20')
      await expect(dimmed.first()).toBeVisible({ timeout: 5_000 })
    }
  })

  test('consecutive loads are idempotent', async ({ page }) => {
    // The previous implementation mutated DB state on every GET. Now two
    // consecutive loads of the same session should return the same payload
    // shape (same number of spans).
    await page.goto('/trace/sessions')
    const firstLink = page.locator('a[href^="/trace/sessions/"]').first()
    await expect(firstLink).toBeVisible({ timeout: 10_000 })
    const href = await firstLink.getAttribute('href')
    expect(href).toBeTruthy()

    // `href` is the frontend route `/trace/sessions/<id>`; the API route drops
    // the `/trace` prefix → `/api/sessions/<id>`.
    const apiPath = `/api${href.replace('/trace', '')}`

    // page.request is a separate APIRequestContext that doesn't inherit the
    // app's Authorization header (the SPA reads the token from localStorage and
    // attaches it per call). The deny-by-default /api interceptor rejects an
    // unauthenticated request, so forward the same Bearer token explicitly.
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    expect(token).toBeTruthy()
    const headers = { Authorization: `Bearer ${token}` }

    const fetchCount = async () => {
      const resp = await page.request.get(apiPath, { headers })
      expect(resp.ok()).toBeTruthy()
      const body = await resp.json()
      return body.spans.length
    }

    const first = await fetchCount()
    const second = await fetchCount()
    const third = await fetchCount()
    expect(second).toBe(first)
    expect(third).toBe(first)
  })
})
